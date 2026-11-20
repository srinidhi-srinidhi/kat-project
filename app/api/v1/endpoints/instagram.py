"""Instagram Endpoints - HTTP routes for Instagram and Facebook connections, Meta webhooks and post insights.

HTTP layer for /api/instagram: runs the Instagram or Facebook OAuth connect and callback, reports status, disconnects, handles signed Meta webhooks, deauthorize and data-deletion callbacks, tracks caption links and serves post insights from local snapshots.

Features:
    - OAuth Connect/Callback: Builds the authorization URL (Instagram or Facebook) and exchanges the code for a token
    - Combined Status: Single call returns live connection status + profile for both Instagram and Facebook
    - Signed Webhook Verification: HMAC-SHA256 check of X-Hub-Signature-256 against INSTAGRAM_APP_SECRET
    - Webhook Handshake + Receiver: Echoes Meta's hub.challenge and processes IG events as a background task
    - Deauthorize/Data-Deletion Callbacks: Verifies Meta's signed_request and soft-deletes/removes the user's connection
    - DB-only Post Insights: Per-post Instagram + Facebook metrics and engagement rate read from local snapshots, no live Graph calls
    - Caption Link Tracking: Public click counter + redirect for links embedded in Instagram captions

Endpoints:
    GET  /instagram/connect          — Generate the Instagram or Facebook OAuth authorization URL
    GET  /instagram/callback         — Handle the OAuth callback, exchange code for token, save connection
    GET  /instagram/status           — Combined Instagram + Facebook connection status and live profiles
    DELETE /instagram/disconnect     — Deactivate the stored Instagram or Facebook connection
    GET  /instagram/webhook          — Meta webhook verification handshake (hub.challenge echo)
    POST /instagram/webhook          — Receive real-time Instagram Business events from Meta
    GET  /instagram/track/{feed_id}  — Caption link click tracker + redirect (no auth)
    POST /instagram/deauthorize      — Meta deauthorize callback (soft-delete the user's connections)
    POST /instagram/data-deletion    — Meta data deletion request callback
    GET  /instagram/deletion-status  — Public status page for a data-deletion confirmation code
    GET  /instagram/post-insights    — Combined Instagram + Facebook per-post insights and best-time-to-post

All authenticated endpoints require the standard Etown Bearer token.
The connect endpoint requires ``contactid`` and ``userprofileid`` query parameters which are
encoded into the OAuth state so the connection can be associated with the correct Etown user.

Classes:
    No classes defined (module uses request/response models imported from app.schemas.api)

Functions:
    track_caption_link(): Increment a post's click counter and redirect to the app deep link
    get_social_connect_url(): Build the OAuth authorization URL for Instagram or Facebook
    instagram_oauth_callback(): Exchange the OAuth code for a token and save the IG connection
    instagram_connection_status(): Return combined Instagram + Facebook connection status and profile
    disconnect_social_account(): Deactivate an Instagram Business or Facebook Page connection
    _verify_instagram_signature(): Verify Meta's X-Hub-Signature-256 header via HMAC-SHA256
    _process_instagram_webhook_events(): Background task that logs each webhook entry/change
    instagram_webhook_verify(): Answer Meta's GET webhook verification handshake
    instagram_webhook_receive(): Validate signature and queue incoming IG events for processing
    instagram_deauthorize(): Handle Meta's deauthorize callback (soft-delete connections)
    instagram_data_deletion(): Handle Meta's data deletion request callback
    instagram_deletion_status(): Public status page linked from a data-deletion confirmation code
    get_social_post_insights(): Return per-post Instagram + Facebook insights and best-time-to-post ranking

Dependencies:
    - fastapi: Router, dependency injection, and HTTP exception handling
    - app.core.config: INSTAGRAM_APP_SECRET, INSTAGRAM_WEBHOOK_VERIFY_TOKEN, and snapshot-window settings
    - app.core.security: Bearer token verification for protected routes
    - app.etown.instagram.instagram_utils: Business logic and Graph API operations
    - app.etown.facebook.facebook_utils: Facebook connection lookups reused for the combined status/insights endpoints
    - app.schemas.api: Pydantic request/response models

Updates:
    17-Jun-2026: Initial implementation with webhook GET (verification) and POST (event receiver) endpoints.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from app.core.config import settings
from app.core.security import verify_token
from app.etown.instagram.instagram_utils import (
    get_social_oauth_url,
    connect_instagram_account,
    get_connection_by_user,
    get_instagram_profile,
    deactivate_connection,
    get_post_insights_from_db,
    get_all_instagram_posts,
    get_best_time_to_post,
    track_caption_link_click,
    parse_signed_request,
    deactivate_connections_by_ig_user,
    delete_user_data_by_ig_user,
)
from app.etown.facebook.facebook_utils import (
    get_connection_by_user as get_fb_connection_by_user,
    get_facebook_page_profile,
    deactivate_connection as fb_deactivate_connection,
    get_post_insights_from_db as fb_get_post_insights_from_db,
    get_all_facebook_posts,
)
from app.schemas.api import (
    InstagramConnectURLResponse,
    InstagramCallbackResponse,
    InstagramStatusResponse,
    InstagramDisconnectResponse,
    InstagramPostInsights,
    FacebookPostInsights,
    SocialPostInsightResponse,
    SocialUserPostsInsightResponse,
    FacebookStatusResponse,
    SocialStatusResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/track/{feed_id}",
    include_in_schema=False,
    summary="Track caption link click and redirect",
)
async def track_caption_link(feed_id: str):
    """Handle GET /api/instagram/track/{feed_id}: count a caption link click and redirect to its destination."""
    destination = await track_caption_link_click(feed_id)
    return RedirectResponse(url=destination, status_code=307)


@router.get(
    "/connect",
    response_model=InstagramConnectURLResponse,
    summary="Get Social OAuth URL (Instagram or Facebook)",
    description=(
        "Returns the OAuth authorization URL for the requested platform. "
        "Pass **platform=instagram** (default) to begin an Instagram Business account connection, "
        "or **platform=facebook** to begin a Facebook Page connection. "
        "Redirect the user's browser to the returned URL. After approval, the platform redirects "
        "back to the respective configured callback URL."
    ),
    tags=["instagram"],
)
async def get_social_connect_url(
    contactid: str = Query(..., description="Etown user contact ID"),
    userprofileid: str = Query(..., description="Etown user profile ID"),
    platform: str = Query("instagram", description="Platform to connect: 'instagram' or 'facebook'"),
    _token: str = Depends(verify_token),
):
    """Handle GET /api/instagram/connect: return the Instagram or Facebook OAuth URL for a user."""
    platform = platform.lower().strip()
    if platform not in ("instagram", "facebook"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported platform '{platform}'. Use 'instagram' or 'facebook'.",
        )
    try:
        oauth_url = get_social_oauth_url(platform=platform, state=f"{contactid}|{userprofileid}")
        return InstagramConnectURLResponse(oauth_url=oauth_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(
            f"SOCIAL_CONNECT_URL_ERROR | platform={platform} contactid={contactid} | {exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Failed to generate {platform.capitalize()} OAuth URL")


@router.get(
    "/callback",
    response_model=InstagramCallbackResponse,
    summary="Instagram OAuth Callback",
    description=(
        "Called by Instagram after the user approves permissions. "
        "Exchanges the authorization code for a long-lived access token, "
        "resolves the Instagram Business Account ID returned by the token exchange, "
        "and saves the connection to the database.\n\n"
        "**Error scenarios handled:**\n"
        "- User denied permissions (``error`` param present)\n"
        "- Token exchange failure (invalid/expired code, redirect URI mismatch)\n"
    ),
    tags=["instagram"],
)
async def instagram_oauth_callback(
    code: Optional[str] = Query(None, description="Authorization code from Meta"),
    state: Optional[str] = Query(None, description="Encoded contactid|userprofileid passed in the OAuth state parameter"),
    error: Optional[str] = Query(None, description="Error code from Meta if user denied access"),
    error_description: Optional[str] = Query(None, description="Human-readable error from Meta"),
):
    """Handle GET /api/instagram/callback: exchange the Meta OAuth code and save the Instagram connection."""

    if error:
        logger.warning(f"INSTAGRAM_CALLBACK_DENIED | state={state} | error={error} | {error_description}")
        raise HTTPException(
            status_code=400,
            detail=f"Instagram connection was denied: {error_description or error}",
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
        result = await connect_instagram_account(contactid=contactid, userprofileid=userprofileid, code=code)
    except ValueError as exc:
        logger.warning(f"INSTAGRAM_CALLBACK_VALIDATION_ERROR | contactid={contactid} | {exc}")
        error_msg = str(exc)
        if "authorization code has been used" in error_msg.lower():
            error_msg = (
                "This authorization code has already been used. "
                "Please restart the connection flow by calling /instagram/connect to get a new link."
            )
        raise HTTPException(status_code=422, detail=error_msg)
    except Exception as exc:
        logger.error(f"INSTAGRAM_CALLBACK_ERROR | contactid={contactid} | {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Instagram connection failed: {exc}")

    profile = result["profile"]
    connection = result["connection"]
    token_expires_at = connection.get("token_expires_at")

    return InstagramCallbackResponse(
        message="Instagram Business account connected successfully",
        instagram_business_id=profile["instagram_business_id"],
        username=profile.get("username"),
        name=profile.get("name"),
        account_type=profile.get("account_type"),
        biography=profile.get("biography"),
        followers_count=profile.get("followers_count"),
        media_count=profile.get("media_count"),
        website=profile.get("website"),
        profile_picture_url=profile.get("profile_picture_url"),
        token_expires_at=token_expires_at.isoformat() if token_expires_at else None,
    )


@router.get(
    "/status",
    response_model=SocialStatusResponse,
    summary="Check Social Connection Status (Instagram + Facebook)",
    description="Returns connection status for both Instagram Business account and Facebook Page in a single call.",
    tags=["instagram"],
)
async def instagram_connection_status(
    contactid: str = Query(..., description="Etown user contact ID"),
    userprofileid: Optional[str] = Query(None, description="Etown user profile ID"),
    _token: str = Depends(verify_token),
):
    """Handle GET /api/instagram/status: return Instagram and Facebook connection status with live profile data."""
    import asyncio
    from app.db.client import Database

    try:
        ig_connection, fb_connection = await asyncio.gather(
            get_connection_by_user(contactid, userprofileid),
            get_fb_connection_by_user(contactid, userprofileid),
            return_exceptions=True,
        )
    except Exception as exc:
        logger.error(f"SOCIAL_STATUS_ERROR | contactid={contactid} userprofileid={userprofileid} | {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch social connection status")

    if isinstance(ig_connection, Exception):
        logger.error(f"INSTAGRAM_STATUS_ERROR | contactid={contactid} | {ig_connection}", exc_info=True)
        ig_connection = None
    if isinstance(fb_connection, Exception):
        logger.error(f"FACEBOOK_STATUS_ERROR | contactid={contactid} | {fb_connection}", exc_info=True)
        fb_connection = None

    async def _build_ig_status() -> InstagramStatusResponse:
        if not ig_connection:
            return InstagramStatusResponse(connected=False)
        ig_id = ig_connection["instagram_business_id"]
        token_expires_at = ig_connection.get("token_expires_at")
        connected_at = ig_connection.get("connected_at")
        if userprofileid:
            token_row = await Database.fetchrow(
                "SELECT access_token FROM etown_instagramconnections "
                "WHERE contactid = $1 AND userprofileid = $2 AND is_active = TRUE ORDER BY connected_at DESC LIMIT 1",
                contactid, userprofileid,
            )
        else:
            token_row = await Database.fetchrow(
                "SELECT access_token FROM etown_instagramconnections "
                "WHERE contactid = $1 AND is_active = TRUE ORDER BY connected_at DESC LIMIT 1",
                contactid,
            )
        profile_data: dict = {}
        if token_row:
            try:
                profile_data = await get_instagram_profile(ig_id, token_row["access_token"])
            except Exception as exc:
                logger.warning(f"INSTAGRAM_STATUS_PROFILE_FETCH_WARN | contactid={contactid} | ig_id={ig_id} | {exc}")
        return InstagramStatusResponse(
            connected=True,
            instagram_business_id=ig_id,
            username=profile_data.get("username") or ig_connection.get("instagram_username"),
            instagram_name=profile_data.get("name") or ig_connection.get("instagram_name"),
            account_type=profile_data.get("account_type"),
            biography=profile_data.get("biography"),
            followers_count=profile_data.get("followers_count"),
            media_count=profile_data.get("media_count"),
            website=profile_data.get("website"),
            profile_picture_url=profile_data.get("profile_picture_url"),
            token_expires_at=token_expires_at.isoformat() if token_expires_at else None,
            connected_at=connected_at.isoformat() if connected_at else None,
        )

    async def _build_fb_status() -> FacebookStatusResponse:
        if not fb_connection:
            return FacebookStatusResponse(connected=False)
        page_id = fb_connection["fb_page_id"]
        token_expires_at = fb_connection.get("token_expires_at")
        connected_at = fb_connection.get("connected_at")
        if userprofileid:
            token_row = await Database.fetchrow(
                "SELECT page_access_token FROM etown_facebookconnections "
                "WHERE contactid = $1 AND userprofileid = $2 AND is_active = TRUE ORDER BY connected_at DESC LIMIT 1",
                contactid, userprofileid,
            )
        else:
            token_row = await Database.fetchrow(
                "SELECT page_access_token FROM etown_facebookconnections "
                "WHERE contactid = $1 AND is_active = TRUE ORDER BY connected_at DESC LIMIT 1",
                contactid,
            )
        profile_data: dict = {}
        if token_row:
            try:
                profile_data = await get_facebook_page_profile(page_id, token_row["page_access_token"])
            except Exception as exc:
                logger.warning(f"FACEBOOK_STATUS_PROFILE_FETCH_WARN | contactid={contactid} | page_id={page_id} | {exc}")
        return FacebookStatusResponse(
            connected=True,
            fb_page_id=page_id,
            fb_page_name=profile_data.get("name") or fb_connection.get("fb_page_name"),
            fb_user_id=fb_connection.get("fb_user_id"),
            category=profile_data.get("category"),
            about=profile_data.get("about"),
            fan_count=profile_data.get("fan_count"),
            followers_count=profile_data.get("followers_count"),
            link=profile_data.get("link"),
            profile_picture_url=profile_data.get("profile_picture_url"),
            token_expires_at=token_expires_at.isoformat() if token_expires_at else None,
            connected_at=connected_at.isoformat() if connected_at else None,
        )

    ig_status, fb_status = await asyncio.gather(_build_ig_status(), _build_fb_status())

    return SocialStatusResponse(instagram=ig_status, facebook=fb_status)


@router.delete(
    "/disconnect",
    response_model=InstagramDisconnectResponse,
    summary="Disconnect Social Account (Instagram or Facebook)",
    description=(
        "Deactivates the stored social connection for the user. "
        "Pass **platform=instagram** and **business_id=<instagram_business_id>** to disconnect an Instagram Business account, "
        "or **platform=facebook** and **business_id=<fb_page_id>** to disconnect a Facebook Page. "
        "The token is invalidated in the database (soft delete). "
        "The user can reconnect at any time via /instagram/connect."
    ),
    tags=["instagram"],
)
async def disconnect_social_account(
    contactid: str = Query(..., description="Etown user contact ID"),
    userprofileid: Optional[str] = Query(None, description="Etown user profile ID"),
    platform: str = Query(..., description="Platform to disconnect: 'instagram' or 'facebook'"),
    business_id: str = Query(..., description="Instagram Business Account ID (instagram) or Facebook Page ID (facebook)"),
    _token: str = Depends(verify_token),
):
    """Handle DELETE /api/instagram/disconnect: deactivate an Instagram Business or Facebook Page connection."""
    platform = platform.lower().strip()
    if platform not in ("instagram", "facebook"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported platform '{platform}'. Use 'instagram' or 'facebook'.",
        )
    try:
        if platform == "instagram":
            success = await deactivate_connection(contactid=contactid, instagram_business_id=business_id)
        else:
            success = await fb_deactivate_connection(contactid=contactid, fb_page_id=business_id)
    except Exception as exc:
        logger.error(
            f"SOCIAL_DISCONNECT_ERROR | platform={platform} contactid={contactid} | {exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Failed to disconnect {platform.capitalize()} account")

    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"No active {platform.capitalize()} connection found for this user and ID.",
        )

    platform_label = "Instagram Business account" if platform == "instagram" else "Facebook Page"
    return InstagramDisconnectResponse(
        message=f"{platform_label} disconnected successfully",
        disconnected=True,
    )


def _verify_instagram_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Return True when the X-Hub-Signature-256 HMAC matches the body, or when no app secret is configured."""
    app_secret = settings.INSTAGRAM_APP_SECRET
    if not app_secret:
        logger.warning("INSTAGRAM_WEBHOOK | INSTAGRAM_APP_SECRET not configured — skipping signature verification")
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


def _process_instagram_webhook_events(payload: dict) -> None:
    """Log every change in each entry of an Instagram webhook payload."""
    entries = payload.get("entry", [])
    for entry in entries:
        ig_account_id = entry.get("id")
        changes = entry.get("changes", [])
        for change in changes:
            field = change.get("field")
            value = change.get("value", {})
            logger.info(
                f"INSTAGRAM_WEBHOOK_EVENT | ig_account_id={ig_account_id} "
                f"field={field} value={json.dumps(value)}"
            )


@router.get(
    "/webhook",
    summary="Instagram Webhook Verification (Meta GET handshake)",
    description=(
        "Meta calls this endpoint when you configure or re-configure the webhook in the "
        "App Dashboard. It checks that ``hub.verify_token`` matches ``INSTAGRAM_WEBHOOK_VERIFY_TOKEN`` "
        "and echoes back the ``hub.challenge`` value so Meta knows the URL is valid."
    ),
    response_class=PlainTextResponse,
    tags=["instagram"],
)
async def instagram_webhook_verify(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """Handle GET /api/instagram/webhook: check the verify token and echo Meta's hub.challenge."""
    expected_token = settings.INSTAGRAM_WEBHOOK_VERIFY_TOKEN

    if hub_mode != "subscribe":
        logger.warning(f"INSTAGRAM_WEBHOOK_VERIFY | Unexpected hub.mode={hub_mode}")
        raise HTTPException(status_code=403, detail="Invalid hub.mode")

    if not hub_verify_token or hub_verify_token != expected_token:
        logger.warning(
            f"INSTAGRAM_WEBHOOK_VERIFY | Token mismatch — received={hub_verify_token}"
        )
        raise HTTPException(status_code=403, detail="Webhook verification failed: token mismatch")

    if not hub_challenge:
        raise HTTPException(status_code=400, detail="Missing hub.challenge")

    logger.info("INSTAGRAM_WEBHOOK_VERIFY | Verification successful, echoing challenge")
    return PlainTextResponse(content=hub_challenge, status_code=200)


@router.post(
    "/webhook",
    summary="Instagram Webhook Receiver (real-time events from Meta)",
    description=(
        "Receives real-time Instagram Business events from Meta: comments, mentions, "
        "story replies, DMs, media updates, etc.\n\n"
        "Validates the ``X-Hub-Signature-256`` header using the Instagram App Secret. "
        "Meta requires a **200 OK within 10 seconds**, so all processing is offloaded "
        "to a background task."
    ),
    tags=["instagram"],
)
async def instagram_webhook_receive(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
):
    """Handle POST /api/instagram/webhook: verify the signature and log Instagram events in the background."""
    raw_body = await request.body()

    if not _verify_instagram_signature(raw_body, x_hub_signature_256):
        logger.warning("INSTAGRAM_WEBHOOK | Rejected: invalid X-Hub-Signature-256")
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        payload = await request.json()
    except Exception:
        logger.exception("INSTAGRAM_WEBHOOK | Invalid JSON body")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if payload.get("object") != "instagram":
        logger.warning(f"INSTAGRAM_WEBHOOK | Unexpected object type: {payload.get('object')}")
        return {"status": "ignored"}

    entry_count = len(payload.get("entry", []))
    logger.info(f"INSTAGRAM_WEBHOOK | Received payload with {entry_count} entry(s)")

    background_tasks.add_task(_process_instagram_webhook_events, payload)

    return {"status": "ok", "entries": entry_count}


@router.post(
    "/deauthorize",
    summary="Instagram Deauthorize Callback",
    description=(
        "Called by Meta when a user removes the app from their Instagram account. "
        "Meta POSTs a ``signed_request`` form field containing the user_id. "
        "We verify the signature with the App Secret and deactivate that user's connections."
    ),
    tags=["instagram"],
)
async def instagram_deauthorize(signed_request: str = Form(...)):
    """Handle POST /api/instagram/deauthorize: deactivate the connections of the user in Meta's signed request."""
    payload = parse_signed_request(signed_request)
    if payload is None:
        raise HTTPException(status_code=400, detail="Invalid signed_request")

    ig_user_id = payload.get("user_id")
    if not ig_user_id:
        logger.warning("INSTAGRAM_DEAUTHORIZE | signed_request missing user_id")
        return {"status": "ignored"}

    try:
        deactivated = await deactivate_connections_by_ig_user(str(ig_user_id))
    except Exception as exc:
        logger.error(f"INSTAGRAM_DEAUTHORIZE_ERROR | ig_user_id={ig_user_id} | {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process deauthorize callback")

    return {"status": "ok", "deactivated": deactivated}


@router.post(
    "/data-deletion",
    summary="Instagram Data Deletion Request Callback",
    description=(
        "Called by Meta when a user requests deletion of their data. "
        "Meta POSTs a ``signed_request`` form field containing the user_id. "
        "We verify the signature, delete the user's stored Instagram data, and return "
        "a status-check URL plus a confirmation code as required by Meta."
    ),
    tags=["instagram"],
)
async def instagram_data_deletion(signed_request: str = Form(...)):
    """Handle POST /api/instagram/data-deletion: delete the user's Instagram data and return a status URL and code."""
    payload = parse_signed_request(signed_request)
    if payload is None:
        raise HTTPException(status_code=400, detail="Invalid signed_request")

    ig_user_id = payload.get("user_id")
    if not ig_user_id:
        logger.warning("INSTAGRAM_DATA_DELETION | signed_request missing user_id")
        raise HTTPException(status_code=400, detail="signed_request missing user_id")

    confirmation_code = f"igdel_{ig_user_id}"
    try:
        await delete_user_data_by_ig_user(str(ig_user_id))
    except Exception as exc:
        logger.error(f"INSTAGRAM_DATA_DELETION_ERROR | ig_user_id={ig_user_id} | {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process data deletion request")

    status_url = f"{settings.API_BASE_URL}/api/instagram/deletion-status?code={confirmation_code}"
    return {"url": status_url, "confirmation_code": confirmation_code}


@router.get(
    "/deletion-status",
    include_in_schema=False,
    summary="Instagram Data Deletion Status",
)
async def instagram_deletion_status(code: str = Query(..., description="Deletion confirmation code")):
    """Handle GET /api/instagram/deletion-status: report a data-deletion request as completed."""
    return JSONResponse(
        status_code=200,
        content={
            "confirmation_code": code,
            "status": "completed",
            "message": "Your Instagram data associated with Etown has been deleted.",
        },
    )


@router.get(
    "/post-insights",
    response_model=SocialUserPostsInsightResponse,
    summary="Get Social Post Insights for a user (Instagram + Facebook combined)",
    description=(
        "Given a user's **contactid** and **userprofileid**, finds every feed post they "
        "published to Instagram and/or Facebook and returns each one's combined insights "
        "and per-post engagement rate, plus the **besttimetopost** ranking (top posts by "
        "views).\n\n"
        "Pass **timeframe** (7D, 15D or 30D) to choose which snapshot window's metrics to "
        "return — the post's numbers as of 7 / 15 / 30 days after publishing. Defaults to "
        "the widest configured window.\n\n"
        "All metrics are served directly from the local database, which is refreshed by a "
        "background collector — no live Meta Graph API calls are made on this request. Any "
        "metric not present in the database is returned as **0** (never null)."
    ),
    tags=["instagram"],
)
async def get_social_post_insights(
    contactid: str = Query(..., description="Etown user contact ID"),
    userprofileid: str = Query(..., description="Etown user profile ID"),
    timeframe: str = Query(
        ...,
        description="Snapshot window to return: '7D', '15D' or '30D'.",
    ),
    _token: str = Depends(verify_token),
):
    """Handle GET /api/instagram/post-insights: return a user's per-post social insights from the DB for a timeframe window."""
    import asyncio as _asyncio

    _tf = timeframe.strip().upper().rstrip("D")
    if not _tf.isdigit() or int(_tf) not in settings.INSIGHTS_SNAPSHOT_WINDOWS_DAYS:
        allowed = ", ".join(f"{w}D" for w in settings.INSIGHTS_SNAPSHOT_WINDOWS_DAYS)
        raise HTTPException(status_code=400, detail=f"Invalid timeframe. Allowed values: {allowed}.")
    period_days = int(_tf)

    def _build_ig(insights: Optional[dict]) -> Optional[InstagramPostInsights]:
        if not insights:
            return None
        return InstagramPostInsights(
            feed_id=insights.get("feed_id", ""),
            instagram_media_id=insights.get("instagram_media_id", ""),
            media_type=insights.get("media_type", ""),
            published_at=insights.get("published_at"),
            feed_url=insights.get("feed_url"),
            thumbnail_url=insights.get("thumbnail_url"),
            impressions=insights.get("impressions") or 0,
            reach=insights.get("reach") or 0,
            likes=insights.get("likes") or 0,
            comments=insights.get("comments") or 0,
            shares=insights.get("shares") or 0,
            saved=insights.get("saved") or 0,
            total_interactions=insights.get("total_interactions") or 0,
            plays=insights.get("plays") or 0,
            caption_link_clicks=insights.get("caption_link_clicks") or 0,
        )

    def _build_fb(insights: Optional[dict]) -> Optional[FacebookPostInsights]:
        if not insights:
            return None
        return FacebookPostInsights(
            feed_id=insights.get("feed_id", ""),
            facebook_post_id=insights.get("facebook_post_id", ""),
            media_type=insights.get("media_type", ""),
            published_at=insights.get("published_at"),
            feed_url=insights.get("feed_url"),
            thumbnail_url=insights.get("thumbnail_url"),
            impressions=insights.get("impressions") or 0,
            reach=insights.get("reach") or 0,
            reactions=insights.get("reactions") or 0,
            likes=insights.get("likes") or 0,
            comments=insights.get("comments") or 0,
            shares=insights.get("shares") or 0,
            clicks=insights.get("clicks") or 0,
            video_views=insights.get("video_views") or 0,
            caption_link_clicks=insights.get("caption_link_clicks") or 0,
        )

    def _num(data: Optional[dict], key: str) -> int:
        return (data.get(key) or 0) if data else 0

    async def _build_feed_insight(feed_id: str) -> SocialPostInsightResponse:
        """Read both platforms' insights for one feed_id and compute its engagement rate."""
        ig_result, fb_result = await _asyncio.gather(
            get_post_insights_from_db(feed_id=feed_id, period_days=period_days),
            fb_get_post_insights_from_db(feed_id=feed_id, period_days=period_days),
        )
        engagements = (
            _num(ig_result, "likes") + _num(fb_result, "likes")
            + _num(ig_result, "comments") + _num(fb_result, "comments")
            + _num(ig_result, "shares") + _num(fb_result, "shares")
        )
        views = _num(ig_result, "plays") + _num(fb_result, "video_views")
        engagement_rate = round(engagements / views * 100, 2) if views else 0.0
        return SocialPostInsightResponse(
            feed_id=feed_id,
            instagram=_build_ig(ig_result),
            facebook=_build_fb(fb_result),
            EngagementRate=engagement_rate,
        )

    try:
        ig_posts, fb_posts, best_time_to_post = await _asyncio.gather(
            get_all_instagram_posts(contactid=contactid, userprofileid=userprofileid),
            get_all_facebook_posts(contactid=contactid, userprofileid=userprofileid),
            get_best_time_to_post(contactid=contactid, userprofileid=userprofileid, period_days=period_days),
        )

        pub_map: dict = {}
        for post in ig_posts + fb_posts:
            fid = post["feed_id"]
            pa = post.get("published_at")
            if fid not in pub_map or (pa and (pub_map[fid] is None or pa > pub_map[fid])):
                pub_map[fid] = pa
        feed_ids = sorted(
            pub_map,
            key=lambda fid: pub_map[fid] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        posts = list(await _asyncio.gather(*[_build_feed_insight(fid) for fid in feed_ids]))

        return SocialUserPostsInsightResponse(
            posts=posts,
            total_posts=len(posts),
            besttimetopost=best_time_to_post,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "SOCIAL_INSIGHTS_ENDPOINT | contactid=%s userprofileid=%s | %s",
            contactid, userprofileid, exc, exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to fetch social post insights")
