"""Local Auth Provider - in-house OTP and magic-link verification over email and SMS.

Auth domain implementation of base.AuthProvider that stores challenges in etown_auth_challenge as peppered digests, delivers codes and links through Microsoft Graph or the SMS gateway, and verifies secrets server-side with throttling and single-use redemption.

Features:
    - OTP: 6-digit CSPRNG code, attempt-limited and expiring, over email or SMS
    - Magic Link: single-use URL token exchanged for a short-lived authorization code (email only)
    - Send Throttling: per-identifier cooldown plus an hourly ceiling
    - Replay Safety: atomic conditional UPDATE, so one secret redeems only once
    - Multilingual Delivery: English and Tamil copy on both channels

Classes:
    LocalAuthProvider: AuthProvider backed by Postgres, Microsoft Graph and the SMS gateway

Functions:
    _utcnow(): Current UTC instant as an aware datetime, matching the timestamptz columns
    _identity_from_challenge(): Build a VerifiedIdentity from a consumed challenge row

Dependencies:
    - app.etown.auth.auth_utils: challenge persistence
    - app.etown.auth.tokens: secret generation, peppered hashing, constant-time compare
    - app.services.email_service: Microsoft Graph sender
    - app.services.email_templates: branded email shell and OTP/button blocks
    - app.etown.auth.base: AuthProvider contract, identity and error types
    - app.etown.otpverification.otpverification_utils: shared SMS gateway sender
    - app.core.config: AUTH_* settings

Updates:
    02-Aug-2026: Initial creation; added the SMS channel for mobile OTP login.
    24-Aug-2026: Login link and OTP emails now render through the shared branded shell.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    26-Oct-2026: SMS numbers in AUTH_STATIC_OTP_NUMBERS get their fixed OTP, unthrottled and with no SMS sent.
"""

import datetime
import logging
from typing import Optional

from app.core.config import settings
from app.etown.auth import auth_utils
from app.etown.auth.base import (
    AuthChallengeError,
    ChallengeHandle,
    ResolvedIdentity,
    VerifiedIdentity,
)
from app.etown.auth.tokens import (
    generate_otp_secret,
    generate_url_secret,
    hash_secret,
    secrets_match,
)
from app.etown.otpverification.otpverification_utils import send_otp_sms, static_otp_for
from app.services.email_service import send_email
from app.services.email_templates import button, otp_panel, paragraph, render_email

logger = logging.getLogger(__name__)


def _utcnow() -> datetime.datetime:
    """Return the current UTC instant as a timezone-aware datetime."""
    return datetime.datetime.now(datetime.timezone.utc)


def _identity_from_challenge(challenge: dict) -> VerifiedIdentity:
    """Build the VerifiedIdentity a consumed challenge row was issued for."""
    return VerifiedIdentity(
        identifier=challenge["identifier"],
        channel=challenge["channel"],
        contactid=challenge["contactid"],
        userprofileid=challenge["userprofileid"],
        role=challenge["role"],
    )


class LocalAuthProvider:
    """Email OTP and magic-link authentication backed by Postgres and Microsoft Graph."""

    name = "local"

    async def start_challenge(
        self,
        *,
        identity: ResolvedIdentity,
        method: str,
        purpose: str = "login",
        language_code: str = "en",
        device_id: Optional[str] = None,
        request_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> ChallengeHandle:
        """Throttle-check, store and deliver an OTP or magic-link challenge by SMS or email.

        Args:
            identity: The resolved account to authenticate.
            method: 'otp' or 'magiclink'; SMS accepts only 'otp'.
            purpose: 'login' or 'verify'.
            language_code: 'ta' for Tamil copy; anything else gets English.
            device_id: Optional client device identifier.
            request_ip: Caller IP, for audit.
            user_agent: Caller user agent, for audit.

        Returns:
            ChallengeHandle: Reference to the issued challenge.

        Raises:
            AuthChallengeError: 'throttled', or 'send_failed' on delivery failure or SMS magic link.
            RuntimeError: When magic links are requested but AUTH_MAGICLINK_API_BASE is unset.
        """
        if identity.channel == "sms" and method != "otp":
            raise AuthChallengeError("send_failed", "Magic links can only be delivered by email.")

        static_otp = (
            static_otp_for(identity.identifier) if identity.channel == "sms" else None
        )

        if static_otp is None:
            throttle = await auth_utils.fetch_send_throttle(identity.identifier)
            last_sent = throttle["last_sent_at"]
            if last_sent is not None:
                elapsed = (_utcnow() - last_sent).total_seconds()
                cooldown = settings.AUTH_RESEND_COOLDOWN_SECONDS
                if elapsed < cooldown:
                    raise AuthChallengeError(
                        "throttled",
                        "Please wait before requesting another code.",
                        retry_after=int(cooldown - elapsed) + 1,
                    )
            if throttle["sends_last_hour"] >= settings.AUTH_MAX_SENDS_PER_HOUR:
                raise AuthChallengeError(
                    "throttled",
                    "Too many login emails requested. Try again later.",
                    retry_after=3600,
                )

        is_tamil = language_code.upper() == "TA"

        if method == "magiclink":
            if not settings.AUTH_MAGICLINK_API_BASE:
                raise RuntimeError(
                    "AUTH_MAGICLINK_API_BASE is not configured; magic-link emails would "
                    "carry an unreachable URL."
                )
            secret = generate_url_secret()
            ttl = settings.AUTH_MAGICLINK_TTL_SECONDS
            link = (
                f"{settings.AUTH_MAGICLINK_API_BASE.rstrip('/')}"
                f"/api/auth/email/magiclink/consume?t={secret}"
            )
            minutes = ttl // 60
            if is_tamil:
                subject = "Etown – உள்நுழைவு இணைப்பு"
                body = render_email(
                    heading="உங்கள் உள்நுழைவு இணைப்பு",
                    preheader=f"{minutes} நிமிடங்களுக்கு மட்டுமே செல்லுபடியாகும்.",
                    content=(
                        paragraph("வணக்கம்,")
                        + paragraph("உங்கள் Etown கணக்கில் உள்நுழைய கீழே உள்ள பொத்தானைத் தட்டவும்.")
                        + button("Etown-இல் உள்நுழையவும்", link)
                        + paragraph(
                            f"இந்த இணைப்பு <strong>{minutes} நிமிடங்களுக்கு</strong> மட்டுமே "
                            "செல்லுபடியாகும், ஒரு முறை மட்டுமே பயன்படுத்த முடியும். இதை "
                            "யாருடனும் பகிர வேண்டாம்."
                        )
                        + paragraph(
                            "நீங்கள் இதைக் கோரவில்லை என்றால், இந்த மின்னஞ்சலைப் புறக்கணிக்கவும்.",
                            muted=True,
                        )
                        + paragraph("நன்றி,<br>Etown Support", muted=True)
                    ),
                    footer="இது Etown-இன் தானியங்கி மின்னஞ்சல். இதற்குப் பதிலளிக்க வேண்டாம்.",
                )
            else:
                subject = "Etown - Your Login Link"
                body = render_email(
                    heading="Your login link",
                    preheader=f"Valid for {minutes} minutes, single use.",
                    content=(
                        paragraph("Hello,")
                        + paragraph("Tap the button below to sign in to your Etown account.")
                        + button("Sign in to Etown", link)
                        + paragraph(
                            f"This link is valid for <strong>{minutes} minutes</strong> and can "
                            "only be used once. Please do not share it with anyone."
                        )
                        + paragraph(
                            "If you did not request this, you can safely ignore this email.",
                            muted=True,
                        )
                        + paragraph("Thanks,<br>Etown Support", muted=True)
                    ),
                )
        else:
            secret = static_otp if static_otp is not None else generate_otp_secret()
            ttl = settings.AUTH_OTP_TTL_SECONDS
            minutes = ttl // 60
            if is_tamil:
                subject = "Etown – உள்நுழைவு OTP"
                body = render_email(
                    heading="உங்கள் உள்நுழைவுக் குறியீடு",
                    preheader=f"{minutes} நிமிடங்களுக்கு மட்டுமே செல்லுபடியாகும்.",
                    content=(
                        paragraph("வணக்கம்,")
                        + paragraph("உங்கள் Etown கணக்கில் உள்நுழைவதற்கான ஒருமுறை கடவுச்சொல்:")
                        + otp_panel(secret, f"{minutes} நிமிடங்களுக்கு செல்லுபடியாகும்")
                        + paragraph(
                            "பாதுகாப்பு காரணங்களுக்காக, இதை யாருடனும் பகிர வேண்டாம்."
                        )
                        + paragraph(
                            "நீங்கள் இதைக் கோரவில்லை என்றால், இந்த மின்னஞ்சலைப் புறக்கணிக்கவும்.",
                            muted=True,
                        )
                        + paragraph("நன்றி,<br>Etown Support", muted=True)
                    ),
                    footer="இது Etown-இன் தானியங்கி மின்னஞ்சல். இதற்குப் பதிலளிக்க வேண்டாம்.",
                )
            else:
                subject = "Etown - Your Login Code"
                body = render_email(
                    heading="Your login code",
                    preheader=f"Valid for {minutes} minutes.",
                    content=(
                        paragraph("Hello,")
                        + paragraph("Use this one-time code to sign in to your Etown account:")
                        + otp_panel(secret, f"Valid for {minutes} minutes")
                        + paragraph(
                            "Please do not share this code with anyone for security reasons."
                        )
                        + paragraph(
                            "If you did not request this code, you can safely ignore this email.",
                            muted=True,
                        )
                        + paragraph("Thanks,<br>Etown Support", muted=True)
                    ),
                )

        challenge = await auth_utils.create_challenge(
            identifier=identity.identifier,
            channel=identity.channel,
            method=method,
            purpose=purpose,
            secret_hash=hash_secret(secret),
            expires_at=_utcnow() + datetime.timedelta(seconds=ttl),
            contactid=identity.contactid,
            userprofileid=identity.userprofileid,
            role=identity.role,
            max_attempts=settings.AUTH_OTP_MAX_ATTEMPTS,
            language_code=language_code,
            device_id=device_id,
            request_ip=request_ip,
            user_agent=user_agent,
        )

        try:
            if static_otp is not None:
                logger.info("[AUTH] static test OTP issued, SMS skipped | challenge=%s", challenge["id"])
            elif identity.channel == "sms":
                if not await send_otp_sms(
                    mobilenumber=identity.identifier, otp=secret, language_code=language_code
                ):
                    raise RuntimeError("SMS gateway reported failure")
            else:
                await send_email(to=identity.identifier, subject=subject, body=body)
        except Exception as exc:
            logger.error(
                "[AUTH] %s/%s delivery failed | challenge=%s | %s",
                identity.channel, method, challenge["id"], exc,
             exc_info=True)
            raise AuthChallengeError(
                "send_failed", "Could not send the login code. Please try again."
            ) from exc

        logger.info(
            "[AUTH] %s/%s issued | challenge=%s | contact=%s",
            identity.channel, method, challenge["id"], identity.contactid,
        )
        return ChallengeHandle(
            request_id=challenge["id"],
            method=method,
            expires_in=ttl,
            debug_secret=secret if settings.AUTH_RETURN_OTP_IN_RESPONSE else None,
        )

    async def verify_challenge(self, *, request_id: str, secret: str) -> VerifiedIdentity:
        """Verify an OTP against its challenge, counting failed attempts, and consume it on success.

        Raises:
            AuthChallengeError: not_found, consumed, expired, too_many_attempts or invalid_secret.
        """
        challenge = await auth_utils.fetch_challenge_by_id(request_id)
        if challenge is None:
            raise AuthChallengeError("not_found", "Unknown or expired login request.")
        if challenge["consumed_at"] is not None:
            raise AuthChallengeError("consumed", "This login request has already been used.")
        if challenge["expires_at"] <= _utcnow():
            raise AuthChallengeError("expired", "This code has expired. Request a new one.")
        if challenge["attempts"] >= challenge["max_attempts"]:
            raise AuthChallengeError(
                "too_many_attempts", "Too many incorrect attempts. Request a new code."
            )

        if not secrets_match(secret, challenge["secret_hash"]):
            attempts = await auth_utils.register_attempt(request_id)
            if attempts >= challenge["max_attempts"]:
                await auth_utils.consume_challenge(request_id)
                raise AuthChallengeError(
                    "too_many_attempts", "Too many incorrect attempts. Request a new code."
                )
            raise AuthChallengeError("invalid_secret", "Incorrect code.")

        if not await auth_utils.consume_challenge(request_id):
            raise AuthChallengeError("consumed", "This login request has already been used.")

        logger.info("[AUTH] otp verified | challenge=%s | contact=%s", request_id, challenge["contactid"])
        return _identity_from_challenge(challenge)

    async def consume_magic_link(self, *, link_secret: str) -> str:
        """Consume a magic-link token and return a newly minted one-time exchange code.

        Raises:
            AuthChallengeError: 'not_found' when unknown or already used, 'expired' past its TTL.
        """
        challenge = await auth_utils.fetch_live_challenge_by_secret(
            hash_secret(link_secret), "magiclink"
        )
        if challenge is None:
            raise AuthChallengeError("not_found", "This login link is invalid or has already been used.")
        if challenge["expires_at"] <= _utcnow():
            raise AuthChallengeError("expired", "This login link has expired. Request a new one.")
        if not await auth_utils.consume_challenge(challenge["id"]):
            raise AuthChallengeError("not_found", "This login link is invalid or has already been used.")

        code = generate_url_secret()
        await auth_utils.create_challenge(
            identifier=challenge["identifier"],
            method="exchange",
            secret_hash=hash_secret(code),
            expires_at=_utcnow() + datetime.timedelta(seconds=settings.AUTH_EXCHANGE_TTL_SECONDS),
            contactid=challenge["contactid"],
            userprofileid=challenge["userprofileid"],
            role=challenge["role"],
            max_attempts=1,
            language_code=challenge["language_code"],
            device_id=challenge["device_id"],
        )
        logger.info("[AUTH] magiclink consumed | challenge=%s", challenge["id"])
        return code

    async def exchange_code(self, *, code: str) -> VerifiedIdentity:
        """Redeem a one-time exchange code and return the identity it proves.

        Raises:
            AuthChallengeError: 'not_found' when unknown or already used, 'expired' past its TTL.
        """
        challenge = await auth_utils.fetch_live_challenge_by_secret(hash_secret(code), "exchange")
        if challenge is None:
            raise AuthChallengeError("not_found", "This login code is invalid or has already been used.")
        if challenge["expires_at"] <= _utcnow():
            raise AuthChallengeError("expired", "This login code has expired. Sign in again.")
        if not await auth_utils.consume_challenge(challenge["id"]):
            raise AuthChallengeError("not_found", "This login code is invalid or has already been used.")

        logger.info("[AUTH] exchange redeemed | challenge=%s | contact=%s", challenge["id"], challenge["contactid"])
        return _identity_from_challenge(challenge)
