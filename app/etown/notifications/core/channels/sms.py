"""SMS Channel Adapter - delivers notification requests through the localised SMS helpers.

Notification channel adapter that maps a request's SMS template key to the matching app.utils.sms helper and sends it to the primary number and, except for one-time codes, the alternate number.

Features:
    - Template Dispatch: maps offer/otp/booking/added_to_mycircle SMS template keys to localised helpers
    - Alternate-Number Fan-Out: also sends to the second mobile, except one-time-code templates
    - Shadow-Mode Skip: logs the template and destination instead of sending in shadow mode
    - Graceful Skip: missing mobile_number or unmapped template_key logs and returns False

Classes:
    SmsAdapter(): ChannelAdapter that delivers a NotificationRequest over SMS

Functions:
    No functions defined (module exposes the SmsAdapter class only)

Dependencies:
    - app.utils.sms (lazy import): localised offer/OTP/booking/MyCircle SMS send helpers
    - app.etown.notifications.core.channels.fanout: the numbers one notification goes to
    - app.etown.notifications.core.config: Shadow-mode flag check
    - app.etown.notifications.core.channels.base: ChannelAdapter base class
    - app.etown.notifications.core.models: Channel enum, NotificationRequest
    - app.etown.notifications.core.registry: TypeConfig

Updates:
    03-Jun-2026: Module created as the SMS adapter wrapping app/utils/sms.py.
    13-Jul-2026: Added the added_to_mycircle_sms template mapping.
    18-Sep-2026: Sends to the account's alternate mobile number as well, one send per number.
"""

from __future__ import annotations

from app.core.logging import logger
from app.etown.notifications.core import config as ncfg
from app.etown.notifications.core.channels import fanout
from app.etown.notifications.core.channels.base import ChannelAdapter
from app.etown.notifications.core.models import Channel, NotificationRequest
from app.etown.notifications.core.registry import TypeConfig


class SmsAdapter(ChannelAdapter):
    channel = Channel.SMS

    async def deliver(self, request: NotificationRequest, cfg: TypeConfig) -> bool:
        """Send the SMS to every delivery number, only logging in shadow mode, and return True if any succeeded."""
        numbers = await fanout.delivery_numbers(request.recipient, cfg.template_key)
        if not numbers:
            logger.info("%s sms: no mobile_number", cfg.key)
            return False

        if ncfg.is_shadow_mode():
            logger.info(
                "[SHADOW] %s sms would send | template=%s to=%s",
                cfg.key, cfg.template_key, ", ".join(numbers),
            )
            return True

        delivered = False
        for number in numbers:
            delivered = await self._send_one(request, cfg, number) or delivered
        return delivered

    async def _send_one(
        self, request: NotificationRequest, cfg: TypeConfig, mobile: str
    ) -> bool:
        """Send the notification's templated SMS to one number, returning False on an unmapped template or error."""
        ctx = request.context
        language = ctx.get("language", "en")
        try:
            from app.utils import sms

            if cfg.template_key == "offer_sms":
                return bool(await sms.send_offer_sms_notification(
                    mobile_number=mobile,
                    businessname=ctx.get("business", ""),
                    offer_title=ctx.get("offer_title", ctx.get("title", "New Offer")),
                    language=language,
                ))
            if cfg.template_key == "otp_sms":
                return bool(await sms.send_otp_sms_notification(
                    mobile_number=mobile,
                    otp=ctx.get("otp", ""),
                    language=language,
                ))
            if cfg.template_key == "booking_sms":
                return bool(await sms.send_booking_sms_notification(
                    mobile_number=mobile,
                    provider_businessname=ctx.get("business", ""),
                    language=language,
                    booking_ref=ctx.get("booking_ref", ""),
                    service_datetime=ctx.get("service_datetime", ""),
                ))
            if cfg.template_key == "added_to_mycircle_sms":
                return bool(await sms.send_mycircle_provider_live_sms(
                    mobile_number=mobile,
                    businessname=ctx.get("business", ""),
                    language=language,
                ))

            logger.info("%s sms: no SMS template mapping for %s", cfg.key, cfg.template_key)
            return False
        except Exception as exc:
            logger.error(
                "%s sms send failed | template=%s: %s",
                cfg.key, cfg.template_key, exc, exc_info=True,
            )
            return False
