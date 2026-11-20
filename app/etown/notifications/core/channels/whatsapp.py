"""WhatsApp Channel Adapter - routes notification template keys to the approved WhatsApp template sends.

Notification-core adapter over app/utils/whatsapp.py: checks the template gate and marketing quota, sends to every number the fanout helper returns, and stores the returned wamid on the ledger so inbound read events match the send.

Features:
    - Per-type dispatch: offer/welcome/otp templates get dedicated calls; others use a generic template send
    - Template gate: skips the send when the template_key is disabled in the template config table
    - Alternate-Number Fan-Out: sends to every number fanout.delivery_numbers() returns
    - Quota spend: marketing sends consume the provider's WhatsApp quota, released on failure
    - Shadow mode: logs what would be sent and returns True without transmitting
    - Wamid capture: stores the Meta message id on the ledger row for the dedupe_key

Classes:
    WhatsAppAdapter(): ChannelAdapter implementation that delivers one request over WhatsApp

Functions:
    _extract_wamid(): Pull the wamid out of a Meta API send response dict

Dependencies:
    - app.utils.whatsapp: offer/welcome/OTP/generic template WhatsApp transport helpers
    - app.etown.notifications.core.channels.fanout: the numbers one notification goes to
    - app.etown.plans.plans_utils (lazy import): check_and_consume/release_consumed quota
    - app.etown.notifications.whatsapp_template_config: is_template_enabled gate
    - app.etown.notifications.ledger: set_wamid to persist the returned message id
    - app.etown.notifications.core.config: is_shadow_mode() dry-run check

Updates:
    26-Jun-2026: Capture wamid from the send response and store it on the ledger.
    18-Aug-2026: Marketing sends spend the provider's whatsapp_quota_month; transactional ones do not.
    18-Sep-2026: Also sends to the account's alternate mobile number.
"""

from __future__ import annotations

from typing import Optional

from app.core.logging import logger
from app.etown.notifications.core import config as ncfg
from app.etown.notifications.core.channels import fanout
from app.etown.notifications.core.channels.base import ChannelAdapter
from app.etown.notifications.core.models import Channel, NotificationRequest
from app.etown.notifications.core.registry import TypeConfig
from app.etown.notifications.whatsapp_template_config import is_template_enabled
_BILLABLE_TEMPLATE_KEYS = frozenset({"offer_whatsapp"})
_QUOTA_OWNER_CONTEXT_KEY = "provider_userprofileid"


def _extract_wamid(raw: object) -> Optional[str]:
    """Pull the wamid out of a Meta API send response dict."""
    if not isinstance(raw, dict):
        return None
    messages = raw.get("messages")
    if isinstance(messages, list) and messages:
        first = messages[0]
        if isinstance(first, dict):
            return first.get("id")
    return None


class WhatsAppAdapter(ChannelAdapter):
    channel = Channel.WHATSAPP

    async def deliver(self, request: NotificationRequest, cfg: TypeConfig) -> bool:
        """Send the WhatsApp template to every delivery number unless disabled or shadowed; True if any send succeeded."""
        numbers = await fanout.delivery_numbers(request.recipient, cfg.template_key)
        if not numbers:
            logger.info("%s whatsapp: no mobile_number", cfg.key)
            return False

        if not await is_template_enabled(cfg.template_key):
            logger.info(
                "%s whatsapp: template_key=%r is DISABLED in "
                "notifications.etown_whatsapp_template_config — skipping send",
                cfg.key, cfg.template_key,
            )
            return False

        if ncfg.is_shadow_mode():
            logger.info(
                "[SHADOW] %s whatsapp would send | template=%s to=%s",
                cfg.key, cfg.template_key, ", ".join(numbers),
            )
            return True

        delivered = False
        for position, number in enumerate(numbers):
            sent = await self._send_one(request, cfg, number, record_wamid=position == 0)
            delivered = sent or delivered
        return delivered

    async def _send_one(
        self,
        request: NotificationRequest,
        cfg: TypeConfig,
        mobile: str,
        *,
        record_wamid: bool = True,
    ) -> bool:
        """Send this notification to one number, spending one quota unit if it is billable.

        Args:
            request: The notification being delivered, for its context and dedupe_key.
            cfg: The type's config, for its template_key.
            mobile: The single destination this call sends to.
            record_wamid: Whether to store this send's wamid in the ledger; True only for the primary number.

        Returns:
            True when Meta accepted it. Never raises.
        """
        ctx = request.context
        language = ctx.get("language", "en")

        quota_owner: Optional[str] = None
        if cfg.template_key in _BILLABLE_TEMPLATE_KEYS:
            quota_owner = ctx.get(_QUOTA_OWNER_CONTEXT_KEY)
            if not quota_owner:
                logger.warning(
                    "%s whatsapp: billable template with no %s in context — sending uncapped",
                    cfg.key, _QUOTA_OWNER_CONTEXT_KEY,
                )
            else:
                from app.etown.plans.plans_utils import PlanLimitError, check_and_consume
                try:
                    await check_and_consume(quota_owner, "whatsapp_sends")
                except PlanLimitError as exc:
                    logger.info(
                        "%s whatsapp: refused by plan for userprofileid=%s — %s",
                        cfg.key, quota_owner, exc,
                    )
                    return False

        try:
            from app.utils import whatsapp as wa
            from app.etown.notifications import ledger

            raw: object = None

            if cfg.template_key == "offer_whatsapp":
                raw = await wa.send_offer_whatsapp_notification(
                    mobile_number=mobile,
                    businessname=ctx.get("business", ""),
                    offer_title=ctx.get("offer_title", ctx.get("title", "")),
                    language=language,
                )
            elif cfg.template_key == "welcome_whatsapp":
                raw = await wa.send_welcome_whatsapp_notification(
                    mobile_number=mobile,
                    name=ctx.get("name", ""),
                    language=language,
                )
            elif cfg.template_key == "otp_whatsapp":
                raw = await wa.send_otp_whatsapp_notification(
                    mobile_number=mobile,
                    otp=ctx.get("otp", ""),
                    language=language,
                )
            else:
                template_name = ctx.get("template_name")
                if not template_name:
                    logger.info("%s whatsapp: no template_name for generic send", cfg.key)
                    return False
                raw = await wa.send_whatsapp_template_message(
                    mobile_number=mobile,
                    template_name=template_name,
                    language_code=ctx.get("language_code"),
                    body_parameters=ctx.get("body_parameters"),
                    body_named_parameters=ctx.get("body_named_parameters"),
                    header_parameters=ctx.get("header_parameters"),
                    button_parameters=ctx.get("button_parameters"),
                )

            if not raw:
                if quota_owner:
                    from app.etown.plans.plans_utils import release_consumed
                    await release_consumed(quota_owner, "whatsapp_sends")
                return False

            wamid = _extract_wamid(raw)
            if wamid and record_wamid and request.dedupe_key:
                await ledger.set_wamid(request.dedupe_key, wamid)
                logger.debug(
                    "%s whatsapp: stored wamid=%s for dedupe_key=%s",
                    cfg.key, wamid, request.dedupe_key,
                )

            return True

        except Exception as exc:
            if quota_owner:
                from app.etown.plans.plans_utils import release_consumed
                await release_consumed(quota_owner, "whatsapp_sends")
            logger.error(
                "%s whatsapp send failed | template=%s: %s",
                cfg.key, cfg.template_key, exc, exc_info=True,
            )
            return False
