"""SMS Helpers - DLT-template SMS sends through the Etown SMS gateways.

Async per-feature senders for offer, booking, invite, MyCircle-live, FOS onboarding and support booking-alert SMS, each posting a DLT-registered English or Tamil template to the shared gateway or the separate booking gateway, with credentials and templates read from settings and env.

Configuration (set in .env):
    SMS_API_URL, SMS_ACCESS_TOKEN, SMS_AUTH_SIGNATURE, SMS_* - shared offer/invite/MyCircle gateway credentials
    SMS_MESSAGE_TEMPLATE_EN / _TA, SMS_TEMPLATE_ID_EN / _TA - offer SMS body + DLT template id per language
    INVITE_SMS_TEMPLATE_ID_EN / _TA - DLT template id for the mobile invite SMS
    MYCIRCLE_SMS_TEMPLATE_ID_EN / _TA, MYCIRCLE_SMS_MESSAGE_EN / _TA - "provider now live" DLT template id + body
    FOS_ONBOARDING_SMS_TEMPLATE_ID_EN, FOS_ONBOARDING_SMS_MESSAGE_EN - FOS onboarding confirmation DLT template id + body
    BOOKING_ALERT_SMS_TEMPLATE_ID_EN, BOOKING_ALERT_SMS_MESSAGE_EN - support-team new-booking alert DLT template id + body
    BOOK_SMS_API_URL, BOOK_SMS_ACCESS_TOKEN, BOOK_SMS_AUTH_SIGNATURE, BOOK_SMS_* - separate booking SMS gateway credentials
    BOOK_SMS_TEMPLATE_ID_EN / _TA, BOOK_SMS_CONTENT_TYPE_EN / _TA - booking-received DLT template id + content type

Features:
    - Offer SMS Notifications: Sends localized offer alert messages to recipients
    - Booking SMS Notifications: booking-received alert to the member via the BOOK_SMS_* gateway
    - Invite SMS Notifications: Sends the mobile app invite SMS carrying the invite code/link
    - MyCircle Live SMS Notifications: "now live" alert to an unverified provider added to a MyCircle
    - FOS Onboarding SMS: fallback confirmation when the onboarding WhatsApp fails
    - Booking Alert SMS: notifies the support team of a new booking
    - OTP SMS Notifications: Placeholder for OTP delivery - not yet implemented, always succeeds
    - Language Support: English and Tamil template selection per feature
    - Template-based Messaging: DLT-registered template usage for regulatory compliance
    - Async Delivery: Non-blocking HTTP dispatch for high-throughput notifications

Classes:
    No classes defined (module uses function-based utilities)

Functions:
    send_offer_sms_notification(): Send localized offer notification SMS
    send_booking_sms_notification(): Send the booking-received SMS with tracking id and slot to the member
    send_invite_sms_notification(): Send the mobile app invite SMS via the shared SMS gateway
    send_otp_sms_notification(): Stub OTP SMS sender - not yet implemented, always returns True
    send_mycircle_provider_live_sms(): Send "you're now live" SMS to an unverified provider added to a MyCircle
    send_fos_business_onboarding_sms(): Send the FOS onboarding confirmation SMS when its WhatsApp could not be delivered
    send_booking_alert_sms(): Alert the customer support team by SMS that a new booking was received

Dependencies:
    - os: Environment variable access for configuration fallback
    - asyncio: Async execution support for non-blocking delivery
    - httpx: Async HTTP client for SMS gateway API calls
    - app.core.config: Application settings and template configuration
    - app.utils.http_client.TrackedHTTPClient: tracked HTTP client for the booking SMS gateway call

Updates:
    26-May-2026: Added full module docstring following project documentation standards
    07-Jul-2026: Added send_invite_sms_notification() for the mobile app invite SMS gateway
    13-Jul-2026: Added send_mycircle_provider_live_sms() for the AddtoMycircleProvider migration (MYCIRCLE_SMS_* template)
    26-Aug-2026: Added a module logger; SMS logs no longer dump the payload or print the OTP to stdout.
    23-Sep-2026: Added send_fos_business_onboarding_sms(), the SMS fallback for the fos_business_onboarding WhatsApp.
    24-Sep-2026: Added send_booking_alert_sms(), the support-team SMS sent beside the booking email.
"""

import os
import asyncio
import logging
import httpx
from app.core.config import settings

logger = logging.getLogger("app.utils.sms")

SMS_API_URL = settings.SMS_API_URL or os.getenv(
    "SMS_API_URL", "http://sms02.etownindia.com/api/sms/v1.0/send-sms"
)
TEMPLATES = {
    "en": settings.SMS_MESSAGE_TEMPLATE_EN,
    "ta": settings.SMS_MESSAGE_TEMPLATE_TA,
}

TEMPLATE_IDS = {
    "en": settings.SMS_TEMPLATE_ID_EN,
    "ta": settings.SMS_TEMPLATE_ID_TA,
}

async def send_offer_sms_notification(mobile_number: str, businessname: str, offer_title: str, language: str = "en") -> bool:
    """Send the localised offer SMS through the gateway, raising ValueError when the template is missing."""
    template = TEMPLATES.get(language, TEMPLATES.get("en"))
    template_id = TEMPLATE_IDS.get(language, TEMPLATE_IDS.get("en"))
    if not template or not template_id:
        raise ValueError(f"Missing SMS template or template ID for language: {language}")

    message = template.replace("[businessname from trigger body]", businessname).replace("[title from offer table]", offer_title)

    import logging
    import traceback
    logger = logging.getLogger("app.utils.sms")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = {
        "accessToken": settings.SMS_ACCESS_TOKEN,
        "authSignature": settings.SMS_AUTH_SIGNATURE,
        "expire": settings.SMS_EXPIRE,
        "route": settings.SMS_ROUTE,
        "smsHeader": settings.SMS_HEADER,
        "contentType": settings.SMS_CONTENT_TYPE,
        "messageContent": message,
        "entityId": settings.SMS_ENTITY_ID,
        "templateId": template_id,
        "removeDuplicateNumbers": settings.SMS_REMOVE_DUPLICATE_NUMBERS,
        "recipients": mobile_number,
    }
    try:
        logger.info(
            "[SMS] offer notification sending | template=%s recipients=%d sender=%s",
            template_id, len(str(mobile_number).split(",")), settings.SMS_HEADER,
        )
        async with httpx.AsyncClient() as client:
            response = await client.post(SMS_API_URL, data=payload, headers=headers, timeout=10)
        logger.info(
            "[SMS] offer notification response | template=%s status=%s body=%.200s",
            template_id, response.status_code, response.text,
        )
        if response.status_code == 200 or response.status_code == 201:
            return True
        if 'success' in response.text.lower():
            return True
    except Exception as exc:
        tb = traceback.format_exc()
        logger.warning(f"Offer SMS send failed: {exc}\n{tb}")
        print(f"Offer SMS send failed: {exc}\n{tb}")
    return False

BOOKING_SMS_MESSAGE_EN = (
    "Dear Customer, Greetings from Etown! {providerbusinessname} has successfully received "
    "your booking request {bookingid} for {servicedatetime}. Track it in the Etown app. We "
    "look forward to serving you!"
)

BOOKING_SMS_MESSAGE_TA = (
    "அன்புள்ள வாடிக்கையாளரே, Etown சார்பில் வணக்கம்! {providerbusinessname} உங்கள் முன்பதிவு "
    "கோரிக்கை {bookingid} ({servicedatetime}) ஐ வெற்றிகரமாகப் பெற்றுள்ளார். Etown செயலியில் "
    "நிலையைக் காணலாம். உங்களுக்குச் சேவை செய்ய ஆவலுடன் காத்திருக்கிறோம்!"
)


async def send_booking_sms_notification(
    mobile_number: str,
    provider_businessname: str,
    language: str = "en",
    booking_ref: str = "",
    service_datetime: str = "",
    message: str = "",
) -> bool:
    """Send the booking-received SMS to the member with the tracking id and slot.

    Args:
        mobile_number: Recipient mobile number.
        provider_businessname: Provider the booking was placed with.
        language: 'en' or 'ta'; selects the body and the DLT template id.
        booking_ref: The booking's tracking id, e.g. ETRQ16969.
        service_datetime: Human-readable requested slot.
        message: Optional full body that replaces the templated text.

    Returns:
        True when the gateway accepted the send.
    """
    import logging
    from app.utils.http_client import TrackedHTTPClient

    logger = logging.getLogger("app.utils.sms")
    if not mobile_number:
        return False
    if not settings.BOOK_SMS_API_URL:
        logger.warning("Booking SMS skipped - BOOK_SMS_API_URL is not configured")
        return False

    is_tamil = (language or "en").lower() == "ta"
    message_template = BOOKING_SMS_MESSAGE_TA if is_tamil else BOOKING_SMS_MESSAGE_EN
    if not booking_ref or not service_datetime:
        logger.warning(
            "Booking SMS missing a template variable | booking_ref=%r service_datetime=%r",
            booking_ref, service_datetime,
        )
    message_content = message or message_template.format(
        providerbusinessname=provider_businessname or "your service partner",
        bookingid=booking_ref or "-",
        servicedatetime=service_datetime or "the requested time",
    )
    template_id = settings.BOOK_SMS_TEMPLATE_ID_TA if is_tamil else settings.BOOK_SMS_TEMPLATE_ID_EN
    content_type = settings.BOOK_SMS_CONTENT_TYPE_TA if is_tamil else settings.BOOK_SMS_CONTENT_TYPE_EN

    params = {
        "accessToken": settings.BOOK_SMS_ACCESS_TOKEN,
        "expire": settings.BOOK_SMS_EXPIRE,
        "authSignature": settings.BOOK_SMS_AUTH_SIGNATURE,
        "route": settings.BOOK_SMS_ROUTE,
        "smsHeader": settings.BOOK_SMS_HEADER,
        "messageContent": message_content,
        "contentType": content_type,
        "entityId": settings.BOOK_SMS_ENTITY_ID,
        "templateId": template_id,
        "removeDuplicateNumbers": settings.BOOK_SMS_REMOVE_DUPLICATE_NUMBERS,
        "recipients": mobile_number,
    }
    try:
        async with TrackedHTTPClient(timeout=10.0) as client:
            response = await client.post(settings.BOOK_SMS_API_URL, params=params)
        status = getattr(response, "status_code", None)
        response_text = str(getattr(response, "text", "") or "")
        accepted = status in (200, 201) or "success" in response_text.lower()
        logger.info(
            "Booking SMS gateway response | status=%s | accepted=%s | response=%.200s",
            status,
            accepted,
            response_text,
        )
        return accepted
    except Exception as e:
        logger.error("Booking SMS failed | error=%s", e, exc_info=True)
        return False

async def send_otp_sms_notification(mobile_number: str, otp: str, language: str = "en") -> bool:
    """Log a warning and return True without sending, since OTP SMS is a stub."""
    logger.warning(
        "[SMS] send_otp_sms_notification is a stub: no OTP was sent | mobile=%s lang=%s",
        mobile_number, language,
    )
    await asyncio.sleep(0)
    return True


_INVITE_MSG_EN = (
    "Etown - All services are at one place! makes lives easier and more convenient with trust "
    "recommendations from friends and family. Avoid queues and waiting times, ready to experience "
    "a smarter, more convenient way to manage your needs. click here to get your invitation with "
    "more -- https://www.etown.com/openapp?code={invite_code}"
)

_INVITE_MSG_TA = (
    "Etown - அனைத்து சேவைகளும் ஒரே இடத்தில்! நண்பர்கள் மற்றும் குடும்பத்தினரின் நம்பகமான "
    "பரிந்துரைகளுடன் வாழ்க்கையை எளிதாகவும் வசதியாகவும் ஆக்குகிறது. வரிசைகள் மற்றும் "
    "காத்திருப்பு நேரங்களைத் தவிர்க்கவும். உங்கள் தேவைகளை நிர்வகிக்க ஒரு சிறந்த, "
    "வசதியான வழியை அனுபவிக்கத் தயாரா? உங்கள் அழைப்பைப் பெறவும் மேலும் அறியவும் இங்கே "
    "கிளிக் செய்யவும். -- https://www.etown.com/openapp?code={invite_code}"
)


async def send_invite_sms_notification(
    mobile_number: str,
    invite_code: str,
    language: str = "EN",
) -> bool:
    """Send the English or Tamil invite SMS carrying an openapp invite link, returning True on success."""
    import logging
    logger = logging.getLogger("app.utils.sms")
    lang = language.strip().upper()
    is_tamil = lang == "TA"

    content_type = "unicode" if is_tamil else "text"
    template_id = settings.INVITE_SMS_TEMPLATE_ID_TA if is_tamil else settings.INVITE_SMS_TEMPLATE_ID_EN
    message = (_INVITE_MSG_TA if is_tamil else _INVITE_MSG_EN).format(invite_code=invite_code)
    params = {
        "accessToken": settings.SMS_ACCESS_TOKEN,
        "expire": settings.SMS_EXPIRE,
        "authSignature": settings.SMS_AUTH_SIGNATURE,
        "route": "transactional",
        "smsHeader": settings.SMS_HEADER,
        "messageContent": message,
        "contentType": content_type,
        "entityId": settings.SMS_ENTITY_ID,
        "templateId": template_id,
        "removeDuplicateNumbers": settings.SMS_REMOVE_DUPLICATE_NUMBERS,
        "recipients": mobile_number,
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(settings.SMS_API_URL, params=params, timeout=10)
        status = response.status_code
        if status in (200, 201) or "success" in response.text.lower():
            return True
        logger.warning("Invite SMS failed | status=%s | response=%s", status, response.text)
    except Exception as exc:
        logger.warning("Invite SMS send failed | error=%s", exc, exc_info=True)
    return False


async def send_mycircle_provider_live_sms(
    mobile_number: str,
    businessname: str,
    language: str = "EN",
) -> bool:
    """Send the MyCircle business-is-live SMS to a provider, returning False when the template is unset or sending fails."""
    import logging
    logger = logging.getLogger("app.utils.sms")
    if not mobile_number:
        return False
    lang = (language or "EN").strip().upper()
    is_tamil = lang == "TA"

    content_type = "unicode" if is_tamil else "text"
    template_id = settings.MYCIRCLE_SMS_TEMPLATE_ID_TA if is_tamil else settings.MYCIRCLE_SMS_TEMPLATE_ID_EN
    template = settings.MYCIRCLE_SMS_MESSAGE_TA if is_tamil else settings.MYCIRCLE_SMS_MESSAGE_EN
    if not template or not template_id:
        logger.warning("MyCircle live SMS skipped | missing MYCIRCLE_SMS template/id for lang=%s", lang)
        return False
    message = template.replace("[businessname from trigger body]", businessname or "")
    params = {
        "accessToken": settings.SMS_ACCESS_TOKEN,
        "expire": settings.SMS_EXPIRE,
        "authSignature": settings.SMS_AUTH_SIGNATURE,
        "route": "transactional",
        "smsHeader": settings.SMS_HEADER,
        "messageContent": message,
        "contentType": content_type,
        "entityId": settings.SMS_ENTITY_ID,
        "templateId": template_id,
        "removeDuplicateNumbers": "1",
        "recipients": mobile_number,
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(settings.SMS_API_URL, params=params, timeout=10)
        status = response.status_code
        if status in (200, 201) or "success" in response.text.lower():
            return True
        logger.warning("MyCircle live SMS failed | status=%s | response=%s", status, response.text)
    except Exception as exc:
        logger.warning("MyCircle live SMS send failed | error=%s", exc, exc_info=True)
    return False


async def send_fos_business_onboarding_sms(mobile_number: str, businessname: str) -> bool:
    """Send the FOS onboarding confirmation SMS to a provider, returning False when the template is unset or sending fails."""
    if not mobile_number:
        return False
    template_id = settings.FOS_ONBOARDING_SMS_TEMPLATE_ID_EN
    template = settings.FOS_ONBOARDING_SMS_MESSAGE_EN
    if not template or not template_id:
        logger.warning("FOS onboarding SMS skipped | missing FOS_ONBOARDING_SMS template/id")
        return False
    params = {
        "accessToken": settings.SMS_ACCESS_TOKEN,
        "expire": settings.SMS_EXPIRE,
        "authSignature": settings.SMS_AUTH_SIGNATURE,
        "route": "transactional",
        "smsHeader": settings.SMS_HEADER,
        "messageContent": template.replace("{businessname}", businessname or ""),
        "contentType": "text",
        "entityId": settings.SMS_ENTITY_ID,
        "templateId": template_id,
        "removeDuplicateNumbers": "1",
        "recipients": mobile_number,
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(settings.SMS_API_URL, params=params, timeout=10)
        status = response.status_code
        if status in (200, 201) or "success" in response.text.lower():
            logger.info("FOS onboarding SMS sent | mobile=%s", mobile_number)
            return True
        logger.warning("FOS onboarding SMS failed | status=%s | response=%s", status, response.text)
    except Exception as exc:
        logger.warning("FOS onboarding SMS send failed | error=%s", exc, exc_info=True)
    return False


async def send_booking_alert_sms(recipients: list, booking_ref: str, businessname: str) -> bool:
    """Send one new-booking alert SMS to all support recipients, returning True when the gateway accepts it."""
    numbers = ",".join(str(number).strip() for number in recipients or [] if str(number).strip())
    if not numbers:
        return False
    template_id = settings.BOOKING_ALERT_SMS_TEMPLATE_ID_EN
    template = settings.BOOKING_ALERT_SMS_MESSAGE_EN
    if not template or not template_id:
        logger.warning("Booking alert SMS skipped | missing BOOKING_ALERT_SMS template/id")
        return False
    message = template.replace("{bookingid}", booking_ref or "-").replace(
        "{businessname}", businessname or "a provider"
    )
    params = {
        "accessToken": settings.SMS_ACCESS_TOKEN,
        "expire": settings.SMS_EXPIRE,
        "authSignature": settings.SMS_AUTH_SIGNATURE,
        "route": "transactional",
        "smsHeader": settings.SMS_HEADER,
        "messageContent": message,
        "contentType": "text",
        "entityId": settings.SMS_ENTITY_ID,
        "templateId": template_id,
        "removeDuplicateNumbers": "1",
        "recipients": numbers,
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(settings.SMS_API_URL, params=params, timeout=10)
        status = response.status_code
        if status in (200, 201) or "success" in response.text.lower():
            logger.info("Booking alert SMS sent | booking=%s | recipients=%s", booking_ref, numbers)
            return True
        logger.warning("Booking alert SMS failed | status=%s | response=%s", status, response.text)
    except Exception as exc:
        logger.warning("Booking alert SMS send failed | error=%s", exc, exc_info=True)
    return False
