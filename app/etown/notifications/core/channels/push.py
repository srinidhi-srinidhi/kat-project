"""Push Channel Adapter - delivers individual, group and broadcast notifications over FCM.

Notification-core adapter that resolves a request's audience to device tokens, builds each payload with core.templates and sends it through core.fcm to every device of each recipient, unless the caller names one explicit device_token.

Features:
    - Lazy Recipient Helpers: devices_utils and offers_utils are imported inside methods, not at load
    - Audience Routing: INDIVIDUAL/GROUP/BROADCAST resolved to device tokens, one send path
    - Multi-Device Fan-Out: every device per recipient, deduped per FCM app instance, semaphore-bounded
    - Server-Side Token Minting: mints an FCM OAuth token when none is supplied and minting is enabled
    - Shadow-Mode Skip: logs the payload and token prefix instead of calling FCM

Classes:
    PushAdapter: ChannelAdapter that delivers a NotificationRequest over FCM push

Functions:
    No functions defined (module exposes the PushAdapter class only)

Dependencies:
    - app.etown.notifications.core.fcm: send_fcm_message, get_fcm_oauth_token
    - app.etown.notifications.core.templates: build_push_payload, push_semantics
    - app.etown.notifications.core.config: Shadow-mode / server-mint flags
    - app.etown.notifications.core.channels.base: ChannelAdapter base class
    - app.etown.notifications.core.models: Channel, NotificationRequest, PushAudience
    - app.etown.notifications.core.registry: TypeConfig for per-type push settings
    - app.etown.devices.devices_utils (lazy import): device-token resolution + fcm_app_instance
    - app.etown.offers.offers_utils (lazy import): all-device-token lookup for BROADCAST

Updates:
    28-Jul-2026: INDIVIDUAL and GROUP pushes fan out to every device of each recipient.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from __future__ import annotations

import asyncio
import json
from typing import List

from app.core.logging import logger
from app.etown.notifications.core import config as ncfg
from app.etown.notifications.core import templates
from app.etown.notifications.core.channels.base import ChannelAdapter
from app.etown.notifications.core.fcm import send_fcm_message, get_fcm_oauth_token
from app.etown.notifications.core.models import Channel, NotificationRequest, PushAudience
from app.etown.notifications.core.registry import TypeConfig

_FANOUT_CONCURRENCY = 20


class PushAdapter(ChannelAdapter):
    channel = Channel.PUSH

    async def _resolve_oauth(self, request: NotificationRequest, cfg: TypeConfig) -> None:
        """Mint a server-side FCM OAuth token onto the request when it has none and minting is enabled."""
        if request.oauth_token:
            return
        if not ncfg.is_server_mint_enabled():
            return
        try:
            request.oauth_token = await get_fcm_oauth_token()
            logger.info("%s push: using server-minted FCM OAuth token", cfg.key)
        except Exception as exc:
            logger.warning("%s push: server-side FCM token mint failed: %s", cfg.key, exc)

    async def deliver(self, request: NotificationRequest, cfg: TypeConfig) -> bool:
        """Send the push to an individual, group or broadcast audience and return whether it was delivered."""
        await self._resolve_oauth(request, cfg)
        audience = request.push_audience
        if audience == PushAudience.INDIVIDUAL:
            return await self._send_individual(request, cfg)
        if audience == PushAudience.GROUP:
            return await self._send_group(request, cfg)
        if audience == PushAudience.BROADCAST:
            return await self._send_broadcast(request, cfg)
        logger.info("PushAdapter: unknown audience %s for %s", audience, cfg.key)
        return False

    async def _send_token(self, request: NotificationRequest, cfg: TypeConfig, token: str) -> bool:
        """Send the push to one device token, only logging it in shadow mode, and return success."""
        sem = templates.push_semantics(cfg.key)
        if not token or len(token) < sem.get("min_token_len", 0):
            return False
        payload = templates.build_push_payload(cfg.template_key, request.context, device_token=token)
        if ncfg.is_shadow_mode():
            logger.info(
                "[SHADOW] %s push would send | token_prefix=%s payload=%s",
                cfg.key, token[:10], json.dumps(payload),
            )
            return True
        return await send_fcm_message(
            oauth_token=request.oauth_token,
            payload=payload,
            device_token=token,
            invalidate_on_bad_token=sem.get("invalidate_on_bad_token", True),
            timeout=sem.get("timeout", 30.0),
            label=f"{cfg.key} push",
        )

    async def _fan_out(self, request: NotificationRequest, cfg: TypeConfig, tokens: List[str]) -> bool:
        """Send to one token per FCM app instance concurrently and return True if any send succeeded."""
        from app.etown.devices.devices_utils import fcm_app_instance

        by_instance: dict[str, str] = {}
        for token in tokens:
            if token:
                by_instance.setdefault(fcm_app_instance(token), token)
        tokens = list(by_instance.values())
        if not tokens:
            return False
        semaphore = asyncio.Semaphore(_FANOUT_CONCURRENCY)

        async def _one(tok: str) -> bool:
            async with semaphore:
                try:
                    return await self._send_token(request, cfg, tok)
                except Exception as exc:
                    logger.warning("%s push fan-out failed for %s: %s", cfg.key, tok[:10], exc)
                    return False

        results = await asyncio.gather(*[_one(t) for t in tokens], return_exceptions=True)
        return any(r is True for r in results)

    async def _send_individual(self, request: NotificationRequest, cfg: TypeConfig) -> bool:
        """Send to the explicit device token, else every device of the recipient, returning True when none exist."""
        from app.etown.devices.devices_utils import get_device_tokens_by_userprofile_id

        if request.recipient.device_token:
            return await self._send_token(request, cfg, request.recipient.device_token)

        tokens: List[str] = []
        if request.recipient.userprofile_id:
            tokens = await get_device_tokens_by_userprofile_id(request.recipient.userprofile_id)
        if not tokens:
            logger.info("%s push: no device token for recipient", cfg.key)
            return True
        if len(tokens) == 1:
            return await self._send_token(request, cfg, tokens[0])
        logger.info("%s push: fanning out to %d devices of the recipient", cfg.key, len(tokens))
        return await self._fan_out(request, cfg, tokens)

    async def _send_group(self, request: NotificationRequest, cfg: TypeConfig) -> bool:
        """Resolve device tokens for every recipient userprofile and fan the push out to them."""
        from app.etown.devices.devices_utils import get_device_tokens_by_userprofile_id

        async def _resolve(uid: str) -> List[str]:
            try:
                return await get_device_tokens_by_userprofile_id(uid)
            except Exception:
                logger.warning("push: could not resolve device tokens for userprofile %s", uid, exc_info=True)
                return []

        per_user = await asyncio.gather(*[_resolve(u) for u in request.recipient.userprofile_ids])
        return await self._fan_out(request, cfg, [t for tokens in per_user for t in tokens])

    async def _send_broadcast(self, request: NotificationRequest, cfg: TypeConfig) -> bool:
        """Fan the push out to every FCM device token except the excluded or posting contact's."""
        from app.etown.offers.offers_utils import get_all_fcm_device_tokens

        tokens = await get_all_fcm_device_tokens(
            exclude_contact_id=request.recipient.exclude_contact_id
            or request.recipient.poster_contact_id
            or ""
        )
        return await self._fan_out(request, cfg, tokens)
