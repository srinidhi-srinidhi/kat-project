"""Notification Scheduler Worker - fires notification jobs on a daily IST schedule.

Background asyncio loop started from the FastAPI lifespan: each tick it reads scheduler.etown_job_config and runs due digest, reminder, backfill and replay jobs exactly once per day across workers, dispatching every send through notification governance.

Features:
    - Opt-in: NOTIFICATIONS_SCHEDULER_ENABLED (default false) must be on or no loop starts
    - Dual-guard once-per-day execution: advisory lock plus claim-row insert across workers
    - DB-driven schedule: build_jobs() reads scheduler.etown_job_config each tick
    - LLM copy-pool digests: rotating pre-generated variants, else the static template
    - Bulk-query digest jobs: weekly_digest precomputes its data in a handful of queries
    - Governance-routed sends: ledger, caps, opt-out and quiet hours apply to every recipient
    - Deferred replay: quiet-hours-deferred notifications are replayed after 08:00 IST
    - Nightly vector backfill: embeds any NULL content_vector rows
    - Independent feed-nudge sweep: a second loop runs feed_no_post_nudge every 2 minutes

Classes:
    ScheduledJob(): Dataclass describing one job's name/fire time/lock key/runner/weekday filter
    DigestSpec(): Dataclass describing one digest's type/daypart/lock keys/all-users flag

Functions:
    now_ist(): Current time in IST
    is_due(): Whether a job is inside its fire window right now
    _unique_by(): De-duplicate rows by a key, keeping the first occurrence
    _ensure_notification_tables(): Create the claim-row and feed-nudge-queue tables if absent
    _claim_and_run(): Run a job iff this worker wins the once-per-day advisory-lock + claim-row race
    _heartbeat_runner(): Safe no-op proof job that only logs
    _vector_backfill_runner(): Nightly backfill of NULL content_vector rows across provider/contact tables
    _make_copy_refresh_runner(): Build a weekly runner that regenerates a DigestSpec's LLM copy pool
    _make_digest_runner(): Build a runner that calls run_digest() for one DigestSpec
    run_rating_reminder(): Daily 12pm IST: prompt yesterday's completed, unrated bookings to rate
    run_digest(): Shared runner for morning/evening/midday digests — one push per recipient
    _deferred_sweep_runner(): Replay quiet-hours-deferred notifications after 08:00 IST
    run_evening_mycircle(): 18:00 IST: push mycircle_activity to users with >=N new circle posts
    run_weekly_digest(): Sunday 10:00 IST: weekly push summary, bulk-precomputed per recipient
    run_re_engagement(): Daily 11:00 IST: push to users dormant for 7+ days
    run_provider_onboard_nudge(): Every 15 days at 10:30 IST: nudge non-provider users to list a business
    run_town_community_nudge(): Daily: push the next Need/Update/Event template to each device-owning profile, one per contact and phone, every other day
    run_provider_weekly_views(): Sunday 13:00 IST: push to providers crossing a weekly view threshold
    run_provider_views_milestone(): Daily 13:00 IST: push when a provider crosses a lifetime view milestone
    run_provider_liked(): Daily: notify a provider (+ circle) when their favourite count crosses a milestone
    run_new_rating(): Daily: notify a provider when their rating count has grown
    run_town_view_milestone(): Daily: nudge providers in a town, one per contact and phone, when its view count crosses a milestone
    run_feed_no_post_nudge(): Sweep: nudge users who opened the feed but posted nothing today
    run_new_town_nearby(): Daily 10:00 IST: announce towns created yesterday to nearby users
    run_app_store_rating_prompt(): Daily 13:00 IST: prompt engaged, not-recently-prompted users to rate the app
    _feed_nudge_loop(): Every 2 minutes: run the feed-nudge sweep under its own advisory lock
    _rating_reminder_refresh_runner(): Weekly refresh of the rating_reminder LLM copy pool
    _feed_nudge_copy_refresh_runner(): Weekly refresh of the feed_no_post_nudge LLM copy pool
    _init_job_runners(): Populate the job-name -> (lock_key, runner) registry once
    build_jobs(): Return the registered jobs, with schedule sourced from the DB job_config
    _loop(): Main scheduler loop — checks due jobs against the DB schedule every tick
    start_notification_scheduler(): Start the scheduler (+ feed-nudge loop) if enabled, from the lifespan
    stop_notification_scheduler(): Cancel and await the scheduler and feed-nudge loop tasks

Dependencies:
    - app.db.client.Database: fetch/execute for all cohort queries, DDL, and advisory locks
    - app.etown.devices.devices_utils HAS_DEVICE_SQL: the shared "person has a device" cohort gate
    - app.etown.notifications.core.job_config: DB-backed job schedule (hour/minute/enabled/params)
    - app.etown.notifications.core.config: db_get() per-type config gate (lazy)
    - app.etown.notifications.core.registry.NotificationType: type keys used by DigestSpec/runners
    - app.etown.notifications.service (GovernanceRequest, send/enqueue): governed sends (lazy)
    - app.etown.notifications.core.notification_generator: LLM copy-pool get/refresh/select (lazy)
    - app.etown.notifications.deferred: replay_due() for the deferred sweep (lazy)
    - app.etown.notifications.triggers: notify_new_town_nearby() (lazy)
    - app.etown.notifications.fingerprint: dedup fingerprints (lazy)
    - app.etown.offers.offers_utils: circle user-profile ids (lazy)
    - app.utils.embeddings: generate_embedding() for the vector backfill (lazy)
    - app.core.config.settings: NOTIFICATIONS_SCHEDULER_ENABLED, NOTIFICATIONS_GOVERNANCE_ENABLED

Updates:
    10-Jun-2026: Added contactid/userprofileid to provider_liked and new_rating ctx dicts.
    12-Aug-2026: run_town_view_milestone() passes the milestone to the SQL as text.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    01-Sep-2026: Weekly-view and milestone counts drop self, anonymous and deleted viewers.
    15-Sep-2026: feed_no_post_nudge per contact over the IST day; contact-level device gates; app-rating dedup.
    12-Oct-2026: Added run_town_community_nudge(), the 48-hour rotating Town Community push nudge.
    13-Oct-2026: run_town_community_nudge() skips in shadow mode and logs accepted, duplicate, failed, suppressed and error counts.
    21-Oct-2026: run_town_community_nudge() targets only recipients with a device and sends each phone to one recipient; 47h cooldown so the daily run nudges every other day.
    21-Oct-2026: run_town_view_milestone() sends each town milestone once per contact and phone, not once per provider profile.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from typing import Awaitable, Callable, List, Optional, Set

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.devices.devices_utils import HAS_DEVICE_SQL
from app.etown.notifications.core import job_config as jcfg
from app.etown.notifications.core.registry import NotificationType

IST = timezone(timedelta(hours=5, minutes=30))

TICK_SECONDS = 60
DUE_WINDOW_SECONDS = 120

_scheduler_task: Optional[asyncio.Task] = None


def _unique_by(rows, key: str):
    """Return the rows with only the first row kept per key value, preserving order."""
    seen = set()
    out = []
    for r in rows:
        k = r[key]
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out

_feed_nudge_task: Optional[asyncio.Task] = None
_FEED_NUDGE_TICK_SECONDS = 120
_FEED_NUDGE_LOCK_KEY = 811014

_CLAIMS_DDL = """
CREATE TABLE IF NOT EXISTS etown_notification_schedule_runs (
    job_name   text        NOT NULL,
    run_date   date        NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_name, run_date)
)
"""
_FEED_NUDGE_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS notifications.etown_feed_nudge_queue (
    contactid     text        NOT NULL,
    userprofileid text        NOT NULL,
    opened_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (contactid)
)
"""


def now_ist() -> datetime:
    """Return the current time in IST."""
    return datetime.now(IST)


@dataclass
class ScheduledJob:
    name: str
    hour: int
    minute: int
    lock_key: int
    runner: Callable[[], Awaitable[None]]
    weekdays: Optional[Set[int]] = None


def is_due(job: ScheduledJob, now: datetime) -> bool:
    """Return True when today is an allowed weekday and now falls inside the job's due window."""
    if job.weekdays is not None and now.weekday() not in job.weekdays:
        return False
    target = now.replace(hour=job.hour, minute=job.minute, second=0, microsecond=0)
    delta = (now - target).total_seconds()
    return 0 <= delta < DUE_WINDOW_SECONDS


async def _ensure_notification_tables() -> None:
    """Create the schedule-runs claim table and the feed-nudge queue table if missing."""
    await Database.execute(_CLAIMS_DDL)
    await Database.execute(_FEED_NUDGE_QUEUE_DDL)


async def _claim_and_run(job: ScheduledJob, run_date: date) -> bool:
    """Run the job when this worker wins its advisory lock and daily claim row, returning True only on success."""
    got = await Database.fetchval("SELECT pg_try_advisory_lock($1)", job.lock_key)
    if not got:
        return False
    try:
        res = await Database.execute(
            "INSERT INTO etown_notification_schedule_runs (job_name, run_date) "
            "VALUES ($1, $2) ON CONFLICT DO NOTHING",
            job.name, run_date,
        )
        claimed = False
        try:
            claimed = int(str(res).split()[-1]) == 1
        except (ValueError, IndexError):
            logger.warning("Scheduler: could not read a row count out of status %r", res)
            claimed = False
        if not claimed:
            return False
        logger.info("Scheduler: running job %s for %s (IST)", job.name, run_date)
        await job.runner()
        logger.info("Scheduler: job %s completed", job.name)
        return True
    except Exception as exc:
        logger.error("Scheduler: job %s failed: %s", job.name, exc, exc_info=True)
        return False
    finally:
        try:
            await Database.execute("SELECT pg_advisory_unlock($1)", job.lock_key)
        except Exception:
            logger.warning("Scheduler: failed to release the advisory lock for job %s", job.name, exc_info=True)


async def _heartbeat_runner() -> None:
    """Log that the scheduler is alive, sending nothing."""
    logger.info("Scheduler heartbeat: notification scheduler is alive at %s IST", now_ist())


async def _vector_backfill_runner() -> None:
    """Embed and fill NULL content_vector rows in providerservices_copy, provider, contact, member and townprofile tables."""
    from app.utils.embeddings import generate_embedding

    BATCH = 10

    async def _embed_and_update(table: str, pk: str, rows, build_text):
        ok = failed = skipped = 0
        for i in range(0, len(rows), BATCH):
            batch = rows[i : i + BATCH]

            async def process(row):
                text = build_text(row)
                if not text.strip():
                    return None, row[pk]
                try:
                    vec = await generate_embedding(text)
                    vec_str = "[" + ",".join(str(v) for v in vec) + "]"
                    await Database.execute(
                        f"UPDATE {table} SET content_vector = $1::vector WHERE {pk} = $2",
                        vec_str, row[pk],
                    )
                    return True, row[pk]
                except Exception as exc:
                    logger.warning("Scheduler: embedding backfill failed for %s", row[pk], exc_info=True)
                    return False, str(exc)

            results = await asyncio.gather(*[process(r) for r in batch])
            for success, info in results:
                if success is None:
                    skipped += 1
                elif success:
                    ok += 1
                else:
                    failed += 1
                    logger.warning("[vector_backfill] %s failure: %s", table, info)

        logger.info("[vector_backfill] %s: ok=%d skipped=%d failed=%d", table, ok, skipped, failed)

    rows_psc = await Database.fetch(
        "SELECT providerservicesid, businessname, servicedescription, address "
        "FROM etown_providerservices_copy WHERE content_vector IS NULL"
    )
    if rows_psc:
        logger.info("[vector_backfill] etown_providerservices_copy: %d rows", len(rows_psc))

        def _psc_text(row):
            parts = [f"Business: {row['businessname']}"] if row['businessname'] else []
            if row['servicedescription']:
                parts.append(f"Description: {row['servicedescription']}")
            if row['address']:
                parts.append(f"Address: {row['address']}")
            return " ".join(parts)

        await _embed_and_update(
            "etown_providerservices_copy", "providerservicesid", rows_psc, _psc_text
        )

    rows_p = await Database.fetch(
        "SELECT providerid, businessname, address, city, description, "
        "contactidname, userprofileidname "
        "FROM etown_provider WHERE content_vector IS NULL"
    )
    if rows_p:
        logger.info("[vector_backfill] etown_provider: %d rows", len(rows_p))

        def _p_text(row):
            parts = [f"Business: {row['businessname']}"] if row['businessname'] else []
            if row['address']:
                parts.append(f"Address: {row['address']}")
            if row['city']:
                parts.append(f"City: {row['city']}")
            if row['description']:
                parts.append(f"Description: {row['description']}")
            if row['contactidname']:
                parts.append(f"Contact: {row['contactidname']}")
            if row['userprofileidname']:
                parts.append(f"Profile: {row['userprofileidname']}")
            return " ".join(parts)

        await _embed_and_update("etown_provider", "providerid", rows_p, _p_text)

    rows_c = await Database.fetch(
        "SELECT contactid, firstname, city FROM etown_contact WHERE content_vector IS NULL"
    )
    if rows_c:
        logger.info("[vector_backfill] etown_contact: %d rows", len(rows_c))

        def _c_text(row):
            return " ".join(filter(None, [row['firstname'], row['city']]))

        await _embed_and_update("etown_contact", "contactid", rows_c, _c_text)

    rows_m = await Database.fetch(
        "SELECT memberid, userprofileidname, contactidname "
        "FROM etown_member WHERE content_vector IS NULL"
    )
    if rows_m:
        logger.info("[vector_backfill] etown_member: %d rows", len(rows_m))

        def _m_text(row):
            return " ".join(filter(None, [row['userprofileidname'], row['contactidname']]))

        await _embed_and_update("etown_member", "memberid", rows_m, _m_text)

    rows_tp = await Database.fetch(
        "SELECT townprofileid, townname, cityname "
        "FROM etown_townprofile WHERE content_vector IS NULL"
    )
    if rows_tp:
        logger.info("[vector_backfill] etown_townprofile: %d rows", len(rows_tp))

        def _tp_text(row):
            return " ".join(filter(None, [row['townname'], row['cityname']]))

        await _embed_and_update("etown_townprofile", "townprofileid", rows_tp, _tp_text)

    if not rows_psc and not rows_p and not rows_c and not rows_m and not rows_tp:
        logger.info("[vector_backfill] All tables clean — no NULL vectors found")


@dataclass(frozen=True)
class DigestSpec:
    name: str
    type_const: str
    daypart: str
    send_lock_key: int
    refresh_lock_key: int
    all_users: bool = False


DIGESTS: List[DigestSpec] = [
    DigestSpec(
        name="morning_digest",
        type_const=NotificationType.MORNING_DIGEST,
        daypart="morning",
        send_lock_key=811001,
        refresh_lock_key=811002,
        all_users=False,
    ),
    DigestSpec(
        name="evening_digest",
        type_const=NotificationType.EVENING_DIGEST,
        daypart="evening",
        send_lock_key=811003,
        refresh_lock_key=811004,
        all_users=True,
    ),
    DigestSpec(
        name="midday_digest",
        type_const=NotificationType.MIDDAY_DIGEST,
        daypart="midday",
        send_lock_key=811021,
        refresh_lock_key=811022,
        all_users=True,
    ),
]


def _make_copy_refresh_runner(spec: DigestSpec) -> Callable[[], Awaitable[None]]:
    """Return a runner that regenerates the digest's LLM copy pool for its daypart."""
    async def _runner() -> None:
        from app.etown.notifications.core.notification_generator import refresh_copy_pool
        await refresh_copy_pool(spec.type_const, daypart=spec.daypart)
    return _runner


def _make_digest_runner(spec: DigestSpec) -> Callable[[], Awaitable[None]]:
    """Return a runner that sends the given digest."""
    async def _runner() -> None:
        await run_digest(spec)
    return _runner

_RATING_REMINDER_COHORT_SQL = f"""
SELECT DISTINCT ON (up.contactid)
    ar.requestoruserprofileid AS userprofileid,
    up.contactid,
    ar.allrequestid         AS booking_id,
    COALESCE(rsl.servicename, 'your recent service') AS service_name,
    COALESCE(rsl.name, 'the provider')               AS provider_name,
    COALESCE(ep.serviceimageurl, '')                 AS serviceimage,
    ep.creationdate                                  AS provider_createdon
FROM etown_allrequests ar
JOIN etown_userprofile up ON up.userprofileid = ar.requestoruserprofileid
LEFT JOIN LATERAL (
    SELECT servicename, name, serviceprovideruserprofileid
    FROM etown_requestservicelists
    WHERE bookingid = ar.allrequestid
    ORDER BY createdon DESC
    LIMIT 1
) rsl ON TRUE
LEFT JOIN etown_provider ep ON ep.userprofileid = rsl.serviceprovideruserprofileid
WHERE LOWER(TRIM(ar.requeststatus)) = 'completed'
  AND ar.bookingcompletedtime IS NOT NULL
  AND (ar.bookingcompletedtime AT TIME ZONE 'Asia/Kolkata')::date
        = (now() AT TIME ZONE 'Asia/Kolkata')::date - INTERVAL '1 day'
  AND ar.ratingvalue IS NULL
  AND (ar."isRating" IS NULL OR LOWER(ar."isRating"::text) = 'false')
  AND rsl.serviceprovideruserprofileid IS NOT NULL
  AND ar.requestoruserprofileid IS NOT NULL
  AND up.contactid IS NOT NULL
  AND  {HAS_DEVICE_SQL.format(contact="up.contactid", profile="ar.requestoruserprofileid")}
ORDER BY up.contactid, ar.bookingcompletedtime DESC
LIMIT $1
"""

async def run_rating_reminder() -> None:
    """Send a rating-reminder push for each user's unrated booking completed yesterday, using LLM copy when enabled."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    from app.etown.notifications.core.registry import NotificationType

    params = await jcfg.get_params("rating_reminder")
    max_recipients = int(params.get("max_recipients", 1000))
    rows = await Database.fetch(_RATING_REMINDER_COHORT_SQL, max_recipients)
    logger.info("rating_reminder: %d recipient(s) (unrated completed bookings from yesterday)", len(rows))
    if not rows:
        return

    pool: list = []
    select_variant = None
    if bool(params.get("llm_enabled", False)):
        try:
            from app.etown.notifications.core.notification_generator import (
                get_active_pool, refresh_copy_pool, select_variant as _select_variant,
                RATING_REMINDER as RATING_REMINDER_KEY,
            )
            pool = await get_active_pool(RATING_REMINDER_KEY)
            if not pool:
                await refresh_copy_pool(RATING_REMINDER_KEY, daypart="rating_reminder")
                pool = await get_active_pool(RATING_REMINDER_KEY)
            select_variant = _select_variant
            logger.info("rating_reminder: using LLM copy pool (%d variant(s))", len(pool))
        except Exception as exc:
            logger.warning("rating_reminder: copy pool unavailable, using fallback copy: %s", exc)
            pool = []

    run_date = now_ist().date()
    semaphore = asyncio.Semaphore(20)

    async def _one(row) -> None:
        async with semaphore:
            try:
                service_name = row["service_name"]
                provider_name = row["provider_name"]
                ctx: dict = {
                    "service_name": service_name,
                    "provider_name": provider_name,
                    "booking_id": row["booking_id"],
                    "serviceimage": row.get("serviceimage", "") or "",
                    "provider_createdon": str(row.get("provider_createdon") or ""),
                    "userprofileid": str(row["userprofileid"]),
                }
                if pool and select_variant is not None:
                    variant = select_variant(
                        pool, key=str(row["userprofileid"]), run_date=run_date, n=None,
                    )
                    if variant:
                        ctx["title"] = (
                            variant["title"]
                            .replace("{service_name}", service_name)
                            .replace("{provider_name}", provider_name)
                        )
                        ctx["body"] = (
                            variant["body"]
                            .replace("{service_name}", service_name)
                            .replace("{provider_name}", provider_name)
                        )
                await gov_send(GovernanceRequest(
                    userprofileid=row["userprofileid"],
                    contactid=row["contactid"],
                    notification_type=NotificationType.RATING_REMINDER,
                    ctx=ctx,
                    dedupe_key=f"rating_reminder:{row['contactid']}:{run_date:%Y-%m-%d}",
                ))
            except Exception as exc:
                logger.warning(
                    "rating_reminder: send failed for %s: %s",
                    row["userprofileid"], exc,
                )

    await asyncio.gather(*[_one(r) for r in rows], return_exceptions=True)
    logger.info("rating_reminder: completed (%d recipient(s))", len(rows))


_DIGEST_ALL_USERS_SQL = f"""
SELECT DISTINCT ON (up.contactid) up.userprofileid, up.contactid
FROM etown_userprofile up
WHERE up.userprofileid IS NOT NULL
  AND up.contactid IS NOT NULL
  AND  {HAS_DEVICE_SQL.format(contact="up.contactid", profile="up.userprofileid")}
ORDER BY up.contactid, up.createdon
"""

_DIGEST_COHORT_SQL = """
WITH edges AS (
    SELECT from_userprofile AS u, to_userprofile AS c FROM etown_memberrelation
        WHERE from_userprofile IS NOT NULL AND to_userprofile IS NOT NULL
    UNION
    SELECT to_userprofile, from_userprofile FROM etown_memberrelation
        WHERE from_userprofile IS NOT NULL AND to_userprofile IS NOT NULL
    UNION
    SELECT fromuserprofileid, touserprofileid FROM etown_providerrelation
        WHERE fromuserprofileid IS NOT NULL AND touserprofileid IS NOT NULL
    UNION
    SELECT touserprofileid, fromuserprofileid FROM etown_providerrelation
        WHERE fromuserprofileid IS NOT NULL AND touserprofileid IS NOT NULL
),
recent_offers AS (
    SELECT offersid, userprofileid
    FROM etown_offers
    WHERE createdon >= (now() AT TIME ZONE 'UTC') - INTERVAL '24 hours'
      AND COALESCE(validity, '') <> 'Expired'
      AND userprofileid IS NOT NULL
),
per_user AS (
    SELECT e.u AS userprofileid, up.contactid, COUNT(ro.offersid)::int AS new_offers
    FROM edges e
    JOIN recent_offers ro ON ro.userprofileid = e.c
    JOIN etown_userprofile up ON up.userprofileid = e.u
    WHERE e.u <> e.c
      AND up.contactid IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM etown_offer_views ov
          WHERE ov.offersid = ro.offersid
            AND ov.userprofileid = e.u
      )
    GROUP BY e.u, up.contactid
    HAVING COUNT(ro.offersid) >= $1
)
SELECT DISTINCT ON (contactid) userprofileid, contactid, new_offers
FROM per_user
ORDER BY contactid, new_offers DESC
LIMIT $2
"""


async def run_digest(spec: DigestSpec) -> None:
    """Send one digest push per recipient through governance, to all device users or the circle-offer cohort."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send

    if spec.all_users:
        rows = await Database.fetch(_DIGEST_ALL_USERS_SQL)
        logger.info("%s: %d recipient(s) (all users with a device)", spec.name, len(rows))
    else:
        params = await jcfg.get_params(spec.name)
        min_offers = int(params.get("min_offers", 3))
        max_recipients = int(params.get("max_recipients", 500))
        rows = await Database.fetch(_DIGEST_COHORT_SQL, min_offers, max_recipients)
        logger.info("%s: %d recipient(s) (circle-offer cohort, min_offers=%d, cap=%d)",
                    spec.name, len(rows), min_offers, max_recipients)
    if not rows:
        return
    pool: list = []
    select_variant = None
    params = await jcfg.get_params(spec.name)
    if bool(params.get("llm_enabled", False)):
        try:
            from app.etown.notifications.core.notification_generator import (
                get_active_pool, refresh_copy_pool, select_variant as _select_variant,
            )
            pool = await get_active_pool(spec.type_const)
            if not pool:
                await refresh_copy_pool(spec.type_const, daypart=spec.daypart)
                pool = await get_active_pool(spec.type_const)
            select_variant = _select_variant
            logger.info("%s: using LLM copy pool (%d variant(s))", spec.name, len(pool))
        except Exception as exc:
            logger.warning("%s: copy pool unavailable, using default copy: %s", spec.name, exc)
            pool = []

    run_date = now_ist().date()
    semaphore = asyncio.Semaphore(20)

    async def _one(row) -> None:
        async with semaphore:
            try:
                n = None if spec.all_users else row["new_offers"]
                ctx: dict = {} if n is None else {"n": n}
                if pool and select_variant is not None:
                    variant = select_variant(
                        pool, key=str(row["userprofileid"]), run_date=run_date, n=n,
                    )
                    if variant:
                        ctx["title"] = variant["title"]
                        ctx["body"] = variant["body"]
                await gov_send(GovernanceRequest(
                    userprofileid=row["userprofileid"],
                    contactid=row["contactid"],
                    notification_type=spec.type_const,
                    ctx=ctx,
                    dedupe_key=f"{spec.name}:{row['contactid']}:{run_date:%Y-%m-%d}",
                ))
            except Exception as exc:
                logger.warning("%s: send failed for %s: %s",
                               spec.name, row["userprofileid"], exc)

    await asyncio.gather(*[_one(r) for r in rows], return_exceptions=True)
    logger.info("%s: completed (%d recipient(s))", spec.name, len(rows))


async def _deferred_sweep_runner() -> None:
    """Replay quiet-hours-deferred notifications that are now due."""
    from app.etown.notifications.deferred import replay_due
    count = await replay_due()
    logger.info("deferred_sweep: replayed %d item(s)", count)


_MYCIRCLE_COHORT_SQL = f"""
SELECT DISTINCT ON (up.contactid) up.userprofileid, up.contactid
FROM public.etown_userprofile up
WHERE up.userprofileid IS NOT NULL
  AND up.contactid IS NOT NULL
  AND  {HAS_DEVICE_SQL.format(contact="up.contactid", profile="up.userprofileid")}
ORDER BY up.contactid, up.createdon
"""

_CIRCLE_POSTS_SQL = """
SELECT count(*) AS new_posts
FROM public.etown_feed f
WHERE f.userprofileid = ANY($1::text[])
  AND f.createdon > COALESCE($2, now() - interval '7 days')
  AND f.userprofileid <> $3
"""


async def run_evening_mycircle() -> None:
    """Send a mycircle_activity push to device users whose circle posted at least min_new_posts since their last feed visit."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    from app.etown.notifications.core import config as ncfg
    from app.etown.offers.offers_utils import get_circle_user_profile_ids

    cfg = await ncfg.db_get("mycircle_activity")
    if cfg is None:
        logger.info("evening_mycircle: type disabled in config — skipping")
        return

    min_posts = int((cfg.get("config") or {}).get("min_new_posts", 5))
    users = await Database.fetch(_MYCIRCLE_COHORT_SQL)
    logger.info("evening_mycircle: evaluating %d user(s) for mycircle_activity", len(users))

    today_str = now_ist().strftime("%Y-%m-%d")
    semaphore = asyncio.Semaphore(20)

    async def _one(u: dict) -> None:
        async with semaphore:
            try:
                upid = u["userprofileid"]
                circle = await get_circle_user_profile_ids(upid)
                if not circle:
                    return

                last_seen = await Database.fetchval(
                    "SELECT last_seen_feed_at FROM notifications.etown_feed_last_seen WHERE userprofileid = $1",
                    upid,
                )
                new_posts = await Database.fetchval(
                    _CIRCLE_POSTS_SQL, circle, last_seen, upid,
                ) or 0

                if new_posts < min_posts:
                    return

                town = await Database.fetchval(
                    "SELECT townname FROM public.etown_provider WHERE userprofileid = $1 LIMIT 1",
                    upid,
                ) or "your town"

                await gov_send(GovernanceRequest(
                    userprofileid=upid,
                    contactid=u["contactid"],
                    notification_type="mycircle_activity",
                    ctx={"n": new_posts, "town": town},
                    dedupe_key=f"mycircle:{u['contactid']}:{today_str}",
                ))
            except Exception as exc:
                logger.warning("evening_mycircle: error for user %s: %s", u.get("userprofileid"), exc)

    await asyncio.gather(*[_one(u) for u in users], return_exceptions=True)
    logger.info("evening_mycircle: completed")


_WEEKLY_USERS_SQL = f"""
SELECT DISTINCT ON (up.contactid) up.userprofileid, up.contactid, c.city
FROM   public.etown_userprofile up
JOIN   public.etown_contact c ON c.contactid = up.contactid
WHERE  up.contactid IS NOT NULL
  AND  {HAS_DEVICE_SQL.format(contact="up.contactid", profile="up.userprofileid")}
ORDER BY up.contactid, up.createdon
LIMIT  2000
"""

_WEEKLY_CITY_PROVIDERS_SQL = """
SELECT city, count(*) AS new_count
FROM   public.etown_provider
WHERE  city = ANY($1::text[])
  AND  creationdate >= now() - interval '7 days'
GROUP  BY city
"""

_WEEKLY_MEMBER_CIRCLES_SQL = """
SELECT from_userprofile AS owner, to_userprofile AS circle_upi
FROM   public.etown_memberrelation
WHERE  from_userprofile = ANY($1::text[]) OR to_userprofile = ANY($1::text[])
UNION ALL
SELECT to_userprofile, from_userprofile
FROM   public.etown_memberrelation
WHERE  from_userprofile = ANY($1::text[]) OR to_userprofile = ANY($1::text[])
"""

_WEEKLY_PROVIDER_CIRCLES_SQL = """
SELECT fromuserprofileid AS owner, touserprofileid AS circle_upi
FROM   public.etown_providerrelation
WHERE  fromuserprofileid = ANY($1::text[]) OR touserprofileid = ANY($1::text[])
UNION ALL
SELECT touserprofileid, fromuserprofileid
FROM   public.etown_providerrelation
WHERE  fromuserprofileid = ANY($1::text[]) OR touserprofileid = ANY($1::text[])
"""

_WEEKLY_CIRCLE_OFFERS_SQL = """
SELECT userprofileid, count(*) AS offer_count
FROM   public.etown_offers
WHERE  userprofileid = ANY($1::text[])
  AND  createdtime >= now() - interval '7 days'
GROUP  BY userprofileid
"""


async def run_weekly_digest() -> None:
    """Send a weekly_digest push with new city providers and circle offers to up to 2000 device users."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    from app.etown.notifications.core import config as ncfg

    cfg = await ncfg.db_get("weekly_digest")
    if cfg is None:
        logger.info("weekly_digest: disabled in config — skipping")
        return

    users = await Database.fetch(_WEEKLY_USERS_SQL)
    if not users:
        logger.info("weekly_digest: no users with devices")
        return

    logger.info("weekly_digest: %d user(s) — pre-computing bulk data", len(users))
    today = now_ist().date()
    week_str = f"{today.year}-W{today.isocalendar()[1]:02d}"

    all_upids = [u["userprofileid"] for u in users if u.get("userprofileid")]
    unique_cities = list({u.get("city") for u in users if u.get("city")})

    city_rows = await Database.fetch(_WEEKLY_CITY_PROVIDERS_SQL, unique_cities)
    city_counts: dict = {r["city"]: int(r["new_count"]) for r in city_rows}

    member_rows, provider_rows = await asyncio.gather(
        Database.fetch(_WEEKLY_MEMBER_CIRCLES_SQL, all_upids),
        Database.fetch(_WEEKLY_PROVIDER_CIRCLES_SQL, all_upids),
    )
    upid_set = set(all_upids)
    circles: dict = {}
    for r in (*member_rows, *provider_rows):
        owner, member = r["owner"], r["circle_upi"]
        if owner and member and owner in upid_set:
            circles.setdefault(owner, set()).add(member)

    all_circle_members = list({m for members in circles.values() for m in members})
    offers_by_member: dict = {}
    if all_circle_members:
        offer_rows = await Database.fetch(_WEEKLY_CIRCLE_OFFERS_SQL, all_circle_members)
        offers_by_member = {r["userprofileid"]: int(r["offer_count"]) for r in offer_rows}

    logger.info(
        "weekly_digest: bulk data ready — %d cities, %d circles, %d offer contributors",
        len(city_counts), len(circles), len(offers_by_member),
    )
    semaphore = asyncio.Semaphore(20)

    async def _one(u: dict) -> None:
        async with semaphore:
            try:
                upid = u["userprofileid"]
                city = u.get("city") or "your area"
                new_providers = city_counts.get(city, 0)
                circle_members = circles.get(upid, set())
                new_offers = sum(offers_by_member.get(m, 0) for m in circle_members)
                await gov_send(GovernanceRequest(
                    userprofileid=upid,
                    contactid=u["contactid"],
                    notification_type="weekly_digest",
                    ctx={
                        "city": city,
                        "new_providers": new_providers,
                        "new_offers": new_offers,
                    },
                    dedupe_key=f"weekly-digest:{u['contactid']}:{week_str}",
                ))
            except Exception as exc:
                logger.warning("weekly_digest: error for user %s: %s", u.get("userprofileid"), exc)

    await asyncio.gather(*[_one(u) for u in users], return_exceptions=True)
    logger.info("weekly_digest: completed %d users via 4 bulk queries", len(users))


_DORMANT_USERS_SQL = f"""
SELECT DISTINCT ON (c.contactid) up.userprofileid, c.contactid, c.city
FROM   public.etown_userprofile up
JOIN   public.etown_contact c ON c.contactid = up.contactid
WHERE  c.contactid IS NOT NULL
  AND  {HAS_DEVICE_SQL.format(contact="c.contactid", profile="up.userprofileid")}
  AND  NOT EXISTS (
         SELECT 1 FROM notifications.etown_app_events ae
         WHERE ae.userprofileid = up.userprofileid
           AND ae.event_type = 'app_open'
           AND ae.event_date >= now() - interval '7 days'
       )
  AND  NOT EXISTS (
         SELECT 1 FROM notifications.etown_notification_ledger nl
         WHERE nl.contactid = c.contactid
           AND nl.notification_type = 're_engagement'
           AND nl.sent_on >= now() - interval '7 days'
       )
ORDER BY c.contactid, up.createdon
LIMIT 500
"""


async def run_re_engagement() -> None:
    """Send a weekly re_engagement push to up to 500 device users with no app open in 7 days."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    from app.etown.notifications.core import config as ncfg

    cfg = await ncfg.db_get("re_engagement")
    if cfg is None:
        logger.info("re_engagement: disabled in config — skipping")
        return

    dormant = await Database.fetch(_DORMANT_USERS_SQL)
    if not dormant:
        logger.info("re_engagement: no dormant users")
        return

    logger.info("re_engagement: re-engaging %d user(s)", len(dormant))
    today = now_ist().date()
    week_str = f"{today.year}-W{today.isocalendar()[1]:02d}"
    semaphore = asyncio.Semaphore(20)

    async def _one(u: dict) -> None:
        async with semaphore:
            try:
                await gov_send(GovernanceRequest(
                    userprofileid=u["userprofileid"],
                    contactid=u["contactid"],
                    notification_type="re_engagement",
                    ctx={"town": u.get("city") or "your town"},
                    dedupe_key=f"re-engagement:{u['contactid']}:{week_str}",
                ))
            except Exception as exc:
                logger.warning(
                    "re_engagement: error for user %s: %s", u.get("userprofileid"), exc
                )

    await asyncio.gather(*[_one(u) for u in dormant], return_exceptions=True)
    logger.info("re_engagement: completed")


_NON_PROVIDER_USERS_SQL = f"""
SELECT DISTINCT ON (c.contactid) up.userprofileid, c.contactid, c.city
FROM   public.etown_userprofile up
JOIN   public.etown_contact c ON c.contactid = up.contactid
WHERE  c.contactid IS NOT NULL
  AND  c.createdon <= now() - interval '3 days'
  AND  NOT EXISTS (
         SELECT 1 FROM public.etown_provider p
         JOIN public.etown_userprofile up2 ON up2.userprofileid = p.userprofileid
         WHERE up2.contactid = c.contactid
       )
  AND  {HAS_DEVICE_SQL.format(contact="c.contactid", profile="up.userprofileid")}
  AND  NOT EXISTS (
         SELECT 1 FROM notifications.etown_notification_ledger nl
         WHERE nl.contactid = c.contactid
           AND nl.notification_type = 'provider_onboard_nudge'
           AND nl.sent_on >= now() - interval '15 days'
       )
ORDER BY c.contactid, up.createdon
LIMIT 1000
"""

_TOWN_COMMUNITY_NUDGE_USERS_SQL = """
WITH profiles AS (
    SELECT up.userprofileid,
           NULLIF(up.contactid, '') AS contactid,
           COALESCE(NULLIF(up.contactid, ''), up.userprofileid) AS recipient_key,
           up.createdon
    FROM public.etown_userprofile up
),

reach_rows AS (
    SELECT p.recipient_key, p.contactid IS NOT NULL AS has_contact,
           COALESCE(NULLIF(cd.device_token, ''), NULLIF(cd.deviceguid, '')) AS token
    FROM profiles p
    JOIN public.etown_contactdevices cd ON cd.userprofileid = p.userprofileid
    UNION ALL
    SELECT p.recipient_key, TRUE AS has_contact,
           COALESCE(NULLIF(cd.device_token, ''), NULLIF(cd.deviceguid, '')) AS token
    FROM (SELECT DISTINCT contactid, recipient_key FROM profiles WHERE contactid IS NOT NULL) p
    JOIN public.etown_contactdevices cd ON cd.contactid = p.contactid
),

reach AS (
    SELECT DISTINCT recipient_key, has_contact, split_part(token, ':', 1) AS instance
    FROM reach_rows
    WHERE NULLIF(split_part(token, ':', 1), '') IS NOT NULL
),

-- A phone reached by several recipients goes only to the first: contact-linked before contactless, then by key.
blocked AS (
    SELECT DISTINCT r.recipient_key
    FROM reach r
    JOIN reach o
      ON o.instance = r.instance
     AND o.recipient_key <> r.recipient_key
     AND (
         (o.has_contact AND NOT r.has_contact)
         OR (o.has_contact = r.has_contact AND o.recipient_key < r.recipient_key)
     )
),

cohort AS (
    SELECT DISTINCT ON (p.recipient_key) p.userprofileid, p.contactid, p.recipient_key
    FROM profiles p
    WHERE ($1::text IS NULL OR p.recipient_key > $1::text)
      AND EXISTS (SELECT 1 FROM reach r WHERE r.recipient_key = p.recipient_key)
      AND NOT EXISTS (SELECT 1 FROM blocked b WHERE b.recipient_key = p.recipient_key)
    ORDER BY p.recipient_key, p.createdon, p.userprofileid
),

history AS (
    SELECT
        c.recipient_key,
        COUNT(nl.id) FILTER (
            WHERE nl.status = 'sent'
        )::int AS sent_count,
        MAX(nl.created_at) FILTER (
            WHERE nl.status IN ('sent', 'sending')
        ) AS last_attempt_at
    FROM cohort c
    LEFT JOIN notifications.etown_notification_ledger nl
        ON COALESCE(nl.contactid, nl.userprofileid) = c.recipient_key
       AND nl.notification_type = 'town_community_nudge'
       AND nl.channel = 'push'
    GROUP BY c.recipient_key
)
SELECT c.userprofileid, c.contactid, c.recipient_key, COALESCE(h.sent_count, 0) AS sent_count, h.last_attempt_at FROM cohort c
JOIN history h
    ON h.recipient_key = c.recipient_key
WHERE h.last_attempt_at IS NULL
   OR h.last_attempt_at <= NOW() - $3::interval
ORDER BY c.recipient_key
LIMIT $2
"""

_TOWN_COMMUNITY_ROUTES = {
    "need": "/createneed",
    "update": "/createupdate",
    "event": "/createevent",
}


async def run_provider_onboard_nudge() -> None:
    """Nudge device users older than 3 days with no provider listing, at most once per 15 days, to list a business."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    from app.etown.notifications.core import config as ncfg

    cfg = await ncfg.db_get("provider_onboard_nudge")
    if cfg is None:
        logger.info("provider_onboard_nudge: disabled in config — skipping")
        return

    users = await Database.fetch(_NON_PROVIDER_USERS_SQL)
    if not users:
        logger.info("provider_onboard_nudge: no eligible users")
        return

    logger.info("provider_onboard_nudge: nudging %d user(s)", len(users))
    today = now_ist().date().isoformat()
    semaphore = asyncio.Semaphore(20)

    async def _one(u: dict) -> None:
        async with semaphore:
            try:
                await gov_send(GovernanceRequest(
                    userprofileid=u["userprofileid"],
                    contactid=u["contactid"],
                    notification_type="provider_onboard_nudge",
                    ctx={},
                    dedupe_key=f"onboard-nudge:{u['contactid']}:{today}",
                ))
            except Exception as exc:
                logger.warning(
                    "provider_onboard_nudge: error for user %s: %s",
                    u.get("userprofileid"), exc,
                )

    await asyncio.gather(*[_one(u) for u in users], return_exceptions=True)
    logger.info("provider_onboard_nudge: completed")


async def run_town_community_nudge() -> None:
    """Push each due profile, one per contact, the next rotating Town Community template, skipping in shadow mode or on placeholder routes."""
    from app.etown.notifications.service import (  GovernanceRequest, send as gov_send, )
    from app.etown.notifications.core import config as ncfg
    from app.etown.notifications.core.templates import ( TOWN_COMMUNITY_TEMPLATES, )
    from app.etown.notifications import ledger
    notification_type = "town_community_nudge"
    cooldown = timedelta(hours=47)
    cfg = await ncfg.db_get(notification_type)
    if cfg is None:
        logger.info("%s: disabled in notification config; skipping", notification_type)
        return
    if ncfg.is_shadow_mode():
        logger.warning(
            "%s: shadow mode is on; skipping run so no recipient's rotation or cooldown changes",
            notification_type,
        )
        return
    routes = dict(_TOWN_COMMUNITY_ROUTES)
    unconfigured = sorted(
        category for category, route in routes.items()
        if not route or route.startswith("REPLACE_WITH_")
    )
    if unconfigured:
        logger.warning(
            "%s: creation route not configured for %s; skipping run",
            notification_type, ", ".join(unconfigured),
        )
        return
    batch_size = 500
    after_key = None
    total_eligible = 0
    counts = {"fcm_accepted": 0, "already_sent": 0, "failed": 0, "suppressed": 0, "errors": 0}
    while True:
        users = await Database.fetch(
            _TOWN_COMMUNITY_NUDGE_USERS_SQL,
            after_key,
            batch_size,
            cooldown,
        )
        if not users:
            break
        after_key = users[-1]["recipient_key"]
        total_eligible += len(users)
        for user in users:
            try:
                userprofile_id = user["userprofileid"]
                contact_id = user["contactid"]
                recipient_key = user["recipient_key"]
                sequence = int(user["sent_count"])
                template_index = sequence % 9
                category_index = template_index % 3
                template_number = template_index // 3 + 1
                category = ("need", "update", "event")[category_index]
                template = TOWN_COMMUNITY_TEMPLATES[category][template_number]
                route = routes[category]
                dedupe_key = (f"town-community-nudge:{recipient_key}:{sequence}")
                if await ledger.was_sent(f"{dedupe_key}:push"):
                    counts["already_sent"] += 1
                    continue
                result = await gov_send(
                    GovernanceRequest(
                        userprofileid=userprofile_id,
                        contactid=contact_id,
                        notification_type=notification_type,
                        ctx={
                            "title": template["title"],
                            "body": template["body"],
                            "category": category,
                            "template_number": template_number,
                            "sequence": sequence,
                            "route": route,
                        },
                        dedupe_key=dedupe_key,
                        feature="town_community",
                    )
                )
                if result == "sent":
                    counts["fcm_accepted"] += 1
                else:
                    counts["suppressed" if result == "suppressed" else "failed"] += 1
                    logger.info("%s: send result=%s recipient=%s sequence=%s",notification_type,result,recipient_key,sequence,)
            except Exception:
                counts["errors"] += 1
                logger.exception("%s: recipient processing failed; recipient=%s",notification_type,user.get("recipient_key"),)
    logger.info(
        "%s: completed; eligible=%d, fcm_accepted=%d, already_sent=%d, failed=%d, suppressed=%d, errors=%d",
        notification_type, total_eligible, counts["fcm_accepted"], counts["already_sent"],
        counts["failed"], counts["suppressed"], counts["errors"],
    )

_PROVIDER_WEEKLY_VIEWS_SQL = f"""
WITH weekly_views AS (
    SELECT v.providerprofileid, COUNT(DISTINCT v.userprofileid) AS view_count
    FROM   public.etown_provider_views v
    LEFT   JOIN public.etown_userprofile vup ON vup.userprofileid = v.userprofileid
    WHERE  v.created_on >= now() - interval '7 days'
      AND  v.userprofileid IS NOT NULL
      AND  v.userprofileid <> v.providerprofileid
      AND  NOT public.etown_is_deleted(v.userprofileid)
      AND  NOT public.etown_is_deleted(vup.contactid)
    GROUP  BY v.providerprofileid
    HAVING COUNT(DISTINCT v.userprofileid) >= $1
)
SELECT wv.providerprofileid AS userprofileid, up.contactid, wv.view_count,
       (SELECT p.businessname FROM public.etown_provider p
         WHERE p.userprofileid = wv.providerprofileid LIMIT 1) AS businessname
FROM   weekly_views wv
JOIN   public.etown_userprofile up ON up.userprofileid = wv.providerprofileid
WHERE  {HAS_DEVICE_SQL.format(contact="up.contactid", profile="wv.providerprofileid")}
  AND  NOT EXISTS (SELECT 1 FROM notifications.etown_notification_ledger nl
                   WHERE nl.userprofileid = wv.providerprofileid
                     AND nl.notification_type = 'provider_weekly_views'
                     AND nl.sent_on >= now() - interval '7 days')
LIMIT 1000
"""


async def run_provider_weekly_views() -> None:
    """Push to providers with at least view_threshold distinct profile viewers in the last 7 days."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    from app.etown.notifications.core import config as ncfg

    cfg = await ncfg.db_get("provider_weekly_views")
    if cfg is None:
        logger.info("provider_weekly_views: disabled in config — skipping")
        return

    threshold = int((cfg.get("config") or {}).get("view_threshold", 25))
    providers = _unique_by(await Database.fetch(_PROVIDER_WEEKLY_VIEWS_SQL, threshold), "userprofileid")
    if not providers:
        logger.info("provider_weekly_views: no providers reached %d views this week", threshold)
        return

    logger.info("provider_weekly_views: notifying %d provider(s) (threshold=%d)",
                len(providers), threshold)
    today = now_ist().date()
    week_str = f"{today.year}-W{today.isocalendar()[1]:02d}"
    semaphore = asyncio.Semaphore(20)

    async def _one(p: dict) -> None:
        async with semaphore:
            try:
                await gov_send(GovernanceRequest(
                    userprofileid=p["userprofileid"],
                    contactid=p["contactid"],
                    notification_type="provider_weekly_views",
                    ctx={"n": p["view_count"], "business": p.get("businessname"), "contactid": p["contactid"], "userprofileid": p["userprofileid"]},
                    dedupe_key=f"provider-weekly-views:{p['userprofileid']}:{week_str}",
                ))
            except Exception as exc:
                logger.warning(
                    "provider_weekly_views: error for provider %s: %s",
                    p.get("userprofileid"), exc,
                )

    await asyncio.gather(*[_one(p) for p in providers], return_exceptions=True)
    logger.info("provider_weekly_views: completed")


_PROVIDER_VIEWS_MILESTONE_SQL = f"""
WITH milestones(threshold) AS (
    SELECT unnest($1::int[])
),
view_counts AS (
    SELECT v.providerprofileid, COUNT(DISTINCT v.userprofileid) AS total_views
    FROM   public.etown_provider_views v
    LEFT   JOIN public.etown_userprofile vup ON vup.userprofileid = v.userprofileid
    WHERE  v.userprofileid IS NOT NULL
      AND  v.userprofileid <> v.providerprofileid
      AND  NOT public.etown_is_deleted(v.userprofileid)
      AND  NOT public.etown_is_deleted(vup.contactid)
    GROUP  BY v.providerprofileid
),
reached AS (
    SELECT vc.providerprofileid, MAX(m.threshold) AS milestone
    FROM   view_counts vc
    JOIN   milestones m ON vc.total_views >= m.threshold
    GROUP  BY vc.providerprofileid
)
SELECT r.providerprofileid AS userprofileid, up.contactid, r.milestone,
       (SELECT p.businessname FROM public.etown_provider p
         WHERE p.userprofileid = r.providerprofileid LIMIT 1) AS businessname
FROM   reached r
JOIN   public.etown_userprofile up ON up.userprofileid = r.providerprofileid
WHERE  {HAS_DEVICE_SQL.format(contact="up.contactid", profile="r.providerprofileid")}
  AND  NOT EXISTS (
         SELECT 1 FROM notifications.etown_notification_ledger nl
         WHERE nl.userprofileid = r.providerprofileid
           AND nl.notification_type = 'provider_views_milestone'
           AND nl.dedupe_key LIKE
               'provider-views-milestone:' || r.providerprofileid || ':' || r.milestone || ':%'
       )
LIMIT 1000
"""

_DEFAULT_VIEW_MILESTONES = [50, 100, 250, 500, 1000, 2500, 5000]


async def run_provider_views_milestone() -> None:
    """Push to providers whose lifetime distinct viewers reached a new view milestone."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    from app.etown.notifications.core import config as ncfg

    cfg = await ncfg.db_get("provider_views_milestone")
    if cfg is None:
        logger.info("provider_views_milestone: disabled in config — skipping")
        return

    milestones = (cfg.get("config") or {}).get("milestones") or _DEFAULT_VIEW_MILESTONES
    milestones = sorted({int(m) for m in milestones})
    providers = _unique_by(await Database.fetch(_PROVIDER_VIEWS_MILESTONE_SQL, milestones), "userprofileid")
    if not providers:
        logger.info("provider_views_milestone: no new milestones reached")
        return

    logger.info("provider_views_milestone: notifying %d provider(s) (milestones=%s)",
                len(providers), milestones)
    semaphore = asyncio.Semaphore(20)

    async def _one(p: dict) -> None:
        async with semaphore:
            try:
                milestone = p["milestone"]
                await gov_send(GovernanceRequest(
                    userprofileid=p["userprofileid"],
                    contactid=p["contactid"],
                    notification_type="provider_views_milestone",
                    ctx={"n": milestone, "business": p.get("businessname"), "contactid": p["contactid"], "userprofileid": p["userprofileid"]},
                    dedupe_key=f"provider-views-milestone:{p['userprofileid']}:{milestone}",
                ))
            except Exception as exc:
                logger.warning(
                    "provider_views_milestone: error for provider %s: %s",
                    p.get("userprofileid"), exc,
                )

    await asyncio.gather(*[_one(p) for p in providers], return_exceptions=True)
    logger.info("provider_views_milestone: completed")


_DEFAULT_LIKE_MILESTONES = [10, 25, 50, 100, 250]

_PROVIDER_LIKED_SQL = """
WITH milestones(threshold) AS (
    SELECT unnest($1::int[])
),
fav_counts AS (
    SELECT ci.providerid, COUNT(*) AS fav_count
    FROM   public.etown_collectionitems ci
    WHERE  ci.isfavourite IS NULL OR LOWER(ci.isfavourite) <> 'unfavourite'
    GROUP  BY ci.providerid
),
reached AS (
    SELECT fc.providerid, fc.fav_count, MAX(m.threshold) AS milestone
    FROM   fav_counts fc
    JOIN   milestones m ON fc.fav_count >= m.threshold
    GROUP  BY fc.providerid, fc.fav_count
)
SELECT p.userprofileid, p.contactid, p.businessname, p.townname,
       r.fav_count, r.milestone
FROM   reached r
JOIN   public.etown_provider p ON p.providerid = r.providerid
WHERE  p.userprofileid IS NOT NULL
  AND  NOT EXISTS (
         SELECT 1 FROM notifications.etown_notification_ledger nl
         WHERE nl.userprofileid = p.userprofileid
           AND nl.notification_type = 'provider_liked'
           AND nl.dedupe_key LIKE
               'provider-liked:' || p.userprofileid || ':' || r.milestone || ':%')
LIMIT 1000
"""

_PROVIDER_LIKED_CIRCLE_SQL = """
SELECT DISTINCT ON (contactid) userprofileid, contactid
FROM (
    SELECT
        CASE WHEN from_userprofile = $1 THEN to_userprofile ELSE from_userprofile END AS userprofileid,
        CASE WHEN from_userprofile = $1 THEN to_contactid   ELSE from_contactid   END AS contactid
    FROM public.etown_memberrelation
    WHERE from_userprofile = $1 OR to_userprofile = $1
    UNION
    SELECT
        CASE WHEN fromuserprofileid = $1 THEN touserprofileid ELSE fromuserprofileid END,
        CASE WHEN fromuserprofileid = $1 THEN tocontactid      ELSE fromcontactid     END
    FROM public.etown_providerrelation
    WHERE fromuserprofileid = $1 OR touserprofileid = $1
) t
WHERE contactid IS NOT NULL
ORDER BY contactid
"""


async def run_provider_liked() -> None:
    """Notify a provider and their circle when the provider's favourite count crosses a new milestone."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    from app.etown.notifications.core import config as ncfg

    cfg = await ncfg.db_get("provider_liked")
    if cfg is None:
        logger.info("provider_liked: disabled in config — skipping")
        return

    milestones = (cfg.get("config") or {}).get("like_milestones") or _DEFAULT_LIKE_MILESTONES
    milestones = sorted({int(m) for m in milestones})
    providers = _unique_by(await Database.fetch(_PROVIDER_LIKED_SQL, milestones), "userprofileid")
    if not providers:
        logger.info("provider_liked: no new favourite milestones reached")
        return

    logger.info("provider_liked: %d provider(s) crossed a favourite milestone (ladder=%s)",
                len(providers), milestones)
    semaphore = asyncio.Semaphore(20)

    async def _one(p: dict) -> None:
        async with semaphore:
            try:
                upid = p["userprofileid"]
                business = p.get("businessname") or "Your profile"
                town = p.get("townname") or ""
                n = p["fav_count"]
                milestone = p["milestone"]
                await gov_send(GovernanceRequest(
                    userprofileid=upid,
                    contactid=p.get("contactid"),
                    notification_type="provider_liked",
                    ctx={
                        "business": business, "n": n, "town": town,
                        "contactid": p.get("contactid", ""),
                        "userprofileid": upid,
                        "title": "\U0001F31F People love your profile!",
                        "body": f"{n} people have added {business} to their favorites on etown. "
                                f"You're a local favorite!",
                    },
                    dedupe_key=f"provider-liked:{upid}:{milestone}:prov",
                ))
                circle = await Database.fetch(_PROVIDER_LIKED_CIRCLE_SQL, upid)
                for u in circle:
                    member = u["userprofileid"]
                    if not member or member == upid:
                        continue
                    circle_contactid = u.get("contactid") or member
                    await gov_send(GovernanceRequest(
                        userprofileid=member,
                        contactid=u.get("contactid"),
                        notification_type="provider_liked",
                        sender_businessid=upid,
                        ctx={
                            "business": business, "n": n, "town": town,
                            "contactid": p.get("contactid", ""),
                            "userprofileid": upid,
                        },
                        dedupe_key=f"provider-liked:{upid}:{circle_contactid}:{milestone}",
                    ))
            except Exception as exc:
                logger.warning("provider_liked: error for provider %s: %s",
                               p.get("userprofileid"), exc)

    await asyncio.gather(*[_one(p) for p in providers], return_exceptions=True)
    logger.info("provider_liked: completed")


_NEW_RATING_SQL = f"""
SELECT r.provideruserprofileid AS userprofileid,
       r.providercontactid     AS contactid,
       r.providerbusinessname  AS businessname,
       r.overallrating, r.ratingcount
FROM   public.etown_ratings r
WHERE  r.provideruserprofileid IS NOT NULL
  AND  COALESCE(r.ratingcount, 0) >= 1
  AND  {HAS_DEVICE_SQL.format(contact="r.providercontactid", profile="r.provideruserprofileid")}
  AND  NOT EXISTS (
         SELECT 1 FROM notifications.etown_notification_ledger nl
         WHERE nl.userprofileid = r.provideruserprofileid
           AND nl.notification_type = 'new_rating'
           AND nl.dedupe_key LIKE
               'new-rating:' || r.provideruserprofileid || ':' || r.ratingcount || ':%')
LIMIT 1000
"""


async def run_new_rating() -> None:
    """Notify each provider with a device once per rating count reached."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    from app.etown.notifications.core import config as ncfg

    cfg = await ncfg.db_get("new_rating")
    if cfg is None:
        logger.info("new_rating: disabled in config — skipping")
        return

    rows = _unique_by(await Database.fetch(_NEW_RATING_SQL), "userprofileid")
    if not rows:
        logger.info("new_rating: no providers with a new rating count")
        return

    logger.info("new_rating: notifying %d provider(s)", len(rows))
    semaphore = asyncio.Semaphore(20)

    async def _one(r: dict) -> None:
        async with semaphore:
            try:
                count = r["ratingcount"]
                await gov_send(GovernanceRequest(
                    userprofileid=r["userprofileid"],
                    contactid=r.get("contactid"),
                    notification_type="new_rating",
                    ctx={
                        "business": r.get("businessname"),
                        "n": count,
                        "rating": r.get("overallrating"),
                        "contactid": r.get("contactid", ""),
                        "userprofileid": r["userprofileid"],
                    },
                    dedupe_key=f"new-rating:{r['userprofileid']}:{count}",
                ))
            except Exception as exc:
                logger.warning("new_rating: error for provider %s: %s",
                               r.get("userprofileid"), exc)

    await asyncio.gather(*[_one(r) for r in rows], return_exceptions=True)
    logger.info("new_rating: completed")


_DEFAULT_TOWN_VIEW_MILESTONES = [50, 100, 250, 500, 1000]

_TOWN_VIEW_MILESTONE_SQL = """
WITH milestones(threshold) AS (
    SELECT unnest($1::int[])
),
view_counts AS (
    SELECT tv.townprofileid, COUNT(*) AS view_count
    FROM   public.etown_town_view_tracking tv
    GROUP  BY tv.townprofileid
),
reached AS (
    SELECT vc.townprofileid, vc.view_count, MAX(m.threshold) AS milestone
    FROM   view_counts vc
    JOIN   milestones m ON vc.view_count >= m.threshold
    GROUP  BY vc.townprofileid, vc.view_count
)
SELECT tp.townprofileid, tp.townname, r.view_count, r.milestone
FROM   reached r
JOIN   public.etown_townprofile tp ON tp.townprofileid = r.townprofileid
WHERE  tp.townname IS NOT NULL AND TRIM(tp.townname) <> ''
LIMIT 1000
"""

_TOWN_PROVIDERS_SQL = f"""
WITH candidates AS (
    SELECT DISTINCT ON (p.userprofileid)
           p.userprofileid, p.businessname,
           COALESCE(NULLIF(up.contactid, ''), NULLIF(p.contactid, '')) AS contactid,
           COALESCE(NULLIF(up.contactid, ''), NULLIF(p.contactid, ''), p.userprofileid) AS recipient_key
    FROM   public.etown_provider p
    LEFT   JOIN public.etown_userprofile up ON up.userprofileid = p.userprofileid
    WHERE  LOWER(TRIM(p.city)) = LOWER(TRIM($1))
      AND  p.userprofileid IS NOT NULL
      AND  {HAS_DEVICE_SQL.format(contact="p.contactid", profile="p.userprofileid")}
    ORDER  BY p.userprofileid
),

notified AS (
    SELECT DISTINCT nl.userprofileid
    FROM   notifications.etown_notification_ledger nl
    WHERE  nl.notification_type = 'town_view_milestone'
      AND  nl.dedupe_key LIKE 'town-view-milestone:' || $2 || ':%:' || $3 || ':%'
),

people AS (
    SELECT pe.uid, NULLIF(up.contactid, '') AS contactid
    FROM   (SELECT userprofileid AS uid FROM candidates
            UNION
            SELECT userprofileid FROM notified) pe
    LEFT   JOIN public.etown_userprofile up ON up.userprofileid = pe.uid
),

-- Phones each profile's push fans out to, resolved like _fetch_person_device_rows().
instances AS (
    SELECT DISTINCT pe.uid,
           split_part(COALESCE(NULLIF(cd.device_token, ''), NULLIF(cd.deviceguid, '')), ':', 1) AS instance
    FROM   people pe
    JOIN   public.etown_contactdevices cd
      ON   cd.userprofileid = pe.uid
       OR  cd.contactid = pe.contactid
       OR  cd.userprofileid IN (SELECT up.userprofileid FROM public.etown_userprofile up
                                WHERE NULLIF(up.contactid, '') = pe.contactid)
    WHERE  NULLIF(split_part(COALESCE(NULLIF(cd.device_token, ''), NULLIF(cd.deviceguid, '')), ':', 1), '') IS NOT NULL
)

-- One push per person: skip a profile whose contact or phone already got this milestone or goes to an earlier candidate.
SELECT c.userprofileid, c.contactid, c.businessname
FROM   candidates c
WHERE  NOT EXISTS (
         SELECT 1
         FROM   notified n
         JOIN   people pe ON pe.uid = n.userprofileid
         WHERE  COALESCE(pe.contactid, pe.uid) = c.recipient_key
            OR  EXISTS (SELECT 1 FROM instances a JOIN instances b ON b.instance = a.instance
                        WHERE a.uid = c.userprofileid AND b.uid = n.userprofileid))
  AND  NOT EXISTS (
         SELECT 1
         FROM   candidates o
         WHERE  o.userprofileid < c.userprofileid
           AND  (o.recipient_key = c.recipient_key
                 OR EXISTS (SELECT 1 FROM instances a JOIN instances b ON b.instance = a.instance
                            WHERE a.uid = c.userprofileid AND b.uid = o.userprofileid)))
ORDER  BY c.userprofileid
LIMIT 2000
"""


async def run_town_view_milestone() -> None:
    """Notify device providers whose city matches a town that has crossed a view milestone."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    from app.etown.notifications.core import config as ncfg

    cfg = await ncfg.db_get("town_view_milestone")
    if cfg is None:
        logger.info("town_view_milestone: disabled in config — skipping")
        return

    milestones = (cfg.get("config") or {}).get("view_milestones") or _DEFAULT_TOWN_VIEW_MILESTONES
    milestones = sorted({int(m) for m in milestones})
    towns = await Database.fetch(_TOWN_VIEW_MILESTONE_SQL, milestones)
    if not towns:
        logger.info("town_view_milestone: no towns crossed a milestone")
        return

    logger.info("town_view_milestone: %d town(s) reached a milestone (ladder=%s)",
                len(towns), milestones)
    semaphore = asyncio.Semaphore(20)

    async def _one(t: dict) -> None:
        async with semaphore:
            try:
                townname = t["townname"]
                milestone = t["milestone"]
                view_count = t["view_count"]
                providers = _unique_by(await Database.fetch(
                    _TOWN_PROVIDERS_SQL, townname, t["townprofileid"], str(milestone),
                ), "userprofileid")
                for p in providers:
                    await gov_send(GovernanceRequest(
                        userprofileid=p["userprofileid"],
                        contactid=p.get("contactid"),
                        notification_type="town_view_milestone",
                        ctx={"town": townname, "n": view_count},
                        dedupe_key=f"town-view-milestone:{t['townprofileid']}:{p['userprofileid']}:{milestone}",
                    ))
            except Exception as exc:
                logger.warning("town_view_milestone: error for town %s: %s",
                               t.get("townprofileid"), exc)

    await asyncio.gather(*[_one(t) for t in towns], return_exceptions=True)
    logger.info("town_view_milestone: completed")


_FEED_NO_POST_NUDGE_SQL = f"""
WITH ist_day AS (
    SELECT (now() AT TIME ZONE 'Asia/Kolkata')::date AS ist_date,
           ((((now() AT TIME ZONE 'Asia/Kolkata')::date)::timestamp
               AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'UTC') AS day_start
)
SELECT q.userprofileid, q.contactid, q.opened_at
FROM   notifications.etown_feed_nudge_queue q, ist_day d
WHERE  q.opened_at <= now() - ($1 || ' minutes')::interval
  AND  (q.opened_at AT TIME ZONE 'Asia/Kolkata')::date = d.ist_date
  AND  {HAS_DEVICE_SQL.format(contact="q.contactid", profile="q.userprofileid")}
  AND  NOT EXISTS (SELECT 1 FROM public.etown_feed f
                   WHERE f.contactid = q.contactid
                     AND f.createdon >= d.day_start)
  AND  NOT EXISTS (SELECT 1 FROM public.etown_feedcomments fc
                   WHERE fc.commentedbycontactid = q.contactid
                     AND fc.createdon >= d.day_start)
  AND  NOT EXISTS (SELECT 1 FROM public.etown_feedlikes fl
                   WHERE fl.likedbycontactid = q.contactid
                     AND fl.createdond >= d.day_start)
  AND  NOT EXISTS (SELECT 1 FROM notifications.etown_notification_ledger nl
                   WHERE nl.contactid = q.contactid
                     AND nl.notification_type = 'feed_no_post_nudge'
                     AND nl.sent_on = d.ist_date)
LIMIT 1000
"""

_FEED_NUDGE_QUEUE_PURGE_SQL = """
DELETE FROM notifications.etown_feed_nudge_queue q
WHERE  (q.opened_at AT TIME ZONE 'Asia/Kolkata')::date
         < (now() AT TIME ZONE 'Asia/Kolkata')::date
  OR   EXISTS (SELECT 1 FROM notifications.etown_notification_ledger nl
               WHERE nl.contactid = q.contactid
                 AND nl.notification_type = 'feed_no_post_nudge'
                 AND nl.sent_on = (now() AT TIME ZONE 'Asia/Kolkata')::date)
"""


async def run_feed_no_post_nudge() -> None:
    """Nudge queued contacts who opened the feed delay_minutes ago with no post, comment or like today, then dequeue them."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    from app.etown.notifications.core import config as ncfg

    cfg = await ncfg.db_get("feed_no_post_nudge")
    if cfg is None:
        return

    delay_minutes = int((cfg.get("config") or {}).get("delay_minutes", 5))
    try:
        await Database.execute(_FEED_NUDGE_QUEUE_PURGE_SQL)
    except Exception as exc:
        logger.warning("feed_no_post_nudge: queue purge failed: %s", exc)

    rows = await Database.fetch(_FEED_NO_POST_NUDGE_SQL, str(delay_minutes))
    if not rows:
        return

    try:
        cids = [u["contactid"] for u in rows if u["contactid"]]
        if cids:
            await Database.execute(
                "DELETE FROM notifications.etown_feed_nudge_queue WHERE contactid = ANY($1::text[])",
                cids,
            )
    except Exception as exc:
        logger.warning("feed_no_post_nudge: queue cleanup failed: %s", exc)

    pool: list = []
    select_variant = None
    job_params = await jcfg.get_params("feed_no_post_nudge")
    if bool(job_params.get("llm_enabled", False)):
        try:
            from app.etown.notifications.core.notification_generator import (
                get_active_pool, refresh_copy_pool, select_variant as _select_variant,
                FEED_NO_POST_NUDGE as FEED_KEY,
            )
            pool = await get_active_pool(FEED_KEY)
            if not pool:
                await refresh_copy_pool(FEED_KEY, daypart="feed_no_post_nudge")
                pool = await get_active_pool(FEED_KEY)
            select_variant = _select_variant
            logger.info("feed_no_post_nudge: using LLM copy pool (%d variant(s))", len(pool))
        except Exception as exc:
            logger.warning("feed_no_post_nudge: copy pool unavailable, using fallback copy: %s", exc)
            pool = []

    logger.info("feed_no_post_nudge: nudging %d user(s) (delay=%dm)", len(rows), delay_minutes)
    run_date = now_ist().date()
    today = run_date.isoformat()
    semaphore = asyncio.Semaphore(20)

    async def _one(u: dict) -> None:
        async with semaphore:
            try:
                ctx: dict = {"userprofileid": str(u["userprofileid"])}
                if pool and select_variant is not None:
                    variant = select_variant(
                        pool, key=str(u["userprofileid"]), run_date=run_date, n=None,
                    )
                    if variant:
                        ctx["title"] = variant["title"]
                        ctx["body"] = variant["body"]
                await gov_send(GovernanceRequest(
                    userprofileid=u["userprofileid"],
                    contactid=u["contactid"],
                    notification_type="feed_no_post_nudge",
                    ctx=ctx,
                    dedupe_key=f"feed-no-post-nudge:{u['contactid']}:{today}",
                ))
            except Exception as exc:
                logger.warning(
                    "feed_no_post_nudge: error for user %s: %s", u.get("userprofileid"), exc,
                )

    await asyncio.gather(*[_one(u) for u in rows], return_exceptions=True)
    logger.info("feed_no_post_nudge: completed")


_NEW_TOWNS_YESTERDAY_SQL = """
SELECT townprofileid, townname,
       townlatitude::double precision  AS lat,
       townlongitude::double precision AS lon
FROM   public.etown_townprofile
WHERE  townlatitude  ~ '^-?[0-9]+(\\.[0-9]+)?$'
  AND  townlongitude ~ '^-?[0-9]+(\\.[0-9]+)?$'
  AND  (createdon AT TIME ZONE 'Asia/Kolkata')::date
         = ((now() AT TIME ZONE 'Asia/Kolkata')::date - 1)
  AND  COALESCE(townstatus, 'active') <> 'inactive'
LIMIT 200
"""


async def run_new_town_nearby() -> None:
    """Announce towns created yesterday to users within the configured proximity radius, 25 km by default."""
    from app.etown.notifications.core import config as ncfg
    from app.etown.notifications.triggers import notify_new_town_nearby

    cfg = await ncfg.db_get("new_town_nearby")
    if cfg is None:
        logger.info("new_town_nearby: disabled in config — skipping")
        return

    radius_km = float(cfg.get("proximity_km") or 25)
    towns = await Database.fetch(_NEW_TOWNS_YESTERDAY_SQL)
    if not towns:
        logger.info("new_town_nearby: no towns created yesterday")
        return

    logger.info("new_town_nearby: announcing %d new town(s) within %.0f km", len(towns), radius_km)
    for t in towns:
        try:
            await notify_new_town_nearby(
                t["townname"] or "A new town",
                t["lat"], t["lon"],
                town_id=t["townprofileid"],
                radius_km=radius_km,
            )
        except Exception as exc:
            logger.warning("new_town_nearby: error for town=%s: %s", t.get("townname"), exc)
    logger.info("new_town_nearby: completed")


_RATING_PROMPT_ELIGIBLE_SQL = f"""
SELECT DISTINCT ON (up.contactid) up.userprofileid, up.contactid
FROM   public.etown_userprofile up
WHERE  up.contactid IS NOT NULL
  AND  {HAS_DEVICE_SQL.format(contact="up.contactid", profile="up.userprofileid")}
  AND  EXISTS (
         SELECT 1 FROM notifications.etown_app_events ae
         WHERE ae.userprofileid = up.userprofileid
           AND ae.event_type = 'app_open'
           AND ae.event_date >= now() - interval '7 days'
       )
  AND  NOT EXISTS (
         SELECT 1 FROM notifications.etown_notification_ledger nl
         WHERE nl.contactid = up.contactid
           AND nl.notification_type = 'app_store_rating_prompt'
           AND nl.sent_on >= now() - interval '30 days'
       )
ORDER BY up.contactid, up.createdon
LIMIT 500
"""


async def run_app_store_rating_prompt() -> None:
    """Enqueue an app-store rating prompt for users active in 7 days and not prompted in 30 days."""
    from app.etown.notifications.service import GovernanceRequest, enqueue as gov_enqueue
    from app.etown.notifications.core import config as ncfg
    from app.etown.notifications import fingerprint

    cfg = await ncfg.db_get("app_store_rating_prompt")
    if cfg is None:
        logger.info("app_store_rating_prompt: disabled in config — skipping")
        return

    users = await Database.fetch(_RATING_PROMPT_ELIGIBLE_SQL)
    if not users:
        logger.info("app_store_rating_prompt: no eligible users today")
        return

    logger.info("app_store_rating_prompt: prompting %d user(s)", len(users))
    semaphore = asyncio.Semaphore(20)

    async def _one(u: dict) -> None:
        async with semaphore:
            try:
                await gov_enqueue(GovernanceRequest(
                    userprofileid=u["userprofileid"],
                    contactid=u["contactid"],
                    notification_type="app_store_rating_prompt",
                    ctx={"action": "in_app"},
                    dedupe_key=fingerprint.monthly("app-rating", u["contactid"]),
                ))
            except Exception as exc:
                logger.warning(
                    "app_store_rating_prompt: error for user %s: %s", u.get("userprofileid"), exc,
                )

    await asyncio.gather(*[_one(u) for u in users], return_exceptions=True)
    logger.info("app_store_rating_prompt: completed")


async def _feed_nudge_loop() -> None:
    """Run the feed-nudge sweep every tick when enabled, under an advisory lock held on one pooled connection."""
    from app.etown.notifications.core import config as ncfg

    while True:
        try:
            if await ncfg.db_get("feed_no_post_nudge") is not None:
                async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as lock_con:
                    got = await lock_con.fetchval("SELECT pg_try_advisory_lock($1)", _FEED_NUDGE_LOCK_KEY)
                    if got:
                        try:
                            await run_feed_no_post_nudge()
                        finally:
                            try:
                                await lock_con.fetchval("SELECT pg_advisory_unlock($1)", _FEED_NUDGE_LOCK_KEY)
                            except Exception:
                                logger.warning("Scheduler: failed to release the feed-nudge advisory lock", exc_info=True)
        except Exception as exc:
            logger.error("feed_no_post_nudge loop error: %s", exc, exc_info=True)
        await asyncio.sleep(_FEED_NUDGE_TICK_SECONDS)


async def _rating_reminder_refresh_runner() -> None:
    """Regenerate the rating-reminder LLM copy pool."""
    from app.etown.notifications.core.notification_generator import (
        refresh_copy_pool, RATING_REMINDER as RATING_REMINDER_KEY,
    )
    await refresh_copy_pool(RATING_REMINDER_KEY, daypart="rating_reminder")


async def _feed_nudge_copy_refresh_runner() -> None:
    """Regenerate the feed-no-post-nudge LLM copy pool."""
    from app.etown.notifications.core.notification_generator import (
        refresh_copy_pool, FEED_NO_POST_NUDGE as FEED_KEY,
    )
    await refresh_copy_pool(FEED_KEY, daypart="feed_no_post_nudge")


_JOB_RUNNERS: dict = {}


def _init_job_runners() -> None:
    """Populate the job-name to (advisory lock key, runner) registry."""
    global _JOB_RUNNERS
    _JOB_RUNNERS = {
        "notification_heartbeat":          (811000, _heartbeat_runner),
        "deferred_sweep":                  (811007, _deferred_sweep_runner),
        "morning_digest":                  (811001, _make_digest_runner(DIGESTS[0])),
        "morning_digest_copy_refresh":     (811002, _make_copy_refresh_runner(DIGESTS[0])),
        "evening_digest":                  (811003, _make_digest_runner(DIGESTS[1])),
        "evening_digest_copy_refresh":     (811004, _make_copy_refresh_runner(DIGESTS[1])),
        "midday_digest":                   (811021, _make_digest_runner(DIGESTS[2])),
        "midday_digest_copy_refresh":      (811022, _make_copy_refresh_runner(DIGESTS[2])),
        "rating_reminder":                 (811005, run_rating_reminder),
        "rating_reminder_copy_refresh":    (811006, _rating_reminder_refresh_runner),
        "evening_mycircle":                (811008, run_evening_mycircle),
        "weekly_digest":                   (811009, run_weekly_digest),
        "re_engagement":                   (811010, run_re_engagement),
        "provider_onboard_nudge":          (811011, run_provider_onboard_nudge),
        "provider_weekly_views":           (811012, run_provider_weekly_views),
        "provider_views_milestone":        (811013, run_provider_views_milestone),
        "provider_liked":                  (811016, run_provider_liked),
        "new_rating":                      (811017, run_new_rating),
        "town_view_milestone":             (811018, run_town_view_milestone),
        "new_town_nearby":                 (811019, run_new_town_nearby),
        "app_store_rating_prompt":         (811020, run_app_store_rating_prompt),
        "feed_no_post_nudge_copy_refresh": (811015, _feed_nudge_copy_refresh_runner),
        "vector_backfill":                 (811023, _vector_backfill_runner),
        "town_community_nudge":            (811024, run_town_community_nudge),
    }


async def build_jobs() -> List[ScheduledJob]:
    """Return a ScheduledJob for each registered runner that is enabled in the job config, with its hour, minute and weekday."""
    if not _JOB_RUNNERS:
        _init_job_runners()

    enabled_rows = await jcfg.get_all_enabled()
    jobs: List[ScheduledJob] = []
    for job_name, (lock_key, runner) in _JOB_RUNNERS.items():
        row = enabled_rows.get(job_name)
        if row is None:
            continue
        params = row.get("params") or {}
        if isinstance(params, str):
            import json as _json
            try:
                params = _json.loads(params)
            except (ValueError, TypeError):
                logger.warning("Scheduler: params for job %s are not valid JSON; using empty params", job_name, exc_info=True)
                params = {}
        weekday_val = params.get("weekday")
        weekdays = {int(weekday_val)} if weekday_val is not None else None
        jobs.append(ScheduledJob(
            name=job_name,
            hour=int(row["hour"]),
            minute=int(row["minute"]),
            lock_key=lock_key,
            runner=runner,
            weekdays=weekdays,
        ))
    return jobs


async def _loop() -> None:
    """Each tick, rebuild the job list and claim-and-run every job that is due."""
    while True:
        try:
            jobs = await build_jobs()
            now = now_ist()
            for job in jobs:
                if is_due(job, now):
                    await _claim_and_run(job, now.date())
        except Exception as exc:
            logger.error("Notification scheduler loop error: %s", exc, exc_info=True)
        await asyncio.sleep(TICK_SECONDS)


async def start_notification_scheduler() -> None:
    """Ensure notification tables and start the scheduler loop, plus the feed-nudge loop when governance is enabled."""
    global _scheduler_task
    if not bool(getattr(settings, "NOTIFICATIONS_SCHEDULER_ENABLED", False)):
        logger.info("Notification scheduler disabled (NOTIFICATIONS_SCHEDULER_ENABLED=false)")
        return
    try:
        await _ensure_notification_tables()
    except Exception as exc:
        logger.error("Notification scheduler: failed to ensure notification tables: %s", exc, exc_info=True)
        return
    jobs = await build_jobs()
    _scheduler_task = asyncio.create_task(_loop())
    logger.info(
        "Notification scheduler started (%d job(s), tick=%ds): %s",
        len(jobs), TICK_SECONDS, ", ".join(f"{j.name}@{j.hour:02d}:{j.minute:02d}IST" for j in jobs),
    )
    global _feed_nudge_task
    if bool(getattr(settings, "NOTIFICATIONS_GOVERNANCE_ENABLED", False)):
        _feed_nudge_task = asyncio.create_task(_feed_nudge_loop())
        logger.info("feed_no_post_nudge loop started (tick=%ds)", _FEED_NUDGE_TICK_SECONDS)


async def stop_notification_scheduler() -> None:
    """Cancel and await the scheduler and feed-nudge loops."""
    global _scheduler_task, _feed_nudge_task
    for task in (_scheduler_task, _feed_nudge_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.debug("notification scheduler cancelled during shutdown")
    _scheduler_task = None
    _feed_nudge_task = None
    logger.info("Notification scheduler stopped")
