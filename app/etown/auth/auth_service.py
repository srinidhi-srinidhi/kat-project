"""Auth Service - provider-independent login, session and refresh logic behind /api/auth.

Domain layer behind /api/auth: resolves an email or mobile number to an Etown account, has the configured AuthProvider issue and verify the challenge, maps its errors to HTTP status codes, and issues, rotates and revokes JWT sessions with hashed refresh tokens.

Features:
    - Provider Selection: AUTH_PROVIDER picks the backend; endpoints never see it
    - Identity Resolution: verified-first email or mobile owner lookup, members and providers
    - Deleted-Account Refusal: no challenge, session, or rotation for a deleted contact
    - Session Issuance: access JWT plus an opaque, hashed, rotating refresh token
    - Reuse Detection: replaying a spent refresh token revokes the contact's whole chain
    - Ownership Recording: sets etown_contact.emailverified on first proof
    - Pre-Registration Tokens: a proven identifier with no account gets a short-lived registration token

Classes:
    No classes defined (module exposes provider selection and session functions)

Functions:
    get_provider(): Return the configured AuthProvider instance
    _as_http(): Translate a provider AuthChallengeError into the API's HTTPException contract
    _issue_session(): Mint an access JWT and store a fresh refresh token for a proven identity
    request_login(): Resolve the identifier and have the provider issue a challenge
    verify_login(): Verify an OTP and, when an account exists, open a session
    issue_session_for_contact(): Open a session for an already-proven contact
    build_magiclink_redirect(): Consume a link token and build the app redirect URL
    exchange_code(): Redeem a magic-link exchange code for a session
    refresh_session(): Rotate a refresh token and issue a fresh session
    logout(): Revoke a refresh token

Dependencies:
    - fastapi: HTTPException for the API-facing error contract
    - app.core.config: AUTH_* settings, including AUTH_PROVIDER
    - app.etown.auth.base: AuthProvider interface, identity types and AuthChallengeError
    - app.etown.auth.auth_provider: LocalAuthProvider, the default in-house provider
    - app.etown.auth.auth_utils: identity, challenge and refresh-token persistence
    - app.etown.auth.tokens: JWT minting and peppered hashing

Updates:
    02-Aug-2026: Initial creation; request_login/verify_login cover email and sms channels.
    19-Aug-2026: verify_login returns a registration token for an identifier with no account.
    18-Sep-2026: Added request_alternate_mobile/verify_alternate_mobile.
    23-Sep-2026: Removed request_alternate_mobile/verify_alternate_mobile and the alternatemobileverified flag.
"""

import datetime
import logging
from typing import Optional
from urllib.parse import urlencode

from fastapi import HTTPException

from app.core.config import settings
from app.etown.auth import auth_utils
from app.etown.auth.base import AuthChallengeError, AuthProvider, ResolvedIdentity, VerifiedIdentity
from app.etown.auth.auth_provider import LocalAuthProvider
from app.etown.auth.tokens import (
    generate_url_secret,
    hash_secret,
    mint_access_token,
    mint_registration_token,
)

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, type] = {"local": LocalAuthProvider}

_ERROR_STATUS: dict[str, int] = {
    "not_found": 404,
    "expired": 410,
    "consumed": 409,
    "invalid_secret": 401,
    "too_many_attempts": 429,
    "throttled": 429,
    "send_failed": 502,
}

_provider_instance: Optional[AuthProvider] = None


def get_provider() -> AuthProvider:
    """Return the cached authentication provider selected by AUTH_PROVIDER.

    Raises:
        RuntimeError: When AUTH_PROVIDER names an unregistered provider.
    """
    global _provider_instance
    if _provider_instance is None:
        provider_cls = _PROVIDERS.get(settings.AUTH_PROVIDER)
        if provider_cls is None:
            raise RuntimeError(
                f"AUTH_PROVIDER='{settings.AUTH_PROVIDER}' is not registered. "
                f"Known providers: {', '.join(sorted(_PROVIDERS))}"
            )
        _provider_instance = provider_cls()
    return _provider_instance


def _as_http(error: AuthChallengeError) -> HTTPException:
    """Map a provider challenge error to an HTTPException, with Retry-After when supplied."""
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
    return HTTPException(
        status_code=_ERROR_STATUS.get(error.code, 400),
        detail=error.message,
        headers=headers,
    )


async def _issue_session(
    identity: VerifiedIdentity,
    device_id: Optional[str],
    user_agent: Optional[str],
) -> tuple[dict, str]:
    """Mint an access JWT and a stored refresh token for a proven identity.

    Returns:
        (session payload, stored refresh token row id).
    """
    access_token, expires_in = mint_access_token(
        identity.contactid, identity.userprofileid, identity.role
    )
    refresh_token = generate_url_secret(48)
    refresh_id = await auth_utils.store_refresh_token(
        contactid=identity.contactid,
        token_hash=hash_secret(refresh_token),
        expires_at=datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(days=settings.AUTH_REFRESH_TTL_DAYS),
        device_id=device_id,
        user_agent=user_agent,
    )
    session = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "contactid": identity.contactid,
        "userprofileid": identity.userprofileid,
        "role": identity.role,
    }
    return session, refresh_id


async def request_login(
    *,
    identifier: str,
    channel: str = "email",
    method: str = "otp",
    purpose: str = "login",
    language_code: str = "en",
    device_id: Optional[str] = None,
    request_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """Validate and resolve an identifier, then have the provider issue an OTP or magic-link challenge.

    Args:
        identifier: Email address or mobile number to authenticate.
        channel: 'email' or 'sms'.
        method: 'otp', or 'magiclink' for email only.
        purpose: 'login', 'verify', or 'auto' to pick by whether an account exists.
        language_code: Delivery language.
        device_id: Optional client device identifier.
        request_ip: Caller IP, for audit.
        user_agent: Caller user agent, for audit.

    Returns:
        dict with request_id, method and expires_in, plus OTP when debug return is on.

    Raises:
        HTTPException: 400 on bad input, 404 for an unknown login, 429 throttled, 502 delivery.
    """
    if channel not in ("email", "sms"):
        raise HTTPException(status_code=400, detail="channel must be 'email' or 'sms'")
    if method not in ("otp", "magiclink"):
        raise HTTPException(status_code=400, detail="method must be 'otp' or 'magiclink'")
    if channel == "sms" and method != "otp":
        raise HTTPException(status_code=400, detail="Magic links can only be delivered by email")
    if purpose not in ("login", "verify", "auto"):
        raise HTTPException(status_code=400, detail="purpose must be 'login', 'verify' or 'auto'")

    normalized = auth_utils.normalize_identifier(identifier, channel)
    if channel == "email" and (not normalized or "@" not in normalized):
        raise HTTPException(status_code=400, detail="A valid email address is required")
    if channel == "sms" and len(normalized) != 10:
        raise HTTPException(status_code=400, detail="A valid 10-digit mobile number is required")

    owner = await auth_utils.fetch_identifier_owner(normalized, channel)
    if purpose == "auto":
        purpose = "login" if owner is not None else "verify"
    if owner is None and purpose == "login":
        raise HTTPException(status_code=404, detail="No account found for this identifier")

    identity = ResolvedIdentity(
        identifier=normalized,
        channel=channel,
        contactid=owner["contactid"] if owner else None,
        userprofileid=owner["userprofileid"] if owner else None,
        role=owner["role"] if owner else None,
    )

    try:
        handle = await get_provider().start_challenge(
            identity=identity,
            method=method,
            purpose=purpose,
            language_code=language_code,
            device_id=device_id,
            request_ip=request_ip,
            user_agent=user_agent,
        )
    except AuthChallengeError as exc:
        raise _as_http(exc) from exc

    response = {
        "request_id": handle.request_id,
        "method": handle.method,
        "expires_in": handle.expires_in,
    }
    if handle.debug_secret is not None:
        response["OTP"] = handle.debug_secret
    return response


async def verify_login(
    *,
    otp: str,
    request_id: Optional[str] = None,
    identifier: Optional[str] = None,
    channel: str = "email",
    device_id: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """Verify an OTP and open a session, or return a registration token when no account exists.

    Args:
        otp: The code the user entered.
        request_id: Challenge reference from request_login; preferred.
        identifier: Email or mobile whose newest live challenge is used without request_id.
        channel: Channel of identifier.
        device_id: Optional client device identifier.
        user_agent: Caller user agent, for audit.

    Returns:
        dict with verified, identifier, channel and contactid plus session or registration token.

    Raises:
        HTTPException: 400 missing input, 401 wrong code, 404/409/410 unusable challenge, 429 exhausted.
    """
    if not otp:
        raise HTTPException(status_code=400, detail="otp is required")

    if not request_id:
        if not identifier:
            raise HTTPException(status_code=400, detail="request_id or identifier is required")
        pending = await auth_utils.fetch_latest_live_challenge(
            auth_utils.normalize_identifier(identifier, channel)
        )
        if pending is None:
            raise HTTPException(status_code=404, detail="No pending code for this identifier")
        request_id = pending["id"]

    try:
        identity = await get_provider().verify_challenge(request_id=request_id, secret=otp)
    except AuthChallengeError as exc:
        raise _as_http(exc) from exc

    result = {
        "verified": True,
        "identifier": identity.identifier,
        "channel": identity.channel,
        "contactid": identity.contactid,
    }
    if identity.contactid is None:
        token, expires_in = mint_registration_token(identity.identifier, identity.channel)
        result.update(
            {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": expires_in,
                "registration": True,
            }
        )
        return result
    if await auth_utils.fetch_contact_identity(identity.contactid) is None:
        raise HTTPException(status_code=404, detail="No account found for this identifier")

    newly_verified = False
    if identity.channel == "email":
        newly_verified = await auth_utils.mark_email_verified(identity.contactid)
    session, _ = await _issue_session(identity, device_id, user_agent)
    result.update(session)
    result["email_newly_verified"] = newly_verified
    return result


async def issue_session_for_contact(
    *,
    contactid: str,
    device_id: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """Open a session for a contact whose identifier ownership was already proven.

    Raises:
        HTTPException: 404 when the contact does not exist.
    """
    owner = await auth_utils.fetch_contact_identity(contactid)
    if owner is None:
        raise HTTPException(status_code=404, detail="Unknown contact")

    identity = VerifiedIdentity(
        identifier=owner["identifier"],
        channel="email",
        contactid=contactid,
        userprofileid=owner["userprofileid"],
        role=owner["role"],
    )
    session, _ = await _issue_session(identity, device_id, user_agent)
    return session


async def build_magiclink_redirect(*, link_token: str) -> str:
    """Consume a magic-link token and return the redirect URL carrying a one-time exchange code.

    Raises:
        HTTPException: 400 missing token, 404/410 unusable link, 500 redirect base unset.
    """
    if not settings.AUTH_MAGICLINK_REDIRECT_BASE:
        logger.error("[AUTH] AUTH_MAGICLINK_REDIRECT_BASE is not configured")
        raise HTTPException(status_code=500, detail="Magic-link redirect is not configured")
    if not link_token:
        raise HTTPException(status_code=400, detail="Missing link token")

    try:
        code = await get_provider().consume_magic_link(link_secret=link_token)
    except AuthChallengeError as exc:
        raise _as_http(exc) from exc

    base = settings.AUTH_MAGICLINK_REDIRECT_BASE
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{urlencode({'code': code})}"


async def exchange_code(
    *,
    code: str,
    device_id: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """Redeem a magic-link exchange code for a session and mark the email verified.

    Raises:
        HTTPException: 400 missing code, 404/409/410 unknown, used or expired code or deleted account.
    """
    if not code:
        raise HTTPException(status_code=400, detail="code is required")

    try:
        identity = await get_provider().exchange_code(code=code)
    except AuthChallengeError as exc:
        raise _as_http(exc) from exc

    if await auth_utils.fetch_contact_identity(identity.contactid) is None:
        raise HTTPException(status_code=404, detail="No account found for this identifier")

    newly_verified = await auth_utils.mark_email_verified(identity.contactid)
    session, _ = await _issue_session(identity, device_id, user_agent)
    session["email_newly_verified"] = newly_verified
    return session


async def refresh_session(
    *,
    refresh_token: str,
    device_id: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """Rotate a refresh token into a fresh session, revoking all of the contact's tokens on reuse.

    Raises:
        HTTPException: 400 missing token, 401 when unknown, expired, spent or the account is gone.
    """
    if not refresh_token:
        raise HTTPException(status_code=400, detail="refresh_token is required")

    stored = await auth_utils.fetch_refresh_token(hash_secret(refresh_token))
    if stored is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if stored["revoked_at"] is not None:
        revoked = await auth_utils.revoke_contact_refresh_tokens(stored["contactid"])
        logger.warning(
            "[AUTH] refresh token reuse detected | contact=%s | revoked=%d",
            stored["contactid"], revoked,
        )
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if stored["expires_at"] <= datetime.datetime.now(datetime.timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh token has expired")

    owner = await auth_utils.fetch_contact_identity(stored["contactid"])
    if owner is None:
        raise HTTPException(status_code=401, detail="Account is no longer active")

    identity = VerifiedIdentity(
        identifier=owner["identifier"],
        channel="email",
        contactid=stored["contactid"],
        userprofileid=owner["userprofileid"],
        role=owner["role"],
    )
    session, new_id = await _issue_session(identity, device_id or stored["device_id"], user_agent)

    if not await auth_utils.rotate_refresh_token(stored["id"], new_id):
        await auth_utils.revoke_refresh_token(hash_secret(session["refresh_token"]))
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    return session


async def logout(*, refresh_token: str) -> bool:
    """Revoke a refresh token and return True when a live token was revoked.

    Raises:
        HTTPException: 400 when no token is supplied.
    """
    if not refresh_token:
        raise HTTPException(status_code=400, detail="refresh_token is required")
    return await auth_utils.revoke_refresh_token(hash_secret(refresh_token))
