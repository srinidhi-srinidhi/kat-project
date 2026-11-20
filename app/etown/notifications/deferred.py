"""Quiet-Hours Deferral Queue - stores quiet-hours notifications and replays them in the morning.

Notification helper that queues a notification suppressed during quiet hours in notifications.etown_notification_deferred when its type has defer_if_quiet set, and replays due rows through the governed send path when the scheduler's deferred sweep runs.

Features:
    - Deferred storage: enqueue() writes a pending row due at the next 08:00 IST
    - Governance re-evaluation on replay: replay_due() re-checks caps and opt-out at delivery
    - Quiet window: 21:00-08:00 IST, set by QUIET_START_HOUR / QUIET_END_HOUR in rules.py

Classes:
    No classes defined (module exposes enqueue/replay_due and a small time helper)

Functions:
    _next_8am_ist(): The next 08:00 IST as a timezone-aware datetime
    enqueue(): Store a deferred notification for replay at the next 08:00 IST
    replay_due(): Replay all deferred items whose deliver_after has passed

Dependencies:
    - app.db.client.Database: notifications.etown_notification_deferred reads/writes
    - app.etown.notifications.service (GovernanceRequest, send): replay target, imported lazily
    - app.core.logging: Centralized logging

Updates:
    05-Jun-2026: Module created for the quiet-hours deferral queue and its 08:00 IST replay sweep.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app.core.logging import logger
from app.db.client import Database

if TYPE_CHECKING:
    from app.etown.notifications.service import GovernanceRequest

IST = timezone(timedelta(hours=5, minutes=30))


def _next_8am_ist() -> datetime:
    """Return the next 08:00 IST as a timezone-aware datetime."""
    now = datetime.now(IST)
    target = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if now.hour >= 8:
        target += timedelta(days=1)
    return target


async def enqueue(req: "GovernanceRequest") -> None:
    """Insert a pending quiet-hours row due at the next 08:00 IST, logging instead of raising on failure."""
    try:
        await Database.execute(
            """INSERT INTO notifications.etown_notification_deferred
               (userprofileid, payload, notification_type, channel, priority,
                deferred_reason, deliver_after, dedupe_key)
               VALUES ($1, $2, $3, $4, $5, 'quiet_hours', $6, $7)""",
            req.userprofileid,
            json.dumps(req.ctx),
            req.notification_type,
            req.channel or "push",
            req.priority,
            _next_8am_ist(),
            req.dedupe_key,
        )
    except Exception as exc:
        logger.warning(
            "deferred.enqueue failed (type=%s user=%s): %s",
            req.notification_type, req.userprofileid, exc,
        )


async def replay_due() -> int:
    """Resend up to 500 due pending rows through the governed send path, mark them delivered and return the count."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send

    rows = await Database.fetch(
        """SELECT id, userprofileid, payload, notification_type, channel, priority, dedupe_key
           FROM notifications.etown_notification_deferred
           WHERE status = 'pending' AND deliver_after <= now()
           ORDER BY priority ASC, created_at ASC
           LIMIT 500""",
    )

    if not rows:
        return 0

    logger.info("deferred.replay_due: processing %d item(s)", len(rows))
    processed = 0

    for row in rows:
        try:
            ctx = json.loads(row["payload"]) if row["payload"] else {}
            req = GovernanceRequest(
                userprofileid=row["userprofileid"],
                notification_type=row["notification_type"],
                channel=row["channel"],
                priority=row["priority"],
                dedupe_key=row["dedupe_key"],
                ctx=ctx,
            )
            await gov_send(req)
            await Database.execute(
                "UPDATE notifications.etown_notification_deferred SET status = 'delivered' WHERE id = $1",
                row["id"],
            )
            processed += 1
        except Exception as exc:
            logger.error(
                "deferred.replay_due: failed for id=%s: %s", row["id"], exc, exc_info=True
            )

    logger.info("deferred.replay_due: delivered %d item(s)", processed)
    return processed
