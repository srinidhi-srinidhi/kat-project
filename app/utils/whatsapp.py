"""WhatsApp Helpers - async Meta WhatsApp Business Cloud client for templates, text and webhooks.

Utility client for the Meta Graph messages API: sends approved template and in-session text messages, wraps them in per-notification helpers for offers, OTPs, welcomes, bookings, unverified-provider nudges and FOS confirmations, and verifies and parses inbound webhooks, raising typed errors on failure.

Features:
    - Configuration: WHATSAPP_* settings hold credentials, API version, webhook secrets and one template name per notification
    - Template Messages: Send approved template messages for notifications outside 24h window
    - Text Messages: Send freeform text messages within 24-hour session windows
    - Offer Notifications: Pre-built helper for business offer broadcast notifications
    - OTP Notifications: Pre-built helper for OTP delivery via WhatsApp
    - Welcome Notifications: Pre-built helper for new user welcome messages
    - Booking Notifications: Pre-built helpers for booking request/accepted/completed events
    - Unverified-provider Nudges: discovery nudge, utility failsafe fallback, and an enquiry nudge
    - FOS Onboarding Confirmation: template sent to a provider just captured in the field
    - Image Headers: templates can carry an image header by public URL
    - Document Headers: templates can carry a document header (e.g. an invoice PDF) by public URL
    - Town Image Review Alert: town image change request template sent to support reviewers
    - Webhook Verification: Meta challenge-response handshake for webhook subscription
    - Webhook Signature Verification: HMAC-SHA256 signature validation for inbound payloads
    - Webhook Payload Parsing: Structured extraction of messages from inbound webhook events
    - MSISDN Normalization & Validation: E.164 normalization plus a local pre-send check
    - Custom Exceptions: Typed error hierarchy for auth, rate-limit, and recipient errors

Classes:
    WhatsAppError: Base exception for WhatsApp API errors
    WhatsAppAuthError: Authentication/authorization failure
    WhatsAppInvalidRecipientError: Invalid or non-WhatsApp recipient number
    WhatsAppRateLimitError: API rate limit exceeded

Functions:
    normalize_msisdn(): Normalize phone number to E.164 format for WhatsApp
    is_valid_whatsapp_number(): Local-only E.164 plausibility check before attempting a send
    _require_credentials(): Validate WHATSAPP_PHONE_NUMBER_ID/ACCESS_TOKEN are configured, return them with the API base/version
    _endpoint(): Build the Graph API messages endpoint URL from configured credentials
    _auth_headers(): Build the Bearer-auth + JSON content-type request headers
    _build_template_payload(): Build a Cloud API template-send payload from named/positional body, header, and button parameters
    _parse_error(): Map a Graph API error response onto the WhatsAppAuthError/InvalidRecipientError/RateLimitError hierarchy
    _post(): POST a message payload to the Graph API messages endpoint via the shared HTTP client pool
    send_whatsapp_template_message(): Send an approved template message
    send_whatsapp_text_message(): Send a freeform text message
    send_offer_whatsapp_notification(): Send offer broadcast notification
    verify_webhook_subscription(): Handle Meta webhook verification challenge
    verify_webhook_signature(): Validate inbound webhook HMAC-SHA256 signature
    parse_webhook_payload(): Extract message events from inbound webhook payload
    send_welcome_whatsapp_notification(): Send welcome message to new members
    send_otp_whatsapp_notification(): Send OTP via WhatsApp template
    send_booking_request_whatsapp_notification(): Send new booking alert to provider
    send_booking_accepted_whatsapp_notification(): Send booking accepted alert to member
    send_booking_completed_whatsapp_notification(): Send booking completed alert to member
    _extract_wamid(): Pull Meta's message id out of a send response
    send_unverified_provider_whatsapp_notification_raising(): Nudge unverified provider discovered in member search
    send_unverified_provider_fallback_whatsapp_notification_raising(): Utility-category failsafe re-sent to numbers the marketing nudge could not reach
    send_unverified_provider_enquiry_whatsapp_notification(): New-customer-inquiry nudge for the notify_unverified API
    send_fos_business_onboarding_whatsapp_notification(): FOS onboarding confirmation sent to the provider just captured
    send_community_post_review_whatsapp_notification(): Event or update submitted-for-approval alert sent to a support reviewer
    send_town_image_request_whatsapp_notification(): Town image change request alert, with the image as header, sent to a reviewer

Dependencies:
    - httpx: Async HTTP client for Meta Graph API requests
    - app.core.config.settings: WHATSAPP_* credentials, template names, and API/timeout configuration
    - app.utils.http_client.get_http_client: Shared/tracked HTTP client pool used for every send
    - app.etown.notifications.whatsapp_template_config.is_template_enabled: DB-driven on/off switch for the unverified-provider nudge templates
    - hashlib / hmac: HMAC-SHA256 webhook signature verification
    - re: Phone number normalization regex

API reference: https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages

Updates:
    26-May-2026: Added Features, Classes, Functions, Dependencies, Updates sections to complete module docstring
    24-Jun-2026: Added booking WhatsApp notification helpers for request/accepted/completed events
    25-Jun-2026: Added send_unverified_provider_whatsapp_notification() and send_unverified_provider_enquiry_whatsapp_notification().
    26-Jun-2026: Added is_valid_whatsapp_number(); sends validate the number before any API call.
    29-Jun-2026: _post() reuses the shared HTTP client pool; the raising sender returns wamid.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    16-Sep-2026: Added send_fos_business_onboarding_whatsapp_notification() for POST /unverified_provider.
    25-Sep-2026: send_fos_business_onboarding_whatsapp_notification() returns (accepted, wamid).
    05-Oct-2026: Added send_community_post_review_whatsapp_notification() for submitted events and updates.
    06-Oct-2026: send_community_post_review_whatsapp_notification() takes the post's CRM record path for its button.
    08-Oct-2026: Template sends take header_image_url; added send_town_image_request_whatsapp_notification() with a review button path.
    22-Oct-2026: Template sends take header_document_url and header_document_filename.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Any, Iterable, Optional

import httpx

from app.core.config import settings
from app.utils.http_client import get_http_client

logger = logging.getLogger(__name__)


class WhatsAppError(Exception):
    """Base WhatsApp Cloud API error."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_text: Optional[str] = None,
        error_code: Optional[int] = None,
    ):
        """Store the message with the HTTP status, raw response text and Graph API error code."""
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text
        self.error_code = error_code


class WhatsAppAuthError(WhatsAppError):
    """Invalid or expired access token / insufficient permissions."""


class WhatsAppInvalidRecipientError(WhatsAppError):
    """Recipient number is invalid or not reachable on WhatsApp."""


class WhatsAppRateLimitError(WhatsAppError):
    """Cloud API rate limit reached."""


_DIGITS_RE = re.compile(r"\D+")
_INDIA_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")

_E164_MIN_DIGITS = 7
_E164_MAX_DIGITS = 15


def normalize_msisdn(mobile_number: str) -> str:
    """Return the number as E.164 digits without '+', prefixing 91 to 10-digit Indian mobiles.

    Raises:
        ValueError: For empty input or a result outside 7 to 15 digits.
    """
    if not mobile_number:
        raise ValueError("mobile_number is required")
    cleaned = _DIGITS_RE.sub("", mobile_number)
    if not cleaned:
        raise ValueError(f"mobile_number has no digits: {mobile_number!r}")
    if _INDIA_MOBILE_RE.match(cleaned):
        cleaned = "91" + cleaned
    if len(cleaned) < _E164_MIN_DIGITS or len(cleaned) > _E164_MAX_DIGITS:
        raise ValueError(
            f"mobile_number {mobile_number!r} normalizes to {cleaned!r} "
            f"({len(cleaned)} digits) which is outside the valid E.164 range "
            f"({_E164_MIN_DIGITS}–{_E164_MAX_DIGITS} digits)."
        )
    return cleaned


def is_valid_whatsapp_number(mobile_number: str) -> bool:
    """Return True if the number normalizes to a plausible E.164 number, checked locally without calling WhatsApp."""
    if not mobile_number:
        return False
    try:
        normalize_msisdn(mobile_number)
        return True
    except ValueError:
        logger.debug("[WhatsApp] %r is not a valid MSISDN", mobile_number)
        return False


def _require_credentials() -> tuple[str, str, str, str]:
    """Return the API base URL, version, phone number id and access token, raising WhatsAppError if either id or token is unset."""
    if not settings.WHATSAPP_PHONE_NUMBER_ID:
        raise WhatsAppError("WHATSAPP_PHONE_NUMBER_ID is not configured")
    if not settings.WHATSAPP_ACCESS_TOKEN:
        raise WhatsAppError("WHATSAPP_ACCESS_TOKEN is not configured")
    return (
        settings.WHATSAPP_API_BASE_URL.rstrip("/"),
        settings.WHATSAPP_API_VERSION,
        settings.WHATSAPP_PHONE_NUMBER_ID,
        settings.WHATSAPP_ACCESS_TOKEN,
    )


def _endpoint() -> str:
    """Return the Cloud API messages URL for the configured phone number id."""
    base, version, phone_id, _ = _require_credentials()
    return f"{base}/{version}/{phone_id}/messages"


def _auth_headers() -> dict[str, str]:
    """Return bearer-token and JSON content-type headers for the Cloud API."""
    _, _, _, token = _require_credentials()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _build_template_payload(
    to: str,
    template_name: str,
    language_code: str,
    body_parameters: Optional[Iterable[str]] = None,
    body_named_parameters: Optional[dict[str, str]] = None,
    header_parameters: Optional[Iterable[str]] = None,
    button_parameters: Optional[Iterable[dict[str, Any]]] = None,
    header_image_url: Optional[str] = None,
    header_document_url: Optional[str] = None,
    header_document_filename: Optional[str] = None,
) -> dict[str, Any]:
    """Build a Cloud API template payload with optional text, image or document header, named or positional body, and button components.

    Raises:
        ValueError: When both positional and named body parameters, or more than one header kind, are given.
    """
    if body_parameters and body_named_parameters:
        raise ValueError(
            "Pass either body_parameters (positional) or "
            "body_named_parameters (named), not both."
        )
    if sum(bool(h) for h in (header_parameters, header_image_url, header_document_url)) > 1:
        raise ValueError(
            "Pass only one of header_parameters (text), header_image_url (image) "
            "or header_document_url (document)."
        )

    components: list[dict[str, Any]] = []
    if header_document_url:
        document: dict[str, Any] = {"link": header_document_url}
        if header_document_filename:
            document["filename"] = header_document_filename
        components.append(
            {
                "type": "header",
                "parameters": [{"type": "document", "document": document}],
            }
        )
    elif header_image_url:
        components.append(
            {
                "type": "header",
                "parameters": [{"type": "image", "image": {"link": header_image_url}}],
            }
        )
    elif header_parameters:
        components.append(
            {
                "type": "header",
                "parameters": [
                    {"type": "text", "text": str(p)} for p in header_parameters
                ],
            }
        )
    if body_named_parameters:
        components.append(
            {
                "type": "body",
                "parameters": [
                    {
                        "type": "text",
                        "parameter_name": name,
                        "text": str(value),
                    }
                    for name, value in body_named_parameters.items()
                ],
            }
        )
    elif body_parameters:
        components.append(
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(p)} for p in body_parameters
                ],
            }
        )
    if button_parameters:
        for idx, btn in enumerate(button_parameters):
            components.append(
                {
                    "type": "button",
                    "sub_type": btn.get("sub_type", "url"),
                    "index": btn.get("index", idx),
                    "parameters": btn.get("parameters", []),
                }
            )

    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
        },
    }
    if components:
        payload["template"]["components"] = components
    return payload


def _parse_error(response_text: str, status_code: int) -> WhatsAppError:
    """Map a Graph API error response to an auth, invalid-recipient, rate-limit or generic WhatsAppError."""
    import json

    error_code: Optional[int] = None
    message = response_text or f"HTTP {status_code}"
    try:
        data = json.loads(response_text)
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            error_code = err.get("code")
            message = err.get("message") or message
            sub_msg = err.get("error_user_msg") or err.get("error_data", {}).get(
                "details"
            )
            if sub_msg:
                message = f"{message} ({sub_msg})"
    except (ValueError, AttributeError):
        logger.debug("[WhatsApp] could not parse the API error body", exc_info=True)

    common: dict[str, Any] = {
        "status_code": status_code,
        "response_text": response_text,
        "error_code": error_code,
    }
    if status_code in (401, 403) or error_code in (190, 200, 10):
        return WhatsAppAuthError(message, **common)
    if error_code in (131026, 131047, 131051, 131008):
        return WhatsAppInvalidRecipientError(message, **common)
    if status_code == 429 or error_code in (131056, 131048, 130429, 80007):
        return WhatsAppRateLimitError(message, **common)
    return WhatsAppError(message, **common)


async def _post(payload: dict[str, Any]) -> dict[str, Any]:
    """POST a payload to the Cloud API and return the JSON response, raising WhatsAppError on network or HTTP errors."""
    url = _endpoint()
    headers = _auth_headers()
    timeout = settings.WHATSAPP_REQUEST_TIMEOUT_SECONDS

    logger.info(
        "[WhatsApp] POST %s to=%s type=%s",
        url,
        payload.get("to"),
        payload.get("type"),
    )
    try:
        async with get_http_client() as client:
            response = await client.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        logger.error("[WhatsApp] Network error: %s", exc, exc_info=True)
        raise WhatsAppError(f"WhatsApp network error: {exc}") from exc

    if response.status_code >= 400:
        logger.error(
            "[WhatsApp] API error status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise _parse_error(response.text, response.status_code)

    try:
        data = response.json()
    except ValueError:
        logger.debug("[WhatsApp] API response body is not JSON; keeping the raw text")
        data = {"raw": response.text}
    logger.info("[WhatsApp] Sent ok response=%s", data)
    return data


async def send_whatsapp_template_message(
    mobile_number: str,
    template_name: str,
    language_code: Optional[str] = None,
    body_parameters: Optional[Iterable[str]] = None,
    body_named_parameters: Optional[dict[str, str]] = None,
    header_parameters: Optional[Iterable[str]] = None,
    button_parameters: Optional[Iterable[dict[str, Any]]] = None,
    header_image_url: Optional[str] = None,
    header_document_url: Optional[str] = None,
    header_document_filename: Optional[str] = None,
) -> dict[str, Any]:
    """Send an approved WhatsApp template message and return the API response.

    Raises:
        ValueError: When template_name is empty.
        WhatsAppInvalidRecipientError: When the number fails local validation, before any API call.
        WhatsAppError: When the API call fails.
    """
    if not template_name:
        raise ValueError("template_name is required")
    try:
        to = normalize_msisdn(mobile_number)
    except ValueError as exc:
        logger.warning(
            "[WhatsApp] Skipping send — invalid phone number %r: %s",
            mobile_number,
            exc,
        )
        raise WhatsAppInvalidRecipientError(
            f"Invalid phone number {mobile_number!r}: {exc}"
        ) from exc
    lang = language_code or settings.WHATSAPP_DEFAULT_LANGUAGE or "en"
    payload = _build_template_payload(
        to=to,
        template_name=template_name,
        language_code=lang,
        body_parameters=body_parameters,
        body_named_parameters=body_named_parameters,
        header_parameters=header_parameters,
        button_parameters=button_parameters,
        header_image_url=header_image_url,
        header_document_url=header_document_url,
        header_document_filename=header_document_filename,
    )
    return await _post(payload)


async def send_whatsapp_text_message(
    mobile_number: str,
    body: str,
    preview_url: bool = False,
) -> dict[str, Any]:
    """Send a freeform text message, which WhatsApp accepts only inside the 24-hour service window."""
    if not body or not body.strip():
        raise ValueError("body is required")
    to = normalize_msisdn(mobile_number)
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": bool(preview_url), "body": body},
    }
    return await _post(payload)


async def send_offer_whatsapp_notification(
    mobile_number: str,
    businessname: str,
    offer_title: str,
    language: str = "en",
) -> bool:
    """Send the offer template with the business name, returning False instead of raising; offer_title is unused."""
    template_name = settings.WHATSAPP_OFFER_TEMPLATE_NAME
    if not template_name:
        logger.warning(
            "[WhatsApp] WHATSAPP_OFFER_TEMPLATE_NAME not configured - skipping send"
        )
        return False
    try:
        await send_whatsapp_template_message(
            mobile_number=mobile_number,
            template_name=template_name,
            language_code=language,
            body_named_parameters={"businessname": businessname or ""},
        )
        return True
    except WhatsAppError as exc:
        logger.warning(
            "[WhatsApp] Offer notification failed for %s: %s",
            mobile_number,
            exc,
        )
        return False
    except Exception as exc:
        logger.exception(
            "[WhatsApp] Unexpected error sending offer notification: %s", exc
        )
        return False


def verify_webhook_subscription(
    mode: Optional[str],
    token: Optional[str],
    challenge: Optional[str],
) -> Optional[str]:
    """Return the challenge when Meta's webhook handshake has subscribe mode and a matching verify token, else None."""
    expected = settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN
    if not expected:
        logger.error("[WhatsApp] WHATSAPP_WEBHOOK_VERIFY_TOKEN is not configured")
        return None
    if mode != "subscribe":
        logger.warning("[WhatsApp] Webhook verify: unexpected mode=%r", mode)
        return None
    if not token or not hmac.compare_digest(token, expected):
        logger.warning("[WhatsApp] Webhook verify: token mismatch")
        return None
    return challenge or ""


def verify_webhook_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Return True if the X-Hub-Signature-256 HMAC of the raw body matches; False when unconfigured or mismatched."""
    app_secret = settings.WHATSAPP_APP_SECRET
    if not app_secret:
        logger.error(
            "[WhatsApp] WHATSAPP_APP_SECRET not configured - cannot verify webhook"
        )
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        logger.warning("[WhatsApp] Missing or malformed signature header")
        return False
    provided = signature_header.split("=", 1)[1].strip().lower()
    expected = hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(provided, expected):
        logger.warning("[WhatsApp] Webhook signature mismatch")
        return False
    return True


def parse_webhook_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a webhook payload into one normalized message, status or error event dict per item."""
    events: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return events
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            phone_number_id = metadata.get("phone_number_id")

            for msg in value.get("messages", []) or []:
                ev: dict[str, Any] = {
                    "kind": "message",
                    "phone_number_id": phone_number_id,
                    "from": msg.get("from"),
                    "wa_id": (value.get("contacts") or [{}])[0].get("wa_id"),
                    "message_id": msg.get("id"),
                    "timestamp": msg.get("timestamp"),
                    "type": msg.get("type"),
                    "raw": msg,
                }
                if msg.get("type") == "text":
                    ev["text"] = (msg.get("text") or {}).get("body")
                elif msg.get("type") == "button":
                    btn = msg.get("button") or {}
                    ev["text"] = btn.get("text")
                    ev["button_id"] = btn.get("payload")
                    ev["context_wamid"] = (msg.get("context") or {}).get("id")
                elif msg.get("type") == "interactive":
                    interactive = msg.get("interactive") or {}
                    if interactive.get("type") == "button_reply":
                        btn_reply = interactive.get("button_reply") or {}
                        ev["text"] = btn_reply.get("title")
                        ev["button_id"] = btn_reply.get("id")
                        ev["context_wamid"] = (msg.get("context") or {}).get("id")
                    elif interactive.get("type") == "list_reply":
                        ev["text"] = (interactive.get("list_reply") or {}).get("title")
                events.append(ev)

            for status in value.get("statuses", []) or []:
                events.append(
                    {
                        "kind": "status",
                        "phone_number_id": phone_number_id,
                        "message_id": status.get("id"),
                        "status": status.get("status"),
                        "timestamp": status.get("timestamp"),
                        "recipient_id": status.get("recipient_id"),
                        "errors": status.get("errors"),
                        "raw": status,
                    }
                )

            for err in value.get("errors", []) or []:
                events.append(
                    {
                        "kind": "error",
                        "phone_number_id": phone_number_id,
                        "raw": err,
                    }
                )
    return events


async def send_welcome_whatsapp_notification(
    mobile_number: str,
    name: str,
    language: str = "en",
) -> bool:
    """Send the welcome template with the customer name, returning False instead of raising on failure."""
    template_name = settings.WHATSAPP_WELCOME_TEMPLATE_NAME
    if not template_name:
        logger.warning(
            "[WhatsApp] WHATSAPP_WELCOME_TEMPLATE_NAME not configured - skipping send"
        )
        return False
    try:
        await send_whatsapp_template_message(
            mobile_number=mobile_number,
            template_name=template_name,
            language_code=language,
            body_named_parameters={"customer_name": name or ""},
        )
        return True
    except WhatsAppError as exc:
        logger.warning(
            "[WhatsApp] Welcome notification failed for %s: %s",
            mobile_number,
            exc,
        )
        return False
    except Exception as exc:
        logger.exception(
            "[WhatsApp] Unexpected error sending welcome notification: %s", exc
        )
        return False


async def send_otp_whatsapp_notification(
    mobile_number: str,
    otp: str,
    language: str = "en",
) -> bool:
    """Send the OTP template with the code as body and URL-button parameter, returning False instead of raising."""
    template_name = settings.WHATSAPP_OTP_TEMPLATE_NAME
    if not template_name:
        logger.warning(
            "[WhatsApp] WHATSAPP_OTP_TEMPLATE_NAME not configured - skipping send"
        )
        return False
    try:
        await send_whatsapp_template_message(
            mobile_number=mobile_number,
            template_name=template_name,
            language_code=language,
            body_parameters=[str(otp)],
            button_parameters=[
                {
                    "sub_type": "url",
                    "index": 0,
                    "parameters": [{"type": "text", "text": str(otp)}],
                }
            ],
        )
        return True
    except WhatsAppError as exc:
        logger.warning(
            "[WhatsApp] OTP notification failed for %s: %s", mobile_number, exc
        )
        return False
    except Exception as exc:
        logger.exception(
            "[WhatsApp] Unexpected error sending OTP notification: %s", exc
        )
        return False


async def send_booking_request_whatsapp_notification(
    mobile_number: str,
    provider_name: str,
    member_name: str,
    booking_items: str,
    booking_ref: str,
    view_booking_url: str = "",
    language: str = "en",
) -> bool:
    """Send the booking-request template to a provider with a view-booking button, returning False instead of raising."""
    template_name = settings.WHATSAPP_BOOKING_REQUEST_TEMPLATE_NAME
    if not template_name:
        logger.warning(
            "[WhatsApp] WHATSAPP_BOOKING_REQUEST_TEMPLATE_NAME not configured - skipping send"
        )
        return False
    try:
        await send_whatsapp_template_message(
            mobile_number=mobile_number,
            template_name=template_name,
            language_code=language,
            body_named_parameters={
                "provider_name": provider_name or "",
                "member_name": member_name or "",
                "booking_id": booking_ref or "",
                "booking_items": booking_items or "",
            },
            button_parameters=[
                {
                    "sub_type": "url",
                    "index": 0,
                    "parameters": [{"type": "text", "text": view_booking_url or booking_ref or ""}],
                }
            ],
        )
        return True
    except WhatsAppError as exc:
        logger.warning(
            "[WhatsApp] Booking request notification failed for %s: %s",
            mobile_number,
            exc,
        )
        return False
    except Exception as exc:
        logger.exception(
            "[WhatsApp] Unexpected error sending booking request notification: %s", exc
        )
        return False


async def send_booking_accepted_whatsapp_notification(
    mobile_number: str,
    member_name: str,
    provider_name: str,
    booking_ref: str,
    language: str = "en",
) -> bool:
    """Send the booking-accepted template to a member, returning False instead of raising on failure."""
    template_name = settings.WHATSAPP_BOOKING_ACCEPTED_TEMPLATE_NAME
    if not template_name:
        logger.warning(
            "[WhatsApp] WHATSAPP_BOOKING_ACCEPTED_TEMPLATE_NAME not configured - skipping send"
        )
        return False
    try:
        await send_whatsapp_template_message(
            mobile_number=mobile_number,
            template_name=template_name,
            language_code=language,
            body_parameters=[
                member_name or "",
                provider_name or "",
                booking_ref or "",
            ],
        )
        return True
    except WhatsAppError as exc:
        logger.warning(
            "[WhatsApp] Booking accepted notification failed for %s: %s",
            mobile_number,
            exc,
        )
        return False
    except Exception as exc:
        logger.exception(
            "[WhatsApp] Unexpected error sending booking accepted notification: %s", exc
        )
        return False


async def send_booking_completed_whatsapp_notification(
    mobile_number: str,
    member_name: str,
    provider_name: str,
    booking_ref: str,
    language: str = "en",
) -> bool:
    """Send the booking-completed template to a member, returning False instead of raising on failure."""
    template_name = settings.WHATSAPP_BOOKING_COMPLETED_TEMPLATE_NAME
    if not template_name:
        logger.warning(
            "[WhatsApp] WHATSAPP_BOOKING_COMPLETED_TEMPLATE_NAME not configured - skipping send"
        )
        return False
    try:
        await send_whatsapp_template_message(
            mobile_number=mobile_number,
            template_name=template_name,
            language_code=language,
            body_parameters=[
                member_name or "",
                provider_name or "",
                booking_ref or "",
            ],
        )
        return True
    except WhatsAppError as exc:
        logger.warning(
            "[WhatsApp] Booking completed notification failed for %s: %s",
            mobile_number,
            exc,
        )
        return False
    except Exception as exc:
        logger.exception(
            "[WhatsApp] Unexpected error sending booking completed notification: %s", exc
        )
        return False


def _extract_wamid(resp: object) -> Optional[str]:
    """Return the first message id from a Cloud API send response, or None."""
    if not isinstance(resp, dict):
        return None
    messages = resp.get("messages")
    if isinstance(messages, list) and messages:
        first = messages[0]
        if isinstance(first, dict):
            return first.get("id")
    return None


async def send_unverified_provider_whatsapp_notification_raising(
    mobile_number: str,
    business_name: str,
    language: str = "en",
) -> tuple[bool, Optional[str]]:
    """Send the unverified-provider discovery nudge if its template is configured and enabled, letting send errors propagate.

    Returns:
        (True, wamid) when accepted, or (False, None) when skipped for a missing or disabled template.

    Raises:
        WhatsAppError: Or a subclass, on an actual send failure.
    """
    template_name = settings.WHATSAPP_UNVERIFIED_PROVIDER_TEMPLATE_NAME
    if not template_name:
        logger.warning(
            "[WhatsApp] WHATSAPP_UNVERIFIED_PROVIDER_TEMPLATE_NAME not configured - skipping send"
        )
        return False, None

    from app.etown.notifications.whatsapp_template_config import is_template_enabled
    if not await is_template_enabled(template_name):
        logger.info(
            "[WhatsApp] Unverified provider nudge skipped: template %r is disabled in DB",
            template_name,
        )
        return False, None

    resp = await send_whatsapp_template_message(
        mobile_number=mobile_number,
        template_name=template_name,
        language_code=language,
        body_named_parameters={"provider_name": business_name or ""},
    )
    return True, _extract_wamid(resp)


async def send_unverified_provider_fallback_whatsapp_notification_raising(
    mobile_number: str,
    business_name: str,
    profile_id: str,
    language: str = "en",
) -> tuple[bool, Optional[str]]:
    """Send the utility failsafe nudge with provider name and reference id if enabled, letting send errors propagate.

    Returns:
        (True, wamid) when accepted, or (False, None) when skipped for a missing or disabled template.

    Raises:
        WhatsAppError: Or a subclass, on an actual send failure.
    """
    template_name = settings.WHATSAPP_UNVERIFIED_PROVIDER_FALLBACK_TEMPLATE_NAME
    if not template_name:
        logger.warning(
            "[WhatsApp] WHATSAPP_UNVERIFIED_PROVIDER_FALLBACK_TEMPLATE_NAME not configured - skipping send"
        )
        return False, None

    from app.etown.notifications.whatsapp_template_config import is_template_enabled
    if not await is_template_enabled(template_name):
        logger.info(
            "[WhatsApp] Unverified provider failsafe skipped: template %r is disabled in DB",
            template_name,
        )
        return False, None

    resp = await send_whatsapp_template_message(
        mobile_number=mobile_number,
        template_name=template_name,
        language_code=language,
        body_named_parameters={
            "provider_name": business_name or "",
            "profile_id": profile_id or "",
        },
    )
    return True, _extract_wamid(resp)


async def send_unverified_provider_enquiry_whatsapp_notification(
    mobile_number: str,
    business_name: str,
    member_name: str = "",
    language: str = "en",
) -> tuple[bool, Optional[str]]:
    """Send the new-customer-enquiry template to an unverified provider without raising.

    Returns:
        (True, None) on success, else (False, reason) for the missing, disabled, invalid-number or failed case.
    """
    template_name = settings.WHATSAPP_UNVERIFIED_PROVIDER_ENQUIRY_TEMPLATE_NAME
    if not template_name:
        logger.warning(
            "[WhatsApp] WHATSAPP_UNVERIFIED_PROVIDER_ENQUIRY_TEMPLATE_NAME not configured - skipping send"
        )
        return False, "template_not_configured"

    from app.etown.notifications.whatsapp_template_config import is_template_enabled
    if not await is_template_enabled(template_name):
        logger.info(
            "[WhatsApp] Unverified provider enquiry skipped: template %r is disabled in DB",
            template_name,
        )
        return False, "template_disabled"

    try:
        await send_whatsapp_template_message(
            mobile_number=mobile_number,
            template_name=template_name,
            language_code=language,
            body_named_parameters={
                "provider_name": business_name or "",
                "member_name": member_name or "",
            },
        )
        return True, None
    except WhatsAppInvalidRecipientError as exc:
        logger.warning(
            "[WhatsApp] Unverified provider enquiry has an unsendable recipient %s: %s",
            mobile_number,
            exc,
        )
        return False, "invalid_number"
    except WhatsAppError as exc:
        logger.warning(
            "[WhatsApp] Unverified provider enquiry failed for %s: %s",
            mobile_number,
            exc,
        )
        return False, "send_failed"
    except Exception as exc:
        logger.exception(
            "[WhatsApp] Unexpected error sending unverified provider enquiry: %s", exc
        )
        return False, "send_failed"


async def send_fos_business_onboarding_whatsapp_notification(
    mobile_number: str,
    provider_name: str,
    provider_id: str,
    language: str = "en",
) -> tuple[bool, Optional[str]]:
    """Send the FOS onboarding confirmation template to a captured provider without raising.

    Returns:
        (True, wamid) when WhatsApp accepted it, else (False, None) for a skipped or failed send.
    """
    template_name = settings.WHATSAPP_FOS_BUSINESS_ONBOARDING_TEMPLATE_NAME
    if not template_name:
        logger.warning(
            "[WhatsApp] WHATSAPP_FOS_BUSINESS_ONBOARDING_TEMPLATE_NAME not configured - skipping send"
        )
        return False, None

    from app.etown.notifications.whatsapp_template_config import is_template_enabled
    if not await is_template_enabled(template_name):
        logger.info(
            "[WhatsApp] FOS business onboarding skipped: template %r is disabled in DB",
            template_name,
        )
        return False, None

    try:
        resp = await send_whatsapp_template_message(
            mobile_number=mobile_number,
            template_name=template_name,
            language_code=language,
            body_named_parameters={
                "provider_name": provider_name or "",
                "provider_id": provider_id or "",
            },
        )
        wamid = _extract_wamid(resp)
        logger.info(
            "[WhatsApp] FOS business onboarding sent | provider_id=%s mobile=%s wamid=%s",
            provider_id, mobile_number, wamid,
        )
        return True, wamid
    except WhatsAppError as exc:
        logger.warning(
            "[WhatsApp] FOS business onboarding failed for %s: %s", mobile_number, exc
        )
        return False, None
    except Exception as exc:
        logger.exception(
            "[WhatsApp] Unexpected error sending FOS business onboarding: %s", exc
        )
        return False, None


async def send_community_post_review_whatsapp_notification(
    mobile_number: str,
    post_type: str,
    poster_name: str,
    postid: str,
    title: str,
    community: str,
    review_path: str,
    language: str = "en",
) -> tuple[bool, Optional[str]]:
    """Send the event or update submitted-for-approval template to one reviewer without raising.

    Args:
        mobile_number: Reviewer's WhatsApp number.
        post_type: event or update; anything else is skipped.
        poster_name: Name of the member who submitted the post.
        postid: The event or update id.
        title: Post title.
        community: Town name.
        review_path: CRM record path appended to the template button's base URL.
        language: Template language code.

    Returns:
        (True, wamid) when WhatsApp accepted it, else (False, None) for a skipped or failed send.
    """
    if post_type == "event":
        template_name = settings.WHATSAPP_EVENT_SUBMITTED_TEMPLATE_NAME
    elif post_type == "update":
        template_name = settings.WHATSAPP_UPDATE_SUBMITTED_TEMPLATE_NAME
    else:
        return False, None
    if not template_name:
        logger.warning(
            "[WhatsApp] %s submitted-for-approval template not configured - skipping send",
            post_type,
        )
        return False, None

    from app.etown.notifications.whatsapp_template_config import is_template_enabled
    if not await is_template_enabled(template_name):
        logger.info(
            "[WhatsApp] Community %s review skipped: template %r is disabled in DB",
            post_type, template_name,
        )
        return False, None

    try:
        resp = await send_whatsapp_template_message(
            mobile_number=mobile_number,
            template_name=template_name,
            language_code=language,
            body_parameters=[
                poster_name or "A member",
                postid,
                title or "-",
                community or "-",
            ],
            button_parameters=[
                {
                    "sub_type": "url",
                    "index": 0,
                    "parameters": [{"type": "text", "text": review_path.lstrip("/")}],
                }
            ],
        )
        wamid = _extract_wamid(resp)
        logger.info(
            "[WhatsApp] Community %s review sent | postid=%s mobile=%s wamid=%s",
            post_type, postid, mobile_number, wamid,
        )
        return True, wamid
    except WhatsAppError as exc:
        logger.warning(
            "[WhatsApp] Community %s review failed for %s: %s", post_type, mobile_number, exc
        )
        return False, None
    except Exception as exc:
        logger.exception(
            "[WhatsApp] Unexpected error sending community %s review: %s", post_type, exc
        )
        return False, None


async def send_town_image_request_whatsapp_notification(
    mobile_number: str,
    image_url: str,
    town_name: str,
    requester_name: str,
    request_id: str,
    review_path: str,
    language: str = "en",
) -> tuple[bool, Optional[str]]:
    """Send the town image change request template, image header plus town, requester and request id, without raising.

    Args:
        mobile_number: Reviewer's WhatsApp number.
        image_url: Public HTTPS URL of the submitted image, used as the header.
        town_name: Town the image is for.
        requester_name: Name of the member who submitted it.
        request_id: The etown_townreview id.
        review_path: CRM path appended to the Review Request button's base URL.
        language: Template language code.

    Returns:
        (True, wamid) when WhatsApp accepted it, else (False, None) for a skipped or failed send.
    """
    template_name = settings.WHATSAPP_TOWN_IMAGE_REQUEST_TEMPLATE_NAME
    if not template_name:
        logger.warning(
            "[WhatsApp] WHATSAPP_TOWN_IMAGE_REQUEST_TEMPLATE_NAME not configured - skipping send"
        )
        return False, None

    from app.etown.notifications.whatsapp_template_config import is_template_enabled
    if not await is_template_enabled(template_name):
        logger.info(
            "[WhatsApp] Town image request skipped: template %r is disabled in DB", template_name,
        )
        return False, None

    try:
        resp = await send_whatsapp_template_message(
            mobile_number=mobile_number,
            template_name=template_name,
            language_code=language,
            header_image_url=image_url,
            body_parameters=[town_name or "-", requester_name or "A member", request_id],
            button_parameters=[
                {
                    "sub_type": "url",
                    "index": 0,
                    "parameters": [{"type": "text", "text": review_path.lstrip("/")}],
                }
            ],
        )
        wamid = _extract_wamid(resp)
        logger.info(
            "[WhatsApp] Town image request sent | townreviewid=%s mobile=%s wamid=%s",
            request_id, mobile_number, wamid,
        )
        return True, wamid
    except WhatsAppError as exc:
        logger.warning(
            "[WhatsApp] Town image request failed for %s: %s", mobile_number, exc
        )
        return False, None
    except Exception as exc:
        logger.exception(
            "[WhatsApp] Unexpected error sending town image request: %s", exc
        )
        return False, None
