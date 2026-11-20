"""Notification Registry - the in-code source of truth for every notification type.

Notification-core registry declaring each type once with its default channels, template key, category, queue and enabled flag; config.py layers env and database overrides on top, and a new type needs only a register() entry plus a template.

Features:
    - Single source of truth: each type's channels, template, category and queue declared once
    - Fail-safe lookup: is_registered() lets the dispatcher skip unknown types before get()
    - Bulk registration: a trailing loop registers many simple, mostly PUSH types from a tuple list
    - Queue fallback: queue_for() gives the in-code lane when no DB config row overrides it

Classes:
    NotificationType(): String constants for every known notification type key
    Category(): Business-taxonomy constants (Social/Business/Marketing/Booking/Milestone)
    Queue(): Delivery-lane constants (critical/transactional/social/marketing)
    TypeConfig(): Frozen dataclass — the static per-type declaration (channels, template, etc.)

Functions:
    register(): Add or replace a type declaration in the registry
    get(): The TypeConfig for a key, raising KeyError if it is not registered
    is_registered(): Whether a type key has been registered
    all_types(): A copy of the full registry, safe to iterate/inspect
    queue_for(): The in-code delivery lane for a type, defaulting to 'marketing'

Dependencies:
    - app.etown.notifications.core.models.Channel: the channel enum used in default_channels

Updates:
    03-Jun-2026: Registry created for the core notification types (offer/feed/booking/etc.).
    13-Jul-2026: Added the social types — provider_share, collection_share, added_to_mycircle(+SMS).
    31-Aug-2026: Added plan_promoted, sent when an operator moves a contact onto a new plan.
    02-Sep-2026: Added need_posted, pushed to providers covering the town a need was posted in.
    05-Oct-2026: Added community_like and community_comment, pushed to a community post's owner.
    06-Oct-2026: Added need_response, a push and in-app notification to a need's poster when someone responds; added event_approved and update_approved, pushed to an event's or update's poster once it is approved; added new_community_event and new_community_update, pushed to the town's providers and nearby members once it is approved.
    07-Oct-2026: community_like and community_comment also write the post owner an in-app notification.
    08-Oct-2026: Added town_image_approved, a push and in-app notification to a town image requester on approval.
    12-Oct-2026: Added town_community_nudge, a rotating marketing push nudging members to post in their Town Community.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

from app.etown.notifications.core.models import Channel


class NotificationType:
    """Known notification type keys.

    These intentionally map onto the existing FCM ``data.type`` wire values where
    one exists (Offers/Feed/DirectMessage/Booking) so the migration is a rename, not
    a behaviour change. New types (flash sale, loyalty, ...) are added here later.
    """

    OFFER = "offer"
    OFFER_SMS = "offer_sms"
    OFFER_WHATSAPP = "offer_whatsapp"
    PLAN_USAGE_WARNING = "plan_usage_warning"
    PLAN_PROMOTED = "plan_promoted"
    FEED = "feed"
    DIRECT_MESSAGE = "direct_message"
    BOOKING_REQUEST = "booking_request"
    WELCOME_WHATSAPP = "welcome_whatsapp"
    OTP_WHATSAPP = "otp_whatsapp"
    MORNING_DIGEST = "morning_digest"
    EVENING_DIGEST = "evening_digest"
    MIDDAY_DIGEST = "midday_digest"
    BOOKING_SMS = "booking_sms"
    RATING_REMINDER = "rating_reminder"
    BOOKING_REQUEST_WHATSAPP = "booking_request_whatsapp"
    BOOKING_ACCEPTED_WHATSAPP = "booking_accepted_whatsapp"
    BOOKING_COMPLETED_WHATSAPP = "booking_completed_whatsapp"
    BOOKING_ADDITIONAL_INFO = "booking_additional_info"

    FLASH_SALE = "flash_sale"
    LOYALTY_REWARD_UNLOCKED = "loyalty_reward_unlocked"
    TARGETED_OFFER_CIRCLE = "targeted_offer_circle"
    TARGETED_OFFER_GROUP = "targeted_offer_group"
    LOYALTY_PROGRESS = "loyalty_progress"
    NEW_PROVIDER_NEARBY = "new_provider_nearby"
    NEW_OFFER_NEARBY = "new_offer_nearby"
    PROVIDER_LIKED = "provider_liked"
    MYCIRCLE_ACTIVITY = "mycircle_activity"
    WEEKLY_DIGEST = "weekly_digest"
    AI_PICK = "ai_pick"
    RE_ENGAGEMENT = "re_engagement"
    PROVIDER_ONBOARD_NUDGE = "provider_onboard_nudge"
    PROVIDER_WEEKLY_VIEWS = "provider_weekly_views"
    PROVIDER_VIEWS_MILESTONE = "provider_views_milestone"
    FEED_NO_POST_NUDGE = "feed_no_post_nudge"
    FEED_COMMENT = "feed_comment"
    FEED_LIKE = "feed_like"
    NEW_RATING = "new_rating"
    TOWN_VIEW_MILESTONE = "town_view_milestone"
    NEW_TOWN_NEARBY = "new_town_nearby"
    APP_STORE_RATING_PROMPT = "app_store_rating_prompt"
    PROVIDER_SHARE = "provider_share"
    COLLECTION_SHARE = "collection_share"
    ADDED_TO_MYCIRCLE = "added_to_mycircle"
    ADDED_TO_MYCIRCLE_SMS = "added_to_mycircle_sms"
    NEED_POSTED = "need_posted"
    COMMUNITY_LIKE = "community_like"
    COMMUNITY_COMMENT = "community_comment"
    NEED_RESPONSE = "need_response"
    EVENT_APPROVED = "event_approved"
    UPDATE_APPROVED = "update_approved"
    NEW_COMMUNITY_EVENT = "new_community_event"
    NEW_COMMUNITY_UPDATE = "new_community_update"
    TOWN_IMAGE_APPROVED = "town_image_approved"
    TOWN_COMMUNITY_NUDGE = "town_community_nudge"

class Category:
    """Business-taxonomy buckets (PDF §2). Descriptive only — does not affect routing."""

    SOCIAL = "Social"
    BUSINESS = "Business"
    MARKETING = "Marketing"
    BOOKING = "Booking"
    MILESTONE = "Milestone"

class Queue:
    """Delivery lanes (PDF §3). Drives queue routing in the queue layer."""

    CRITICAL = "critical"
    TRANSACTIONAL = "transactional"
    SOCIAL = "social"
    MARKETING = "marketing"

QUEUES: Tuple[str, ...] = (Queue.CRITICAL, Queue.TRANSACTIONAL, Queue.SOCIAL, Queue.MARKETING)


@dataclass(frozen=True)
class TypeConfig:
    """Static declaration of a notification type's defaults."""

    key: str
    default_channels: Tuple[Channel, ...]
    template_key: str
    enabled_by_default: bool = True
    description: str = ""
    category: str = Category.MARKETING
    queue: str = Queue.MARKETING


_REGISTRY: Dict[str, TypeConfig] = {}


def register(config: TypeConfig) -> None:
    """Add or replace a type declaration in the registry."""
    _REGISTRY[config.key] = config


def get(type_key: str) -> TypeConfig:
    """Return the TypeConfig for a key, raising KeyError if it is not registered."""
    return _REGISTRY[type_key]


def is_registered(type_key: str) -> bool:
    """Return True when a notification type key is in the registry."""
    return type_key in _REGISTRY


def all_types() -> Dict[str, TypeConfig]:
    """Return a shallow copy of the full notification type registry."""
    return dict(_REGISTRY)


def queue_for(type_key: str) -> str:
    """Return the in-code delivery queue for a notification type, defaulting to marketing."""
    cfg = _REGISTRY.get(type_key)
    return cfg.queue if cfg else Queue.MARKETING


register(TypeConfig(
    key=NotificationType.OFFER,
    default_channels=(Channel.PUSH,),
    template_key="offer",
    description="New offer push (per recipient for circle/group, or boosted broadcast).",
    category=Category.BUSINESS,
    queue=Queue.MARKETING,
))

register(TypeConfig(
    key=NotificationType.OFFER_SMS,
    default_channels=(Channel.SMS,),
    template_key="offer_sms",
    description="Offer SMS to phonebook contacts (localised template).",
    category=Category.MARKETING,
    queue=Queue.MARKETING,
))

register(TypeConfig(
    key=NotificationType.OFFER_WHATSAPP,
    default_channels=(Channel.WHATSAPP,),
    template_key="offer_whatsapp",
    description="Offer WhatsApp broadcast via approved template.",
    category=Category.MARKETING,
    queue=Queue.MARKETING,
))

register(TypeConfig(
    key=NotificationType.PLAN_USAGE_WARNING,
    default_channels=(Channel.PUSH,),
    template_key="plan_usage_warning",
    description="Subscription allowance running low or spent, once per threshold per period.",
    category=Category.MILESTONE,
    queue=Queue.MARKETING,
))

register(TypeConfig(
    key=NotificationType.PLAN_PROMOTED,
    default_channels=(Channel.PUSH,),
    template_key="plan_promoted",
    description="Congratulates a contact when an operator moves them onto a new plan.",
    category=Category.MILESTONE,
    queue=Queue.TRANSACTIONAL,
))

register(TypeConfig(
    key=NotificationType.FEED,
    default_channels=(Channel.PUSH,),
    template_key="feed",
    description="New feed post push, fanned out per recipient across the poster's circle.",
    category=Category.SOCIAL,
    queue=Queue.SOCIAL,
))

register(TypeConfig(
    key=NotificationType.DIRECT_MESSAGE,
    default_channels=(Channel.PUSH,),
    template_key="direct_message",
    description="New direct message push to the recipient's device.",
    category=Category.SOCIAL,
    queue=Queue.CRITICAL,
))

register(TypeConfig(
    key=NotificationType.BOOKING_REQUEST,
    default_channels=(Channel.PUSH, Channel.INAPP),
    template_key="booking",
    description="Service/booking request push + stored in-app notification.",
    category=Category.BOOKING,
    queue=Queue.CRITICAL,
))

register(TypeConfig(
    key=NotificationType.BOOKING_ADDITIONAL_INFO,
    default_channels=(Channel.PUSH,),
    template_key="booking",
    description="Push to the provider when a member adds instructions to an existing booking.",
    category=Category.BOOKING,
    queue=Queue.CRITICAL,
))

register(TypeConfig(
    key=NotificationType.WELCOME_WHATSAPP,
    default_channels=(Channel.WHATSAPP,),
    template_key="welcome_whatsapp",
    description="Welcome WhatsApp message on signup.",
    category=Category.MARKETING,
    queue=Queue.TRANSACTIONAL,
))

register(TypeConfig(
    key=NotificationType.OTP_WHATSAPP,
    default_channels=(Channel.WHATSAPP,),
    template_key="otp_whatsapp",
    description="OTP delivery via WhatsApp auth template.",
    category=Category.BOOKING,
    queue=Queue.CRITICAL,
))

register(TypeConfig(
    key=NotificationType.MORNING_DIGEST,
    default_channels=(Channel.PUSH,),
    template_key="morning_digest",
    description="Daily morning push: count of new offers in the user's circle.",
    category=Category.BUSINESS,
    queue=Queue.MARKETING,
))

register(TypeConfig(
    key=NotificationType.EVENING_DIGEST,
    default_channels=(Channel.PUSH,),
    template_key="evening_digest",
    description="Daily evening push: count of new offers in the user's circle.",
    category=Category.BUSINESS,
    queue=Queue.MARKETING,
))

register(TypeConfig(
    key=NotificationType.MIDDAY_DIGEST,
    default_channels=(Channel.PUSH,),
    template_key="midday_digest",
    description="Daily 12pm push: etown feature promotion digest sent to all users.",
    category=Category.BUSINESS,
    queue=Queue.MARKETING,
))

register(TypeConfig(
    key=NotificationType.BOOKING_REQUEST_WHATSAPP,
    default_channels=(Channel.WHATSAPP,),
    template_key="booking_request_whatsapp",
    description="WhatsApp to provider on new booking request.",
    category=Category.BOOKING,
    queue=Queue.TRANSACTIONAL,
))

register(TypeConfig(
    key=NotificationType.BOOKING_ACCEPTED_WHATSAPP,
    default_channels=(Channel.WHATSAPP,),
    template_key="booking_accepted_whatsapp",
    description="WhatsApp to member when provider accepts a booking.",
    category=Category.BOOKING,
    queue=Queue.TRANSACTIONAL,
))

register(TypeConfig(
    key=NotificationType.BOOKING_COMPLETED_WHATSAPP,
    default_channels=(Channel.WHATSAPP,),
    template_key="booking_completed_whatsapp",
    description="WhatsApp to member when provider completes a booking.",
    category=Category.BOOKING,
    queue=Queue.TRANSACTIONAL,
))

register(TypeConfig(
    key=NotificationType.BOOKING_SMS,
    default_channels=(Channel.SMS,),
    template_key="booking_sms",
    description="Booking-confirmation SMS to the requestor (BOOK_SMS_* gateway).",
    category=Category.BOOKING,
    queue=Queue.CRITICAL,
))

register(TypeConfig(
    key=NotificationType.RATING_REMINDER,
    default_channels=(Channel.PUSH,),
    template_key="rating_reminder",
    description="Daily 12pm IST prompt to rate a booking completed the previous day.",
    category=Category.BOOKING,
    queue=Queue.TRANSACTIONAL,
))

register(TypeConfig(
    key=NotificationType.PROVIDER_SHARE,
    default_channels=(Channel.PUSH,),
    template_key="provider_share",
    description="Push to recipients when a user shares a provider profile (one push per recipient).",
    category=Category.SOCIAL,
    queue=Queue.SOCIAL,
))

register(TypeConfig(
    key=NotificationType.COLLECTION_SHARE,
    default_channels=(Channel.PUSH,),
    template_key="collection_share",
    description="Push to recipients when a user shares a collection (one push per recipient).",
    category=Category.SOCIAL,
    queue=Queue.SOCIAL,
))

register(TypeConfig(
    key=NotificationType.ADDED_TO_MYCIRCLE,
    default_channels=(Channel.PUSH,),
    template_key="added_to_mycircle",
    description="Push to a verified provider when a user adds them to their MyCircle.",
    category=Category.SOCIAL,
    queue=Queue.SOCIAL,
))

register(TypeConfig(
    key=NotificationType.ADDED_TO_MYCIRCLE_SMS,
    default_channels=(Channel.SMS,),
    template_key="added_to_mycircle_sms",
    description="'You're now live' SMS to an unverified provider added to a MyCircle (MYCIRCLE_SMS_* gateway).",
    category=Category.MARKETING,
    queue=Queue.MARKETING,
))

register(TypeConfig(
    key=NotificationType.TOWN_COMMUNITY_NUDGE,
    default_channels=(Channel.PUSH,),
    template_key="town_community_nudge",
    description="Rotating reminders encouraging members to create Town Community posts.",
    category=Category.MARKETING,
    queue=Queue.MARKETING,
))


for _key, _tpl, _channels, _category, _queue in [
    (NotificationType.FLASH_SALE,             "flash_sale",              (Channel.PUSH,),              Category.MARKETING, Queue.MARKETING),
    (NotificationType.LOYALTY_REWARD_UNLOCKED,"loyalty_reward_unlocked", (Channel.PUSH,),              Category.MILESTONE, Queue.TRANSACTIONAL),
    (NotificationType.TARGETED_OFFER_CIRCLE,  "offer",                   (Channel.PUSH, Channel.INAPP),Category.BUSINESS,  Queue.MARKETING),
    (NotificationType.TARGETED_OFFER_GROUP,   "offer",                   (Channel.PUSH,),              Category.BUSINESS,  Queue.MARKETING),
    (NotificationType.LOYALTY_PROGRESS,       "loyalty_progress",        (Channel.INAPP, Channel.PUSH),Category.MILESTONE, Queue.TRANSACTIONAL),
    (NotificationType.NEW_PROVIDER_NEARBY,    "new_provider_nearby",     (Channel.PUSH,),              Category.BUSINESS,  Queue.MARKETING),
    (NotificationType.NEW_OFFER_NEARBY,       "new_offer_nearby",        (Channel.PUSH,),              Category.BUSINESS,  Queue.MARKETING),
    (NotificationType.PROVIDER_LIKED,         "provider_liked",          (Channel.PUSH, Channel.INAPP),Category.MILESTONE, Queue.TRANSACTIONAL),
    (NotificationType.MYCIRCLE_ACTIVITY,      "mycircle_activity",       (Channel.PUSH, Channel.INAPP),Category.SOCIAL,    Queue.SOCIAL),
    (NotificationType.WEEKLY_DIGEST,          "weekly_digest",           (Channel.PUSH,),              Category.BUSINESS,  Queue.MARKETING),
    (NotificationType.AI_PICK,                "ai_pick",                 (Channel.PUSH, Channel.INAPP),Category.BUSINESS,  Queue.MARKETING),
    (NotificationType.RE_ENGAGEMENT,          "re_engagement",           (Channel.PUSH,),              Category.MARKETING, Queue.MARKETING),
    (NotificationType.PROVIDER_ONBOARD_NUDGE, "provider_onboard_nudge",  (Channel.PUSH,),              Category.BUSINESS,  Queue.MARKETING),
    (NotificationType.PROVIDER_WEEKLY_VIEWS,  "provider_weekly_views",   (Channel.PUSH,),              Category.MILESTONE, Queue.TRANSACTIONAL),
    (NotificationType.PROVIDER_VIEWS_MILESTONE,"provider_views_milestone",(Channel.PUSH,),             Category.MILESTONE, Queue.TRANSACTIONAL),
    (NotificationType.FEED_NO_POST_NUDGE,     "feed_no_post_nudge",      (Channel.PUSH,),              Category.SOCIAL,    Queue.SOCIAL),
    (NotificationType.FEED_COMMENT,           "feed_comment",            (Channel.PUSH,),              Category.SOCIAL,    Queue.SOCIAL),
    (NotificationType.FEED_LIKE,              "feed_like",               (Channel.PUSH,),              Category.SOCIAL,    Queue.SOCIAL),
    (NotificationType.NEW_RATING,             "new_rating",              (Channel.PUSH,),              Category.MILESTONE, Queue.TRANSACTIONAL),
    (NotificationType.TOWN_VIEW_MILESTONE,    "town_view_milestone",     (Channel.PUSH,),              Category.MILESTONE, Queue.TRANSACTIONAL),
    (NotificationType.NEW_TOWN_NEARBY,        "new_town_nearby",         (Channel.PUSH,),              Category.SOCIAL,    Queue.SOCIAL),
    (NotificationType.APP_STORE_RATING_PROMPT,"app_store_rating_prompt", (Channel.PUSH,),              Category.BOOKING,   Queue.CRITICAL),
    (NotificationType.NEED_POSTED,            "need_posted",             (Channel.PUSH, Channel.INAPP),Category.BUSINESS,  Queue.TRANSACTIONAL),
    (NotificationType.COMMUNITY_LIKE,         "community_like",          (Channel.PUSH, Channel.INAPP),Category.SOCIAL,    Queue.SOCIAL),
    (NotificationType.COMMUNITY_COMMENT,      "community_comment",       (Channel.PUSH, Channel.INAPP),Category.SOCIAL,    Queue.SOCIAL),
    (NotificationType.NEED_RESPONSE,          "need_response",           (Channel.PUSH, Channel.INAPP),Category.BUSINESS,  Queue.TRANSACTIONAL),
    (NotificationType.EVENT_APPROVED,         "event_approved",          (Channel.PUSH,),              Category.BUSINESS,  Queue.TRANSACTIONAL),
    (NotificationType.UPDATE_APPROVED,        "update_approved",         (Channel.PUSH,),              Category.BUSINESS,  Queue.TRANSACTIONAL),
    (NotificationType.NEW_COMMUNITY_EVENT,    "new_community_event",     (Channel.PUSH,),              Category.SOCIAL,    Queue.SOCIAL),
    (NotificationType.NEW_COMMUNITY_UPDATE,   "new_community_update",    (Channel.PUSH,),              Category.SOCIAL,    Queue.SOCIAL),
    (NotificationType.TOWN_IMAGE_APPROVED,    "town_image_approved",     (Channel.PUSH, Channel.INAPP),Category.BUSINESS,  Queue.TRANSACTIONAL),
]:
    register(TypeConfig(
        key=_key,
        default_channels=_channels,
        template_key=_tpl,
        enabled_by_default=True,
        category=_category,
        queue=_queue,
    ))
