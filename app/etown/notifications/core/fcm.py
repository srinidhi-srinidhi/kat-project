"""FCM Transport Service - shared Firebase Cloud Messaging v1 sender and token hygiene.

Notification core service that posts a caller-built FCM message through the shared HTTP client, mints and caches the OAuth token, classifies dead-token errors, and records and soft-invalidates dead tokens in etown_contactdevices.

Features:
    - Single FCM v1 HTTP POST path (uses the shared TrackedHTTPClient pool)
    - Robust status extraction across the httpx / aiohttp response shapes
    - Permanent-vs-transient error classification (404 / INVALID_ARGUMENT token)
    - Soft invalidation of dead tokens in etown_contactdevices (opt-out per call)
    - Records every dead token as an app uninstall before invalidating it
    - Never raises to the caller: returns a bool so fan-out keeps going
    - Server-side OAuth token minting with an in-process cache (5-minute expiry buffer)

Classes:
    No classes defined (module exposes send_fcm_message and its supporting helpers)

Functions:
    is_invalid_token_response(): classify an FCM error as a dead-token error
    mark_fcm_token_invalid(): soft-invalidate a stale token row
    send_fcm_message(): POST one FCM v1 message; returns True on HTTP 200
    get_fcm_oauth_token(): mint (or return cached) Google OAuth2 token for FCM

Dependencies:
    - google.auth / google.oauth2.service_account: mints the FCM OAuth2 bearer token
    - app.utils.http_client.get_http_client: shared pooled HTTP client for the POST
    - app.db.client.Database: UPDATE etown_contactdevices for token invalidation
    - app.etown.devices.uninstall_utils: Records the dead token as an uninstall
    - app.core.config.settings: FCM_ENDPOINT

Updates:
    03-Jun-2026: Extracted from four duplicated senders; server-side OAuth token minting.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    15-Sep-2026: Dead tokens are recorded as app uninstalls before invalidation.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.devices.uninstall_utils import record_device_uninstall
from app.utils.http_client import get_http_client

_FCM_SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]
_SERVICE_ACCOUNT_PATH = Path(__file__).parents[3] / "certificate" / "etowndev.json"
_TOKEN_EXPIRY_BUFFER_SECONDS = 300
_cached_credentials: Optional[Credentials] = None


def is_invalid_token_response(status_code: int, response_text: str) -> bool:
    """Return True for a 404, or a 400 INVALID_ARGUMENT naming message.token, meaning the device token is dead."""
    if status_code == 404:
        return True
    if status_code != 400:
        return False
    try:
        body = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        logger.debug("fcm: error body is not valid JSON; not treating it as an invalid token")
        return False
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return False
    if err.get("status") == "INVALID_ARGUMENT":
        for detail in err.get("details", []) or []:
            for fv in detail.get("fieldViolations", []) or []:
                if fv.get("field") == "message.token":
                    return True
    return False


async def mark_fcm_token_invalid(device_token: str) -> None:
    """Clear a dead FCM token from etown_contactdevices device_token or deviceguid, keeping the row."""
    if not device_token:
        return
    try:
        await Database.execute(
            """
            UPDATE etown_contactdevices
               SET device_token = CASE WHEN device_token = $1 THEN NULL ELSE device_token END,
                   deviceguid   = CASE WHEN deviceguid   = $1 THEN NULL ELSE deviceguid   END,
                   device_token_type = CASE WHEN device_token = $1 THEN 'invalid' ELSE device_token_type END,
                   last_used_at = NOW(),
                   modifieddate = NOW()
             WHERE device_token = $1 OR deviceguid = $1
            """,
            device_token,
        )
        logger.info("Invalidated FCM token (prefix=%s)", device_token[:10])
    except Exception as exc:
        logger.warning("Failed to invalidate FCM token: %s", exc)


async def send_fcm_message(
    *,
    oauth_token: Optional[str],
    payload: Dict[str, Any],
    device_token: Optional[str] = None,
    invalidate_on_bad_token: bool = True,
    timeout: float = 30.0,
    label: str = "FCM notification",
    detection_source: str = "live_push",
) -> bool:
    """POST one FCM v1 message; on a dead-token response record an uninstall and optionally invalidate the token.

    Args:
        oauth_token: FCM OAuth bearer token; when missing the send is skipped.
        payload: The full FCM request body, shaped by the caller.
        device_token: Target token, used for logging, uninstall recording and invalidation.
        invalidate_on_bad_token: Whether a dead-token response clears the token.
        timeout: HTTP timeout in seconds.
        label: Human-readable prefix for log lines.
        detection_source: Recorded on the uninstall row, e.g. live_push or probe.

    Returns:
        True on HTTP 200, False otherwise; never raises.
    """
    if not oauth_token:
        logger.info("Skipping %s: missing OAuth token", label)
        return False

    target = f"token_prefix={device_token[:10] if device_token else 'unknown'}"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {oauth_token}",
    }

    try:
        async with get_http_client(timeout=timeout) as client:
            async with client.post(
                settings.FCM_ENDPOINT,
                json=payload,
                headers=headers,
            ) as response:
                status_code = (
                    response.status if hasattr(response, "status") else response.status_code
                )
                if status_code == 200:
                    logger.info("%s sent | %s", label, target)
                    return True

                _text = getattr(response, "text", None)
                response_text = (
                    (await _text()) if callable(_text)
                    else (_text if _text is not None else str(response))
                )
                is_invalid = bool(device_token) and is_invalid_token_response(
                    status_code, response_text
                )
                emit = logger.warning if is_invalid else logger.error
                emit(
                    "%s failed | status=%s %s invalid_token=%s | response=%s",
                    label, status_code, target, is_invalid, response_text,
                )
                if is_invalid:
                    await record_device_uninstall(
                        device_token,
                        detection_source=detection_source,
                        status_code=status_code,
                        error_text=response_text,
                    )
                    if invalidate_on_bad_token:
                        await mark_fcm_token_invalid(device_token)
                return False
    except Exception as exc:
        logger.error("%s error | %s | %s", label, target, exc, exc_info=True)
        return False


async def get_fcm_oauth_token() -> str:
    """Return a cached FCM OAuth token, minting a new one from the service account when near expiry."""
    global _cached_credentials

    if _cached_credentials is not None and _cached_credentials.token:
        expiry = _cached_credentials.expiry
        if expiry is not None:
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            remaining = (expiry - now_utc).total_seconds()
            if remaining > _TOKEN_EXPIRY_BUFFER_SECONDS:
                logger.debug("FCM OAuth token cache hit (expires in %.0fs)", remaining)
                return _cached_credentials.token

    logger.info("Minting fresh FCM OAuth token from %s", _SERVICE_ACCOUNT_PATH)
    credentials = Credentials.from_service_account_file(
        str(_SERVICE_ACCOUNT_PATH),
        scopes=_FCM_SCOPES,
    )
    await asyncio.to_thread(credentials.refresh, Request())
    _cached_credentials = credentials
    logger.info(
        "FCM OAuth token minted (expires at %s UTC)",
        credentials.expiry,
    )
    return credentials.token
