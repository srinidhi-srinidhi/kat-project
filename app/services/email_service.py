"""Email Service - async HTML email sends through Microsoft Graph with client-credentials auth.

Service used by OTP, auth, invitation, booking, plan, support and review flows to send transactional HTML email via the Graph sendMail endpoint, caching the Azure AD app-only token and raising on failure so each caller decides whether to swallow it.

Flow:
    1. Acquire an OAuth2 access token from Azure AD using client credentials.
    2. Cache the token (valid ~1 hour) to avoid a round-trip on every email.
    3. POST the email payload to the Graph API /sendMail endpoint.

Features:
    - OAuth2 Client Credentials: No passwords, no SMTP AUTH, no policy conflicts
    - Token Caching: In-memory cache with 60-second buffer before expiry
    - HTML Bodies: Sends HTML content via Graph API message format
    - Multiple Recipients: Accepts one address or a sequence, de-duplicated
    - Inline Images: Optional images attached inline and shown in the body by cid
    - File Attachments: Optional files such as a PDF attached to the message
    - Async Native: Pure async httpx — no thread executor needed
    - Error Propagation: Raises on failure so callers can decide to swallow

Functions:
    _get_token_lock(): Lazily create the module-level asyncio.Lock guarding token-cache access
    _get_access_token(): Acquire a new Azure AD OAuth2 token, or return the cached one if still valid
    _normalize_recipients(): Reduce a recipient string or sequence to a de-duplicated address list
    send_email(): Send a single HTML email via Microsoft Graph API

Dependencies:
    - httpx: Async HTTP client for Graph API and token requests
    - asyncio: Lock for thread-safe token cache access
    - base64: Encoding inline image and file attachments
    - app.core.config: GRAPH_* credentials and sender configuration
    - app.core.logging: Centralized application logging

Updates:
    06-Jul-2026: Initial creation with Microsoft Graph API
    03-Sep-2026: send_email() accepts several recipients.
    08-Oct-2026: send_email() takes inline_images shown in the body by cid.
    22-Oct-2026: send_email() takes file attachments.
"""

import asyncio
import base64
import time
from typing import Optional, Sequence, Tuple, Union

import httpx

from app.core.config import settings
from app.core.logging import logger

_GRAPH_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
_TOKEN_BUFFER_SECONDS = 60

_token_cache: tuple[str, float] = ("", 0.0)
_token_lock: Optional[asyncio.Lock] = None


def _get_token_lock() -> asyncio.Lock:
    """Return the module-level token-cache lock, creating it lazily inside the running event loop."""
    global _token_lock
    if _token_lock is None:
        _token_lock = asyncio.Lock()
    return _token_lock


async def _get_access_token() -> str:
    """Return a cached Microsoft Graph access token, fetching a new one from Azure AD near expiry.

    Raises:
        httpx.HTTPStatusError: When the Azure AD token endpoint rejects the request.
    """
    global _token_cache
    async with _get_token_lock():
        token, expiry = _token_cache
        if token and time.time() < expiry - _TOKEN_BUFFER_SECONDS:
            return token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                _GRAPH_TOKEN_URL.format(tenant=settings.GRAPH_TENANT_ID),
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.GRAPH_CLIENT_ID,
                    "client_secret": settings.GRAPH_CLIENT_SECRET,
                    "scope": "https://graph.microsoft.com/.default",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data["access_token"]
            expiry = time.time() + data.get("expires_in", 3600)
            _token_cache = (token, expiry)
            logger.debug("EMAIL_SERVICE | TOKEN_REFRESHED | expires_in=%s", data.get("expires_in"))
            return token


def _normalize_recipients(to: Union[str, Sequence[str]]) -> list[str]:
    """Split comma- or semicolon-separated addresses into an ordered list without blanks or case-insensitive repeats."""
    raw = [to] if isinstance(to, str) else list(to)
    addresses: list[str] = []
    for entry in raw:
        for address in str(entry).replace(";", ",").split(","):
            address = address.strip()
            if address and address.lower() not in {a.lower() for a in addresses}:
                addresses.append(address)
    return addresses


async def send_email(
    to: Union[str, Sequence[str]],
    subject: str,
    body: str,
    inline_images: Optional[Sequence[Tuple[str, str, str, bytes]]] = None,
    attachments: Optional[Sequence[Tuple[str, str, bytes]]] = None,
) -> None:
    """Send one HTML email to all recipients on its To line through Microsoft Graph.

    Args:
        to: One address, several separated by commas or semicolons, or a sequence of them.
        subject: Email subject line.
        body: HTML body content; plain text is also acceptable.
        inline_images: (content_id, filename, content_type, bytes) images the body shows with src="cid:<content_id>".
        attachments: (filename, content_type, bytes) files attached to the message, e.g. a PDF.

    Raises:
        ValueError: When Graph settings are missing or no recipient address is given.
        httpx.HTTPStatusError: When Graph rejects the token or send request.
    """
    if not all([settings.GRAPH_TENANT_ID, settings.GRAPH_CLIENT_ID, settings.GRAPH_CLIENT_SECRET, settings.GRAPH_SENDER_EMAIL]):
        raise ValueError(
            "Graph API email configuration is incomplete. "
            "Check GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, GRAPH_SENDER_EMAIL in .env."
        )

    recipients = _normalize_recipients(to)
    if not recipients:
        raise ValueError(f"send_email was given no recipient address (to={to!r}).")

    token = await _get_access_token()

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in recipients],
        },
        "saveToSentItems": False,
    }
    message_attachments = [
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": filename,
            "contentType": content_type,
            "contentBytes": base64.b64encode(content).decode("ascii"),
            "isInline": True,
            "contentId": content_id,
        }
        for content_id, filename, content_type, content in inline_images or []
    ]
    message_attachments += [
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": filename,
            "contentType": content_type,
            "contentBytes": base64.b64encode(content).decode("ascii"),
        }
        for filename, content_type, content in attachments or []
    ]
    if message_attachments:
        payload["message"]["attachments"] = message_attachments

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _GRAPH_SEND_URL.format(sender=settings.GRAPH_SENDER_EMAIL),
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()

    logger.info("EMAIL_SERVICE | SENT | to=%s | subject=%s", ", ".join(recipients), subject)
