"""WhatsApp Endpoints - HTTP routes for outbound WhatsApp sends and the Meta webhook.

HTTP layer for /api/whatsapp: bearer-protected template, text and recent-events routes, plus the Meta webhook handshake and signed receiver that processes STOP opt-outs, button clicks, reachability and delivery statuses in the background.

Features:
    - Template Message Sending: POST endpoint for approved template message dispatch
    - Text Message Sending: POST endpoint for freeform text in 24h session windows
    - Webhook Verification: GET endpoint for Meta challenge-response handshake
    - Webhook Reception: POST endpoint for inbound messages and statuses, processed in the background
    - Recent Events Retrieval: GET endpoint for recent WhatsApp webhook events from logs
    - Signature Verification: HMAC-SHA256 payload validation for inbound webhooks
    - Opt-out Handling: An inbound "STOP" reply flips the sender's WhatsApp notification preference off
    - Reachability Tracking: An inbound message from a provider's number marks it WhatsApp-reachable
    - Delivery Status Ledger: Status callbacks record read/failed state; failures trigger a nudge fallback
    - Authentication: Template, text and recent-events routes require a valid bearer token
    - Event Logging: Inbound webhook events stored to database logs and a local status JSONL file

Classes:
    No classes defined (module uses FastAPI router pattern)

Functions:
    send_whatsapp_template(): POST /send_template — send an approved template message
    send_whatsapp_text(): POST /send_text — send a freeform text message
    whatsapp_webhook_verify(): GET /webhook — Meta webhook verification handshake
    whatsapp_webhook_receive(): POST /webhook — receive inbound messages and status updates
    whatsapp_recent_events(): GET /recent_events — retrieve recent WhatsApp webhook events
    _whatsapp_error_to_http(): Map a WhatsAppError subclass onto the appropriate HTTPException
    _extract_message_id(): Pull the Meta-assigned message id out of a send-message response payload
    _append_status_jsonl(): Persist one status event to logs/whatsapp_status.jsonl for wamid lookup
    _mark_inbound_sender_reachable(): Flip etown_provider.whatsapp True when an inbound message proves the sender is reachable
    _process_whatsapp_events(): Background handler for parsed webhook events — STOP opt-out, button clicks, and status/read/failed tracking

Dependencies:
    - fastapi: Router, dependency injection, and HTTP exception handling
    - json: Request/response data serialization and the status JSONL log
    - logging: Operation tracking and debugging
    - time: Request/status timestamp tracking
    - app.core.security: Token verification for authenticated routes
    - app.utils.whatsapp: WhatsApp API client and webhook utilities
    - app.agent_infrastructure.tools.tools_utils.search_notifications: Blocked-nudge fallback on a failed delivery status
    - app.etown.notifications.preferences: set_whatsapp_stopped — honours inbound STOP replies
    - app.etown.notifications.whatsapp_clicks: record_button_click — logs button/interactive taps
    - app.etown.notifications.whatsapp_status: record_status / mark_ledger_opened_by_wamid — delivery status ledger updates
    - app.etown.provider.provider_utils: update_provider_whatsapp_reachability — flips a provider's WhatsApp-reachable flag (imported locally)

Updates:
    26-May-2026: Added Features, Classes, Functions, Dependencies, Updates sections to complete module docstring
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import PlainTextResponse

STATUS_LOG_PATH = Path("logs/whatsapp_status.jsonl")

from app.core.security import verify_token
from app.schemas.api import (
    WhatsAppSendResponse,
    WhatsAppTemplateRequest,
    WhatsAppTextRequest,
)
from app.utils.whatsapp import (
    WhatsAppAuthError,
    WhatsAppError,
    WhatsAppInvalidRecipientError,
    WhatsAppRateLimitError,
    normalize_msisdn,
    parse_webhook_payload,
    send_whatsapp_template_message,
    send_whatsapp_text_message,
    verify_webhook_signature,
    verify_webhook_subscription,
)
from app.agent_infrastructure.tools.tools_utils.search_notifications import (
    handle_blocked_nudge_fallback as _handle_blocked_nudge_fallback,
)
from app.etown.notifications.preferences import set_whatsapp_stopped as _set_wa_stopped
from app.etown.notifications.whatsapp_clicks import record_button_click as _record_button_click
from app.etown.notifications.whatsapp_status import (
    record_status as _record_wa_status,
    mark_ledger_opened_by_wamid as _mark_ledger_opened,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _whatsapp_error_to_http(exc: WhatsAppError) -> HTTPException:
    """Map a WhatsAppError subclass onto a 400, 429 or 502 HTTPException."""
    if isinstance(exc, WhatsAppAuthError):
        return HTTPException(status_code=502, detail=f"WhatsApp auth error: {exc}")
    if isinstance(exc, WhatsAppInvalidRecipientError):
        return HTTPException(status_code=400, detail=f"Invalid recipient: {exc}")
    if isinstance(exc, WhatsAppRateLimitError):
        return HTTPException(status_code=429, detail=f"WhatsApp rate limited: {exc}")
    return HTTPException(status_code=502, detail=f"WhatsApp send failed: {exc}")


def _extract_message_id(raw: dict) -> Optional[str]:
    """Return the first message id from a Meta send response, or None."""
    messages = raw.get("messages") if isinstance(raw, dict) else None
    if isinstance(messages, list) and messages:
        first = messages[0]
        if isinstance(first, dict):
            return first.get("id")
    return None


@router.post(
    "/send_template",
    response_model=WhatsAppSendResponse,
    status_code=200,
    summary="Send WhatsApp template message",
    description=(
        "Send an approved WhatsApp Business template message via the Meta Cloud "
        "API. Use this for any notification outside the 24-hour customer service "
        "window. The template name must be pre-approved in the Meta Business Manager."
    ),
)
async def send_whatsapp_template(
    request: WhatsAppTemplateRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/whatsapp/send_template: send an approved template message via the Meta Cloud API."""
    try:
        raw = await send_whatsapp_template_message(
            mobile_number=request.mobile_number,
            template_name=request.template_name,
            language_code=request.language_code,
            body_parameters=request.body_parameters,
            body_named_parameters=request.body_named_parameters,
            header_parameters=request.header_parameters,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except WhatsAppError as exc:
        logger.error("WhatsApp template send failed: %s", exc, exc_info=True)
        raise _whatsapp_error_to_http(exc)

    return WhatsAppSendResponse(
        success=True,
        message_id=_extract_message_id(raw),
        recipient=normalize_msisdn(request.mobile_number),
        raw=raw,
    )


@router.post(
    "/send_text",
    response_model=WhatsAppSendResponse,
    status_code=200,
    summary="Send WhatsApp freeform text message",
    description=(
        "Send a freeform WhatsApp text message. Only allowed within the 24-hour "
        "customer service window (i.e. after the user messaged the business). "
        "Outside that window, use /send_template."
    ),
)
async def send_whatsapp_text(
    request: WhatsAppTextRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/whatsapp/send_text: send a freeform text message within the 24-hour window."""
    try:
        raw = await send_whatsapp_text_message(
            mobile_number=request.mobile_number,
            body=request.body,
            preview_url=request.preview_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except WhatsAppError as exc:
        logger.error("WhatsApp text send failed: %s", exc, exc_info=True)
        raise _whatsapp_error_to_http(exc)

    return WhatsAppSendResponse(
        success=True,
        message_id=_extract_message_id(raw),
        recipient=normalize_msisdn(request.mobile_number),
        raw=raw,
    )


@router.get(
    "/webhook",
    summary="WhatsApp webhook verification (Meta GET handshake)",
    description=(
        "Endpoint Meta calls when (re)configuring the webhook in the App "
        "Dashboard. It echoes back the `hub.challenge` parameter if "
        "`hub.verify_token` matches WHATSAPP_WEBHOOK_VERIFY_TOKEN."
    ),
    response_class=PlainTextResponse,
)
async def whatsapp_webhook_verify(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """Handle GET /api/whatsapp/webhook: echo Meta's hub.challenge when the verify token matches, else 403."""
    challenge = verify_webhook_subscription(
        mode=hub_mode, token=hub_verify_token, challenge=hub_challenge
    )
    if challenge is None:
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    return PlainTextResponse(content=challenge, status_code=200)


@router.post(
    "/webhook",
    summary="WhatsApp webhook receiver (inbound messages + status updates)",
    description=(
        "Receives inbound WhatsApp messages, delivery/read status updates and "
        "errors from Meta. Validates the X-Hub-Signature-256 header using "
        "WHATSAPP_APP_SECRET. Meta requires a 200 OK within ~10s, so heavy work "
        "is queued as a background task."
    ),
)
async def whatsapp_webhook_receive(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
):
    """Handle POST /api/whatsapp/webhook: verify the signature, parse Meta events and queue them for background processing."""
    raw_body = await request.body()

    if not verify_webhook_signature(raw_body, x_hub_signature_256):
        logger.warning("WhatsApp webhook rejected: invalid signature")
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        payload = await request.json()
    except Exception:
        logger.exception("WhatsApp webhook: invalid JSON body")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if payload.get("object") != "whatsapp_business_account":
        logger.warning(
            "WhatsApp webhook: unexpected object=%r", payload.get("object")
        )
        return {"status": "ignored"}

    events = parse_webhook_payload(payload)
    logger.info(
        "WhatsApp webhook received: %d event(s) (messages=%d, statuses=%d)",
        len(events),
        sum(1 for e in events if e["kind"] == "message"),
        sum(1 for e in events if e["kind"] == "status"),
    )
    background_tasks.add_task(_process_whatsapp_events, events)

    return {"status": "ok", "events": len(events)}


@router.get(
    "/recent_events",
    summary="Read recent WhatsApp webhook events (status updates)",
    description=(
        "Returns the most recent status events that Meta has POSTed to the "
        "webhook, read from logs/whatsapp_status.jsonl. Useful when Azure "
        "Log stream isn't accessible. Optional `wamid` filters to a single "
        "message id; `limit` caps the number of records (default 50)."
    ),
)
async def whatsapp_recent_events(
    limit: int = Query(50, ge=1, le=500, description="Max events to return"),
    wamid: Optional[str] = Query(None, description="Filter to this wamid only"),
    token: str = Depends(verify_token),
):
    """Handle GET /api/whatsapp/recent_events: return the newest status events from the JSONL log, optionally filtered by wamid."""
    if not STATUS_LOG_PATH.exists():
        return {"path": str(STATUS_LOG_PATH), "count": 0, "events": []}

    events: list[dict] = []
    try:
        with STATUS_LOG_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.debug("whatsapp events: skipping a status-log line that is not valid JSON")
                    continue
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read log: {exc}")

    if wamid:
        events = [e for e in events if e.get("wamid") == wamid]

    events = events[-limit:]
    return {
        "path": str(STATUS_LOG_PATH),
        "count": len(events),
        "events": list(reversed(events)),
    }


def _append_status_jsonl(event: dict) -> None:
    """Append a status event to logs/whatsapp_status.jsonl, logging a warning on failure."""
    try:
        STATUS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "received_at": int(time.time()),
            "wamid": event.get("message_id"),
            "status": event.get("status"),
            "recipient_id": event.get("recipient_id"),
            "timestamp": event.get("timestamp"),
            "errors": event.get("errors"),
            "raw": event.get("raw"),
        }
        with STATUS_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("Could not persist WhatsApp status event: %s", exc)


async def _mark_inbound_sender_reachable(ev: dict) -> None:
    """Mark the inbound message sender's provider record WhatsApp-reachable, best effort and never raising."""
    sender = ev.get("from") or ev.get("wa_id")
    if not sender:
        return
    try:
        from app.etown.provider.provider_utils import (
            update_provider_whatsapp_reachability,
        )
        await update_provider_whatsapp_reachability(sender, True)
    except Exception as exc:
        logger.warning(
            "Could not mark inbound sender %s WhatsApp-reachable: %s", sender, exc
        )


async def _process_whatsapp_events(events: list[dict]) -> None:
    """Process parsed webhook events: handle inbound STOP and button replies, and record, ledger and log status updates."""
    for ev in events:
        kind = ev.get("kind")
        if kind == "message":
            logger.info(
                "WhatsApp inbound message from %s (wamid=%s, type=%s): %r",
                ev.get("from"),
                ev.get("message_id"),
                ev.get("type"),
                ev.get("text"),
            )
            await _mark_inbound_sender_reachable(ev)
            text_body = (ev.get("text") or "").strip().upper()
            if text_body == "STOP":
                userprofileid = ev.get("userprofileid") or ev.get("from")
                if userprofileid:
                    try:
                        await _set_wa_stopped(userprofileid)
                        logger.info("WhatsApp STOP received from %s — preferences updated", userprofileid)
                    except Exception as _exc:
                        logger.warning("WhatsApp STOP: failed to update preferences for %s: %s", userprofileid, _exc)
            if ev.get("type") in ("button", "interactive"):
                await _record_button_click(ev)
        elif kind == "status":
            logger.info(
                "WhatsApp status update: wamid=%s status=%s recipient=%s errors=%s",
                ev.get("message_id"),
                ev.get("status"),
                ev.get("recipient_id"),
                ev.get("errors"),
            )
            await _record_wa_status(ev)
            if ev.get("status") == "read":
                await _mark_ledger_opened(ev.get("message_id"))
            if ev.get("status") == "failed":
                await _handle_blocked_nudge_fallback(ev.get("message_id"), ev.get("errors"))
            _append_status_jsonl(ev)
        elif kind == "error":
            logger.error("WhatsApp webhook error event: %s", ev.get("raw"))
