"""Auth Endpoints - HTTP routes for passwordless email and mobile OTP plus magic-link login.

HTTP layer for /api/auth: checks the shared bearer token on most routes, issues and verifies server-side OTP and magic-link challenges through auth_service, and opens, rotates, revokes and reports per-user JWT sessions; codes are stored only as peppered digests.

Features:
    - Two Channels: one challenge engine over email or SMS, issuing the same session
    - OTP Echo Switch: AUTH_RETURN_OTP_IN_RESPONSE returns the code in the challenge, otherwise never sent back
    - Alternate Numbers: a stored alternatemobile opens the same session, ranked behind primaries
    - Two Delivery Methods: 6-digit OTP or single-use magic link (email only)
    - Authorization-Code Redirect: magic links hand back a one-time code, never a token
    - Session Lifecycle: issue, rotate with reuse detection, and revoke, whichever channel opened it
    - Email Ownership: successful email verification flags the contact and mirrors to Dataverse

Classes:
    EmailLoginRequest(): Request body for POST /email/request - email, delivery method, language
    LoginChallengeResponse(): Issued challenge - request_id, method, expiry, optional OTP echo
    EmailLoginVerifyRequest(): Request body for POST /email/verify - request_id and otp
    MobileLoginRequest(): Request body for POST /mobile/request - mobilenumber and language
    MobileLoginVerifyRequest(): Request body for POST /mobile/verify - otp plus request_id or mobile
    MobileVerifyResponse(): SMS OTP result - a session, or a registration token if unregistered
    ExchangeRequest(): Request body for POST /session/exchange - the magic-link exchange code
    RefreshRequest(): Request body for POST /session/refresh - the refresh token to rotate
    LogoutRequest(): Request body for POST /session/logout - the refresh token to revoke
    SessionResponse(): Access + refresh token pair plus the session's identity claims
    LogoutResponse(): Whether the refresh token was revoked

Endpoints:
    POST /email/request: Issue and email a login challenge
    POST /email/verify: Verify an emailed OTP and open a session
    GET  /email/magiclink/consume: Consume a link and redirect into the app (unauthenticated)
    POST /mobile/request: SMS an OTP to any mobile number, registered or not
    POST /mobile/verify: Verify an SMS OTP, opening a session or returning a registration token
    POST /session/exchange: Redeem a magic-link code for a session
    POST /session/refresh: Rotate a refresh token
    POST /session/logout: Revoke a refresh token
    GET  /session/me: Return the claims of the presented access JWT

Functions:
    email_login_request(): Handle POST /email/request
    email_login_verify(): Handle POST /email/verify
    magiclink_consume(): Handle GET /email/magiclink/consume
    mobile_login_request(): Handle POST /mobile/request
    mobile_login_verify(): Handle POST /mobile/verify
    session_exchange(): Handle POST /session/exchange
    session_refresh(): Handle POST /session/refresh
    session_logout(): Handle POST /session/logout
    session_me(): Handle GET /session/me

Dependencies:
    - fastapi: API framework, routing and redirect responses
    - pydantic: Request/response model validation
    - app.core.security: verify_token, the shared-token gate on the routes here
    - app.etown.auth.auth_service: all authentication orchestration
    - app.etown.auth.tokens: access-token decoding for /session/me
    - app.etown.auth.auth_syncing: background Dataverse mirror of the emailverified flag

Updates:
    02-Aug-2026: Initial creation
    19-Aug-2026: /mobile/verify returns a registration token for unregistered numbers; registration_required always false.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    18-Sep-2026: Added /mobile/alternate/request and /mobile/alternate/verify.
    23-Sep-2026: Removed /mobile/alternate/request and /mobile/alternate/verify.
"""

import logging
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.core.security import verify_token
from app.etown.auth import auth_service
from app.etown.auth.auth_syncing import sync_emailverified_to_dataverse
from app.etown.auth.tokens import TokenError, decode_access_token

logger = logging.getLogger(__name__)

router = APIRouter()


class EmailLoginRequest(BaseModel):
    emailid: str
    method: Literal["otp", "magiclink"] = "otp"
    language_code: str = "en"
    device_id: Optional[str] = None


class LoginChallengeResponse(BaseModel):
    request_id: str
    method: str
    expires_in: int
    OTP: Optional[str] = Field(
        default=None,
        description="Only populated while AUTH_RETURN_OTP_IN_RESPONSE is enabled for frontend migration. Never set in production.",
    )


class EmailLoginVerifyRequest(BaseModel):
    request_id: str
    otp: str
    device_id: Optional[str] = None


class MobileLoginRequest(BaseModel):
    mobilenumber: str
    language_code: str = "en"
    device_id: Optional[str] = None


class MobileLoginVerifyRequest(BaseModel):
    otp: str
    request_id: Optional[str] = None
    mobilenumber: Optional[str] = None
    device_id: Optional[str] = None


class MobileVerifyResponse(BaseModel):
    """Result of an SMS OTP check. Fields that cannot exist yet are omitted, never null.

    A registered number gets the full session: contactid, userprofileid, role, access and
    refresh tokens. An unregistered one gets a short-lived registration access_token that
    proves the number and authorises nothing — there is no account to name, so contactid,
    userprofileid, role and refresh_token are left out rather than sent as nulls, and
    POST /api/users/register turns it into a real session.

    Read contactid, not registration_required, to tell the two apart: contactid is present
    only for an account that exists. registration_required is pinned false (19-Aug-2026)
    and no longer routes anyone to sign-up.
    """

    verified: bool
    registration_required: bool = Field(
        default=False,
        description="Always false. Kept so existing clients keep parsing; the caller decides "
                    "sign-up from the absence of contactid, which no session can omit.",
    )
    contactid: Optional[str] = None
    userprofileid: Optional[str] = None
    role: Optional[int] = None
    access_token: Optional[str] = Field(
        default=None,
        description="Session access JWT for a registered number; a short-lived, "
                    "authorises-nothing registration JWT when the number owns no account.",
    )
    refresh_token: Optional[str] = None
    token_type: Optional[str] = None
    expires_in: Optional[int] = None


class ExchangeRequest(BaseModel):
    code: str
    device_id: Optional[str] = None


class RefreshRequest(BaseModel):
    refresh_token: str
    device_id: Optional[str] = None


class LogoutRequest(BaseModel):
    refresh_token: str


class SessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    contactid: str
    userprofileid: Optional[str] = None
    role: Optional[int] = None


class LogoutResponse(BaseModel):
    revoked: bool


@router.post(
    "/email/request",
    response_model=LoginChallengeResponse,
    status_code=200,
    summary="Request Email Login",
    description="Etown - Mobile API - Issue and email a login challenge (6-digit OTP or magic link)",
    responses={
        200: {"description": "Challenge issued and emailed"},
        400: {"description": "Invalid email address or method"},
        404: {"description": "No account found for this email address"},
        429: {"description": "Throttled — too many login emails requested"},
        502: {"description": "Login email could not be delivered"},
    },
)
async def email_login_request(
    request: EmailLoginRequest,
    http_request: Request,
    _: str = Depends(verify_token),
) -> LoginChallengeResponse:
    """Handle POST /api/auth/email/request: email the caller a login OTP or magic link."""
    forwarded = http_request.headers.get("x-forwarded-for")
    request_ip = forwarded.split(",")[0].strip() if forwarded else (
        http_request.client.host if http_request.client else None
    )

    result = await auth_service.request_login(
        identifier=request.emailid,
        channel="email",
        method=request.method,
        language_code=request.language_code,
        device_id=request.device_id,
        request_ip=request_ip,
        user_agent=http_request.headers.get("user-agent"),
    )
    return LoginChallengeResponse(**result)


@router.post(
    "/email/verify",
    response_model=SessionResponse,
    status_code=200,
    summary="Verify Email Login OTP",
    description="Etown - Mobile API - Verify an emailed OTP and open a session",
    responses={
        200: {"description": "OTP verified; session issued"},
        401: {"description": "Incorrect code"},
        404: {"description": "Unknown login request"},
        409: {"description": "Login request already used"},
        410: {"description": "Code has expired"},
        429: {"description": "Too many incorrect attempts"},
    },
)
async def email_login_verify(
    request: EmailLoginVerifyRequest,
    http_request: Request,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_token),
) -> SessionResponse:
    """Handle POST /api/auth/email/verify: verify an emailed OTP, return a session, and mirror a new email verification."""
    session = await auth_service.verify_login(
        request_id=request.request_id,
        otp=request.otp,
        device_id=request.device_id,
        user_agent=http_request.headers.get("user-agent"),
    )
    if session.pop("email_newly_verified", False):
        background_tasks.add_task(sync_emailverified_to_dataverse, session["contactid"])
    return SessionResponse(**session)


@router.get(
    "/email/magiclink/consume",
    status_code=302,
    summary="Consume Magic Link",
    description=(
        "Etown - Mobile API - Consume an emailed magic link and redirect into the app. "
        "Deliberately unauthenticated: this URL is opened by the user's mail client, which "
        "cannot send the shared bearer header. The 256-bit single-use link token is the credential."
    ),
    responses={
        302: {"description": "Redirect to the app carrying a one-time exchange code"},
        400: {"description": "Missing link token"},
        404: {"description": "Link is invalid or already used"},
        410: {"description": "Link has expired"},
        500: {"description": "Magic-link redirect is not configured"},
    },
)
async def magiclink_consume(
    t: str = Query(..., description="Single-use token from the emailed link"),
) -> RedirectResponse:
    """Handle GET /api/auth/email/magiclink/consume: consume a magic-link token and redirect to the app with an exchange code."""
    redirect_url = await auth_service.build_magiclink_redirect(link_token=t)
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post(
    "/mobile/request",
    response_model=LoginChallengeResponse,
    status_code=200,
    summary="Request Mobile Login",
    description=(
        "Etown - Mobile API - SMS a 6-digit OTP to a mobile number, whether or not it owns "
        "an account. One screen serves sign-in and sign-up: /mobile/verify returns a session "
        "for a registered number, or a registration token for a new one."
    ),
    responses={
        200: {"description": "OTP issued and sent"},
        400: {"description": "A valid 10-digit mobile number is required"},
        429: {"description": "Throttled — too many codes requested"},
        502: {"description": "OTP SMS could not be delivered"},
    },
)
async def mobile_login_request(
    request: MobileLoginRequest,
    http_request: Request,
    _: str = Depends(verify_token),
) -> LoginChallengeResponse:
    """Handle POST /api/auth/mobile/request: SMS a 6-digit OTP to the mobile number, registered or not."""
    forwarded = http_request.headers.get("x-forwarded-for")
    request_ip = forwarded.split(",")[0].strip() if forwarded else (
        http_request.client.host if http_request.client else None
    )

    result = await auth_service.request_login(
        identifier=request.mobilenumber,
        channel="sms",
        method="otp",
        purpose="auto",
        language_code=request.language_code,
        device_id=request.device_id,
        request_ip=request_ip,
        user_agent=http_request.headers.get("user-agent"),
    )
    return LoginChallengeResponse(**result)


@router.post(
    "/mobile/verify",
    response_model=MobileVerifyResponse,
    response_model_exclude_none=True,
    status_code=200,
    summary="Verify Mobile OTP",
    description=(
        "Etown - Mobile API - Verify an SMS OTP. A registered number gets a session; an "
        "unregistered one gets a short-lived registration token, having proved it owns the "
        "number. Absent fields are omitted, never null, and registration_required is always "
        "false — read contactid to tell a real session from a proven-only number."
    ),
    responses={
        200: {
            "description": (
                "OTP verified; session issued, or a registration token for a number "
                "that owns no account yet"
            )
        },
        400: {"description": "Neither request_id nor mobilenumber was supplied"},
        401: {"description": "Incorrect code"},
        404: {"description": "Unknown or unusable login request"},
        409: {"description": "Login request already used"},
        410: {"description": "Code has expired"},
        429: {"description": "Too many incorrect attempts"},
    },
)
async def mobile_login_verify(
    request: MobileLoginVerifyRequest,
    http_request: Request,
    _: str = Depends(verify_token),
) -> MobileVerifyResponse:
    """Handle POST /api/auth/mobile/verify: verify an SMS OTP and return a session or a registration token."""
    result = await auth_service.verify_login(
        otp=request.otp,
        request_id=request.request_id,
        identifier=request.mobilenumber,
        channel="sms",
        device_id=request.device_id,
        user_agent=http_request.headers.get("user-agent"),
    )
    result.pop("registration", None)
    return MobileVerifyResponse(registration_required=False, **result)


@router.post(
    "/session/exchange",
    response_model=SessionResponse,
    status_code=200,
    summary="Exchange Magic-Link Code",
    description="Etown - Mobile API - Redeem the one-time code from a magic-link redirect for a session",
    responses={
        200: {"description": "Code redeemed; session issued"},
        404: {"description": "Code is invalid or already used"},
        410: {"description": "Code has expired"},
    },
)
async def session_exchange(
    request: ExchangeRequest,
    http_request: Request,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_token),
) -> SessionResponse:
    """Handle POST /api/auth/session/exchange: redeem a magic-link exchange code for an access and refresh token pair."""
    session = await auth_service.exchange_code(
        code=request.code,
        device_id=request.device_id,
        user_agent=http_request.headers.get("user-agent"),
    )
    if session.pop("email_newly_verified", False):
        background_tasks.add_task(sync_emailverified_to_dataverse, session["contactid"])
    return SessionResponse(**session)


@router.post(
    "/session/refresh",
    response_model=SessionResponse,
    status_code=200,
    summary="Refresh Session",
    description="Etown - Mobile API - Rotate a refresh token and issue a fresh session",
    responses={
        200: {"description": "Session rotated"},
        401: {"description": "Refresh token is invalid, expired, or already spent"},
    },
)
async def session_refresh(
    request: RefreshRequest,
    http_request: Request,
    _: str = Depends(verify_token),
) -> SessionResponse:
    """Handle POST /api/auth/session/refresh: rotate a refresh token and issue a fresh session."""
    session = await auth_service.refresh_session(
        refresh_token=request.refresh_token,
        device_id=request.device_id,
        user_agent=http_request.headers.get("user-agent"),
    )
    return SessionResponse(**session)


@router.post(
    "/session/logout",
    response_model=LogoutResponse,
    status_code=200,
    summary="Logout",
    description="Etown - Mobile API - Revoke a refresh token",
    responses={200: {"description": "Revocation result"}},
)
async def session_logout(
    request: LogoutRequest,
    _: str = Depends(verify_token),
) -> LogoutResponse:
    """Handle POST /api/auth/session/logout: revoke a refresh token, ending that session."""
    return LogoutResponse(revoked=await auth_service.logout(refresh_token=request.refresh_token))


@router.get(
    "/session/me",
    status_code=200,
    summary="Current Session",
    description=(
        "Etown - Mobile API - Return the claims carried by the presented access JWT. "
        "Never rejects the caller: an unrecognised or expired bearer yields null claims "
        "rather than a 401, so a stale token cannot end a live app session."
    ),
    responses={
        200: {"description": "Claims for the authenticated account, or nulls when no identity could be resolved"},
    },
)
async def session_me(
    http_request: Request,
) -> dict:
    """Handle GET /api/auth/session/me: return the bearer JWT's claims, even if expired, or nulls without rejecting."""
    identity = {"contactid": None, "userprofileid": None, "role": None}

    try:
        header = http_request.headers.get("authorization") or ""
        credential = header[7:].strip() if header[:7].lower() == "bearer " else ""

        if credential:
            claims = None
            try:
                claims = decode_access_token(credential)
            except TokenError as exc:
                logger.info("[AUTH] /session/me presented a token this API cannot verify | %s", exc)
                try:
                    claims = decode_access_token(credential, allow_expired=True)
                    logger.info("[AUTH] /session/me answered from an expired but authentic token")
                except TokenError:
                    logger.info("[AUTH] /session/me token failed verification even when expiry was allowed", exc_info=True)
                    claims = None

            if claims is not None:
                return {
                    "contactid": claims.get("contactid"),
                    "userprofileid": claims.get("userprofileid"),
                    "role": claims.get("role"),
                }
    except Exception as exc:
        logger.warning("[AUTH] /session/me resolution failed, answering 200 with null claims | %s", exc)

    return identity
