"""OTP Verification Utilities - OTP generation, SMS and email delivery, and phone ownership lookup.

Domain layer behind /api/otp and the auth provider: generates 6-digit OTPs, sends them by SMS gateway or branded Microsoft Graph email in English or Tamil, and resolves the verified-first owner of a mobile number.

Features:
    - OTP Generation: Generate a 6-digit random OTP
    - OTP Delivery (SMS): Send OTP via SMS in English or Tamil
    - OTP Delivery (Email): Send OTP via Microsoft Graph email in English or Tamil
    - Branded Emails: OTP emails render through the shared branded email shell
    - Phone Ownership Lookup: Verified-first lookup of the contact owning a mobile number

Functions:
    generate_otp(): Generate a 6-digit random OTP string
    static_otp_for(): Return the fixed test OTP configured for a mobile number, or None
    send_otp_sms(): Send OTP via external SMS gateway
    send_otp_email(): Send OTP via Microsoft Graph email
    get_phone_owner(): Return the contact (id + verified flag) that owns a phone number, or None

Dependencies:
    - httpx: Async HTTP client for SMS gateway
    - app.core.config: Application settings
    - app.db.client: Database pool and retry decorator
    - app.etown.user.user_utils: fetch_phone_owner(), the verified-first phone lookup this module delegates to
    - app.services.email_service: Microsoft Graph email sender
    - app.services.email_templates: Shared branded email shell and OTP panel

Updates:
    27-May-2026: Initial creation with OTP generation and SMS delivery
    22-Jun-2026: Added get_contact_id_for_phone() for pre-OTP phone ownership validation
    13-Jul-2026: Added send_otp_email() for email OTP delivery (PowerApps UserEmailValidation migration)
    14-Jul-2026: get_contact_id_for_phone() renamed get_phone_owner(), returning contactid and verified.
    29-Jul-2026: get_phone_owner() delegates to fetch_phone_owner() for verified-first, deterministic resolution.
    24-Aug-2026: OTP verification emails now render through the shared branded shell.
    26-Aug-2026: Logged previously-silent exception handlers with tracebacks; rewrote vague log messages.
    26-Oct-2026: Added static_otp_for() for test numbers listed in AUTH_STATIC_OTP_NUMBERS.
"""

import random
import re
import logging
from typing import Optional
import httpx

from app.core.config import settings
from app.db.client import Database, with_retry
from app.etown.user.user_utils import fetch_phone_owner
from app.services.email_service import send_email
from app.services.email_templates import otp_panel, paragraph, render_email

logger = logging.getLogger(__name__)


@with_retry()
async def get_phone_owner(phone_number: str) -> Optional[dict]:
    """Return the contactid and verified flag of the contact owning a phone number, or None."""
    return await fetch_phone_owner(phone_number)


def generate_otp() -> str:
    """Return a random 6-digit OTP from the non-cryptographic random module."""
    return str(random.randint(100000, 999999))


def static_otp_for(mobilenumber: str) -> Optional[str]:
    """Return the fixed OTP configured in AUTH_STATIC_OTP_NUMBERS for a test mobile number, or None."""
    return settings.AUTH_STATIC_OTP_NUMBERS.get(re.sub(r"\D", "", mobilenumber or "")[-10:])


async def send_otp_sms(mobilenumber: str, otp: str, language_code: str = "en") -> bool:
    """Send an English or Tamil OTP SMS through the OTP gateway and return whether it was accepted."""
    if language_code.upper() == "EN":
        params = {
            "accessToken": settings.OTP_ACCESS_TOKEN,
            "expire": settings.OTP_EXPIRE,
            "authSignature": settings.OTP_AUTH_SIGNATURE,
            "route": settings.OTP_ROUTE,
            "smsHeader": settings.OTP_HEADER,
            "messageContent": (
                f"Welcome to Etown Services! Your OTP for verification is {otp}. "
                "Do not share this code with anyone. "
                "This code is valid for 10 minutes. Welcome to Our Community!"
            ),
            "contentType": settings.OTP_CONTENT_TYPE_EN,
            "entityId": settings.OTP_ENTITY_ID,
            "templateId": settings.OTP_TEMPLATE_ID_EN,
            "removeDuplicateNumbers": settings.OTP_REMOVE_DUPLICATE_NUMBERS,
            "recipients": mobilenumber,
        }
    elif language_code.upper() == "TA":
        params = {
            "accessToken": settings.OTP_ACCESS_TOKEN,
            "expire": settings.OTP_EXPIRE,
            "authSignature": settings.OTP_AUTH_SIGNATURE,
            "route": settings.OTP_ROUTE,
            "smsHeader": settings.OTP_HEADER,
            "messageContent": (
                f"Etown சேவைகளுக்கு வருக! சரிபார்ப்புக்கான உங்கள் OTP {otp}. "
                "இந்தக் குறியீட்டை யாருடனும் பகிர வேண்டாம். "
                "இந்தக் குறியீடு 10 நிமிடங்களுக்கு செல்லுபடியாகும். "
                "எங்கள் சமூகத்திற்கு வருக!"
            ),
            "contentType": settings.OTP_CONTENT_TYPE_TA,
            "entityId": settings.OTP_ENTITY_ID,
            "templateId": settings.OTP_TEMPLATE_ID_TA,
            "removeDuplicateNumbers": settings.OTP_REMOVE_DUPLICATE_NUMBERS,
            "recipients": mobilenumber,
        }
    else:
        return False

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(settings.OTP_API_URL, params=params, timeout=10)
        logger.info(
            "[OTP] SMS dispatch response | mobile=%s status=%s body=%.200s",
            mobilenumber, response.status_code, response.text,
        )
        if response.status_code in (200, 201) or "success" in response.text.lower():
            return True
    except Exception as e:
        logger.error(f"[OTP] SMS send failed for {mobilenumber}: {e}", exc_info=True)
    return False


async def send_otp_email(emailid: str, otp: str, language_code: str = "en") -> bool:
    """Email an OTP in English or Tamil, returning False for other languages or on send failure."""
    if language_code.upper() == "EN":
        subject = "Etown - OTP Verification Mail"
        body = render_email(
            heading="Verify your email",
            preheader="Your Etown verification code, valid for 5 minutes.",
            content=(
                paragraph("Hello,")
                + paragraph("Your one-time password (OTP) for verification is:")
                + otp_panel(otp, "Valid for 5 minutes")
                + paragraph(
                    "Please do not share this code with anyone for security reasons."
                )
                + paragraph(
                    "If you did not request this OTP, you can safely ignore this email.",
                    muted=True,
                )
                + paragraph("Thanks,<br>Etown Support", muted=True)
            ),
        )
    elif language_code.upper() == "TA":
        subject = "Etown – OTP உறுதிப்படுத்தல்"
        body = render_email(
            heading="உங்கள் மின்னஞ்சலை உறுதிப்படுத்தவும்",
            preheader="உங்கள் Etown குறியீடு 5 நிமிடங்களுக்கு செல்லுபடியாகும்.",
            content=(
                paragraph("வணக்கம்,")
                + paragraph(
                    "Etown கணக்கை உறுதிப்படுத்த, உங்கள் ஒருமுறை கடவுச்சொல் (OTP):"
                )
                + otp_panel(otp, "5 நிமிடங்களுக்கு செல்லுபடியாகும்")
                + paragraph("பாதுகாப்பு காரணங்களுக்காக, இதை யாருடனும் பகிர வேண்டாம்.")
                + paragraph(
                    "நீங்கள் இதைக் கோரவில்லை என்றால், இந்த மின்னஞ்சலைப் புறக்கணிக்கவும்.",
                    muted=True,
                )
                + paragraph("நன்றி,<br>Etown Support", muted=True)
            ),
            footer="இது Etown-இன் தானியங்கி மின்னஞ்சல். இதற்குப் பதிலளிக்க வேண்டாம்.",
        )
    else:
        return False

    try:
        await send_email(to=emailid, subject=subject, body=body)
        return True
    except Exception as e:
        logger.error(f"[OTP] Email send failed for {emailid}: {e}", exc_info=True)
        return False
