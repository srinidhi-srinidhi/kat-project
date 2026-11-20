"""Offer Expiry Worker - marks lapsed offers as Expired every hour.

Background asyncio loop started unconditionally from the FastAPI lifespan: one UPDATE sets validity to Expired and isactive to false on etown_offers rows past their scheduledendtime, or older than 24 hours when no end time is set.

Features:
    - Periodic Expiry: Hourly background task that marks offers as Expired
    - Dual Expiry Logic: Handles both scheduled-end-time and age-based (24h) expiry
    - Safe Startup/Shutdown: Lifecycle hooks for FastAPI lifespan integration
    - Idempotent Updates: Only updates rows where validity != 'Expired'
    - Bounded Scan: Only offers ending or created within the last 90 days are checked
    - Performance Logging: Logs count of rows updated per run
    - Error Resilience: Catches and logs exceptions without crashing the worker loop

Classes:
    No classes defined (module uses function-based background worker pattern)

Functions:
    run_expiry_job(): Execute one expiry cycle and return count of expired offers
    _expiry_loop(): Background loop running run_expiry_job() every EXPIRY_INTERVAL_SECONDS
    start_offer_expiry_worker(): Start the background expiry worker task
    stop_offer_expiry_worker(): Stop and cancel the running expiry worker task

Dependencies:
    - asyncio: Background task scheduling and sleep between cycles
    - app.core.logging: Centralized logging for expiry operations
    - app.db.client: Async PostgreSQL database access

Updates:
    26-May-2026: Added Features, Classes, Functions, Dependencies, Updates sections to complete module docstring
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import asyncio

from app.core.logging import logger
from app.db.client import Database

EXPIRY_INTERVAL_SECONDS = 3600

_expiry_task: asyncio.Task | None = None

_EXPIRY_SQL = """
    UPDATE etown_offers
    SET validity     = 'Expired',
        isactive     = 'false',
        modifieddate = NOW()
    WHERE validity <> 'Expired'
      AND COALESCE(scheduledendtime, createdon) >= NOW() AT TIME ZONE 'UTC' - INTERVAL '90 days'
      AND (
          (scheduledendtime IS NOT NULL
           AND scheduledendtime < NOW() AT TIME ZONE 'UTC')
          OR
          (scheduledendtime IS NULL
           AND createdon IS NOT NULL
           AND createdon < NOW() AT TIME ZONE 'UTC' - INTERVAL '24 hours')
      )
"""


async def run_expiry_job() -> int:
    """Mark lapsed offers Expired and inactive, returning the number of rows updated."""
    result = await Database.execute(_EXPIRY_SQL)
    try:
        count = int(str(result).split()[-1])
    except (ValueError, IndexError):
        logger.warning("offer expiry: could not read a row count out of status %r", result)
        count = 0
    return count


async def _expiry_loop() -> None:
    """Run run_expiry_job every EXPIRY_INTERVAL_SECONDS forever, logging any error."""
    while True:
        try:
            count = await run_expiry_job()
            if count:
                logger.info("Offer expiry worker: marked %d offer(s) as Expired in DB", count)
        except Exception as exc:
            logger.error("Offer expiry worker error: %s", exc, exc_info=True)
        await asyncio.sleep(EXPIRY_INTERVAL_SECONDS)


async def start_offer_expiry_worker() -> None:
    """Start the offer expiry background worker task."""
    global _expiry_task
    _expiry_task = asyncio.create_task(_expiry_loop())
    logger.info(
        "Offer expiry background worker started (interval: %ds)", EXPIRY_INTERVAL_SECONDS
    )


async def stop_offer_expiry_worker() -> None:
    """Cancel and await the offer expiry background worker task."""
    global _expiry_task
    if _expiry_task and not _expiry_task.done():
        _expiry_task.cancel()
        try:
            await _expiry_task
        except asyncio.CancelledError:
            logger.debug("offer expiry worker cancelled during shutdown")
    _expiry_task = None
    logger.info("Offer expiry background worker stopped")
