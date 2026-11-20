"""Town Mapping Worker - links unlinked live providers to the nearest covering town, creating none.

Background worker that runs a weekly full keyset pass over live providers with coordinates and no etown_etownprovider row, links each to the nearest town covering it within the absorb radius or TOWN_MAPPING_LINK_MAX_KM, and mirrors every new link to Dataverse with retries.

Features:
    - town_pop stays AS MATERIALIZED, and town ordering keeps distance, non-NULL population, createdon and id keys
    - Runs weekly at Sunday 04:00 IST, from a fixed +5:30 offset needing no tz database
    - One indexed statement resolves and links a whole chunk; no per-provider round trip
    - A full keyset pass per run, so an unlinkable majority cannot crowd out the linkable
    - Distance is inline haversine, not etown_town_distance_km, so the plan stays parallel-safe
    - Metro providers absorb at the metro radius, so neighbourhoods are not merged
    - Test towns are off the candidate list, and a distance tie goes to the busier town
    - Creates no town profile under any circumstance
    - Dataverse mirror per link, sequential, retried with backoff, never stops the pass
    - Reach is env-tunable via TOWN_MAPPING_LINK_MAX_KM; the code default stays narrow
    - Feature-flagged off by default, and a no-op when the flag is off

Functions:
    _link_reach_km(): The effective link reach in km, or None for the absorb radius.
    sweep_unlinked_providers(): One full keyset pass; returns the rows written.
    _mirror_with_retry(): Mirror one link to Dataverse, retrying before giving up.
    _next_run_at(): The next weekly window in IST, and the delay to it.
    _mapping_loop(): Sleep until the window, run a pass, mirror each link, repeat.
    start_town_mapping_worker(): Start the loop.
    stop_town_mapping_worker(): Cancel and await the loop.

Dependencies:
    - app.db.client.Database: Shared asyncpg pool.
    - app.core.config.settings: TOWN_MAPPING_ENABLED, TOWN_MAPPING_LINK_MAX_KM.
    - app.etown.mytown.mytown_utils: absorb radii and the metro list.
    - app.etown.mytown.mytown_syncing: ets_etownproviderses mirror.

Updates:
    03-Sep-2026: Created. Links unlinked live providers to the nearest covering town, never creating one.
    04-Sep-2026: Test towns excluded; population tiebreak; weekly full pass; added TOWN_MAPPING_LINK_MAX_KM; mirror retries.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database

_mapping_task: Optional[asyncio.Task] = None

IST = timezone(timedelta(hours=5, minutes=30))

TOWN_MAPPING_WEEKDAY = 6
TOWN_MAPPING_HOUR = 4

TOWN_MAPPING_CHUNK = 2000
TOWN_MAPPING_MAX_CHUNKS = 200

TOWN_MAPPING_MIRROR_ATTEMPTS = 3
TOWN_MAPPING_MIRROR_BACKOFF_SECONDS = (5.0, 30.0)

TOWN_MAPPING_LINK_MAX_KM: Optional[float] = None


def _link_reach_km() -> Optional[float]:
    """Return the link reach in km from settings, else the module default, where None means the absorb radius."""
    configured = getattr(settings, "TOWN_MAPPING_LINK_MAX_KM", None)
    return configured if configured is not None else TOWN_MAPPING_LINK_MAX_KM


_CANDIDATE_SQL = """
SELECT p.providerid
  FROM etown_provider p
 WHERE p.providerid > $1
   AND p.deleted IS DISTINCT FROM 'true'
   AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
   AND p.disabled IS NOT TRUE
   AND p.latitude IS NOT NULL
   AND p.longitude IS NOT NULL
   AND p.latitude::double precision BETWEEN -90 AND 90
   AND p.longitude::double precision BETWEEN -180 AND 180
   AND NOT (p.latitude::double precision = 0 AND p.longitude::double precision = 0)
   AND NOT EXISTS (
           SELECT 1 FROM etown_etownprovider ep
            WHERE ep.providerid = p.providerid
       )
 ORDER BY p.providerid
 LIMIT $2
"""

_SWEEP_SQL = """
WITH town_pop AS MATERIALIZED (
    SELECT townprofileid, count(*) AS n
      FROM etown_etownprovider
     WHERE townprofileid IS NOT NULL
       AND providerid IS NOT NULL
       AND COALESCE(deleted, 'false') <> 'true'
     GROUP BY 1
),
cand AS (
    SELECT p.providerid,
           COALESCE(p.businessname, '')                                   AS businessname,
           p.latitude::double precision                                   AS lat,
           p.longitude::double precision                                  AS lon,
           NULLIF(btrim(COALESCE(p.areaname, p.city)), '')                AS areaname,
           CASE WHEN lower(btrim(COALESCE(p.city, ''))) = ANY($1::text[])
                THEN $2::double precision ELSE $3::double precision END   AS radius_km
      FROM etown_provider p
     WHERE p.providerid = ANY($4::text[])
),
resolved AS (
    SELECT c.providerid, c.businessname, c.areaname,
           t.townprofileid, t.townname, t.distance_km,
           (t.distance_km <= c.radius_km) AS covered
      FROM cand c
      CROSS JOIN LATERAL (
          SELECT tp.townprofileid, tp.townname,
                 ( 6371 * acos( LEAST(1.0, GREATEST(-1.0,
                     cos(radians(c.lat)) * cos(radians(tp.townlatitude::double precision))
                     * cos(radians(tp.townlongitude::double precision) - radians(c.lon))
                     + sin(radians(c.lat)) * sin(radians(tp.townlatitude::double precision))
                 )))) AS distance_km
            FROM etown_townprofile tp
            LEFT JOIN town_pop pop ON pop.townprofileid = tp.townprofileid
           WHERE tp.townname IS NOT NULL
             AND btrim(tp.townname) <> ''
             AND tp.townname NOT ILIKE 'test%'
             AND COALESCE(tp.cityname, '') NOT ILIKE 'test%'
             AND tp.townlatitude IS NOT NULL
             AND tp.townlongitude IS NOT NULL
             AND tp.townlatitude ~ E'^[-+]?[0-9]*\\.?[0-9]+([eE][-+]?[0-9]+)?$'
             AND tp.townlongitude ~ E'^[-+]?[0-9]*\\.?[0-9]+([eE][-+]?[0-9]+)?$'
             AND tp.townlatitude::double precision BETWEEN -90 AND 90
             AND tp.townlongitude::double precision BETWEEN -180 AND 180
           ORDER BY 3 ASC, COALESCE(pop.n, 0) DESC, tp.createdon ASC NULLS LAST,
                    tp.townprofileid ASC
           LIMIT 1
      ) t
     WHERE t.distance_km <= COALESCE($5::double precision, c.radius_km)
),
ins AS (
    INSERT INTO etown_etownprovider
        (etownproviderid, providerid, providerbusinessname, townprofileid, townname,
         areaname, is_primary, createdon)
    SELECT providerid, providerid, businessname, townprofileid, townname,
           areaname, TRUE, NOW()
      FROM resolved
    ON CONFLICT DO NOTHING
    RETURNING providerid, providerbusinessname, townprofileid, townname
)
SELECT i.providerid, i.providerbusinessname, i.townprofileid, i.townname,
       r.covered, r.distance_km
  FROM ins i
  JOIN resolved r ON r.providerid = i.providerid
"""


async def sweep_unlinked_providers(
    chunk: int = TOWN_MAPPING_CHUNK,
    max_chunks: int = TOWN_MAPPING_MAX_CHUNKS,
) -> List[Dict[str, Any]]:
    """Link each unlinked, located provider to its nearest town within reach, paging candidates by providerid.

    Args:
        chunk: Candidates resolved per statement.
        max_chunks: Ceiling on statements in one pass.

    Returns:
        One dict per link written, with town, distance and whether the absorb radius covered it.
    """
    from app.etown.mytown.mytown_utils import (
        TOWN_ABSORB_RADIUS_KM,
        TOWN_ABSORB_RADIUS_METRO_KM,
        _load_metro_cities,
    )

    metro = list(_load_metro_cities())
    reach = _link_reach_km()
    written: List[Dict[str, Any]] = []
    cursor = ""
    considered = 0

    for pass_no in range(1, int(max_chunks) + 1):
        ids = [
            r["providerid"]
            for r in await Database.fetch_once(_CANDIDATE_SQL, cursor, int(chunk))
        ]
        if not ids:
            break
        cursor = ids[-1]
        considered += len(ids)

        rows = await Database.fetch_once(
            _SWEEP_SQL,
            metro,
            TOWN_ABSORB_RADIUS_METRO_KM,
            TOWN_ABSORB_RADIUS_KM,
            ids,
            reach,
        )
        written.extend(dict(r) for r in rows)

        if len(ids) < int(chunk):
            break
        if pass_no == int(max_chunks):
            logger.warning(
                "Town mapping stopped at the %d-chunk ceiling with the cursor at %s; "
                "%d candidate(s) considered this pass",
                max_chunks, cursor, considered,
            )

    fallback = sum(1 for row in written if not row.get("covered"))
    logger.info(
        "Town mapping pass considered %d candidate(s) and wrote %d link(s) "
        "(%d within the absorb radius, %d beyond it) | reach=%s",
        considered, len(written), len(written) - fallback, fallback,
        f"{reach}km" if reach else "absorb radius",
    )
    return written


def _next_run_at(now: Optional[datetime] = None) -> Tuple[datetime, float]:
    """Return the next weekly IST run instant strictly after now and the delay to it in seconds."""
    now = now or datetime.now(IST)
    target = now.replace(hour=TOWN_MAPPING_HOUR, minute=0, second=0, microsecond=0)
    target += timedelta(days=(TOWN_MAPPING_WEEKDAY - now.weekday()) % 7)
    if target <= now:
        target += timedelta(days=7)
    return target, (target - now).total_seconds()


async def _mirror_with_retry(mirror, row: Dict[str, Any]) -> bool:
    """Mirror one town link to Dataverse with backoff retries, logging MIRROR_FAILED and returning False if all fail.

    Args:
        mirror: The sync_etownprovider_to_dataverse coroutine function.
        row: One written link with providerid, business name, townprofileid and townname.

    Returns:
        True once a mirror attempt succeeds, False if every attempt failed.
    """
    for attempt in range(1, TOWN_MAPPING_MIRROR_ATTEMPTS + 1):
        try:
            if await mirror(
                etownproviderid=row["providerid"],
                provider_id=row["providerid"],
                business_name=row["providerbusinessname"] or "",
                townprofileid=row["townprofileid"],
            ):
                if attempt > 1:
                    logger.info(
                        "TOWN_MAPPING | MIRROR_RECOVERED | providerid=%s | attempt=%d",
                        row["providerid"], attempt,
                    )
                return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "TOWN_MAPPING | MIRROR_ERROR | providerid=%s | attempt=%d/%d | %s",
                row["providerid"], attempt, TOWN_MAPPING_MIRROR_ATTEMPTS, exc,
            )

        if attempt < TOWN_MAPPING_MIRROR_ATTEMPTS:
            delay = TOWN_MAPPING_MIRROR_BACKOFF_SECONDS[
                min(attempt - 1, len(TOWN_MAPPING_MIRROR_BACKOFF_SECONDS) - 1)
            ]
            await asyncio.sleep(delay)

    logger.error(
        "TOWN_MAPPING | MIRROR_FAILED | providerid=%s | townprofileid=%s | town=%s | "
        "exhausted %d attempts; the Postgres link stands and will not be retried by a later "
        "pass",
        row["providerid"], row["townprofileid"], row["townname"],
        TOWN_MAPPING_MIRROR_ATTEMPTS,
    )
    return False


async def _mapping_loop() -> None:
    """Loop forever: sleep until the weekly window, run a sweep, then mirror each new link sequentially."""
    from app.etown.mytown.mytown_syncing import sync_etownprovider_to_dataverse

    while True:
        target, delay = _next_run_at()
        logger.info(
            "Town mapping sleeping until %s IST (%.1f hours)",
            target.strftime("%Y-%m-%d %H:%M"), delay / 3600.0,
        )
        await asyncio.sleep(delay)

        try:
            written = await sweep_unlinked_providers()
            mirrored = failed = 0
            for row in written:
                if await _mirror_with_retry(sync_etownprovider_to_dataverse, row):
                    mirrored += 1
                else:
                    failed += 1
            if written:
                logger.info(
                    "Town mapping linked %d provider(s) to a covering town "
                    "(e.g. %s -> %s) | mirrored=%d failed=%d",
                    len(written), written[0]["providerid"], written[0]["townname"],
                    mirrored, failed,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Town mapping cycle failed: %s", exc, exc_info=True)


async def start_town_mapping_worker() -> None:
    """Start the town mapping sweep, unless the feature flag is off."""
    global _mapping_task
    if not getattr(settings, "TOWN_MAPPING_ENABLED", False):
        logger.info("Town mapping worker not started (TOWN_MAPPING_ENABLED is off)")
        return
    _mapping_task = asyncio.create_task(_mapping_loop())
    target, delay = _next_run_at()
    logger.info(
        "Town mapping worker started (weekly, %s %02d:00 IST, chunks of %d, "
        "creates no towns) | next run %s IST in %.1f hours",
        ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[TOWN_MAPPING_WEEKDAY],
        TOWN_MAPPING_HOUR, TOWN_MAPPING_CHUNK,
        target.strftime("%Y-%m-%d %H:%M"), delay / 3600.0,
    )


async def stop_town_mapping_worker() -> None:
    """Cancel and await the town mapping sweep task."""
    global _mapping_task
    if _mapping_task and not _mapping_task.done():
        _mapping_task.cancel()
        try:
            await _mapping_task
        except asyncio.CancelledError:
            logger.debug("town mapping worker cancelled during shutdown")
    _mapping_task = None
    logger.info("Town mapping worker stopped")
