"""Notification Templates - FCM push payload builders and per-type push semantics.

Notification core module: build_push_payload turns a template_key and the request context dict into an FCM v1 message body for every registered push type, and push_semantics returns each type's timeout and bad-token handling.

Features:
    - One big if/elif dispatch keyed by template_key, covering every registered push type
    - LLM-copy override: a ctx "title"/"body" replaces the branch's static default line
    - Byte-for-byte FCM compatibility: notification/data keys match the pre-dispatcher senders
    - Per-type push semantics: PUSH_SEMANTICS / push_semantics() set timeout and bad-token invalidation

Classes:
    No classes defined (module exposes build_push_payload, PUSH_SEMANTICS, and push_semantics)

Functions:
    build_push_payload(): Build the FCM v1 request body for a push notification from a template_key + ctx
    push_semantics(): Per-type timeout / invalidate_on_bad_token / min_token_len, defaulting sensibly

Dependencies:
    - app.etown.notifications.core.registry.NotificationType: keys used in PUSH_SEMANTICS
    - app.core.logging: payload-build failure logging

Updates:
    03-Jun-2026: Module created with the initial push payload builders (offer/feed/booking/etc.).
    26-Jun-2026: Added the midday_digest template branch.
    13-Jul-2026: Added the provider_share and collection_share template branches.
    21-Aug-2026: The booking branch honours a ctx title and image, for booking-message pushes.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    31-Aug-2026: Added the plan_promoted template branch.
    02-Sep-2026: Added the need_posted template branch and its push semantics.
    05-Oct-2026: Added the community_like and community_comment template branches.
    06-Oct-2026: Added the event_approved and update_approved template branch, the new_community_event and new_community_update branch, the need_response branch, and their push semantics; community push data carries the recipient's role.
    08-Oct-2026: Added the town_image_approved template branch and its push semantics; plan_promoted takes a ctx route.
    12-Oct-2026: Added TOWN_COMMUNITY_TEMPLATES, the town_community_nudge template branch and its push semantics.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.etown.notifications.core.registry import NotificationType
from app.core.logging import logger

TOWN_COMMUNITY_TEMPLATES = {
    "need": {
        1: {
            "title": "Need a hand?",
            "body": (
                "Got something you need help with? "
                "Your town community is here for you!"
            ),
        },
        2: {
            "title": "Two heads are better!",
            "body": (
                "Need a service or a little local help? "
                "Ask your community on etown."
            ),
        },
        3: {
            "title": "Your town might have the answer!",
            "body": (
                "Looking for a helping hand? "
                "Post your need and connect with your community."
            ),
        },
    },
    "update": {
        1: {
            "title": "Got news to share?",
            "body": (
                "Something useful happening in your town? "
                "Keep your community in the loop!"
            ),
        },
        2: {
            "title": "Your little update could help!",
            "body": (
                "Share local news, important information, "
                "or something your neighbours should know."
            ),
        },
        3: {
            "title": "Spread the word!",
            "body": (
                "Have something worth sharing? "
                "Let your town community know on etown."
            ),
        },
    },
    "event": {
        1: {
            "title": "Something happening nearby?",
            "body": (
                "Hosting an event? Share it on etown "
                "and get your community involved!"
            ),
        },
        2: {
            "title": "Make it a town thing!",
            "body": (
                "From local gatherings to special occasions, "
                "there's always something to share."
            ),
        },
        3: {
            "title": "Got plans for the community?",
            "body": (
                "Organising something exciting? "
                "Put it on the town calendar with etown."
            ),
        },
    },
}

def build_push_payload(
    template_key: str,
    ctx: Dict[str, Any],
    *,
    device_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the FCM v1 message body for a template key and context, targeting the device token when given."""
    message: Dict[str, Any] = {}

    if template_key == "offer":
        notification: Dict[str, Any] = {
            "title": ctx.get("title", ""),
            "body": ctx.get("body", ""),
        }
        if ctx.get("image"):
            notification["image"] = ctx["image"]
        message = {
            "notification": notification,
            "data": {
                "type": "Offers",
                "offerid": ctx.get("offer_id", ""),
                "route": "/fullscreenoffers",
            },
        }

    elif template_key == "feed":
        feed_notification: Dict[str, Any] = {"title": "etown", "body": ctx.get("body", "")}
        if ctx.get("image"):
            feed_notification["image"] = ctx["image"]
        message = {
            "notification": feed_notification,
            "data": {
                "type": "Feed",
                "feedid": ctx.get("feed_id", ""),
                "route": "/fullscreenpost",
                "userprofile": ctx.get("userprofile_id", "") or "",
                "type": "Feed",
                "isTextPost": "true" if ctx.get("is_text_post") else "false",
            },
        }

    elif template_key == "direct_message":
        message = {
            "notification": {
                "title": ctx.get("sender_name", "New Message"),
                "body": ctx.get("preview", ""),
            },
            "data": {
                "type": "DirectMessage",
                "conversation_id": ctx.get("conversation_id", ""),
                "sender_id": ctx.get("sender_id", ""),
                "recipient_id": ctx.get("recipient_id", ""),
                "opponent_type": ctx.get("rolename", ""),
                "sender_image": ctx.get("sender_image", ""),
            },
        }

    elif template_key == "booking":
        data: Dict[str, Any] = {
            "type": "Booking",
            "route": "/booking",
            "userprofile": ctx.get("userprofile_id", "") or "",
            "role": ctx.get("role", "") or "",
        }
        for _k in ("provider_userprofile", "booking_state", "booking_id",
                   "business_name", "serviceimage", "provider_createdon"):
            if _k in ctx:
                data[_k] = ctx.get(_k, "") or ""
        booking_notification: Dict[str, Any] = {
            "title": ctx.get("title") or "etown",
            "body": ctx.get("body", "") or "",
        }
        if ctx.get("image"):
            booking_notification["image"] = ctx["image"]
        message = {
            "notification": booking_notification,
            "data": data,
        }

    elif template_key == "morning_digest":
        title = ctx.get("title")
        body = ctx.get("body")
        if not title or not body:
            n = ctx.get("n", 0)
            offers_word = "offer" if str(n) == "1" else "offers"
            title = "Good morning!"
            body = f"{n} new {offers_word} from your circle today. Tap to explore."
        message = {
            "notification": {
                "title": title,
                "body": body,
            },
            "data": {
                "type": "Offers",
                "offerid": ctx.get("offerid", ""),
                "route": "/fullscreenoffers",
            },
        }

    elif template_key == "evening_digest":
        title = ctx.get("title")
        body = ctx.get("body")
        if not title or not body:
            title = "Good evening!"
            body = "Discover what else etown can do. Tap to explore the app."
        message = {
            "notification": {
                "title": title,
                "body": body,
            },
            "data": {
                "type": "General",
                "route": "/",
            },
        }

    elif template_key == "midday_digest":
        title = ctx.get("title")
        body = ctx.get("body")
        if not title or not body:
            title = "It's a great time to explore!"
            body = "See what etown has for you today. Tap to discover."
        message = {
            "notification": {
                "title": title,
                "body": body,
            },
            "data": {
                "type": "General",
                "route": "/",
            },
        }

    elif template_key == "rating_reminder":
        title = ctx.get("title")
        body = ctx.get("body")
        if not title or not body:
            title = "How was your experience?"
            body = "Please rate your recent booking."
        message = {
            "notification": {
                "title": title,
                "body": body,
            },
            "data": {
                "type": "Rating",
                "route": "/ratingscreen",
                "booking_id": ctx.get("booking_id", "") or "",
                "business_name": ctx.get("provider_name", "") or "",
                "userprofile": ctx.get("userprofileid", "") or "",
                "serviceimage": ctx.get("serviceimage", "") or "",
                "provider_createdon": ctx.get("provider_createdon", "") or "",
            },
        }

    elif template_key == "weekly_digest":
        city = ctx.get("city") or ctx.get("town", "your area")
        new_providers = int(ctx.get("new_providers", 0) or 0)
        new_offers = int(ctx.get("new_offers", 0) or 0)
        parts = []
        if new_providers:
            parts.append(f"{new_providers} new provider{'s' if new_providers != 1 else ''}")
        if new_offers:
            parts.append(f"{new_offers} circle offer{'s' if new_offers != 1 else ''}")
        summary = " and ".join(parts) if parts else "updates"
        message = {
            "notification": {
                "title": "Your weekly etown digest",
                "body": f"This week in {city}: {summary}. Tap to explore.",
            },
            "data": {"type": "General", "route": "/"},
        }

    elif template_key == "mycircle_activity":
        n = ctx.get("n", 0)
        town = ctx.get("town", "your town")
        message = {
            "notification": {
                "title": "Your circle is active",
                "body": f"{n} new post{'s' if str(n) != '1' else ''} from your circle in {town}. See what\u2019s happening.",
            },
            "data": {"type": "Feed", "route": "/mycircle"},
        }

    elif template_key == "new_provider_nearby":
        business = ctx.get("business", "A new provider")
        town = ctx.get("town", "your area")
        message = {
            "notification": {
                "title": "New in your area!",
                "body": f"{business} just joined etown near {town}. Check them out.",
            },
            "data": {
                "type": "providerview",
                "route": "/providerscreen",
                "contactid": ctx.get("contactid", ""),
                "userprofileid": ctx.get("userprofileid", ""),
            },
        }

    elif template_key == "new_offer_nearby":
        business = ctx.get("business", "A provider nearby")
        town = ctx.get("town", "your area")
        message = {
            "notification": {
                "title": "New offer near you",
                "body": f"{business} shared a new offer in {town}.",
            },
            "data": {
                "type": "Offers",
                "offerid": ctx.get("offer_id", ""),
                "route": "/fullscreenoffers",
            },
        }

    elif template_key == "provider_liked":
        business = ctx.get("business", "A provider")
        n = ctx.get("n", "")
        title = ctx.get("title")
        body = ctx.get("body")
        if not title or not body:
            title = "People love this!"
            body = (f"{business} has been favorited by {n} people in your community."
                    if n else f"{business} is popular in your community.")
        message = {
            "notification": {"title": title, "body": body},
            "data": {
                "type": "providerview",
                "route": "/providerscreen",
                "contactid": ctx.get("contactid", ""),
                "userprofileid": ctx.get("userprofileid", ""),
            },
        }

    elif template_key == "flash_sale":
        business = ctx.get("business", "A business")
        x = ctx.get("x", "")
        discount = f" \u2014 {x}% off!" if x else ""
        message = {
            "notification": {
                "title": "Flash sale live now",
                "body": f"{business}{discount} Ends in 3 hours. Tap to grab it.",
            },
            "data": {
                "type": "Offers",
                "offerid": ctx.get("offer_id", ""),
                "route": "/fullscreenoffers",
            },
        }

    elif template_key == "loyalty_progress":
        business = ctx.get("business", "the business")
        n = ctx.get("n", "")
        message = {
            "notification": {
                "title": "Reward almost ready!",
                "body": f"{n} more visit{'s' if str(n) != '1' else ''} to {business} and your free reward is yours." if n else f"Keep visiting {business} to earn your reward.",
            },
            "data": {"type": "General", "route": "/loyalty"},
        }

    elif template_key == "loyalty_reward_unlocked":
        business = ctx.get("business", "the business")
        message = {
            "notification": {
                "title": "Your reward is waiting!",
                "body": f"You have earned your reward at {business}. Visit to claim it.",
            },
            "data": {"type": "General", "route": "/loyalty"},
        }

    elif template_key == "ai_pick":
        business = ctx.get("business", "A provider")
        message = {
            "notification": {
                "title": "Picked for you",
                "body": f"{business} is offering exactly what you are looking for today.",
            },
            "data": {"type": "General", "route": "/"},
        }

    elif template_key == "re_engagement":
        town = ctx.get("town", "your town")
        message = {
            "notification": {
                "title": "It\u2019s been a while!",
                "body": f"New deals and conversations are waiting in {town} right now.",
            },
            "data": {"type": "Reengage", "route": "/"},
        }

    elif template_key == "provider_onboard_nudge":
        message = {
            "notification": {
                "title": "Got a business? Join etown!",
                "body": "List your business on etown for free and connect with customers in your area.",
            },
            "data": {"type": "Onboard", "route": "/onboard"},
        }

    elif template_key == "provider_weekly_views":
        n = ctx.get("n", 0)
        business = ctx.get("business") or "Your profile"
        message = {
            "notification": {
                "title": "\U0001F4C8 Your profile is trending on etown!",
                "body": f"{business} was viewed by {n} people this week. Keep it up!",
            },
            "data": {
                "type": "providerview", 
                "route": "/providerscreen",
                "contactid": ctx.get("contactid", ""),
                "userprofileid": ctx.get("userprofileid", "")
            },
        }

    elif template_key == "provider_views_milestone":
        n = ctx.get("n", 0)
        business = ctx.get("business") or "Your profile"
        message = {
            "notification": {
                "title": "\U0001F389 Milestone unlocked!",
                "body": f"{business} has now been viewed by {n}+ people on etown. Congratulations!",
            },
            "data": {
                "type": "providerview", 
                "route": "/providerscreen",
                "contactid": ctx.get("contactid", ""),
                "userprofileid": ctx.get("userprofileid", "")
            },
        }

    elif template_key == "feed_no_post_nudge":
        title = ctx.get("title")
        body = ctx.get("body")
        if not title or not body:
            title = "Got something to share?"
            body = "You were just browsing the feed — post an update and let your community see it!"
        message = {
            "notification": {"title": title, "body": body},
            "data": {
                "type": "Feed",
                "route": "/fullscreenpost",
                "userprofileid": ctx.get("userprofileid", ""),
            },
        }

    elif template_key == "feed_comment":
        actor = ctx.get("actor", "Someone")
        message = {
            "notification": {
                "title": "New comment on your post",
                "body": f"{actor} commented on your post.",
            },
            "data": {
                "type": "Comment",
                "feedid": ctx.get("feed_id", ""),
                "route": "/fullscreenpost",
                "userprofile": ctx.get("userprofile_id", "") or "",
                "isTextPost": "true" if ctx.get("is_text_post") else "false",
            },
        }

    elif template_key == "feed_like":
        actor = ctx.get("actor", "Someone")
        message = {
            "notification": {
                "title": "Your post got a like ❤️",
                "body": f"{actor} liked your post.",
            },
            "data": {
                "type": "Like",
                "feedid": ctx.get("feed_id", ""),
                "route": "/fullscreenpost",
                "userprofile": ctx.get("userprofile_id", "") or "",
                "isTextPost": "true" if ctx.get("is_text_post") else "false",
            },
        }

    elif template_key in ("community_like", "community_comment"):
        actor = ctx.get("actor", "Someone")
        post_type = ctx.get("post_type", "") or "post"
        liked = template_key == "community_like"
        message = {
            "notification": {
                "title": f"Your {post_type} got a like ❤️" if liked else f"New comment on your {post_type}",
                "body": f"{actor} liked your {post_type}." if liked else f"{actor} commented on your {post_type}.",
            },
            "data": {
                "type": "CommunityLike" if liked else "CommunityComment",
                "post_type": post_type,
                "postid": ctx.get("postid", "") or "",
                f"{post_type}id": ctx.get("postid", "") or "",
                "townprofileid": ctx.get("townprofileid", "") or "",
                "userprofile": ctx.get("userprofile_id", "") or "",
                "role": str(ctx.get("role") or ""),
                "route": "/needdetail",
            },
        }

    elif template_key == "new_rating":
        n = ctx.get("n", 0)
        business = ctx.get("business") or "Your business"
        rating = ctx.get("rating")
        try:
            rating_str = f"{float(rating):.1f}★" if rating is not None else ""
        except (TypeError, ValueError):
            logger.debug("push template: rating %r is not a number; omitting it", rating)
            rating_str = ""
        suffix = f" (now {rating_str}, {n} ratings)" if rating_str else f" (now {n} ratings)"
        message = {
            "notification": {
                "title": "⭐ You’ve got a new rating!",
                "body": f"Someone just rated {business}{suffix}. Tap to see your reviews.",
            },
            "data": {
                "type": "Rating",
                "route": "/providerscreen",
                "contactid": ctx.get("contactid", ""),
                "userprofileid": ctx.get("userprofileid", ""),
            },
        }

    elif template_key == "town_view_milestone":
        n = ctx.get("n", 0)
        town = ctx.get("town") or "Your town"
        message = {
            "notification": {
                "title": f"\U0001F4CD {town} is buzzing on etown!",
                "body": f"{town} just crossed {n} views. Now's a great time to post an offer "
                        f"and reach customers exploring your area.",
            },
            "data": {"type": "General", "route": "/providers"},
        }

    elif template_key == "new_town_nearby":
        town = ctx.get("town") or "A new town"
        message = {
            "notification": {
                "title": "New town near you!",
                "body": f"{town} just launched on etown near your area. "
                        f"Explore what’s happening nearby.",
            },
            "data": {
                "type": "MyTown",
                "route": ctx.get("route", "/mytown"),
                "town": ctx.get("town", "") or "",
            },
        }

    elif template_key == "app_store_rating_prompt":
        title = ctx.get("title") or "Enjoying etown?"
        body = ctx.get("body") or (
            "Tap to rate us — your feedback helps your local community grow."
        )
        message = {
            "notification": {"title": title, "body": body},
            "data": {
                "type": "AppRating",
                "route": ctx.get("route", "/apprating"),
                "action": ctx.get("action", "in_app"),
                "store_url": ctx.get("store_url", "") or "",
            },
        }

    elif template_key == "provider_share":
        message = {
            "notification": {
                "title": "etown",
                "body": ctx.get("body", "") or "",
            },
            "data": {
                "type": "Provider",
                "providerid": ctx.get("providerid", "") or "",
                "route": "/providerview",
            },
        }

    elif template_key == "collection_share":
        message = {
            "notification": {
                "title": "etown",
                "body": ctx.get("body", "") or "",
            },
            "data": {
                "type": "Collection",
                "collectionid": ctx.get("collectionid", "") or "",
                "route": "/collectionview",
            },
        }

    elif template_key == "plan_promoted":
        plan = ctx.get("plan_code") or ctx.get("plan_name")
        message = {
            "notification": {
                "title": ctx.get("title") or "Your plan has been upgraded",
                "body": ctx.get("body") or (
                    f"Your {plan} Subscription is now active and ready to use." if plan
                    else "Your subscription plan has been updated."
                ),
            },
            "data": {
                "type": "General",
                "route": ctx.get("route") or "/",
                "plan_code": str(ctx.get("plan_code") or ""),
            },
        }

    elif template_key == "need_posted":
        message = {
            "notification": {
                "title": ctx.get("title") or "New requirement near you",
                "body": ctx.get("body", "") or "",
            },
            "data": {
                "type": "Need",
                "needid": ctx.get("needid", "") or "",
                "townprofileid": ctx.get("townprofileid", "") or "",
                "role": str(ctx.get("role") or ""),
                "route": "/needdetail",
            },
        }

    elif template_key in ("event_approved", "update_approved"):
        post_type = "event" if template_key == "event_approved" else "update"
        post_title = ctx.get("post_title") or f"your {post_type}"
        community_name = ctx.get("community_name") or "town"
        message = {
            "notification": {
                "title": f"Your {post_type.capitalize()} Has Been Approved",
                "body": (
                    f"Your {post_type} “{post_title}” has been approved and is now live "
                    f"in the {community_name} Community."
                ),
            },
            "data": {
                "type": f"{post_type.capitalize()}Approved",
                "post_type": post_type,
                "postid": ctx.get("postid", "") or "",
                f"{post_type}id": ctx.get("postid", "") or "",
                "townprofileid": ctx.get("townprofileid", "") or "",
                "userprofile": ctx.get("userprofile_id", "") or "",
                "role": str(ctx.get("role") or ""),
                "route": "/needdetail",
            },
        }

    elif template_key in ("new_community_event", "new_community_update"):
        is_event = template_key == "new_community_event"
        post_type = "event" if is_event else "update"
        community_name = ctx.get("community_name") or "your town"
        post_title = ctx.get("post_title") or (f"A new {post_type}")
        message = {
            "notification": {
                "title": f"New {post_type.capitalize()} in {community_name}",
                "body": (
                    f"{post_title} has been added to the {community_name} Community. "
                    "Check out the event details and join in!"
                    if is_event
                    else f"There’s a new update in the {community_name} Community: {post_title}. "
                    "Check it out for more details."
                ),
            },
            "data": {
                "type": "NewCommunityEvent" if is_event else "NewCommunityUpdate",
                "post_type": post_type,
                "postid": ctx.get("postid", "") or "",
                f"{post_type}id": ctx.get("postid", "") or "",
                "townprofileid": ctx.get("townprofileid", "") or "",
                "role": str(ctx.get("role") or ""),
                "route": "/needdetail",
            },
        }

    elif template_key == "need_response":
        message = {
            "notification": {
                "title": ctx.get("title") or "New Response to Your Need",
                "body": ctx.get("body", "") or "",
            },
            "data": {
                "type": "NeedResponse",
                "needid": ctx.get("needid", "") or "",
                "conversationid": ctx.get("conversation_id", "") or "",
                "sender_userprofile_id": ctx.get("sender_userprofile_id", "") or "",
                "sender_contact_id": ctx.get("sender_contact_id", "") or "",
                "role": str(ctx.get("role") or ""),
                "route": "/needdetail",
            },
        }

    elif template_key == "town_image_approved":
        message = {
            "notification": {
                "title": ctx.get("title") or "Town Image Change Approved",
                "body": ctx.get("body", "") or "",
            },
            "data": {
                "type": "TownImageApproved",
                "townreviewid": ctx.get("townreviewid", "") or "",
                "townprofileid": ctx.get("townprofileid", "") or "",
                "role": str(ctx.get("role") or ""),
                "route": "/mytown",
            },
        }

    elif template_key == "added_to_mycircle":
        message = {
            "notification": {
                "title": "etown",
                "body": ctx.get("body", "") or "",
            },
            "data": {
                "type": "Added to MyCircle",
                "route": "/mycircle",
            },
        }

    
    elif template_key == "town_community_nudge":
        category = str(ctx.get("category") or "").strip().lower()
        try:
            template_number = int(ctx.get("template_number") or 0)
        except (TypeError, ValueError):
            template_number = 0
        template = TOWN_COMMUNITY_TEMPLATES.get(category, {}).get(template_number, {})
        title = ctx.get("title") or template.get("title", "")
        body = ctx.get("body") or template.get("body", "")
        route = str(ctx.get("route") or "")
        message = {
            "notification": {
                "title": title,
                "body": body,
            },
            "data": {
                "type": "TownCommunityNudge",
                "category": category,
                "template_number": str(template_number),
                "sequence": "" if ctx.get("sequence") is None else str(ctx["sequence"]),
                "route": route,
            },
        }

    else:
        message = {
            "notification": {
                "title": ctx.get("title", "etown"),
                "body": ctx.get("body", "You have a new update on etown."),
            },
            "data": {"type": ctx.get("data_type", "General"), "route": ctx.get("route", "/")},
        }

    if device_token:
        message["token"] = device_token

    return {"message": message}


PUSH_SEMANTICS = {
    NotificationType.OFFER: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 100},
    NotificationType.FEED: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 100},
    NotificationType.DIRECT_MESSAGE: {"timeout": 15.0, "invalidate_on_bad_token": False, "min_token_len": 0},
    NotificationType.BOOKING_REQUEST: {"timeout": 10.0, "invalidate_on_bad_token": False, "min_token_len": 0},
    NotificationType.BOOKING_ADDITIONAL_INFO: {"timeout": 10.0, "invalidate_on_bad_token": False, "min_token_len": 0},
    NotificationType.MORNING_DIGEST: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.EVENING_DIGEST: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.RATING_REMINDER: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.FLASH_SALE: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.MYCIRCLE_ACTIVITY: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.NEW_PROVIDER_NEARBY: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.NEW_OFFER_NEARBY: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.PROVIDER_LIKED: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.LOYALTY_PROGRESS: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.LOYALTY_REWARD_UNLOCKED: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.AI_PICK: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.RE_ENGAGEMENT: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.TARGETED_OFFER_CIRCLE: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.TARGETED_OFFER_GROUP: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.NEW_TOWN_NEARBY: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.APP_STORE_RATING_PROMPT: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.ADDED_TO_MYCIRCLE: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.NEED_POSTED: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.NEED_RESPONSE: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.EVENT_APPROVED: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.UPDATE_APPROVED: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.NEW_COMMUNITY_EVENT: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.NEW_COMMUNITY_UPDATE: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.TOWN_IMAGE_APPROVED: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    NotificationType.TOWN_COMMUNITY_NUDGE: {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
}


def push_semantics(type_key: str) -> Dict[str, Any]:
    """Return the timeout and bad-token handling for a notification type, with a 30-second default."""
    return PUSH_SEMANTICS.get(
        type_key,
        {"timeout": 30.0, "invalidate_on_bad_token": True, "min_token_len": 0},
    )
