"""Search Metadata Worker - periodically rebuilds the derived city and term lookup tables.

Background worker that rebuilds etown_metadata_city and etown_metadata_term from etown_providerservices_copy on a fixed interval, using DELETE plus INSERT in one transaction under a Postgres advisory lock so only one worker refreshes and readers are never blocked.

Features:
    - Never TRUNCATE: its AccessExclusiveLock would queue every concurrent provider write behind the rebuild
    - Periodic Refresh: Rebuilds both metadata tables every SEARCH_METADATA_REFRESH_SECONDS
    - Lock-light: DELETE+INSERT in a transaction (RowExclusiveLock, MVCC-atomic to readers)
    - Multi-worker Safe: pg_try_advisory_lock ensures one refresh across gunicorn workers/containers
    - Feature-flagged: on by default; SEARCH_METADATA_REFRESH_ENABLED=false disables it
    - Safe Startup/Shutdown: lifecycle hooks for the FastAPI lifespan

Classes:
    No classes defined (function-based background worker pattern).

Functions:
    refresh_search_metadata(): Rebuild both derived tables once; returns row counts.
    _refresh_loop(): Advisory-locked loop that refreshes the tables every interval tick.
    start_metadata_refresh_worker(): Start the periodic refresh task.
    stop_metadata_refresh_worker(): Cancel and await the refresh task.

Dependencies:
    - asyncio: background task scheduling
    - app.core.config: feature flag + interval settings
    - app.core.logging: centralised logging
    - app.db.client: async PostgreSQL pool access

Updates:
    09-Jul-2026: Initial creation - replaces the per-write TRUNCATE triggers on etown_metadata_city.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import asyncio
from typing import Optional

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database

_REFRESH_LOCK_KEY = 820117

_refresh_task: Optional[asyncio.Task] = None

_CITY_INSERT = """
    INSERT INTO etown_metadata_city (city)
    SELECT DISTINCT LOWER(TRIM(city)) AS city
    FROM etown_providerservices_copy
    WHERE city IS NOT NULL
      AND LENGTH(TRIM(city)) > 2
    ON CONFLICT (city) DO NOTHING
"""

_TERM_INSERT = """
    INSERT INTO etown_metadata_term (term)
    SELECT DISTINCT term FROM (
        SELECT LOWER(TRIM(REPLACE(servicecategory, '_', ' '))) AS term
        FROM etown_providerservices_copy
        WHERE servicecategory IS NOT NULL AND LENGTH(TRIM(servicecategory)) > 2

        UNION

        SELECT LOWER(TRIM(businesscategory)) AS term
        FROM etown_providerservices_copy
        WHERE businesscategory IS NOT NULL AND LENGTH(TRIM(businesscategory)) > 2

        UNION

        SELECT LOWER(TRIM(unnest(string_to_array(services, ',')))) AS term
        FROM etown_providerservices_copy
        WHERE services IS NOT NULL

        UNION

        SELECT LOWER(TRIM(unnest(string_to_array(
            REGEXP_REPLACE(LOWER(TRIM(REPLACE(servicecategory, '_', ' '))), '[^a-z0-9 ]', ' ', 'g'), ' '
        )))) AS term
        FROM etown_providerservices_copy
        WHERE servicecategory IS NOT NULL AND LENGTH(TRIM(servicecategory)) > 2

        UNION

        SELECT LOWER(TRIM(unnest(string_to_array(
            REGEXP_REPLACE(LOWER(TRIM(businesscategory)), '[^a-z0-9 ]', ' ', 'g'), ' '
        )))) AS term
        FROM etown_providerservices_copy
        WHERE businesscategory IS NOT NULL AND LENGTH(TRIM(businesscategory)) > 2

        UNION

        SELECT word AS term FROM (
            SELECT LOWER(unnest(string_to_array(
                REGEXP_REPLACE(LOWER(TRIM(businessname)), '[^a-z0-9 ]', ' ', 'g'), ' '
            ))) AS word
            FROM etown_providerservices_copy
            WHERE businessname IS NOT NULL
        ) words
        WHERE LENGTH(word) > 3
        GROUP BY word HAVING COUNT(*) >= 2
    ) terms
    WHERE LENGTH(term) > 2
    ON CONFLICT (term) DO NOTHING
"""


async def refresh_search_metadata() -> dict:
    """Rebuild etown_metadata_city and etown_metadata_term from provider data and return their row counts."""
    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        async with con.transaction():
            await con.execute("DELETE FROM etown_metadata_city")
            await con.execute(_CITY_INSERT)
        async with con.transaction():
            await con.execute("DELETE FROM etown_metadata_term")
            await con.execute(_TERM_INSERT)

        cities = await con.fetchval("SELECT count(*) FROM etown_metadata_city")
        terms = await con.fetchval("SELECT count(*) FROM etown_metadata_term")
    return {"cities": int(cities or 0), "terms": int(terms or 0)}


async def _refresh_loop() -> None:
    """Rebuild the metadata tables every SEARCH_METADATA_REFRESH_SECONDS under an advisory lock so one worker refreshes."""
    interval = int(getattr(settings, "SEARCH_METADATA_REFRESH_SECONDS", 300))
    while True:
        try:
            async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as lock_con:
                got = await lock_con.fetchval("SELECT pg_try_advisory_lock($1)", _REFRESH_LOCK_KEY)
                if got:
                    try:
                        counts = await refresh_search_metadata()
                        logger.info(
                            "search-metadata refresh: %d cities, %d terms",
                            counts["cities"], counts["terms"],
                        )
                    finally:
                        try:
                            await lock_con.fetchval("SELECT pg_advisory_unlock($1)", _REFRESH_LOCK_KEY)
                        except Exception:
                            logger.warning("search-metadata refresh: failed to release the advisory lock", exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("search-metadata refresh loop error: %s", exc, exc_info=True)
        await asyncio.sleep(interval)


async def start_metadata_refresh_worker() -> None:
    """Start the periodic search-metadata refresh task unless SEARCH_METADATA_REFRESH_ENABLED is false."""
    global _refresh_task
    if not bool(getattr(settings, "SEARCH_METADATA_REFRESH_ENABLED", True)):
        logger.info("Search-metadata refresh disabled (SEARCH_METADATA_REFRESH_ENABLED=false)")
        return
    _refresh_task = asyncio.create_task(_refresh_loop())
    logger.info(
        "Search-metadata refresh worker started (interval=%ss)",
        int(getattr(settings, "SEARCH_METADATA_REFRESH_SECONDS", 300)),
    )


async def stop_metadata_refresh_worker() -> None:
    """Cancel and await the search-metadata refresh task."""
    global _refresh_task
    if _refresh_task and not _refresh_task.done():
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            logger.debug("search-metadata refresh worker cancelled during shutdown")
    _refresh_task = None
    logger.info("Search-metadata refresh worker stopped")
