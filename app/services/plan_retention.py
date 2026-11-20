"""Plan Retention Worker - prunes expired plan usage counters and orphaned conversation claims.

Background worker that deletes, in batches, expired daily and monthly rows from notifications.etown_provider_plan_usage and etown_provider_plan_conversation claims whose conversation no longer exists in etownuserchaatiehistory. It is a no-op unless PLAN_RETENTION_ENABLED is on.

Features:
    - Separate windows for daily and monthly counters, reflecting their volumes
    - Never touches a live period, so no sweep can reset a cap mid-month or mid-day
    - Claim rows pruned only once their conversation is gone, never by age alone
    - Batched deletes, so a first run against a backlog cannot hold a long lock
    - Feature-flagged off by default, and a no-op when the flag is off

Functions:
    _rowcount(): Parse the deleted-row count out of an asyncpg status string.
    prune_usage_counters(): Delete one batch of expired counters; returns the count.
    prune_conversation_claims(): Delete one batch of orphaned claims; returns the count.
    _retention_loop(): Prune counters and claims every interval until cancelled.
    start_plan_retention_worker(): Start the loop.
    stop_plan_retention_worker(): Cancel and await the loop.

Dependencies:
    - app.db.client.Database: Shared asyncpg pool.
    - app.core.config.settings: PLAN_RETENTION_ENABLED.
    - app.etown.plans.plans_utils.DAILY_METRICS: Metrics pruned on the daily window.
    - app.core.logging: Centralised logger.
    - notifications.etown_provider_plan_usage / etown_provider_plan_conversation.
    - public.etownuserchaatiehistory: Decides whether a claim still guards anything.

Updates:
    20-Aug-2026: Created. Prunes spent counters; claims only once their conversation is gone.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import asyncio
from typing import Optional

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database

_retention_task: Optional[asyncio.Task] = None

PLAN_RETENTION_INTERVAL_SECONDS = 86400

MONTHLY_RETENTION_MONTHS = 13

DAILY_RETENTION_DAYS = 90

PLAN_RETENTION_BATCH = 5000

_PRUNE_USAGE_SQL = f"""
DELETE FROM notifications.etown_provider_plan_usage
 WHERE ctid IN (
     SELECT ctid FROM notifications.etown_provider_plan_usage
      WHERE (
              (metric = ANY($1::text[])
               AND period_start < (now() AT TIME ZONE 'Asia/Kolkata')::date
                                  - INTERVAL '{DAILY_RETENTION_DAYS} days')
           OR (NOT (metric = ANY($1::text[]))
               AND period_start < date_trunc('month', current_date)::date
                                  - INTERVAL '{MONTHLY_RETENTION_MONTHS} months')
            )
      LIMIT {PLAN_RETENTION_BATCH}
 )
"""

_PRUNE_CLAIMS_SQL = f"""
DELETE FROM notifications.etown_provider_plan_conversation c
 WHERE c.ctid IN (
     SELECT c2.ctid
       FROM notifications.etown_provider_plan_conversation c2
      WHERE c2.charged_at < now() - INTERVAL '30 days'
        AND NOT EXISTS (
                SELECT 1 FROM public.etownuserchaatiehistory h
                 WHERE h.conversation_id = c2.conversation_id
            )
      LIMIT {PLAN_RETENTION_BATCH}
 )
"""


def _rowcount(status: object) -> int:
    """Return the row count from an asyncpg command status string, or 0 if unreadable."""
    try:
        return int(str(status).rsplit(" ", 1)[1])
    except (IndexError, ValueError):
        logger.warning("plan retention: could not read a row count out of status %r", status)
        return 0


async def prune_usage_counters() -> int:
    """Delete one batch of expired daily and monthly usage counters and return the rows deleted."""
    from app.etown.plans.plans_utils import DAILY_METRICS
    return _rowcount(await Database.execute(_PRUNE_USAGE_SQL, list(DAILY_METRICS)))


async def prune_conversation_claims() -> int:
    """Delete one batch of claims whose conversation no longer exists and return the rows deleted."""
    return _rowcount(await Database.execute(_PRUNE_CLAIMS_SQL))


async def _retention_loop() -> None:
    """Prune usage counters and orphaned claims forever, sleeping PLAN_RETENTION_INTERVAL_SECONDS between cycles."""
    while True:
        try:
            counters = await prune_usage_counters()
            claims = await prune_conversation_claims()
            if counters or claims:
                logger.info(
                    "Plan retention pruned %d counter row(s) and %d orphaned claim(s)",
                    counters, claims,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Plan retention cycle failed: %s", exc, exc_info=True)
        await asyncio.sleep(PLAN_RETENTION_INTERVAL_SECONDS)


async def start_plan_retention_worker() -> None:
    """Start the plan retention loop task unless PLAN_RETENTION_ENABLED is off."""
    global _retention_task
    if not getattr(settings, "PLAN_RETENTION_ENABLED", False):
        logger.info("Plan retention worker not started (PLAN_RETENTION_ENABLED is off)")
        return
    _retention_task = asyncio.create_task(_retention_loop())
    logger.info(
        "Plan retention worker started (daily keep %dd, monthly keep %dmo, batch %d)",
        DAILY_RETENTION_DAYS, MONTHLY_RETENTION_MONTHS, PLAN_RETENTION_BATCH,
    )


async def stop_plan_retention_worker() -> None:
    """Cancel and await the retention worker task."""
    global _retention_task
    if _retention_task and not _retention_task.done():
        _retention_task.cancel()
        try:
            await _retention_task
        except asyncio.CancelledError:
            logger.debug("plan retention worker cancelled during shutdown")
    _retention_task = None
    logger.info("Plan retention worker stopped")
