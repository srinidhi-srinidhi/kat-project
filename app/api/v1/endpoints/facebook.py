"""Facebook Endpoints - HTTP routes for Page OAuth, Meta webhooks, compliance callbacks and link tracking.

HTTP layer for /api/facebook: the bearer-protected connect route builds the OAuth URL, while the callback, webhook, deauthorize, data-deletion and caption-tracking routes are public and verified by Meta signatures or state, delegating persistence to facebook_utils.

Features:
    - OAuth Connect/Callback: Builds the authorization URL and exchanges the returned code for a long-lived Page token
    - Signed Webhook Verification: HMAC-SHA256 check of X-Hub-Signature-256 against FACEBOOK_APP_SECRET
    - Webhook Handshake + Receiver: Echoes Meta's hub.challenge and processes Page events as a background task
    - Deauthorize/Data-Deletion Callbacks: Verifies Meta's signed_request and soft-deletes/removes the user's connection
    - Caption Link Tracking: Public click counter + redirect for links embedded in Facebook posts

Endpoints:
    GET  /facebook/connect          — Generate the Facebook OAuth authorization URL
    GET  /facebook/callback         — Handle the OAuth callback, exchange code for token, save connection
    GET  /facebook/webhook          — Meta webhook verification handshake (hub.challenge echo)
    POST /facebook/webhook          — Receive real-time Facebook Page events from Meta
    GET  /facebook/track/{feed_id}  — Caption link click tracker + redirect (no auth)
    POST /facebook/deauthorize      — Meta deauthorize callback (soft-delete the user's connections)
    POST /facebook/data-deletion    — Meta data deletion request callback
    GET  /facebook/deletion-status  — Public status page for a data-deletion confirmation code

Post insights are served (from the database only) by the combined GET /instagram/post-insights
endpoint — this module no longer exposes a live Facebook insights route.

All authenticated endpoints require the standard Etown Bearer token.
The connect endpoint requires ``contactid`` and ``userprofileid`` query parameters which are
encoded into the OAuth state so the connection can be associated with the correct Etown user.

Classes:
    No classes defined (module uses request/response models imported from app.schemas.api)

Functions:
    track_caption_link(): Increment a post's click counter and redirect to the app deep link
    get_facebook_connect_url(): Build the Facebook OAuth authorization URL
    facebook_oauth_callback(): Exchange the OAuth code for a token and save the Page connection
    _verify_facebook_signature(): Verify Meta's X-Hub-Signature-256 header via HMAC-SHA256
    _process_facebook_webhook_events(): Background task that logs each webhook entry/change
    facebook_webhook_verify(): Answer Meta's GET webhook verification handshake
    facebook_webhook_receive(): Validate signature and queue incoming Page events for processing
    facebook_deauthorize(): Handle Meta's deauthorize callback (soft-delete connections)
    facebook_data_deletion(): Handle Meta's data deletion request callback
    facebook_deletion_status(): Public status page linked from a data-deletion confirmation code

Dependencies:
    - fastapi: Router, dependency injection, and HTTP exception handling
    - app.core.config: FACEBOOK_APP_SECRET, FACEBOOK_WEBHOOK_VERIFY_TOKEN, and API_BASE_URL settings
    - app.core.security: Bearer token verification for protected routes
    - app.etown.facebook.facebook_utils: Business logic and Graph API operations
    - app.schemas.api: Pydantic request/response models

Updates:
    30-Jun-2026: Initial implementation (mirrors the Instagram Business integration)
"""

import hashlib
import hmac
import json
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from app.core.config import settings
from app.core.security import verify_token
from app.etown.facebook.facebook_utils import (
    connect_facebook_account,
    get_connection_by_user,
    get_facebook_page_profile,
    get_oauth_url,
    deactivate_connection,
    track_caption_link_click,
    parse_signed_request,
    deactivate_connections_by_fb_user,
    delete_user_data_by_fb_user,
)
from app.schemas.api import (
    FacebookCallbackResponse,
    FacebookAllPostsResponse,
    FacebookConnectURLResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/track/{feed_id}",
    include_in_schema=False,
    summary="Track caption link click and redirect",
)
async def track_caption_link(feed_id: str):
    """Handle GET /api/facebook/track/{feed_id}: count a caption link click and redirect to its destination, unauthenticated."""
    destination = await track_caption_link_click(feed_id)
    return RedirectResponse(url=destination, status_code=307)


@router.get(
    "/connect",
    response_model=FacebookConnectURLResponse,
    summary="Get Facebook OAuth URL",
    description=(
        "Returns the Facebook OAuth authorization URL. "
        "Redirect the user's browser to the returned URL. After the user approves permissions, "
        "Facebook redirects back to the configured callback URL to complete the connection."
    ),
    tags=["facebook"],
)
async def get_facebook_connect_url(
    contactid: str = Query(..., description="Etown user contact ID"),
    userprofileid: str = Query(..., description="Etown user profile ID"),
    _token: str = Depends(verify_token),
):
    """Handle GET /api/facebook/connect: return the Facebook OAuth URL with contactid|userprofileid as its state."""
    try:
        oauth_url = get_oauth_url(state=f"{contactid}|{userprofileid}")
        return FacebookConnectURLResponse(oauth_url=oauth_url)
    except Exception as exc:
        logger.error(
            f"FACEBOOK_CONNECT_URL_ERROR | contactid={contactid} | {exc}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Failed to generate Facebook OAuth URL: {exc}")


@router.get(
    "/callback",
    response_model=FacebookCallbackResponse,
    summary="Facebook OAuth Callback",
    description=(
        "Called by Facebook after the user approves permissions. "
        "Exchanges the authorization code for a long-lived user token, discovers the Pages "
        "the user manages, connects the first Page, and saves the connection to the database.\n\n"
        "**Error scenarios handled:**\n"
        "- User denied permissions (``error`` param present)\n"
        "- Token exchange failure (invalid/expired code, redirect URI mismatch)\n"
        "- User has no manageable Facebook Page\n"
    ),
    tags=["facebook"],
)
async def facebook_oauth_callback(
    code: Optional[str] = Query(None, description="Authorization code from Meta"),
    state: Optional[str] = Query(None, description="Encoded contactid|userprofileid passed in the OAuth state parameter"),
    error: Optional[str] = Query(None, description="Error code from Meta if user denied access"),
    error_description: Optional[str] = Query(None, description="Human-readable error from Meta"),
):
    """Handle GET /api/facebook/callback: exchange Meta's code, connect the user's Page and return its profile."""

    if error:
        logger.warning(f"FACEBOOK_CALLBACK_DENIED | state={state} | error={error} | {error_description}")
        raise HTTPException(
            status_code=400,
            detail=f"Facebook connection was denied: {error_description or error}",
        )

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code in callback")

    if not state or "|" not in state:
        raise HTTPException(
            status_code=400,
            detail="Missing or malformed state parameter. Expected format: contactid|userprofileid. Please restart the connection flow.",
        )

    contactid, userprofileid = state.split("|", 1)
    userprofileid = userprofileid or None

    if not contactid:
        raise HTTPException(
            status_code=400,
            detail="Missing contactid in OAuth state parameter. Please restart the connection flow.",
        )

    try:
        result = await connect_facebook_account(contactid=contactid, userprofileid=userprofileid, code=code)
    except ValueError as exc:
        logger.warning(f"FACEBOOK_CALLBACK_VALIDATION_ERROR | contactid={contactid} | {exc}")
        error_msg = str(exc)
        if "authorization code has been used" in error_msg.lower():
            error_msg = (
                "This authorization code has already been used. "
                "Please restart the connection flow by calling /facebook/connect to get a new link."
            )
        raise HTTPException(status_code=422, detail=error_msg)
    except Exception as exc:
        logger.error(f"FACEBOOK_CALLBACK_ERROR | contactid={contactid} | {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Facebook connection failed: {exc}")

    profile = result["profile"]
    connection = result["connection"]
    token_expires_at = connection.get("token_expires_at")

    return FacebookCallbackResponse(
        message="Facebook Page connected successfully",
        fb_page_id=profile["fb_page_id"],
        fb_page_name=profile.get("fb_page_name"),
        fb_user_id=profile.get("fb_user_id"),
        category=profile.get("category"),
        about=profile.get("about"),
        fan_count=profile.get("fan_count"),
        followers_count=profile.get("followers_count"),
        link=profile.get("link"),
        profile_picture_url=profile.get("profile_picture_url"),
        token_expires_at=token_expires_at.isoformat() if token_expires_at else None,
    )


def _verify_facebook_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Return True if the X-Hub-Signature-256 HMAC matches the body, or if no app secret is configured."""
    app_secret = settings.FACEBOOK_APP_SECRET
    if not app_secret:
        logger.warning("FACEBOOK_WEBHOOK | FACEBOOK_APP_SECRET not configured — skipping signature verification")
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_sig = signature_header[len("sha256="):]
    computed_sig = hmac.HMAC(
        key=app_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed_sig, expected_sig)


def _process_facebook_webhook_events(payload: dict) -> None:
    """Log each change of each entry in a Facebook Page webhook payload."""
    entries = payload.get("entry", [])
    for entry in entries:
        page_id = entry.get("id")
        changes = entry.get("changes", [])
        for change in changes:
            field = change.get("field")
            value = change.get("value", {})
            logger.info(
                f"FACEBOOK_WEBHOOK_EVENT | page_id={page_id} "
                f"field={field} value={json.dumps(value)}"
            )


@router.get(
    "/webhook",
    summary="Facebook Webhook Verification (Meta GET handshake)",
    description=(
        "Meta calls this endpoint when you configure or re-configure the webhook in the "
        "App Dashboard. It checks that ``hub.verify_token`` matches ``FACEBOOK_WEBHOOK_VERIFY_TOKEN`` "
        "and echoes back the ``hub.challenge`` value so Meta knows the URL is valid."
    ),
    response_class=PlainTextResponse,
    tags=["facebook"],
)
async def facebook_webhook_verify(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """Handle GET /api/facebook/webhook: check Meta's subscribe mode and verify token, then echo the challenge."""
    expected_token = settings.FACEBOOK_WEBHOOK_VERIFY_TOKEN

    if hub_mode != "subscribe":
        logger.warning(f"FACEBOOK_WEBHOOK_VERIFY | Unexpected hub.mode={hub_mode}")
        raise HTTPException(status_code=403, detail="Invalid hub.mode")

    if not hub_verify_token or hub_verify_token != expected_token:
        logger.warning(f"FACEBOOK_WEBHOOK_VERIFY | Token mismatch — received={hub_verify_token}")
        raise HTTPException(status_code=403, detail="Webhook verification failed: token mismatch")

    if not hub_challenge:
        raise HTTPException(status_code=400, detail="Missing hub.challenge")

    logger.info("FACEBOOK_WEBHOOK_VERIFY | Verification successful, echoing challenge")
    return PlainTextResponse(content=hub_challenge, status_code=200)


@router.post(
    "/webhook",
    summary="Facebook Webhook Receiver (real-time events from Meta)",
    description=(
        "Receives real-time Facebook Page events from Meta: feed comments, reactions, "
        "mentions, ratings, etc.\n\n"
        "Validates the ``X-Hub-Signature-256`` header using the Facebook App Secret. "
        "Meta requires a **200 OK within 10 seconds**, so all processing is offloaded "
        "to a background task."
    ),
    tags=["facebook"],
)
async def facebook_webhook_receive(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
):
    """Handle POST /api/facebook/webhook: verify the signature and log Page events in the background."""
    raw_body = await request.body()

    if not _verify_facebook_signature(raw_body, x_hub_signature_256):
        logger.warning("FACEBOOK_WEBHOOK | Rejected: invalid X-Hub-Signature-256")
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        payload = await request.json()
    except Exception:
        logger.exception("FACEBOOK_WEBHOOK | Invalid JSON body")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if payload.get("object") != "page":
        logger.warning(f"FACEBOOK_WEBHOOK | Unexpected object type: {payload.get('object')}")
        return {"status": "ignored"}

    entry_count = len(payload.get("entry", []))
    logger.info(f"FACEBOOK_WEBHOOK | Received payload with {entry_count} entry(s)")

    background_tasks.add_task(_process_facebook_webhook_events, payload)

    return {"status": "ok", "entries": entry_count}


@router.post(
    "/deauthorize",
    summary="Facebook Deauthorize Callback",
    description=(
        "Called by Meta when a user removes the app from their Facebook account. "
        "Meta POSTs a ``signed_request`` form field containing the user_id. "
        "We verify the signature with the App Secret and deactivate that user's connections."
    ),
    tags=["facebook"],
)
async def facebook_deauthorize(signed_request: str = Form(...)):
    """Handle POST /api/facebook/deauthorize: verify Meta's signed request and deactivate the user's connections."""
    payload = parse_signed_request(signed_request)
    if payload is None:
        raise HTTPException(status_code=400, detail="Invalid signed_request")

    fb_user_id = payload.get("user_id")
    if not fb_user_id:
        logger.warning("FACEBOOK_DEAUTHORIZE | signed_request missing user_id")
        return {"status": "ignored"}

    try:
        deactivated = await deactivate_connections_by_fb_user(str(fb_user_id))
    except Exception as exc:
        logger.error(f"FACEBOOK_DEAUTHORIZE_ERROR | fb_user_id={fb_user_id} | {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process deauthorize callback")

    return {"status": "ok", "deactivated": deactivated}


@router.post(
    "/data-deletion",
    summary="Facebook Data Deletion Request Callback",
    description=(
        "Called by Meta when a user requests deletion of their data. "
        "Meta POSTs a ``signed_request`` form field containing the user_id. "
        "We verify the signature, delete the user's stored Facebook data, and return "
        "a status-check URL plus a confirmation code as required by Meta."
    ),
    tags=["facebook"],
)
async def facebook_data_deletion(signed_request: str = Form(...)):
    """Handle POST /api/facebook/data-deletion: delete the user's Facebook data and return a status URL and code."""
    payload = parse_signed_request(signed_request)
    if payload is None:
        raise HTTPException(status_code=400, detail="Invalid signed_request")

    fb_user_id = payload.get("user_id")
    if not fb_user_id:
        logger.warning("FACEBOOK_DATA_DELETION | signed_request missing user_id")
        raise HTTPException(status_code=400, detail="signed_request missing user_id")

    confirmation_code = f"fbdel_{fb_user_id}"
    try:
        await delete_user_data_by_fb_user(str(fb_user_id))
    except Exception as exc:
        logger.error(f"FACEBOOK_DATA_DELETION_ERROR | fb_user_id={fb_user_id} | {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process data deletion request")

    status_url = f"{settings.API_BASE_URL}/api/facebook/deletion-status?code={confirmation_code}"
    return {"url": status_url, "confirmation_code": confirmation_code}


@router.get(
    "/deletion-status",
    include_in_schema=False,
    summary="Facebook Data Deletion Status",
)
async def facebook_deletion_status(code: str = Query(..., description="Deletion confirmation code")):
    """Handle GET /api/facebook/deletion-status: report a data-deletion request as completed for its code."""
    return JSONResponse(
        status_code=200,
        content={
            "confirmation_code": code,
            "status": "completed",
            "message": "Your Facebook data associated with Etown has been deleted.",
        },
    )
