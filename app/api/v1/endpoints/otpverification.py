"""OTP Verification Endpoints - HTTP routes that send mobile and email OTPs and open login sessions.

HTTP layer for /api/otp: checks the shared bearer token, generates and sends OTPs through otpverification_utils, promotes an unverified owner of the number in the background, and returns an Etown session from /loginverify when the number owns an account.

Features:
    - Mobile Number Verify: POST endpoint to generate and send OTP to a mobile number
    - Phone Ownership Guard: Blocks OTP only if the phone belongs to a different verified contact
    - On-OTP Promotion: an OTP for an unverified provider/member's number promotes it in the background
    - Login Session: /loginverify also returns an Etown session for a number that owns an account
    - Email Verify: Authenticated POST endpoint to generate and email an OTP (en/ta)

Endpoints:
    POST /numberverify: Generate and send an OTP to the given mobile number
    POST /loginverify: Generate and send a login OTP, and open a session for the owning account
    POST /emailverify: Generate and email an OTP to the given email address

Classes:
    NumberVerifyRequest(): Body for POST /numberverify — mobilenumber, language_code, optional contact_id
    NumberVerifyResponse(): Response for POST /numberverify — echoed mobilenumber and the generated OTP
    LoginVerifyRequest(): Body for POST /loginverify — mobilenumber and language_code
    LoginVerifyResponse(): Response for POST /loginverify — OTP plus an optional session for the owning account
    EmailVerifyRequest(): Body for POST /emailverify — emailid and language_code
    EmailVerifyResponse(): Response for POST /emailverify — echoed emailid and the generated OTP

Functions:
    number_verify(): Handle POST /numberverify endpoint
    login_verify(): Handle POST /loginverify endpoint
    email_verify(): Handle POST /emailverify endpoint

Dependencies:
    - fastapi: API framework and routing
    - pydantic: Request/response model validation
    - app.core.security: Bearer token verification (verify_token)
    - app.etown.otpverification.otpverification_utils: OTP generation and delivery logic
    - app.etown.user.verification_promotion: shared unverified -> verified promotion + Dataverse mirror
    - app.etown.auth.auth_service / auth_utils: owner resolution and session issuance for /loginverify

Updates:
    27-May-2026: Initial creation with numberverify endpoint
    22-Jun-2026: Added contact_id field and phone-ownership validation before OTP trigger
    13-Jul-2026: Added /emailverify endpoint (PowerApps UserEmailValidation migration)
    14-Jul-2026: /numberverify ownership guard uses get_phone_owner; schedules unverified-listing promotion.
    29-Jul-2026: Promotion delegated to promote_verified_number.
    26-Aug-2026: Logged silent exception handlers with tracebacks.
    26-Oct-2026: /numberverify and /loginverify return the AUTH_STATIC_OTP_NUMBERS OTP without an SMS for test numbers.
"""

import logging
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from typing import Optional
from pydantic import BaseModel

from app.core.security import verify_token
from app.etown.auth import auth_service, auth_utils
from app.etown.otpverification.otpverification_utils import (
    generate_otp,
    get_phone_owner,
    send_otp_sms,
    send_otp_email,
    static_otp_for,
)
from app.etown.user.verification_promotion import promote_verified_number

logger = logging.getLogger(__name__)

router = APIRouter()


class NumberVerifyRequest(BaseModel):
    mobilenumber: str
    language_code: str
    contact_id: Optional[str] = None


class LoginVerifyRequest(BaseModel):
    mobilenumber: str
    language_code: str


class LoginVerifyResponse(BaseModel):
    mobilenumber: str
    OTP: str
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_type: Optional[str] = None
    expires_in: Optional[int] = None
    contactid: Optional[str] = None
    userprofileid: Optional[str] = None
    role: Optional[int] = None


class NumberVerifyResponse(BaseModel):
    mobilenumber: str
    OTP: str


class EmailVerifyRequest(BaseModel):
    emailid: str
    language_code: str


class EmailVerifyResponse(BaseModel):
    emailid: str
    OTP: str


@router.post(
    "/numberverify",
    response_model=NumberVerifyResponse,
    status_code=200,
    summary="Mobile Number Verify",
    description="Etown - Mobile API - Chaatie - Mobilenumber Verify API",
    responses={
        200: {
            "description": "OTP generated and sent successfully",
            "content": {
                "application/json": {
                    "example": {
                        "mobilenumber": "9876543210",
                        "OTP": "125438"
                    }
                }
            }
        },
        400: {
            "description": "Invalid input",
            "content": {
                "application/json": {
                    "example": {"detail": "mobilenumber is required"}
                }
            }
        },
        409: {
            "description": "Phone number registered to a different account",
            "content": {
                "application/json": {
                    "example": {"detail": "Mobile number already registered to another account"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to generate OTP"}
                }
            }
        }
    }
)
async def number_verify(request: NumberVerifyRequest, background_tasks: BackgroundTasks):
    """Handle POST /api/otp/numberverify: SMS and return an OTP, rejecting a number verified to another contact."""

    if not request.mobilenumber:
        raise HTTPException(status_code=400, detail="mobilenumber is required")

    owner = await get_phone_owner(request.mobilenumber)
    if (
        owner is not None
        and owner["verified"]
        and request.contact_id is not None
        and owner["contactid"] != request.contact_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Mobile number already registered to another account",
        )

    try:
        otp = static_otp_for(request.mobilenumber)
        if otp is None:
            otp = generate_otp()
            await send_otp_sms(
                mobilenumber=request.mobilenumber,
                otp=otp,
                language_code=request.language_code,
            )

        if owner is not None:
            background_tasks.add_task(
                promote_verified_number,
                mobilenumber=request.mobilenumber,
                contact_id=owner["contactid"],
            )

        return NumberVerifyResponse(
            mobilenumber=request.mobilenumber,
            OTP=otp
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[NUMBERVERIFY] Unexpected error for {request.mobilenumber}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate OTP")


@router.post(
    "/loginverify",
    response_model=LoginVerifyResponse,
    status_code=200,
    summary="Login OTP Verify",
    description="Etown - Mobile API - Login OTP Generation API (also opens a session when the number owns an account)",
    responses={
        200: {
            "description": "OTP generated and sent successfully; session included when the number owns an account",
            "content": {
                "application/json": {
                    "example": {
                        "mobilenumber": "9876543210",
                        "OTP": "125438",
                        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                        "refresh_token": "0Qw8n1t2...",
                        "token_type": "Bearer",
                        "expires_in": 900,
                        "contactid": "392855bc-7ffb-459a-9120-07ed51659e40",
                        "userprofileid": "354b3245-0a64-4f14-a72b-4235d1c4b204",
                        "role": 2
                    }
                }
            }
        },
        400: {
            "description": "Invalid input",
            "content": {
                "application/json": {
                    "example": {"detail": "mobilenumber is required"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to generate OTP"}
                }
            }
        }
    }
)

async def login_verify(request: LoginVerifyRequest):
    """Handle POST /api/otp/loginverify: SMS and return a login OTP, plus a session when the number owns an account."""

    if not request.mobilenumber:
        raise HTTPException(status_code=400, detail="mobilenumber is required")

    try:
        otp = static_otp_for(request.mobilenumber)
        if otp is None:
            otp = generate_otp()
            await send_otp_sms(
                mobilenumber=request.mobilenumber,
                otp=otp,
                language_code=request.language_code,
            )

        session = {}
        owner = await auth_utils.fetch_identifier_owner(
            auth_utils.normalize_identifier(request.mobilenumber, "sms"), "sms"
        )
        if owner is not None:
            try:
                session = await auth_service.issue_session_for_contact(contactid=owner["contactid"])
            except Exception as e:
                logger.error(f"[LOGINVERIFY] Session issue failed for {request.mobilenumber}: {e}", exc_info=True)

        return LoginVerifyResponse(
            mobilenumber=request.mobilenumber,
            OTP=otp,
            **session
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[LOGINVERIFY] Unexpected error for {request.mobilenumber}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate OTP")


@router.post(
    "/emailverify",
    response_model=EmailVerifyResponse,
    status_code=200,
    summary="Email OTP Verify",
    description="Etown - Mobile API - Email Validation and Verification API (generate + email a 6-digit OTP)",
    responses={
        200: {
            "description": "OTP generated and emailed successfully",
            "content": {
                "application/json": {
                    "example": {
                        "emailid": "user@example.com",
                        "OTP": "125438"
                    }
                }
            }
        },
        400: {
            "description": "Invalid input",
            "content": {
                "application/json": {
                    "example": {"detail": "emailid is required"}
                }
            }
        },
        401: {
            "description": "Invalid or missing authentication token",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid or missing authentication token"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to generate OTP"}
                }
            }
        }
    }
)
async def email_verify(request: EmailVerifyRequest, _: str = Depends(verify_token)):
    """Handle POST /api/otp/emailverify: email a 6-digit OTP to the address and return it."""

    if not request.emailid:
        raise HTTPException(status_code=400, detail="emailid is required")

    try:
        otp = generate_otp()
        await send_otp_email(
            emailid=request.emailid,
            otp=otp,
            language_code=request.language_code,
        )

        return EmailVerifyResponse(
            emailid=request.emailid,
            OTP=otp
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[EMAILVERIFY] Unexpected error for {request.emailid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate OTP")
