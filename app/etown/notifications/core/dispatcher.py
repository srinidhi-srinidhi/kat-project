"""Notification Dispatcher - policy chokepoint that routes one notification request to its channels.

Notification core layer: dispatch() looks the type up in the registry, checks it is enabled, resolves channels from request, env and registry defaults minus kill switches, then calls each channel adapter in isolation and returns a NotificationResult; recipients, payloads and transport live in the adapters and templates.

Features:
    - Fail-safe lookup: an unregistered notification type is skipped, not raised
    - Enablement gate: registry default combined with env overrides (see config.py)
    - Channel resolution: request > env override > registry default, then kill switches
    - Per-channel isolation: a failing adapter is marked failed; other channels still run
    - Result reporting: a NotificationResult records what was delivered/skipped per channel

Classes:
    NotificationDispatcher(): Orchestrates one NotificationRequest across its resolved channels

Functions:
    dispatch(): Module-level convenience wrapper around the shared dispatcher instance

Dependencies:
    - app.etown.notifications.core.config: is_type_enabled(), resolve_channels()
    - app.etown.notifications.core.registry: is_registered(), get() for the TypeConfig
    - app.etown.notifications.core.channels.base: get_adapter() to resolve a ChannelAdapter
    - app.etown.notifications.core.models: NotificationRequest, NotificationResult
    - app.core.logging: skip and failure logging

Updates:
    03-Jun-2026: Module created as the central dispatch chokepoint for all notification types.
"""

from __future__ import annotations

from app.core.logging import logger
from app.etown.notifications.core import config as ncfg
from app.etown.notifications.core import registry
from app.etown.notifications.core.channels.base import get_adapter
from app.etown.notifications.core.models import NotificationRequest, NotificationResult


class NotificationDispatcher:
    """Orchestrates one NotificationRequest across its resolved channels."""

    async def dispatch(self, request: NotificationRequest) -> NotificationResult:
        """Deliver a request on each enabled channel of its registered type and return per-channel outcomes."""
        result =NotificationResult(notification_type=request.notification_type)

        if not registry.is_registered(request.notification_type):
            logger.warning(
                "Dispatch skipped: unknown notification type %r", request.notification_type
            )
            return result

        cfg = registry.get(request.notification_type)

        if not ncfg.is_type_enabled(request.notification_type, cfg):
            logger.info("Dispatch skipped: type %s disabled", cfg.key)
            return result

        channels = ncfg.resolve_channels(request, cfg)
        if not channels:
            logger.info("Dispatch skipped: no enabled channels for %s", cfg.key)
            return result

        for channel in channels:
            try:
                adapter = get_adapter(channel)
                ok = await adapter.deliver(request, cfg)
                result.mark(channel, ok)
            except Exception as exc:
                logger.error(
                    "Dispatch error on channel %s for %s: %s",
                    channel.value, cfg.key, exc, exc_info=True,
                )
                result.mark(channel, False)

        logger.info(
            "Dispatched %s | delivered=%s skipped=%s",
            cfg.key,
            {c.value: v for c, v in result.delivered.items()},
            {c.value: r for c, r in result.skipped.items()},
        )
        return result


_DISPATCHER = NotificationDispatcher()


async def dispatch(request: NotificationRequest) -> NotificationResult:
    """Dispatch one notification request through the module-level NotificationDispatcher."""
    return await _DISPATCHER.dispatch(request)
