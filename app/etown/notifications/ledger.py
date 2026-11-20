"""Notification Ledger Utilities - send ledger for cap accounting, idempotent claims and read receipts.

Notification data layer over notifications.etown_notification_ledger: counts sends for the per-user and per-sender caps by IST calendar date, atomically claims a dedupe_key so only one caller sends, and records WhatsApp message ids and opens.

Features:
    - Cap accounting: count_* functions back the per-user and per-sender daily/weekly caps
    - Atomic idempotency: claim_send() lets exactly one caller dispatch a dedupe_key
    - Re-claim of non-terminal rows: stale 'sending', 'failed' or 'suppressed_*' rows re-claimable
    - Fail-open on DB errors: claim_send()/claim_once() default to allowing the send
    - Read-receipt support: set_wamid()/mark_opened() match open events to the ledger row

Classes:
    No classes defined (module exposes the ledger read/write functions below)

Functions:
    count_today(): Count sent notifications for a user on a channel today (IST date), optionally of given types
    count_today_by_contact(): Same as count_today() but keyed by contactid
    count_business_week(): Count WhatsApp sends from one business to a user in the last 7 days
    last_open_within_days(): Whether the user opened at least one push in the last N days
    record(): Insert one ledger row; the dedupe_key unique index makes this idempotent
    was_sent(): Whether a ledger row with this dedupe_key exists with status 'sent'
    claim_send(): Atomically claim delivery of a dedupe_key; True iff the caller now owns it
    finalize(): Mark a previously claim_send()-claimed row terminal ('sent' or 'failed')
    claim_once(): One-shot atomic claim-and-mark-sent for broadcast-style, no-finalize sends
    mark_opened(): Set opened_at for the ledger row identified by a dedupe_key
    set_wamid(): Record the outbound WhatsApp message id on the ledger row for read-receipt lookup
    lowest_digest_priority_today(): Highest numeric priority among digest-class sends today, or None

Dependencies:
    - app.db.client.Database: all reads/writes against notifications.etown_notification_ledger
    - app.core.logging: Centralized logging

Updates:
    05-Jun-2026: Module created for ledger-based cap accounting and idempotency.
    26-Jun-2026: Added wamid tracking (set_wamid) to support WhatsApp read-receipt lookups.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

from app.core.logging import logger
from app.db.client import Database

if TYPE_CHECKING:
    from app.etown.notifications.service import GovernanceRequest


async def count_today(userprofileid: str, channel: str, notification_types: Optional[List[str]] = None) -> int:
    """Return how many notifications were sent to a userprofile on a channel today (IST), optionally only of the given types."""
    query = """SELECT count(*) FROM notifications.etown_notification_ledger
           WHERE userprofileid = $1
             AND channel = $2
             AND sent_on = (now() AT TIME ZONE 'Asia/Kolkata')::date
             AND status = 'sent'"""
    if notification_types is None:
        return await Database.fetchval(query, userprofileid, channel) or 0
    return await Database.fetchval(
        query + "\n             AND notification_type = ANY($3::text[])",
        userprofileid, channel, list(notification_types),
    ) or 0


async def count_today_by_contact(contactid: str, channel: str, notification_types: Optional[List[str]] = None) -> int:
    """Return how many notifications were sent to a contact, across all its profiles, on a channel today (IST), optionally only of the given types."""
    query = """SELECT count(*) FROM notifications.etown_notification_ledger
           WHERE contactid = $1
             AND channel = $2
             AND sent_on = (now() AT TIME ZONE 'Asia/Kolkata')::date
             AND status = 'sent'"""
    if notification_types is None:
        return await Database.fetchval(query, contactid, channel) or 0
    return await Database.fetchval(
        query + "\n             AND notification_type = ANY($3::text[])",
        contactid, channel, list(notification_types),
    ) or 0


async def count_business_week(userprofileid: str, sender_businessid: str) -> int:
    """Return how many WhatsApp messages a business sent to a userprofile in the last 7 days."""
    return await Database.fetchval(
        """SELECT count(*) FROM notifications.etown_notification_ledger
           WHERE userprofileid = $1
             AND sender_businessid = $2
             AND channel = 'whatsapp'
             AND created_at >= now() - interval '7 days'
             AND status = 'sent'""",
        userprofileid, sender_businessid,
    ) or 0


async def last_open_within_days(userprofileid: str, days: int) -> bool:
    """Return True if the userprofile opened at least one push in the last ``days`` days."""
    return bool(await Database.fetchval(
        """SELECT 1 FROM notifications.etown_notification_ledger
           WHERE userprofileid = $1
             AND channel = 'push'
             AND opened_at IS NOT NULL
             AND opened_at >= now() - ($2 || ' days')::interval
           LIMIT 1""",
        userprofileid, str(days),
    ))


async def record(req: "GovernanceRequest", status: str, reason: Optional[str] = None) -> None:
    """Insert one ledger row, ignoring a duplicate dedupe_key and logging any error."""
    try:
        await Database.execute(
            """INSERT INTO notifications.etown_notification_ledger
               (userprofileid, contactid, channel, notification_type, feature,
                sender_businessid, priority, dedupe_key, status, suppression_reason,
                sent_on)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                       (now() AT TIME ZONE 'Asia/Kolkata')::date)
               ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING""",
            req.userprofileid,
            req.contactid,
            req.channel,
            req.notification_type,
            req.feature,
            req.sender_businessid,
            req.priority,
            req.dedupe_key,
            status,
            reason,
        )
    except Exception as exc:
        logger.error(
            "ledger.record failed (type=%s status=%s): %s",
            req.notification_type, status, exc,
         exc_info=True)

async def was_sent(dedupe_key: str) -> bool:
    """Return True if a ledger row with this dedupe_key has status 'sent'."""
    if not dedupe_key:
        return False
    return bool(await Database.fetchval(
        """SELECT 1 FROM notifications.etown_notification_ledger
           WHERE dedupe_key = $1 AND status = 'sent' LIMIT 1""",
        dedupe_key,
    ))

_SENDING_STALE_MINUTES = 10


async def claim_send(req: "GovernanceRequest") -> bool:
    """Atomically claim a dedupe_key as 'sending' and return True if this caller owns it; fail open on errors."""
    if not req.dedupe_key:
        return True
    try:
        row = await Database.fetchval(
            """INSERT INTO notifications.etown_notification_ledger
                   (userprofileid, contactid, channel, notification_type, feature,
                    sender_businessid, priority, dedupe_key, status, sent_on)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'sending',
                       (now() AT TIME ZONE 'Asia/Kolkata')::date)
               ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL
               DO UPDATE SET status = 'sending',
                             created_at = now(),
                             suppression_reason = NULL,
                             sent_on = (now() AT TIME ZONE 'Asia/Kolkata')::date
               WHERE etown_notification_ledger.status NOT IN ('sent', 'sending')
                  OR (etown_notification_ledger.status = 'sending'
                      AND etown_notification_ledger.created_at
                          < now() - ($9 || ' minutes')::interval)
               RETURNING id""",
            req.userprofileid, req.contactid, req.channel, req.notification_type,
            req.feature, req.sender_businessid, req.priority, req.dedupe_key,
            str(_SENDING_STALE_MINUTES),
        )
        return row is not None
    except Exception as exc:
        logger.error(
            "ledger.claim_send failed (type=%s key=%s): %s — proceeding without claim",
            req.notification_type, req.dedupe_key, exc,
         exc_info=True)
        return True


async def finalize(dedupe_key: str, status: str, reason: Optional[str] = None) -> None:
    """Set the terminal status and reason on a claimed ledger row, logging any error."""
    if not dedupe_key:
        return
    try:
        await Database.execute(
            """UPDATE notifications.etown_notification_ledger
               SET status = $2, suppression_reason = $3
               WHERE dedupe_key = $1""",
            dedupe_key, status, reason,
        )
    except Exception as exc:
        logger.error(
            "ledger.finalize failed (key=%s status=%s): %s", dedupe_key, status, exc,
         exc_info=True)


async def claim_once(
    dedupe_key: str,
    *,
    notification_type: str,
    channel: str = "push",
    userprofileid: str = "broadcast",
) -> bool:
    """Insert a 'sent' ledger row for a dedupe_key and return True if it was new; fail open on errors."""
    if not dedupe_key:
        return True
    try:
        row = await Database.fetchval(
            """INSERT INTO notifications.etown_notification_ledger
                   (userprofileid, channel, notification_type, dedupe_key, status, sent_on)
               VALUES ($1, $2, $3, $4, 'sent', (now() AT TIME ZONE 'Asia/Kolkata')::date)
               ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
               RETURNING id""",
            userprofileid, channel, notification_type, dedupe_key,
        )
        return row is not None
    except Exception as exc:
        logger.error("ledger.claim_once failed (key=%s): %s — proceeding", dedupe_key, exc, exc_info=True)
        return True


async def mark_opened(dedupe_key: str) -> None:
    """Set opened_at on the ledger row for a dedupe_key if it is not already set."""
    try:
        await Database.execute(
            """UPDATE notifications.etown_notification_ledger
               SET opened_at = now()
               WHERE dedupe_key = $1 AND opened_at IS NULL""",
            dedupe_key,
        )
    except Exception as exc:
        logger.warning("ledger.mark_opened failed (key=%s): %s", dedupe_key, exc)


async def set_wamid(dedupe_key: str, wamid: str) -> None:
    """Record the outbound WhatsApp wamid on the ledger row for a dedupe_key if none is set."""
    if not dedupe_key or not wamid:
        return
    try:
        await Database.execute(
            """UPDATE notifications.etown_notification_ledger
               SET wamid = $2
               WHERE dedupe_key = $1 AND wamid IS NULL""",
            dedupe_key, wamid,
        )
    except Exception as exc:
        logger.warning("ledger.set_wamid failed (key=%s wamid=%s): %s", dedupe_key, wamid, exc)


async def lowest_digest_priority_today(userprofileid: str) -> Optional[int]:
    """Return the highest numeric priority among digest-class pushes sent to a userprofile today, or None."""
    return await Database.fetchval(
        """SELECT max(priority) FROM notifications.etown_notification_ledger
           WHERE userprofileid = $1
             AND channel = 'push'
             AND sent_on = (now() AT TIME ZONE 'Asia/Kolkata')::date
             AND status = 'sent'
             AND priority >= 40
        """,
        userprofileid,
    )
