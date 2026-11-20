"""Notification Preferences Utilities - per-user channel opt-outs consulted by the governance gate.

Notification-domain store over notifications.etown_notification_preferences that service.py asks whether a user accepts push, WhatsApp or in-app sends, with safe defaults when no row exists; it also records WhatsApp STOP replies, feed last-seen times and the feed nudge queue.

Features:
    - Channel Opt-out: can_send() checks push/whatsapp/inapp; the WhatsApp STOP flag always wins.
    - Safe Defaults: get() returns push on / WhatsApp opt-in off / in-app on when no DB row exists.
    - WhatsApp STOP Handling: set_whatsapp_stopped() records a STOP reply to block further WhatsApp.
    - Feed Last-seen: touch_feed_last_seen() upserts the My Circle feed last-seen time.
    - Feed Nudge Dedup: record_feed_open() queues one nudge per contactid, not per userprofileid.

Classes:
    Preferences(): Dataclass snapshot of one user's push/whatsapp/inapp/stopped flags.

Functions:
    get(): Return stored preferences for a userprofileid, or safe defaults if no row exists.
    can_send(): Return True when the user allows notifications on the given channel.
    set_whatsapp_stopped(): Mark the user as having replied STOP to a WhatsApp message.
    touch_feed_last_seen(): Upsert the My Circle feed last-seen timestamp for a userprofileid.
    record_feed_open(): Upsert the user's latest feed-open into the nudge queue, keyed by contactid.

Dependencies:
    - app.db.client: Async database client
    - app.core.logging: Centralised logging infrastructure

Updates:
    05-Jun-2026: Module created with Preferences, get(), can_send(), set_whatsapp_stopped(), touch_feed_last_seen().
    08-Jun-2026: Added record_feed_open() for the My Circle feed nudge queue.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import logger
from app.db.client import Database


@dataclass
class Preferences:
    push_enabled: bool = True
    whatsapp_opt_in: bool = False
    inapp_enabled: bool = True
    stopped: bool = False


async def get(userprofileid: str) -> Preferences:
    """Return a userprofile's stored notification preferences, or defaults when no row exists."""
    row = await Database.fetchrow(
        "SELECT * FROM notifications.etown_notification_preferences WHERE userprofileid = $1",
        userprofileid,
    )
    if not row:
        return Preferences()
    return Preferences(
        push_enabled=bool(row["push_enabled"]),
        whatsapp_opt_in=bool(row["whatsapp_opt_in"]),
        inapp_enabled=bool(row["inapp_enabled"]),
        stopped=row["whatsapp_stopped_at"] is not None,
    )


def can_send(prefs: Preferences, channel: str) -> bool:
    """Return True when the preferences allow a channel; unknown channels are always allowed."""
    if channel == "push":
        return prefs.push_enabled
    if channel == "whatsapp":
        return prefs.whatsapp_opt_in and not prefs.stopped
    if channel == "inapp":
        return prefs.inapp_enabled
    return True


async def set_whatsapp_stopped(userprofileid: str) -> None:
    """Upsert a userprofile's preferences to opt out of WhatsApp and stamp whatsapp_stopped_at."""
    await Database.execute(
        """INSERT INTO notifications.etown_notification_preferences
               (userprofileid, whatsapp_opt_in, whatsapp_stopped_at)
           VALUES ($1, false, now())
           ON CONFLICT (userprofileid) DO UPDATE
               SET whatsapp_opt_in = false,
                   whatsapp_stopped_at = now(),
                   updated_at = now()""",
        userprofileid,
    )


async def touch_feed_last_seen(userprofileid: str) -> None:
    """Upsert the My Circle feed last-seen timestamp for a userprofile to now."""
    await Database.execute(
        """INSERT INTO notifications.etown_feed_last_seen (userprofileid, last_seen_feed_at)
           VALUES ($1, now())
           ON CONFLICT (userprofileid) DO UPDATE SET last_seen_feed_at = now()""",
        userprofileid,
    )


async def record_feed_open(userprofileid: str) -> None:
    """Upsert the profile's latest feed open into the contact-keyed nudge queue, logging instead of raising."""
    try:
        contactid = await Database.fetchval(
            "SELECT contactid FROM public.etown_userprofile WHERE userprofileid = $1",
            userprofileid,
        )
        if not contactid:
            logger.debug("record_feed_open: skipping enqueue for %s — contactid not set yet", userprofileid)
            return
        await Database.execute(
            """INSERT INTO notifications.etown_feed_nudge_queue (contactid, userprofileid, opened_at)
               VALUES ($1, $2, now())
               ON CONFLICT (contactid) DO UPDATE
                   SET opened_at     = now(),
                       userprofileid = $2""",
            contactid, userprofileid,
        )
    except Exception:
        logger.warning("record_feed_open failed for %s", userprofileid, exc_info=True)
