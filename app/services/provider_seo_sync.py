"""Provider SEO Sync Worker - drains the queue that mirrors verified providers to ets_providerseometadata.

Background worker that claims providerids queued by the etown_provider, etown_providerservices and etown_allrequests triggers in etown_provider_seo_sync_queue, reconciles each one's ets_providerseometadatas record in Dataverse, and retries failures with backoff.

Features:
    - Fed by triggers that fire only when a mirrored column changes, so every write path is covered
    - Claims with FOR UPDATE SKIP LOCKED and a lease, so several app workers never sync one provider at once
    - Dequeues only if the row was not re-queued mid-sync, so a change made during a sync is not lost
    - Exponential backoff from 1 minute to 1 hour on failure, with the last error kept on the row
    - Waits up to five attempts for the ets_providers record before mirroring without the lookup
    - One Dataverse token per batch
    - Feature-flagged off by default, and a no-op when the flag is off

Functions:
    drain_provider_seo_queue(): Sync one batch of due providers; returns how many were claimed.
    _sync_loop(): Runs drain_provider_seo_queue() on the configured interval.
    start_provider_seo_sync_worker(): Start the loop.
    stop_provider_seo_sync_worker(): Cancel and await the loop.

Dependencies:
    - app.db.client.Database: Shared asyncpg pool.
    - app.core.config.settings: PROVIDER_SEO_SYNC_ENABLED and the Dataverse URL.
    - app.core.logging: Centralized logging.
    - app.etown.provider.provider_syncing: get_dataverse_token().
    - app.etown.provider.provider_seo_syncing: sync_provider_seo_metadata_to_dataverse().
    - public.etown_provider_seo_sync_queue: Created by scripts/2026_10_provider_seo_metadata_sync.sql.

Updates:
    21-Oct-2026: Created; drains etown_provider_seo_sync_queue into ets_providerseometadata.
"""

import asyncio
from typing import Optional

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.provider.provider_seo_syncing import sync_provider_seo_metadata_to_dataverse
from app.etown.provider.provider_syncing import get_dataverse_token

_sync_task: Optional[asyncio.Task] = None

PROVIDER_SEO_SYNC_INTERVAL_SECONDS = 10

PROVIDER_SEO_SYNC_BATCH = 10

PROVIDER_SEO_SYNC_LEASE_SECONDS = 300

PROVIDER_SEO_BIND_WAIT_ATTEMPTS = 5

_CLAIM_SQL = """
UPDATE etown_provider_seo_sync_queue q
   SET claimed_until = now() + make_interval(secs => $2)
 WHERE q.providerid IN (
        SELECT providerid
          FROM etown_provider_seo_sync_queue
         WHERE next_attempt_at <= now()
           AND (claimed_until IS NULL OR claimed_until < now())
         ORDER BY next_attempt_at
         LIMIT $1
           FOR UPDATE SKIP LOCKED
       )
RETURNING q.providerid, q.enqueued_at, q.attempts
"""

_DONE_SQL = """
DELETE FROM etown_provider_seo_sync_queue
 WHERE providerid = $1
   AND enqueued_at = $2
"""

_FAILED_SQL = """
UPDATE etown_provider_seo_sync_queue
   SET attempts = attempts + 1,
       last_error = $3,
       next_attempt_at = now() + make_interval(secs => LEAST(60 * power(2, attempts), 3600))
 WHERE providerid = $1
   AND enqueued_at = $2
"""

_RELEASE_SQL = """
UPDATE etown_provider_seo_sync_queue
   SET claimed_until = NULL
 WHERE providerid = $1
"""


async def drain_provider_seo_queue() -> int:
    """Claim one batch of due providers, mirror each to Dataverse, and dequeue or reschedule it.

    Returns:
        Number of providers claimed.
    """
    rows = await Database.fetch(
        _CLAIM_SQL, PROVIDER_SEO_SYNC_BATCH, float(PROVIDER_SEO_SYNC_LEASE_SECONDS)
    )
    if not rows:
        return 0

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("Provider SEO sync could not get a Dataverse token: %s", exc, exc_info=True)
        for row in rows:
            await Database.execute(_FAILED_SQL, row["providerid"], row["enqueued_at"], f"token: {exc}"[:500])
            await Database.execute(_RELEASE_SQL, row["providerid"])
        return len(rows)

    for row in rows:
        provider_id = row["providerid"]
        try:
            error = await sync_provider_seo_metadata_to_dataverse(
                provider_id,
                access_token,
                bind_wait=row["attempts"] < PROVIDER_SEO_BIND_WAIT_ATTEMPTS,
            )
        except Exception as exc:
            logger.error("Provider SEO sync failed for %s: %s", provider_id, exc, exc_info=True)
            error = str(exc)

        try:
            if error is None:
                await Database.execute(_DONE_SQL, provider_id, row["enqueued_at"])
            else:
                await Database.execute(_FAILED_SQL, provider_id, row["enqueued_at"], error[:500])
            await Database.execute(_RELEASE_SQL, provider_id)
        except Exception as exc:
            logger.error(
                "Provider SEO sync could not update the queue row for %s: %s",
                provider_id, exc, exc_info=True,
            )
    return len(rows)


async def _sync_loop() -> None:
    """Run drain_provider_seo_queue forever at PROVIDER_SEO_SYNC_INTERVAL_SECONDS, logging cycle failures."""
    while True:
        try:
            claimed = await drain_provider_seo_queue()
            if claimed:
                logger.info("Provider SEO sync worker processed %d provider(s)", claimed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Provider SEO sync worker cycle failed: %s", exc, exc_info=True)
        await asyncio.sleep(PROVIDER_SEO_SYNC_INTERVAL_SECONDS)


async def start_provider_seo_sync_worker() -> None:
    """Start the provider SEO sync background task unless PROVIDER_SEO_SYNC_ENABLED is off or Dataverse is unset."""
    global _sync_task
    if not getattr(settings, "PROVIDER_SEO_SYNC_ENABLED", False):
        logger.info("Provider SEO sync worker not started (PROVIDER_SEO_SYNC_ENABLED is off)")
        return
    if not settings.DATAVERSE_URL:
        logger.warning("Provider SEO sync worker not started (DATAVERSE_URL not configured)")
        return
    _sync_task = asyncio.create_task(_sync_loop())
    logger.info(
        "Provider SEO sync background worker started (interval: %ds, batch: %d)",
        PROVIDER_SEO_SYNC_INTERVAL_SECONDS, PROVIDER_SEO_SYNC_BATCH,
    )


async def stop_provider_seo_sync_worker() -> None:
    """Cancel and await the provider SEO sync background worker task."""
    global _sync_task
    if _sync_task and not _sync_task.done():
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            logger.debug("provider SEO sync worker cancelled during shutdown")
    _sync_task = None
    logger.info("Provider SEO sync background worker stopped")
