"""Campaign Guard Helpers - once-only campaign claims and per-recipient dedupe keys.

Notification helpers that stop a broadcast campaign from starting twice by claiming its id in notifications.etown_notification_campaign_runs, build the fingerprint key that keeps one recipient from receiving the same campaign twice, and record completion time and sent count.

Features:
    - Once-Only Campaign Claim: INSERT ... ON CONFLICT DO NOTHING; only the first caller gets True
    - Per-Recipient Dedupe Key: canonical fingerprint key matched by ledger/queue unique indexes
    - Completion Bookkeeping: best-effort recording of completion time + sent count

Classes:
    No classes defined (module exposes three standalone helper functions)

Functions:
    claim_campaign(): Claim a campaign id for sending; True only for the first caller
    recipient_dedupe_key(): Canonical per-recipient dedupe key for a campaign send
    mark_campaign_done(): Best-effort: record completion time + sent count (never raises)

Dependencies:
    - app.db.client: Async PostgreSQL access to notifications.etown_notification_campaign_runs
    - app.etown.notifications.fingerprint: Shared dedupe-key helper (fingerprint.once)
    - app.core.logging: Centralized application logging

Updates:
    11-Jun-2026: Module created to guard against duplicate campaign blasts.
    26-Aug-2026: Logged silent exception handlers with tracebacks.
"""

from __future__ import annotations

from typing import Optional

from app.core.logging import logger
from app.db.client import Database
from app.etown.notifications import fingerprint


async def claim_campaign(campaign_id: str) -> bool:
    """Insert a campaign run row and return True only for the first claimant; False on conflict or error."""
    if not campaign_id:
        return False
    try:
        res = await Database.execute(
            """INSERT INTO notifications.etown_notification_campaign_runs (campaign_id)
               VALUES ($1) ON CONFLICT (campaign_id) DO NOTHING""",
            campaign_id,
        )
        try:
            return int(str(res).split()[-1]) == 1
        except (ValueError, IndexError):
            logger.warning("campaigns: could not read a row count out of status %r", res)
            return False
    except Exception as exc:
        logger.error("claim_campaign failed (id=%s): %s", campaign_id, exc, exc_info=True)
        return False


def recipient_dedupe_key(campaign_id: str, userprofileid: str) -> str:
    """Return the per-recipient dedupe fingerprint for a campaign send."""
    return fingerprint.once("campaign", campaign_id, userprofileid)


async def mark_campaign_done(campaign_id: str, sent: Optional[int] = None) -> None:
    """Stamp a campaign run's completion time and sent count, logging instead of raising on failure."""
    try:
        await Database.execute(
            """UPDATE notifications.etown_notification_campaign_runs
               SET completed_at = now(), sent_count = COALESCE($2, sent_count)
               WHERE campaign_id = $1""",
            campaign_id, sent,
        )
    except Exception as exc:
        logger.warning("mark_campaign_done failed (id=%s): %s", campaign_id, exc)
