"""Community View Retention Worker - deletes community view rows older than the retention window.

Background worker that deletes, in batches, public.etown_communityview rows created more than COMMUNITY_VIEW_RETENTION_DAYS ago, leaving the view_count stored on etown_need, etown_update and etown_event untouched. It is a no-op unless COMMUNITY_VIEW_RETENTION_ENABLED is on.

Features:
    - Age-only window on created_at, COMMUNITY_VIEW_RETENTION_DAYS (default 30)
    - Post view_count columns are never read or written, so totals never drop
    - Today's rows are always kept, so the once-a-day public view rule is unaffected
    - Batched deletes repeated until a short batch, so a backlog cannot hold a long lock
    - Feature-flagged, and a no-op when the flag is off

Functions:
    _rowcount(): Parse the deleted-row count out of an asyncpg status string.
    prune_community_views(): Delete one batch of expired view rows; returns the count.
    _retention_loop(): Prune expired view rows every interval until cancelled.
    start_community_view_retention_worker(): Start the loop.
    stop_community_view_retention_worker(): Cancel and await the loop.

Dependencies:
    - app.db.client.Database: Shared asyncpg pool.
    - app.core.config.settings: COMMUNITY_VIEW_RETENTION_ENABLED and COMMUNITY_VIEW_RETENTION_DAYS.
    - app.core.logging: Centralised logger.
    - public.etown_communityview: The view log being pruned.

Updates:
    12-Oct-2026: Created. Deletes etown_communityview rows past the retention window, keeping post view counts; batches by ctid, not viewid.
"""

import asyncio
from typing import Optional

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database

_retention_task: Optional[asyncio.Task] = None

COMMUNITY_VIEW_RETENTION_INTERVAL_SECONDS = 86400

COMMUNITY_VIEW_RETENTION_BATCH = 5000

_PRUNE_VIEWS_SQL = f"""
DELETE FROM public.etown_communityview
 WHERE ctid = ANY(ARRAY(
     SELECT ctid FROM public.etown_communityview
      WHERE created_at < now() - make_interval(days => $1::int)
      LIMIT {COMMUNITY_VIEW_RETENTION_BATCH}
 ))
"""


def _rowcount(status: object) -> int:
    """Return the row count from an asyncpg command status string, or 0 if unreadable."""
    try:
        return int(str(status).rsplit(" ", 1)[1])
    except (IndexError, ValueError):
        logger.warning("community view retention: could not read a row count out of status %r", status)
        return 0


async def prune_community_views() -> int:
    """Delete one batch of view rows older than COMMUNITY_VIEW_RETENTION_DAYS and return the rows deleted."""
    days = max(int(settings.COMMUNITY_VIEW_RETENTION_DAYS), 1)
    return _rowcount(await Database.execute(_PRUNE_VIEWS_SQL, days))


async def _retention_loop() -> None:
    """Prune expired view rows batch by batch, then sleep COMMUNITY_VIEW_RETENTION_INTERVAL_SECONDS, until cancelled."""
    while True:
        try:
            total = 0
            while True:
                deleted = await prune_community_views()
                total += deleted
                if deleted < COMMUNITY_VIEW_RETENTION_BATCH:
                    break
            if total:
                logger.info("Community view retention pruned %d view row(s)", total)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Community view retention cycle failed: %s", exc, exc_info=True)
        await asyncio.sleep(COMMUNITY_VIEW_RETENTION_INTERVAL_SECONDS)


async def start_community_view_retention_worker() -> None:
    """Start the community view retention loop task unless COMMUNITY_VIEW_RETENTION_ENABLED is off."""
    global _retention_task
    if not getattr(settings, "COMMUNITY_VIEW_RETENTION_ENABLED", False):
        logger.info("Community view retention worker not started (COMMUNITY_VIEW_RETENTION_ENABLED is off)")
        return
    _retention_task = asyncio.create_task(_retention_loop())
    logger.info(
        "Community view retention worker started (keep %dd, batch %d)",
        settings.COMMUNITY_VIEW_RETENTION_DAYS, COMMUNITY_VIEW_RETENTION_BATCH,
    )


async def stop_community_view_retention_worker() -> None:
    """Cancel and await the community view retention worker task."""
    global _retention_task
    if _retention_task and not _retention_task.done():
        _retention_task.cancel()
        try:
            await _retention_task
        except asyncio.CancelledError:
            logger.debug("community view retention worker cancelled during shutdown")
    _retention_task = None
    logger.info("Community view retention worker stopped")
