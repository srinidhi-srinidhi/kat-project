"""Notifications Core Package - re-exports the dispatcher, request models, type registry and FCM transport.

Central chokepoint for notifications: re-exports the request, result and routing models, NotificationType and send_fcm_message eagerly, and resolves dispatch and NotificationDispatcher lazily to avoid an import cycle with the channel adapters.

Public API:
    dispatch(request)            -> orchestrate one NotificationRequest
    NotificationDispatcher       -> the orchestrator class
    NotificationRequest          -> the unit of work handed to the dispatcher
    NotificationResult           -> per-channel outcome of a dispatch
    Recipient                    -> addressing for a notification
    Channel / PushAudience       -> routing enums
    NotificationType             -> registered notification type keys
    send_fcm_message(...)        -> low-level shared FCM v1 transport

Classes:
    No classes defined here (dispatcher and model classes are re-exported)

Functions:
    __getattr__(): Lazily resolves ``dispatch`` / ``NotificationDispatcher`` from ``core.dispatcher``

Dependencies:
    - app.etown.notifications.core.models: request, result, recipient and routing types (re-exported)
    - app.etown.notifications.core.registry: NotificationType (re-exported)
    - app.etown.notifications.core.fcm: send_fcm_message and token-invalidation helpers (re-exported)
    - app.etown.notifications.core.dispatcher (lazy import): dispatch, NotificationDispatcher

Import note: this ``__init__`` deliberately imports only the leaf modules
(models / registry / fcm) eagerly. ``dispatch`` and ``NotificationDispatcher`` are
exposed lazily via ``__getattr__`` because the dispatcher pulls in the channel
adapters, which import the legacy sender modules — and those modules import
``core.fcm`` (hence this package) at load time. Eager-importing the dispatcher here
would create an import cycle; lazy access breaks it cleanly.

See app/etown/notifications/ARCHITECTURE.md for the full design + migration plan.

Updates:
    03-Jun-2026: Package created as the central façade over FCM, WhatsApp, SMS and in-app dispatch.
"""

from app.etown.notifications.core.models import (
    Channel,
    PushAudience,
    NotificationRequest,
    NotificationResult,
    Recipient,
)
from app.etown.notifications.core.registry import NotificationType
from app.etown.notifications.core.fcm import (
    send_fcm_message,
    mark_fcm_token_invalid,
    is_invalid_token_response,
)

__all__ = [
    "dispatch",
    "NotificationDispatcher",
    "NotificationRequest",
    "NotificationResult",
    "Recipient",
    "Channel",
    "PushAudience",
    "NotificationType",
    "send_fcm_message",
    "mark_fcm_token_invalid",
    "is_invalid_token_response",
]


def __getattr__(name: str):
    """Lazily return dispatch or NotificationDispatcher from core.dispatcher, raising AttributeError for other names."""
    if name in ("dispatch", "NotificationDispatcher"):
        from app.etown.notifications.core import dispatcher as _dispatcher
        return getattr(_dispatcher, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
