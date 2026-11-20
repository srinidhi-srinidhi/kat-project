"""Notification Core Models - the enums and dataclasses that flow through the notification dispatcher.

Notification-core value objects with no database or HTTP imports: the Channel and PushAudience enums, the Recipient addressing record, the NotificationRequest handed to the dispatcher and the per-channel NotificationResult it returns.

Features:
    - String-valued enums: Channel/PushAudience serialise cleanly to logs and config
    - Loosely-typed Recipient: every addressing field is optional (direct, circle/group, broadcast)
    - Per-channel result tracking: NotificationResult records delivered/skipped independently

Classes:
    Channel(): String enum of the delivery channels the platform supports (push/whatsapp/sms/inapp)
    PushAudience(): String enum of the fan-out shape a push takes (individual/group/broadcast)
    Recipient(): Addressing for a single notification; every field optional, taken as needed
    NotificationRequest(): One logical notification handed to the dispatcher (type + recipient + context)
    NotificationResult(): Per-channel delivered/skipped outcome of dispatching one request

Functions:
    No functions defined (module exposes only the dataclasses/enums above)

Dependencies:
    - dataclasses, enum: the models are plain dataclasses and string enums, no DB/HTTP imports

Updates:
    03-Jun-2026: Module created with the base Channel/Recipient/NotificationRequest/NotificationResult models.
    31-Jul-2026: Recipient extended with poster/group/broadcast fields to address every device of a person.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Channel(str, Enum):
    """Delivery channels. String-valued so they serialise cleanly to logs/config."""

    PUSH = "push"
    WHATSAPP = "whatsapp"
    SMS = "sms"
    INAPP = "inapp"


class PushAudience(str, Enum):
    INDIVIDUAL = "individual"
    GROUP = "group"
    BROADCAST = "broadcast"


@dataclass
class Recipient:
    """Addressing for a single notification.

    Every field is optional because different channels/types resolve recipients
    differently (a broadcast only needs ``exclude_contact_id``; a direct push
    needs ``userprofile_id`` or a raw ``device_token``; WhatsApp/SMS need
    ``mobile_number``). The channel adapters take what they need and ignore the rest.
    """

    userprofile_id: Optional[str] = None
    contact_id: Optional[str] = None
    device_token: Optional[str] = None
    mobile_number: Optional[str] = None

    poster_contact_id: Optional[str] = None
    poster_userprofile_id: Optional[str] = None
    userprofile_ids: List[str] = field(default_factory=list)
    exclude_contact_id: Optional[str] = None


@dataclass
class NotificationRequest:
    """One logical notification handed to the dispatcher.

    The dispatcher resolves which channels fire from the type registry + config,
    so callers describe *intent* (type + recipient + context) rather than wiring
    up channels themselves.
    """

    notification_type: str
    recipient: Recipient
    context: Dict[str, Any] = field(default_factory=dict)

    push_audience: PushAudience = PushAudience.INDIVIDUAL

    oauth_token: Optional[str] = None

    channels: Optional[List[Channel]] = None

    dedupe_key: Optional[str] = None


@dataclass
class NotificationResult:
    """Outcome of dispatching one request across its resolved channels."""

    notification_type: str
    delivered: Dict[Channel, bool] = field(default_factory=dict)
    skipped: Dict[Channel, str] = field(default_factory=dict)

    @property
    def any_delivered(self) -> bool:
        """Return True when at least one channel delivered."""
        return any(self.delivered.values())

    def mark(self, channel: Channel, ok: bool) -> None:
        """Record whether a channel delivered."""
        self.delivered[channel] = ok

    def skip(self, channel: Channel, reason: str) -> None:
        """Record why a channel was skipped."""
        self.skipped[channel] = reason
