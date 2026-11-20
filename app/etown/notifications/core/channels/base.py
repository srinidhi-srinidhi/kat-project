"""Notification Channel Base - adapter interface and lazy per-channel resolver.

Notification core module defining the ChannelAdapter contract the dispatcher calls once per resolved channel, and get_adapter(), which imports and caches one adapter instance per channel on first use so package import stays free of sender modules.

Features:
    - Uniform Adapter Interface: one abstract ``deliver(request, cfg) -> bool`` for every channel
    - Singleton Resolution: ``get_adapter`` caches one lazily imported instance per channel
    - Never Raises: adapters return True on success and False on skip or failure, so one channel cannot derail a fan-out

Classes:
    ChannelAdapter: Abstract base every concrete channel adapter implements

Functions:
    get_adapter(): Return the singleton adapter for a channel, importing it lazily

Dependencies:
    - app.etown.notifications.core.models: Channel enum, NotificationRequest
    - app.etown.notifications.core.registry: TypeConfig

Updates:
    03-Jun-2026: Module created as the shared adapter interface for the notification core.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict

from app.etown.notifications.core.models import Channel, NotificationRequest
from app.etown.notifications.core.registry import TypeConfig


class ChannelAdapter(ABC):
    """One delivery channel."""

    channel: Channel

    @abstractmethod
    async def deliver(self, request: NotificationRequest, cfg: TypeConfig) -> bool:
        """Deliver the request over this channel and return True on success."""
        raise NotImplementedError


_INSTANCES: Dict[Channel, ChannelAdapter] = {}


def get_adapter(channel: Channel) -> ChannelAdapter:
    """Return the cached adapter for a channel, importing it lazily; raise ValueError for an unknown channel."""
    if channel in _INSTANCES:
        return _INSTANCES[channel]

    if channel == Channel.PUSH:
        from app.etown.notifications.core.channels.push import PushAdapter
        adapter: ChannelAdapter = PushAdapter()
    elif channel == Channel.WHATSAPP:
        from app.etown.notifications.core.channels.whatsapp import WhatsAppAdapter
        adapter = WhatsAppAdapter()
    elif channel == Channel.SMS:
        from app.etown.notifications.core.channels.sms import SmsAdapter
        adapter = SmsAdapter()
    elif channel == Channel.INAPP:
        from app.etown.notifications.core.channels.inapp import InAppAdapter
        adapter = InAppAdapter()
    else:
        raise ValueError(f"No adapter registered for channel {channel!r}")

    _INSTANCES[channel] = adapter
    return adapter
