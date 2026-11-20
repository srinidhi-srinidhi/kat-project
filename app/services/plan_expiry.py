"""Plan Expiry Worker - retires subscriptions whose paid term has ended.

Background worker that moves lapsed etown_provider and etown_member subscriptions onto free, clears their plan dates, marks them expired, records each transition in notifications.etown_plan_change, expires lapsed etown_subscription_history rows and mirrors to Dataverse; enforcement stays with the read-time effective-plan rule.

Features:
    - Retires only rows the read rule already treats as lapsed, so the two cannot disagree
    - Clears the tier and both dates, so no raw reader is left showing a plan that has ended
    - Mirrors every retirement to Dataverse, so a lapse is not recorded in Postgres alone
    - Prunes each retired provider's coverage towns to the free tier, keeping the home town
    - Writes one history row per retirement, attributed to the worker
    - Batched, so a backlog cannot hold a long transaction on etown_provider
    - Feature-flagged off by default, and a no-op when the flag is off
    - Records, never enforces: _EFFECTIVE_PLAN_CODE already treats a lapsed plan as free on read
    - Lapsed tier kept in history: from_plan_code on etown_plan_change names the tier that ended
    - Lapsed etown_subscription_history rows marked expired on the same date rule

Functions:
    retire_expired_plans(): Retire one batch; returns how many rows changed.
    _expiry_loop(): Runs retire_expired_plans() on the configured interval.
    start_plan_expiry_worker(): Start the loop.
    stop_plan_expiry_worker(): Cancel and await the loop.

Dependencies:
    - app.db.client.Database: Shared asyncpg pool.
    - app.core.config.settings: PLAN_EXPIRY_ENABLED, PLAN_EXPIRY_INTERVAL_SECONDS.
    - app.core.logging: Centralized logging.
    - app.etown.plans.plans_syncing: Mirrors each retirement to Dataverse.
    - app.etown.plans.plans_utils: invalidate_plan_cache() after a retirement (lazy).
    - app.etown.mytown.mytown_utils: prune_coverage_towns_to_plan() (lazy).
    - public.etown_provider: The rows retired.
    - public.etown_member: The member copy of the subscription, retired and mirrored to ets_members.
    - notifications.etown_plan_change: Where each retirement is recorded.
    - public.etown_subscription_history: Active rows past plan_expires_on are marked expired.

Updates:
    20-Aug-2026: Created. Retires lapsed subscriptions and records the transition.
    24-Aug-2026: Each retired provider's plan columns are mirrored to Dataverse.
    26-Aug-2026: Logged silent exception handlers with tracebacks.
    01-Sep-2026: Retirement moves plan_code to free and clears both dates.
    03-Sep-2026: Retirement drops paid coverage towns.
    17-Sep-2026: Added _RETIRE_MEMBERS_SQL to retire member plan columns.
    27-Oct-2026: Added _EXPIRE_SUBSCRIPTIONS_SQL to expire lapsed subscription history rows.
"""

import asyncio
from typing import Optional

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.plans.plans_syncing import (
    sync_member_plan_to_dataverse,
    sync_provider_plan_to_dataverse,
)

_expiry_task: Optional[asyncio.Task] = None

PLAN_EXPIRY_INTERVAL_SECONDS = 3600

PLAN_EXPIRY_BATCH = 500

_RETIRE_SQL = f"""
WITH lapsed AS (
    SELECT userprofileid, providerid, contactid, plan_code, subscription_status,
           plan_started_on, plan_expires_on
      FROM etown_provider
     WHERE subscription_status = 'active'
       AND plan_code <> 'free'
       AND plan_expires_on IS NOT NULL
       AND plan_expires_on < current_date
       AND COALESCE(deleted, 'false') <> 'true'
     ORDER BY plan_expires_on
     LIMIT {PLAN_EXPIRY_BATCH}
),
retired AS (
    UPDATE etown_provider p
       SET subscription_status = 'expired',
           plan_code = 'free',
           plan_started_on = NULL,
           plan_expires_on = NULL
      FROM lapsed l
     WHERE p.userprofileid = l.userprofileid
    RETURNING l.userprofileid, l.providerid, l.contactid, l.plan_code,
              l.subscription_status, l.plan_started_on, l.plan_expires_on
)
INSERT INTO notifications.etown_plan_change
    (userprofileid, providerid, contactid, from_plan_code, to_plan_code,
     from_status, to_status, plan_started_on, plan_expires_on, changed_by, reason)
SELECT userprofileid, providerid, contactid, plan_code, 'free',
       subscription_status, 'expired', plan_started_on, plan_expires_on,
       'plan_expiry_worker', 'plan_expires_on passed'
  FROM retired
RETURNING providerid, userprofileid, contactid
"""

_RETIRE_MEMBERS_SQL = f"""
UPDATE etown_member m
   SET subscription_status = 'expired',
       plan_code = 'free',
       plan_started_on = NULL,
       plan_expires_on = NULL
 WHERE m.memberid IN (
        SELECT memberid
          FROM etown_member
         WHERE subscription_status = 'active'
           AND plan_code <> 'free'
           AND plan_expires_on IS NOT NULL
           AND plan_expires_on < current_date
           AND COALESCE(deleted, 'false') <> 'true'
         ORDER BY plan_expires_on
         LIMIT {PLAN_EXPIRY_BATCH}
       )
RETURNING m.memberid, m.contactid
"""

_EXPIRE_SUBSCRIPTIONS_SQL = """
UPDATE public.etown_subscription_history
   SET status = 'expired', updated_at = now()
 WHERE status = 'active'
   AND plan_expires_on IS NOT NULL
   AND plan_expires_on < current_date
"""


async def retire_expired_plans() -> int:
    """Retire one batch of lapsed provider and member plans, mirror them to Dataverse and prune provider coverage towns.

    Returns:
        Number of providers retired; retired member rows are not counted.
    """
    rows = await Database.fetch(_RETIRE_SQL)

    try:
        member_rows = await Database.fetch(_RETIRE_MEMBERS_SQL)
    except Exception as exc:
        logger.error("Plan expiry worker could not retire member rows: %s", exc, exc_info=True)
        member_rows = []

    try:
        await Database.execute(_EXPIRE_SUBSCRIPTIONS_SQL)
    except Exception as exc:
        logger.error("Plan expiry worker could not expire subscription history: %s", exc, exc_info=True)

    retired = len(rows)
    if member_rows:
        await sync_member_plan_to_dataverse(
            [row["contactid"] for row in member_rows if row["contactid"]]
        )
        logger.info(
            "Plan expiry worker retired %d lapsed member subscription(s)", len(member_rows)
        )

    if retired:
        from app.etown.plans.plans_utils import invalidate_plan_cache
        invalidate_plan_cache()

        await sync_provider_plan_to_dataverse(
            [row["providerid"] for row in rows if row["providerid"]]
        )

        from app.etown.mytown.mytown_utils import prune_coverage_towns_to_plan
        for row in rows:
            if not row["providerid"]:
                continue
            try:
                await prune_coverage_towns_to_plan(row["providerid"], row["userprofileid"])
            except Exception as exc:
                logger.error(
                    "Plan expiry worker could not prune coverage towns for provider %s: %s",
                    row["providerid"], exc, exc_info=True,
                )
    return retired


async def _expiry_loop() -> None:
    """Run retire_expired_plans forever at PLAN_EXPIRY_INTERVAL_SECONDS, logging cycle failures."""
    while True:
        try:
            retired = await retire_expired_plans()
            if retired:
                logger.info("Plan expiry worker retired %d lapsed subscription(s)", retired)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Plan expiry worker cycle failed: %s", exc, exc_info=True)
        await asyncio.sleep(PLAN_EXPIRY_INTERVAL_SECONDS)


async def start_plan_expiry_worker() -> None:
    """Start the plan expiry background task unless PLAN_EXPIRY_ENABLED is off."""
    global _expiry_task
    if not getattr(settings, "PLAN_EXPIRY_ENABLED", False):
        logger.info("Plan expiry worker not started (PLAN_EXPIRY_ENABLED is off)")
        return
    _expiry_task = asyncio.create_task(_expiry_loop())
    logger.info(
        "Plan expiry background worker started (interval: %ds, batch: %d)",
        PLAN_EXPIRY_INTERVAL_SECONDS, PLAN_EXPIRY_BATCH,
    )


async def stop_plan_expiry_worker() -> None:
    """Cancel and await the plan expiry background worker task."""
    global _expiry_task
    if _expiry_task and not _expiry_task.done():
        _expiry_task.cancel()
        try:
            await _expiry_task
        except asyncio.CancelledError:
            logger.debug("plan expiry worker cancelled during shutdown")
    _expiry_task = None
    logger.info("Plan expiry background worker stopped")
