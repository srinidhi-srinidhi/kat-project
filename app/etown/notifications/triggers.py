"""Notification Triggers - fans one domain event out to its notification audience.

Async functions run as background tasks by the endpoints: each resolves an audience such as nearby users, a post's owner or providers covering a town, and sends through the governance gate, except share and plan-promotion pushes, which go out directly as GROUP pushes.

Features:
    - Proximity Fan-out: new provider, offer and town triggers notify device users in a lat/lon box.
    - Owner Exclusion: notify_new_provider_nearby() skips the owner, also matched by mobile number.
    - Circle Exclusion: notify_new_offer_nearby() skips the provider's circle, served by TARGETED_OFFER_CIRCLE.
    - Feed Engagement: Feed like/comment notify the owner with a role-aware actor name, skipping self.
    - Community Engagement: Community need/update/event like/comment notify the owner by push and in-app.
    - Direct Group Push: Provider/collection shares send one GROUP push, bypassing governance.
    - Rating Prompt: notify_app_store_rating_prompt() queues an in-app or store-redirect rating ask.
    - Plan Promotion: notify_plan_promoted() sends one GROUPed congratulation push per contact.
    - Need Fan-out: notify_need_posted() notifies providers covering the town, excluding the poster.
    - Need Response: notify_need_response() tells a need's poster by push and in-app that someone responded.
    - Post Approval: notify_post_approved() pushes an update's or event's poster once it is approved.
    - Town Broadcast: notify_post_live_in_town() pushes the town's providers and nearby members once an update or event is approved.
    - Town Image Review: notify_town_image_requested() WhatsApps and emails reviewers a submitted town image.
    - Town Image Approval: notify_town_image_approved() tells the requester by push and in-app once approved.

Classes:
    No classes defined (module exposes trigger functions only)

Functions:
    _owner_mobile_key(): Last ten digits of the owner's number, or '' to disable that exclusion
    notify_new_provider_nearby(): Notify every user within ~10 km of a newly onboarded provider.
    notify_new_offer_nearby(): Notify non-circle users within ~10 km of a new offer.
    _notify_feed_engagement(): Shared helper resolving a feed's owner/actor and sending the notification.
    notify_feed_comment(): Notify a post's owner when a comment is added.
    notify_feed_like(): Notify a post's owner when the post is liked.
    _notify_community_engagement(): Shared helper resolving a community post's owner/actor and sending the notification.
    notify_community_like(): Notify a community post's owner when it is liked.
    notify_community_comment(): Notify a community post's owner when a comment is added.
    notify_need_response(): Push and write a need's poster an in-app notification when someone responds.
    notify_post_approved(): Push an update's or event's poster that it was approved and is live in the community.
    notify_post_live_in_town(): Push the town's providers and nearby members that an approved update or event is live.
    _whatsapp_community_reviewers(): WhatsApp the reviewer numbers one template with the first post's id and link and every town.
    _email_community_reviewers(): Email the reviewer addresses every post's id, town and review link in one message.
    notify_community_post_submitted(): WhatsApp and email the support reviewers once that an event or update awaits approval in its towns.
    notify_town_image_approved(): Push and write the requester an in-app notification that their town image was approved.
    notify_town_image_requested(): WhatsApp and email the support reviewers a town image change request with the image.
    notify_new_town_nearby(): Notify users within a configurable radius that a new town launched.
    notify_app_store_rating_prompt(): Queue an in-app or store-redirect app-rating prompt for one user.
    notify_provider_shared(): Push to every recipient when a user shares a provider profile.
    notify_collection_shared(): Push to every recipient when a user shares a collection.
    notify_plan_promoted(): Tell a contact their providers are now on a new subscription plan.
    notify_need_posted(): Notify providers covering a town that a community need was posted.

Dependencies:
    - app.db.client: Async database client for audience resolution queries
    - app.etown.notifications.service: GovernanceRequest, send()/enqueue() — the governance gate
    - app.etown.notifications.core.dispatcher: dispatch() — direct-push path for share triggers
    - app.etown.notifications.core.models: NotificationRequest, PushAudience, Recipient for GROUP pushes
    - app.etown.notifications.core.registry: NotificationType — notification type identifiers
    - app.etown.notifications.fingerprint: dedupe-key helpers (daily())
    - app.etown.devices.devices_utils: HAS_DEVICE_SQL contact-level device gate
    - app.services.email_service: send_email() for the reviewer emails
    - app.utils.whatsapp: reviewer WhatsApp templates for community posts and town image requests
    - app.utils.blob_storage: download, MIME sniffing and public URL for the town image request image
    - app.utils.ist_clock: now_ist() stamping a town image request's submitted time

Updates:
  13-Jul-2026: Added notify_provider_shared and notify_collection_shared GROUP push dispatches.
  18-Aug-2026: feed_like / feed_comment actor name is role-aware.
  31-Aug-2026: Added notify_plan_promoted for POST /plans/promote.
  02-Sep-2026: Added notify_need_posted for providers covering the town.
  09-Sep-2026: notify_need_posted excludes the poster's whole contact.
  15-Sep-2026: Nearby-users cohort uses the shared contact-level device gate.
  17-Sep-2026: notify_new_provider_nearby() also excludes the owner by mobile number.
  05-Oct-2026: Added notify_community_like, notify_community_comment and notify_community_post_submitted.
  06-Oct-2026: Added notify_need_response, an in-app notification to a need's poster; community review WhatsApp and email link to the post's CRM record; added notify_post_approved and notify_post_live_in_town for approved updates and events; notify_need_response also pushes the poster; notify_need_posted titles the push 'New Need in <town>' and names the poster in the body; community pushes carry the recipient's userprofile role.
  07-Oct-2026: Community likes and comments also write the post owner an in-app notification naming the actor; need response in-app type is 'Need Response'.
  08-Oct-2026: Added notify_town_image_requested for POST /mytown/request_townimage, emailing the image inline, both with a CRM Review Request button; added notify_town_image_approved; notify_plan_promoted takes a push route.
  21-Oct-2026: notify_community_post_submitted sends one WhatsApp and one email for a post across several towns.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import date
from typing import List, Optional

from app.core.config import settings
from app.db.client import Database
from app.etown.devices.devices_utils import HAS_DEVICE_SQL
from app.etown.notifications import fingerprint
from app.etown.notifications.service import (
    GovernanceRequest,
    send as gov_send,
    enqueue as gov_enqueue,
)
from app.etown.notifications.core.dispatcher import dispatch
from app.etown.notifications.core.models import (
    NotificationRequest,
    PushAudience,
    Recipient,
)
from app.etown.notifications.core.registry import NotificationType
from app.services.email_service import send_email
from app.utils.blob_storage import detect_mime_type, download_blob_bytes, to_public_blob_url
from app.utils.ist_clock import now_ist
from app.utils.whatsapp import (
    send_community_post_review_whatsapp_notification,
    send_town_image_request_whatsapp_notification,
)

logger = logging.getLogger(__name__)

_LAT_DELTA = 0.09
_LON_DELTA = 0.103
_INLINE_EMAIL_IMAGE_MAX_BYTES = 3 * 1024 * 1024

def _owner_mobile_key(mobilephone) -> str:
    """Return the last ten digits of a mobile number, or '' when it has fewer than ten."""
    digits = re.sub(r"\D+", "", mobilephone or "")
    return digits[-10:] if len(digits) >= 10 else ""


_NEARBY_USERS_SQL = f"""
SELECT DISTINCT ON (c.contactid) up.userprofileid, up.contactid
FROM   public.etown_userprofile up
JOIN   public.etown_contact c ON c.contactid = up.contactid
WHERE  c.latitude  IS NOT NULL
  AND  c.longitude IS NOT NULL
  AND  abs(c.latitude  - $1) < $3
  AND  abs(c.longitude - $2) < $4
  AND  c.contactid <> $5
  AND  ($6 = '' OR RIGHT(regexp_replace(COALESCE(c.mobilephone, ''), '[^0-9]', '', 'g'), 10) <> $6)
  AND  {HAS_DEVICE_SQL.format(contact="c.contactid", profile="up.userprofileid")}
ORDER BY c.contactid, up.createdon
LIMIT 200
"""

_CIRCLE_CONTACT_IDS_SQL = """
SELECT DISTINCT up2.contactid
FROM (
    SELECT to_userprofile   AS other_upid FROM public.etown_memberrelation   WHERE from_userprofile   = $1
    UNION ALL
    SELECT from_userprofile AS other_upid FROM public.etown_memberrelation   WHERE to_userprofile     = $1
    UNION ALL
    SELECT touserprofileid  AS other_upid FROM public.etown_providerrelation WHERE fromuserprofileid  = $1
    UNION ALL
    SELECT fromuserprofileid AS other_upid FROM public.etown_providerrelation WHERE touserprofileid   = $1
) edges
JOIN public.etown_userprofile up2 ON up2.userprofileid = edges.other_upid
WHERE up2.contactid IS NOT NULL
"""


async def notify_new_provider_nearby(
    provider_upid: str,
    businessname: str,
    townname: str,
    contactid: str,
) -> None:
    """Send NEW_PROVIDER_NEARBY to users within about 10 km, excluding the owner by contactid and mobile number."""
    try:
        loc = await Database.fetchrow(
            "SELECT latitude, longitude, mobilephone FROM public.etown_contact WHERE contactid = $1",
            contactid,
        )
        if not loc or loc["latitude"] is None or loc["longitude"] is None:
            logger.info(
                "notify_new_provider_nearby: no location for contactid=%s — skipping", contactid
            )
            return

        lat = float(loc["latitude"])
        lon = float(loc["longitude"])
        owner_mobile = _owner_mobile_key(loc["mobilephone"])
        if not owner_mobile:
            logger.info(
                "notify_new_provider_nearby: contactid=%s has no usable mobile number — the "
                "owner is excluded by contactid alone", contactid,
            )

        users = await Database.fetch(
            _NEARBY_USERS_SQL, lat, lon, _LAT_DELTA, _LON_DELTA, contactid, owner_mobile
        )
        if not users:
            logger.info(
                "notify_new_provider_nearby: 0 nearby users for provider=%s", businessname
            )
            return

        logger.info(
            "notify_new_provider_nearby: notifying %d user(s) about provider=%s",
            len(users), businessname,
        )
        today = date.today().isoformat()
        semaphore = asyncio.Semaphore(20)

        async def _one(u: dict) -> None:
            async with semaphore:
                await gov_send(GovernanceRequest(
                    userprofileid=u["userprofileid"],
                    contactid=u["contactid"],
                    notification_type=NotificationType.NEW_PROVIDER_NEARBY,
                    sender_businessid=provider_upid,
                    ctx={
                        "business": businessname,
                        "town": townname or "your area",
                        "contactid": contactid,
                        "userprofileid": provider_upid,
                    },
                    dedupe_key=f"new-provider:{provider_upid}:{u['contactid']}:{today}",
                ))

        await asyncio.gather(*[_one(u) for u in users], return_exceptions=True)
        logger.info("notify_new_provider_nearby: done for provider=%s", businessname)

    except Exception:
        logger.exception(
            "notify_new_provider_nearby: unhandled error for provider=%s", businessname
        )


async def notify_new_offer_nearby(
    offer_unicid: str,
    businessname: str,
    provider_upid: str,
    townname: str,
    contactid: str,
) -> None:
    """Send NEW_OFFER_NEARBY to users within about 10 km who are not the owner or in the provider's circle."""
    try:
        loc = await Database.fetchrow(
            """SELECT c.latitude, c.longitude, c.mobilephone, p.townname
               FROM public.etown_contact c
               LEFT JOIN public.etown_provider p ON p.userprofileid = $2
               WHERE c.contactid = $1""",
            contactid, provider_upid,
        )
        if not loc or loc["latitude"] is None or loc["longitude"] is None:
            logger.info(
                "notify_new_offer_nearby: no location for contactid=%s — skipping", contactid
            )
            return

        lat = float(loc["latitude"])
        lon = float(loc["longitude"])
        townname = loc["townname"] or townname or "your area"

        circle_rows = await Database.fetch(_CIRCLE_CONTACT_IDS_SQL, provider_upid)
        exclude_contacts = {r["contactid"] for r in circle_rows} | {contactid}

        users = await Database.fetch(
            _NEARBY_USERS_SQL, lat, lon, _LAT_DELTA, _LON_DELTA, contactid,
            _owner_mobile_key(loc["mobilephone"]),
        )
        users = [u for u in users if u["contactid"] not in exclude_contacts]

        if not users:
            logger.info(
                "notify_new_offer_nearby: 0 non-circle nearby users for offer=%s", offer_unicid
            )
            return

        logger.info(
            "notify_new_offer_nearby: notifying %d user(s) for offer=%s",
            len(users), offer_unicid,
        )
        semaphore = asyncio.Semaphore(20)

        async def _one(u: dict) -> None:
            async with semaphore:
                await gov_send(GovernanceRequest(
                    userprofileid=u["userprofileid"],
                    contactid=u["contactid"],
                    notification_type=NotificationType.NEW_OFFER_NEARBY,
                    sender_businessid=provider_upid,
                    ctx={
                        "business": businessname,
                        "town": townname or "your area",
                        "offer_id": offer_unicid,
                    },
                    dedupe_key=f"new-offer-nearby:{offer_unicid}:{u['contactid']}",
                ))

        await asyncio.gather(*[_one(u) for u in users], return_exceptions=True)
        logger.info("notify_new_offer_nearby: done for offer=%s", offer_unicid)

    except Exception:
        logger.exception(
            "notify_new_offer_nearby: unhandled error for offer=%s", offer_unicid
        )


_FEED_OWNER_LOOKUP_SQL = """
SELECT userprofileid, contactid,
       (feedfilelocationurl IS NULL AND feedvideofilelocation IS NULL) AS is_text_post
FROM public.etown_feed WHERE feedid = $1
"""
_UPID_INFO_SQL = """
SELECT up.contactid,
       CASE
           WHEN up.role = 1 THEN NULLIF((
                SELECT p.businessname FROM public.etown_provider p
                 WHERE p.userprofileid = up.userprofileid LIMIT 1), '')
           WHEN up.role = 2 THEN NULLIF(up.contactidname, '')
       END AS name
FROM public.etown_userprofile up
WHERE up.userprofileid = $1
"""


async def _notify_feed_engagement(
    feed_id: str, actor_upid: str, notif_type: str, dedupe_prefix: str
) -> None:
    """Notify a feed post's owner of a like or comment unless the actor is the owner."""
    feed = await Database.fetchrow(_FEED_OWNER_LOOKUP_SQL, feed_id)
    if not feed:
        return
    owner_upid: Optional[str] = feed["userprofileid"]
    owner_contactid: Optional[str] = feed["contactid"]
    if not owner_upid:
        return
    if owner_upid == actor_upid:
        return
    actor = None
    actor_contactid: Optional[str] = None
    if actor_upid:
        actor_row = await Database.fetchrow(_UPID_INFO_SQL, actor_upid)
        if actor_row:
            actor = actor_row["name"]
            actor_contactid = actor_row["contactid"]
    if owner_contactid and actor_contactid == owner_contactid:
        return
    today = date.today().isoformat()
    await gov_send(GovernanceRequest(
        userprofileid=owner_upid,
        contactid=owner_contactid,
        notification_type=notif_type,
        ctx={
            "actor": actor or "Someone",
            "feed_id": feed_id,
            "userprofile_id": owner_upid,
            "is_text_post": bool(feed["is_text_post"]),
        },
        dedupe_key=f"{dedupe_prefix}:{feed_id}:{actor_upid}:{today}",
    ))


async def notify_feed_comment(feed_id: str, commenter_upid: str) -> None:
    """Notify a feed post's owner that someone commented, logging any error."""
    try:
        await _notify_feed_engagement(
            feed_id, commenter_upid, NotificationType.FEED_COMMENT, "feed-comment"
        )
    except Exception:
        logger.exception("notify_feed_comment: unhandled error for feed=%s", feed_id)


async def notify_feed_like(feed_id: str, liker_upid: str) -> None:
    """Notify a feed post's owner that someone liked it, logging any error."""
    try:
        await _notify_feed_engagement(
            feed_id, liker_upid, NotificationType.FEED_LIKE, "feed-like"
        )
    except Exception:
        logger.exception("notify_feed_like: unhandled error for feed=%s", feed_id)


_COMMUNITY_POST_TABLES = {
    "need": ("etown_need", "needid"),
    "update": ("etown_update", "updateid"),
    "event": ("etown_event", "eventid"),
}


async def _notify_community_engagement(
    post_type: str, postid: str, actor_upid: str, notif_type: str, dedupe_prefix: str
) -> None:
    """Notify a community post's owner of a like or comment unless the actor is the owner."""
    table, id_column = _COMMUNITY_POST_TABLES[post_type]
    post = await Database.fetchrow(
        f"SELECT n.userprofileid, n.contactid, n.townprofileid, up.role FROM public.{table} n "
        f"LEFT JOIN public.etown_userprofile up ON up.userprofileid = n.userprofileid "
        f"WHERE n.{id_column} = $1",
        postid,
    )
    if not post or not post["userprofileid"] or post["userprofileid"] == actor_upid:
        return
    owner_upid: str = post["userprofileid"]
    owner_contactid: Optional[str] = post["contactid"]
    actor = None
    actor_contactid: Optional[str] = None
    if actor_upid:
        actor_row = await Database.fetchrow(_UPID_INFO_SQL, actor_upid)
        if actor_row:
            actor = actor_row["name"]
            actor_contactid = actor_row["contactid"]
    if owner_contactid and actor_contactid == owner_contactid:
        return
    today = date.today().isoformat()
    liked = notif_type == NotificationType.COMMUNITY_LIKE
    await gov_send(GovernanceRequest(
        userprofileid=owner_upid,
        contactid=owner_contactid,
        notification_type=notif_type,
        ctx={
            "actor": actor or "Someone",
            "post_type": post_type,
            "postid": postid,
            "townprofileid": post["townprofileid"],
            "userprofile_id": owner_upid,
            "role": post["role"],
            "inapp_content": (
                f"{actor or 'Someone'} {'liked' if liked else 'commented on'} your {post_type}."
            ),
            "inapp_type": "Community Like" if liked else "Community Comment",
            "sender_contact_id": actor_contactid,
            "sender_userprofile_id": actor_upid or None,
        },
        dedupe_key=f"{dedupe_prefix}:{postid}:{actor_upid}:{today}",
    ))


async def notify_community_like(post_type: str, postid: str, liker_upid: str) -> None:
    """Notify a community need, update or event's owner that someone liked it, logging any error."""
    try:
        await _notify_community_engagement(
            post_type, postid, liker_upid, NotificationType.COMMUNITY_LIKE, "community-like"
        )
    except Exception:
        logger.exception("notify_community_like: unhandled error for %s=%s", post_type, postid)


async def notify_community_comment(post_type: str, postid: str, commenter_upid: str) -> None:
    """Notify a community need, update or event's owner that someone commented, logging any error."""
    try:
        await _notify_community_engagement(
            post_type, postid, commenter_upid, NotificationType.COMMUNITY_COMMENT, "community-comment"
        )
    except Exception:
        logger.exception("notify_community_comment: unhandled error for %s=%s", post_type, postid)


async def notify_need_response(
    needid: str, needresponseid: str, responder_upid: str, conversation_id: Optional[str] = None
) -> None:
    """Push and write a need's poster an in-app notification that someone responded to it, logging any error."""
    try:
        need = await Database.fetchrow(
            "SELECT n.userprofileid, n.contactid, n.title, up.role FROM public.etown_need n "
            "LEFT JOIN public.etown_userprofile up ON up.userprofileid = n.userprofileid "
            "WHERE n.needid = $1",
            needid,
        )
        if not need or not need["userprofileid"]:
            return
        responder = await Database.fetchrow(_UPID_INFO_SQL, responder_upid)
        name = (responder["name"] if responder else None) or "Someone"
        title = (need["title"] or "").strip() or "your need"
        await gov_send(GovernanceRequest(
            userprofileid=need["userprofileid"],
            contactid=need["contactid"],
            notification_type=NotificationType.NEED_RESPONSE,
            ctx={
                "title": "New Response to Your Need",
                "body": (
                    f"{name} has responded to your need “{title}”. "
                    "Check the response and continue the conversation."
                ),
                "needid": needid,
                "conversation_id": conversation_id,
                "inapp_type": "Need Response",
                "role": need["role"],
                "sender_contact_id": responder["contactid"] if responder else None,
                "sender_userprofile_id": responder_upid,
            },
            dedupe_key=fingerprint.once("need-response", needresponseid),
        ))
    except Exception:
        logger.exception("notify_need_response: unhandled error for need=%s", needid)


_POST_APPROVED_TYPES = {
    "event": NotificationType.EVENT_APPROVED,
    "update": NotificationType.UPDATE_APPROVED,
}


async def notify_post_approved(post_type: str, postid: str) -> None:
    """Push an event's or update's poster that it was approved and is live in its town's community, logging any error."""
    try:
        table, id_column = _COMMUNITY_POST_TABLES[post_type]
        post = await Database.fetchrow(
            f"SELECT n.userprofileid, n.contactid, n.title, n.townname, n.townprofileid, up.role "
            f"FROM public.{table} n "
            f"LEFT JOIN public.etown_userprofile up ON up.userprofileid = n.userprofileid "
            f"WHERE n.{id_column} = $1",
            postid,
        )
        if not post or not post["userprofileid"]:
            logger.info("notify_post_approved: no poster for %s=%s", post_type, postid)
            return
        await gov_send(GovernanceRequest(
            userprofileid=post["userprofileid"],
            contactid=post["contactid"],
            notification_type=_POST_APPROVED_TYPES[post_type],
            ctx={
                "post_title": (post["title"] or "").strip() or None,
                "community_name": (post["townname"] or "").strip() or None,
                "postid": postid,
                "townprofileid": post["townprofileid"],
                "userprofile_id": post["userprofileid"],
                "role": post["role"],
            },
            dedupe_key=fingerprint.once(f"{post_type}-approved", postid),
        ))
    except Exception:
        logger.exception("notify_post_approved: unhandled error for %s=%s", post_type, postid)


_NEARBY_MEMBERS_SQL = f"""
SELECT DISTINCT ON (c.contactid) up.userprofileid, up.contactid, up.role
FROM   public.etown_userprofile up
JOIN   public.etown_contact c ON c.contactid = up.contactid
WHERE  up.role = 2
  AND  COALESCE(up.deleted, 'false') <> 'true'
  AND  COALESCE(c.deleted, 'false')  <> 'true'
  AND  c.latitude  IS NOT NULL
  AND  c.longitude IS NOT NULL
  AND  abs(c.latitude  - $1) < $3
  AND  abs(c.longitude - $2) < $4
  AND  c.contactid <> $5
  AND  {HAS_DEVICE_SQL.format(contact="c.contactid", profile="up.userprofileid")}
ORDER BY c.contactid, up.createdon
LIMIT 1000
"""

_POST_LIVE_TYPES = {
    "event": NotificationType.NEW_COMMUNITY_EVENT,
    "update": NotificationType.NEW_COMMUNITY_UPDATE,
}


async def notify_post_live_in_town(post_type: str, postid: str, *, radius_km: float = 10.0) -> int:
    """Push the providers covering an approved event's or update's town and the members near it, and return how many succeeded.

    Args:
        post_type: event or update; picks the table and the notification type.
        postid: eventid or updateid of the approved post.
        radius_km: Members whose saved location is within this distance of the post are included.
    """
    try:
        table, id_column = _COMMUNITY_POST_TABLES[post_type]
        post = await Database.fetchrow(
            f"SELECT userprofileid, contactid, title, townname, townprofileid, latitude, longitude "
            f"FROM public.{table} WHERE {id_column} = $1",
            postid,
        )
        if not post:
            logger.info("notify_post_live_in_town: no %s=%s", post_type, postid)
            return 0
        poster_upid = post["userprofileid"] or ""
        poster_contactid = post["contactid"] or ""

        providers = []
        if post["townprofileid"]:
            providers = await Database.fetch(
                _TOWN_PROVIDERS_SQL, post["townprofileid"], poster_upid, poster_contactid
            )
        members = []
        lat, lon = post["latitude"], post["longitude"]
        if lat is not None and lon is not None and (float(lat), float(lon)) != (0.0, 0.0):
            members = await Database.fetch(
                _NEARBY_MEMBERS_SQL, float(lat), float(lon),
                float(radius_km) / 111.0, float(radius_km) / 97.0, poster_contactid,
            )

        recipients = {}
        for r in list(providers) + list(members):
            key = r["contactid"] or r["userprofileid"]
            if key and key not in recipients and r["userprofileid"] != poster_upid:
                recipients[key] = r
        if not recipients:
            logger.info(
                "notify_post_live_in_town: 0 recipients for %s=%s town=%s",
                post_type, postid, post["townname"],
            )
            return 0

        ctx = {
            "post_title": (post["title"] or "").strip() or None,
            "community_name": (post["townname"] or "").strip() or None,
            "postid": postid,
            "townprofileid": post["townprofileid"],
        }
        logger.info(
            "notify_post_live_in_town: notifying %d recipient(s) (%d provider(s), %d nearby member(s)) "
            "about %s=%s town=%s",
            len(recipients), len(providers), len(members), post_type, postid, post["townname"],
        )
        semaphore = asyncio.Semaphore(20)

        async def _one(r: dict) -> None:
            async with semaphore:
                await gov_enqueue(GovernanceRequest(
                    userprofileid=r["userprofileid"],
                    contactid=r["contactid"],
                    notification_type=_POST_LIVE_TYPES[post_type],
                    ctx={**ctx, "role": r["role"]},
                    dedupe_key=fingerprint.once(f"new-{post_type}", postid, r["userprofileid"]),
                ))

        results = await asyncio.gather(*[_one(r) for r in recipients.values()], return_exceptions=True)
        failed = 0
        for recipient, outcome in zip(recipients.values(), results):
            if isinstance(outcome, BaseException):
                failed += 1
                logger.error(
                    "notify_post_live_in_town: recipient failed | %s=%s user=%s: %s",
                    post_type, postid, recipient["userprofileid"], outcome,
                    exc_info=(type(outcome), outcome, outcome.__traceback__),
                )
        logger.info(
            "notify_post_live_in_town: done for %s=%s — %d of %d recipient(s) enqueued",
            post_type, postid, len(recipients) - failed, len(recipients),
        )
        return len(recipients) - failed

    except Exception:
        logger.exception("notify_post_live_in_town: unhandled error for %s=%s", post_type, postid)
        return 0


async def _whatsapp_community_reviewers(
    post_type: str, posts: List[dict], title: str, poster_name: str
) -> None:
    """WhatsApp every COMMUNITY_REVIEW_WHATSAPP_RECIPIENTS number one template naming the first post and every town."""
    postid = posts[0]["postid"]
    recipients = list(dict.fromkeys(settings.COMMUNITY_REVIEW_WHATSAPP_RECIPIENTS))
    if not recipients:
        logger.info(
            "notify_community_post_submitted: no COMMUNITY_REVIEW_WHATSAPP_RECIPIENTS for %s=%s",
            post_type, postid,
        )
        return
    community = ", ".join(p["townname"] for p in posts if p["townname"])
    results = await asyncio.gather(*(
        send_community_post_review_whatsapp_notification(
            mobile_number=number,
            post_type=post_type,
            poster_name=poster_name,
            postid=postid,
            title=title,
            community=community,
            review_path=posts[0]["review_path"],
        )
        for number in recipients
    ))
    logger.info(
        "notify_community_post_submitted: %s=%s (%d town(s)) WhatsApp sent to %d/%d reviewer(s)",
        post_type, postid, len(posts), sum(1 for ok, _ in results if ok), len(recipients),
    )


async def _email_community_reviewers(
    post_type: str, posts: List[dict], title: str, poster_name: str
) -> None:
    """Email COMMUNITY_REVIEW_EMAIL_RECIPIENTS one message listing every post's id, town and review link, logging any failure."""
    postids = ", ".join(p["postid"] for p in posts)
    recipients = settings.COMMUNITY_REVIEW_EMAIL_RECIPIENTS
    if not recipients:
        logger.info(
            "notify_community_post_submitted: no COMMUNITY_REVIEW_EMAIL_RECIPIENTS for %s=%s",
            post_type, postids,
        )
        return
    label = post_type.capitalize()
    base_url = settings.COMMUNITY_REVIEW_BASE_URL.rstrip("/")
    details = [
        (label, title or "-"),
        ("Submitted by", poster_name),
        ("Communities", ", ".join(p["townname"] or "-" for p in posts)),
    ]
    post_items = "".join(
        f"<li><strong>{html.escape(p['townname'] or '-')}</strong> – {label} ID: "
        f"{html.escape(p['postid'])} – "
        f'<a href="{html.escape(base_url + "/" + p["review_path"].lstrip("/"), quote=True)}">'
        f"Review {label}</a></li>"
        for p in posts
    )
    body = (
        "<p>Hi Support Team,</p>"
        f"<p>A new {post_type} has been submitted and is awaiting your review.</p>"
        f"<p><strong>{label} Details</strong></p>"
        "<ul>"
        + "".join(
            f"<li><strong>{name}:</strong> {html.escape(str(value))}</li>"
            for name, value in details
        )
        + "</ul>"
        f"<p><strong>{label}s by Community</strong></p>"
        f"<ul>{post_items}</ul>"
        f"<p>Kindly review each {post_type} and <strong>approve or reject</strong> it from the support dashboard.</p>"
        f"<p>If you have any questions regarding this submission, please check the {post_type} "
        "details from the support dashboard.</p>"
        "<p>Thank you,<br><strong>Team etown</strong></p>"
    )
    try:
        await send_email(
            to=recipients,
            subject=f"New {label} Submitted for Approval – {title or posts[0]['postid']}",
            body=body,
        )
        logger.info(
            "notify_community_post_submitted: %s=%s email sent to %s",
            post_type, postids, ", ".join(recipients),
        )
    except Exception:
        logger.exception(
            "notify_community_post_submitted: email failed for %s=%s", post_type, postids
        )


async def notify_community_post_submitted(
    post_type: str, posts: List[dict], title: str, poster_upid: str
) -> None:
    """WhatsApp and email the support reviewers once that an event or update awaits review in one or more towns."""
    postids = ", ".join(str(p.get("postid")) for p in posts)
    try:
        if not posts:
            return
        poster_name = None
        if poster_upid:
            row = await Database.fetchrow(_UPID_INFO_SQL, poster_upid)
            if row:
                poster_name = row["name"]
        poster_name = poster_name or "A member"
        posts = [
            {
                "postid": str(p["postid"]),
                "townname": (p.get("townname") or "").strip(),
                "review_path": (
                    f"main.aspx?appid={settings.COMMUNITY_REVIEW_APP_ID}"
                    f"&pagetype=entityrecord&etn=ets_{post_type}&id={p['postid']}"
                ),
            }
            for p in posts
        ]
        results = await asyncio.gather(
            _whatsapp_community_reviewers(post_type, posts, title, poster_name),
            _email_community_reviewers(post_type, posts, title, poster_name),
            return_exceptions=True,
        )
        for channel, result in zip(("whatsapp", "email"), results):
            if isinstance(result, BaseException):
                logger.error(
                    "notify_community_post_submitted: %s failed for %s=%s",
                    channel, post_type, postids, exc_info=result,
                )
    except Exception:
        logger.exception("notify_community_post_submitted: unhandled error for %s=%s", post_type, postids)


async def notify_town_image_approved(townreviewid: str) -> None:
    """Push and write a town image requester an in-app notification that their image was approved, logging any error."""
    try:
        review = await Database.fetchrow(
            "SELECT tr.userprofileid, tr.contactid, tr.townname, tr.townprofileid, up.role "
            "FROM public.etown_townreview tr "
            "LEFT JOIN public.etown_userprofile up ON up.userprofileid = tr.userprofileid "
            "WHERE tr.townreviewid = $1::uuid",
            townreviewid,
        )
        if not review or not review["userprofileid"]:
            logger.info("notify_town_image_approved: no requester for townreviewid=%s", townreviewid)
            return
        town = (review["townname"] or "").strip() or "your town"
        await gov_send(GovernanceRequest(
            userprofileid=review["userprofileid"],
            contactid=review["contactid"],
            notification_type=NotificationType.TOWN_IMAGE_APPROVED,
            ctx={
                "title": "Town Image Change Approved",
                "body": (
                    f"Your requested town image change for {town} has been approved "
                    "and is now live in the community."
                ),
                "inapp_type": "Town Image Approved",
                "townreviewid": townreviewid,
                "townprofileid": review["townprofileid"],
                "role": review["role"],
            },
            dedupe_key=fingerprint.once("town-image-approved", townreviewid),
        ))
    except Exception:
        logger.exception("notify_town_image_approved: unhandled error for townreviewid=%s", townreviewid)


async def notify_town_image_requested(
    townreviewid: str,
    townprofileid: str,
    townname: Optional[str],
    requester_name: Optional[str],
    imageurl: str,
) -> None:
    """WhatsApp and email the support reviewers that a town image change request awaits review, logging any error."""
    try:
        town = (townname or "").strip() or "-"
        requester = (requester_name or "").strip() or "A member"
        image_link = to_public_blob_url(imageurl)
        submitted_at = now_ist().strftime("%d %b %Y, %I:%M %p IST")
        review_path = (
            settings.TOWN_IMAGE_REVIEW_PATH
            .replace("{appid}", settings.COMMUNITY_REVIEW_APP_ID)
            .replace("{townprofileid}", townprofileid)
            .replace("{townreviewid}", townreviewid)
            .lstrip("/")
        )
        review_url = f"{settings.COMMUNITY_REVIEW_BASE_URL.rstrip('/')}/{review_path}"

        async def _whatsapp() -> None:
            numbers = list(dict.fromkeys(settings.COMMUNITY_REVIEW_WHATSAPP_RECIPIENTS))
            if not numbers:
                logger.info(
                    "notify_town_image_requested: no COMMUNITY_REVIEW_WHATSAPP_RECIPIENTS for townreviewid=%s",
                    townreviewid,
                )
                return
            results = await asyncio.gather(*(
                send_town_image_request_whatsapp_notification(
                    mobile_number=number,
                    image_url=image_link,
                    town_name=town,
                    requester_name=requester,
                    request_id=townreviewid,
                    review_path=review_path,
                )
                for number in numbers
            ))
            logger.info(
                "notify_town_image_requested: townreviewid=%s WhatsApp sent to %d/%d reviewer(s)",
                townreviewid, sum(1 for ok, _ in results if ok), len(numbers),
            )

        async def _email() -> None:
            recipients = settings.COMMUNITY_REVIEW_EMAIL_RECIPIENTS
            if not recipients:
                logger.info(
                    "notify_town_image_requested: no COMMUNITY_REVIEW_EMAIL_RECIPIENTS for townreviewid=%s",
                    townreviewid,
                )
                return
            details = [
                ("Town", town),
                ("Requested by", requester),
                ("Request ID", townreviewid),
                ("Submitted", submitted_at),
            ]
            inline_images = []
            image_src = image_link
            try:
                image_bytes = await download_blob_bytes(imageurl)
                if image_bytes and len(image_bytes) <= _INLINE_EMAIL_IMAGE_MAX_BYTES:
                    content_type = detect_mime_type(image_bytes)
                    extension = content_type.split("/")[-1].replace("jpeg", "jpg")
                    inline_images.append(
                        ("townimage", f"town-image.{extension}", content_type, image_bytes)
                    )
                    image_src = "cid:townimage"
            except Exception:
                logger.warning(
                    "notify_town_image_requested: inline image unavailable for townreviewid=%s, linking it",
                    townreviewid, exc_info=True,
                )
            review_button = (
                '<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:16px 0;">'
                '<tr><td align="center" bgcolor="#1F3864" style="border-radius:6px;">'
                f'<a href="{html.escape(review_url, quote=True)}" target="_blank" '
                'style="display:inline-block;padding:12px 28px;font-family:Arial,sans-serif;'
                'font-size:15px;font-weight:bold;color:#ffffff;text-decoration:none;border-radius:6px;">'
                "Review Request</a></td></tr></table>"
            )
            body = (
                "<p>Hi Support Team,</p>"
                "<p>A new town image change request has been submitted and is awaiting your review.</p>"
                "<p><strong>Submitted Image</strong></p>"
                f'<p><img src="{html.escape(image_src, quote=True)}" alt="Submitted town image" width="480" '
                'style="max-width:480px;width:100%;height:auto;border-radius:8px;display:block;"></p>'
                "<p><strong>Request Details</strong></p>"
                + "".join(
                    f"<p style=\"margin:0\"><strong>{name}:</strong> {html.escape(str(value))}</p>"
                    for name, value in details
                )
                + "<p>Kindly review the submitted image and approve or reject the request from the Admin Dashboard.</p>"
                + review_button
                + "<p>Thank you,<br><strong>Team etown</strong></p>"
            )
            await send_email(
                to=recipients,
                subject=f"New Town Image Change Request – {town}",
                body=body,
                inline_images=inline_images,
            )
            logger.info(
                "notify_town_image_requested: townreviewid=%s email sent to %s",
                townreviewid, ", ".join(recipients),
            )

        results = await asyncio.gather(_whatsapp(), _email(), return_exceptions=True)
        for channel, result in zip(("whatsapp", "email"), results):
            if isinstance(result, BaseException):
                logger.error(
                    "notify_town_image_requested: %s failed for townreviewid=%s",
                    channel, townreviewid, exc_info=result,
                )
    except Exception:
        logger.exception("notify_town_image_requested: unhandled error for townreviewid=%s", townreviewid)


async def notify_new_town_nearby(
    town_name: str,
    town_lat: float,
    town_lon: float,
    *,
    town_id: Optional[str] = None,
    radius_km: float = 10.0,
    exclude_contactid: str = "",
) -> None:
    """Enqueue NEW_TOWN_NEARBY for users within radius_km of a new town.

    Args:
        town_name: Name of the new town, used in the notification copy.
        town_lat: Town latitude.
        town_lon: Town longitude.
        town_id: Optional town id used as the dedupe key.
        radius_km: Notification radius in km.
        exclude_contactid: Contact dropped from the blast; empty string excludes no one.
    """
    try:
        if town_lat is None or town_lon is None:
            logger.info("notify_new_town_nearby: no coordinates for town=%s — skipping", town_name)
            return

        lat_delta = float(radius_km) / 111.0
        lon_delta = float(radius_km) / 97.0

        users = await Database.fetch(
            _NEARBY_USERS_SQL, float(town_lat), float(town_lon), lat_delta, lon_delta,
            exclude_contactid, "",
        )
        if not users:
            logger.info("notify_new_town_nearby: 0 nearby users for town=%s", town_name)
            return

        logger.info(
            "notify_new_town_nearby: notifying %d user(s) about town=%s", len(users), town_name
        )
        town_key = town_id or f"{town_name}:{round(float(town_lat), 3)}:{round(float(town_lon), 3)}"
        semaphore = asyncio.Semaphore(20)

        async def _one(u: dict) -> None:
            async with semaphore:
                await gov_enqueue(GovernanceRequest(
                    userprofileid=u["userprofileid"],
                    contactid=u["contactid"],
                    notification_type=NotificationType.NEW_TOWN_NEARBY,
                    ctx={"town": town_name or "A new town"},
                    dedupe_key=fingerprint.daily("new-town", town_key, u["userprofileid"]),
                ))

        await asyncio.gather(*[_one(u) for u in users], return_exceptions=True)
        logger.info("notify_new_town_nearby: done for town=%s", town_name)

    except Exception:
        logger.exception("notify_new_town_nearby: unhandled error for town=%s", town_name)


async def notify_app_store_rating_prompt(
    userprofileid: str,
    contactid: Optional[str] = None,
    *,
    action: str = "in_app",
    store_url: Optional[str] = None,
) -> None:
    """Enqueue a once-daily app rating prompt for one user, either in-app or a store redirect."""
    try:
        if not userprofileid:
            return
        today = date.today().isoformat()
        await gov_enqueue(GovernanceRequest(
            userprofileid=userprofileid,
            contactid=contactid,
            notification_type=NotificationType.APP_STORE_RATING_PROMPT,
            ctx={"action": action, "store_url": store_url or ""},
            dedupe_key=f"app-rating:{userprofileid}:{today}",
        ))
    except Exception:
        logger.exception(
            "notify_app_store_rating_prompt: unhandled error for user=%s", userprofileid
        )


_SENDER_FIRSTNAME_SQL = "SELECT firstname FROM public.etown_contact WHERE contactid = $1"


async def notify_provider_shared(
    providerid: str,
    shared_userprofile_ids: List[str],
    sender_contactid: str,
    oauth_token: Optional[str] = None,
) -> bool:
    """Send a PROVIDER_SHARE group push naming the sender and return whether any push was delivered.

    Args:
        providerid: Provider GUID being shared, carried in the push data.
        shared_userprofile_ids: Recipient user profile GUIDs to notify.
        sender_contactid: Contact GUID of the sharing user, for the first name.
        oauth_token: Client-supplied FCM OAuth bearer token passed through to FCM.
    """
    try:
        recipients = [u for u in (shared_userprofile_ids or []) if u]
        if not recipients:
            logger.info("notify_provider_shared: no recipients for provider=%s", providerid)
            return False

        firstname = ""
        if sender_contactid:
            row = await Database.fetchrow(_SENDER_FIRSTNAME_SQL, sender_contactid)
            if row and row["firstname"]:
                firstname = row["firstname"]

        body = f"{firstname} shared a provider profile".strip()

        result = await dispatch(NotificationRequest(
            notification_type=NotificationType.PROVIDER_SHARE,
            recipient=Recipient(userprofile_ids=recipients),
            context={"body": body, "providerid": providerid},
            push_audience=PushAudience.GROUP,
            oauth_token=oauth_token,
        ))
        logger.info(
            "notify_provider_shared: provider=%s recipients=%d delivered=%s",
            providerid, len(recipients), result.any_delivered,
        )
        return result.any_delivered

    except Exception:
        logger.exception("notify_provider_shared: unhandled error for provider=%s", providerid)
        return False


_COLLECTION_OWNER_FIRSTNAME_SQL = """
    SELECT c.firstname
    FROM public.etown_collections col
    JOIN public.etown_contact c ON c.contactid = col.usercontactid
    WHERE col.collectionid = $1
"""


async def notify_collection_shared(
    collectionid: str,
    shared_userprofile_ids: List[str],
    oauth_token: Optional[str] = None,
) -> bool:
    """Send a COLLECTION_SHARE group push naming the owner and return whether any push was delivered.

    Args:
        collectionid: Collection GUID being shared, carried in the push data.
        shared_userprofile_ids: Recipient user profile GUIDs to notify.
        oauth_token: Client-supplied FCM OAuth bearer token, or None.
    """
    try:
        recipients = [u for u in (shared_userprofile_ids or []) if u]
        if not recipients:
            logger.info("notify_collection_shared: no recipients for collection=%s", collectionid)
            return False

        firstname = ""
        if collectionid:
            row = await Database.fetchrow(_COLLECTION_OWNER_FIRSTNAME_SQL, collectionid)
            if row and row["firstname"]:
                firstname = row["firstname"]

        body = f"{firstname} shared a collection".strip()

        result = await dispatch(NotificationRequest(
            notification_type=NotificationType.COLLECTION_SHARE,
            recipient=Recipient(userprofile_ids=recipients),
            context={"body": body, "collectionid": collectionid},
            push_audience=PushAudience.GROUP,
            oauth_token=oauth_token,
        ))
        logger.info(
            "notify_collection_shared: collection=%s recipients=%d delivered=%s",
            collectionid, len(recipients), result.any_delivered,
        )
        return result.any_delivered

    except Exception:
        logger.exception("notify_collection_shared: unhandled error for collection=%s", collectionid)
        return False


async def notify_plan_promoted(
    plan_code: str,
    userprofile_ids: List[str],
    contactid: Optional[str] = None,
    route: Optional[str] = None,
) -> bool:
    """Send one PLAN_PROMOTED group push to all promoted profiles and return whether any was delivered.

    Args:
        plan_code: Tier just granted, rendered into the message body.
        userprofile_ids: Every provider profile the promotion moved.
        contactid: Owning contact, used for the log line only.
        route: App route the push opens on tap; the template defaults to '/'.
    """
    try:
        recipients = [u for u in (userprofile_ids or []) if u]
        if not recipients:
            logger.info("notify_plan_promoted: no recipients for plan=%s", plan_code)
            return False

        result = await dispatch(NotificationRequest(
            notification_type=NotificationType.PLAN_PROMOTED,
            recipient=Recipient(userprofile_ids=recipients),
            context={"plan_code": plan_code, "route": route},
            push_audience=PushAudience.GROUP,
        ))
        logger.info(
            "notify_plan_promoted: contact=%s plan=%s profiles=%d delivered=%s",
            contactid, plan_code, len(recipients), result.any_delivered,
        )
        return result.any_delivered

    except Exception:
        logger.exception(
            "notify_plan_promoted: unhandled error for contact=%s plan=%s", contactid, plan_code
        )
        return False
    
_TOWN_PROVIDERS_SQL = """
SELECT DISTINCT ON (p.userprofileid) p.userprofileid, p.contactid, p.businessname, up.role
FROM   public.etown_etownprovider ep
JOIN   public.etown_provider p ON p.providerid = ep.providerid
LEFT JOIN public.etown_userprofile up ON up.userprofileid = p.userprofileid
WHERE  ep.townprofileid = $1
  AND  COALESCE(ep.deleted, 'false')     <> 'true'
  AND  COALESCE(ep.deactivated, 'false') <> 'true'
  AND  COALESCE(p.deleted, 'false')      <> 'true'
  AND  COALESCE(p.deactivated, 'false')  <> 'true'
  AND  p.disabled IS NOT TRUE
  AND  p.userprofileid IS NOT NULL
  AND  p.userprofileid <> $2
  AND  ($3 = '' OR p.contactid IS NULL OR p.contactid <> $3)
ORDER  BY p.userprofileid, ep.is_primary DESC
LIMIT  500
"""

async def notify_need_posted(
    needid: str,
    townname: str,
    townprofileid: str,
    title: str,
    *,
    poster_upid: str = "",
    poster_contactid: str = "",
) -> int:
    """Enqueue NEED_POSTED for every active provider covering the town and return how many succeeded.

    Args:
        needid: Need primary key, carried in the push data.
        townname: Town the need was posted in, rendered into the title.
        townprofileid: Town whose covering providers are notified.
        title: The member's one-line requirement, rendered into the body.
        poster_upid: Poster's userprofileid, named in the body and excluded from the blast.
        poster_contactid: Poster's contactid, recorded as sender and excluded from the blast.
    """
    try:
        providers = await Database.fetch(
            _TOWN_PROVIDERS_SQL, townprofileid, poster_upid or "", poster_contactid or ""
        )
        if not providers:
            logger.warning(
                "notify_need_posted: 0 providers cover town=%s (%s) — need=%s will get no quotes",
                townname, townprofileid, needid,
            )
            return 0

        poster = await Database.fetchrow(_UPID_INFO_SQL, poster_upid) if poster_upid else None
        member_name = ((poster["name"] if poster else None) or "").strip() or "A member"
        need_title = (title or "").strip() or "a new requirement"
        town = (townname or "").strip()
        push_title = f"New Need in {town}" if town else "New Need near you"
        body = f"{member_name} has posted a new need: {need_title}. Check the details and respond if you can help."

        logger.info(
            "notify_need_posted: notifying %d provider(s) about need=%s town=%s",
            len(providers), needid, townname,
        )
        semaphore = asyncio.Semaphore(20)

        async def _one(p: dict) -> None:
            async with semaphore:
                await gov_enqueue(GovernanceRequest(
                    userprofileid=p["userprofileid"],
                    contactid=p["contactid"],
                    notification_type=NotificationType.NEED_POSTED,
                    ctx={
                        "title": push_title,
                        "body": body,
                        "needid": needid,
                        "townprofileid": townprofileid,
                        "inapp_type": "Need",
                        "role": p["role"] or 1,
                        "sender_contact_id": poster_contactid or None,
                        "sender_userprofile_id": poster_upid or None,
                    },
                    dedupe_key=fingerprint.once("need", needid, p["userprofileid"]),
                ))

        results = await asyncio.gather(*[_one(p) for p in providers], return_exceptions=True)
        failed = 0
        for provider, outcome in zip(providers, results):
            if isinstance(outcome, BaseException):
                failed += 1
                logger.error(
                    "notify_need_posted: recipient failed | need=%s provider=%s: %s",
                    needid, provider["userprofileid"], outcome,
                    exc_info=(type(outcome), outcome, outcome.__traceback__),
                )
        if failed:
            logger.warning(
                "notify_need_posted: done for need=%s — %d of %d recipient(s) failed",
                needid, failed, len(providers),
            )
        else:
            logger.info(
                "notify_need_posted: done for need=%s — %d recipient(s) raised",
                needid, len(providers),
            )
        return len(providers) - failed

    except Exception:
        logger.exception("notify_need_posted: unhandled error for need=%s", needid)
        return 0
