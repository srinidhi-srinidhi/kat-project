"""Social Insights Collector Worker - refreshes stored Instagram and Facebook post metrics.

Background asyncio loop started from the FastAPI lifespan: fetches due posts' metrics from the Meta Graph API and upserts them into etown_social_insights_snapshots, the table every insight endpoint reads instead of calling Meta on the request path.

Features:
    - Tiered Refresh Cadence: recent posts refresh more often than mature ones; old posts stop
    - Configured Timeframes: every window and interval comes from the INSIGHTS_* settings in app.core.config
    - Due-Check Gating: a post is fetched only once its refresh interval has elapsed
    - Windowed Snapshots: one row per period window, frozen once the post ages past it
    - Upsert Storage: exactly one row per (platform, feed_id, period_days), updated in place
    - Bounded Concurrency: Graph API calls per cycle capped by INSIGHTS_BATCH_CONCURRENCY
    - Graceful Skipping: publishing, deleted or erroring posts are skipped and retried later

Classes:
    No classes defined (module is a function-based background worker)

Functions:
    _post_age_days(): Age of a post in fractional days, tolerant of naive/aware datetimes
    create_initial_insight_rows(): Insert the all-zero per-window snapshot rows for a new post
    _update_windowed_snapshots(): Update a post's still-open window rows with fresh metrics
    _fetch_due_posts(): Return the posts whose insights are due for a refresh
    collect_instagram_insights(): Refresh all due Instagram posts
    collect_facebook_insights(): Refresh all due Facebook posts
    run_collection_job(): Execute one full cycle for both platforms
    _collector_loop(): Background loop running run_collection_job() on an interval
    start_social_insights_collector(): Start the background asyncio task
    stop_social_insights_collector(): Cancel and await the background task

Dependencies:
    - app.db.client: Database.execute()/fetch() for reading due posts and upserting snapshot rows
    - app.core.config: INSIGHTS_* windows, refresh intervals, and batch-concurrency settings
    - app.core.logging: Centralized logging for collection cycles and errors
    - app.etown.instagram.instagram_utils: get_post_insights() Instagram fetch (lazy import)
    - app.etown.facebook.facebook_utils: get_post_insights() Facebook fetch, lazy, as fb_get_post_insights()

Updates:
    01-Jul-2026: Initial implementation.
    05-Jul-2026: Priority-based refresh schedule, with timeframes moved to app.core.config.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.db.client import Database
from app.core.config import settings
from app.core.logging import logger

RECENT_WINDOW_HOURS: int = settings.INSIGHTS_RECENT_WINDOW_HOURS
RECENT_INTERVAL_HOURS: int = settings.INSIGHTS_RECENT_INTERVAL_HOURS
MATURE_INTERVAL_HOURS: int = settings.INSIGHTS_MATURE_INTERVAL_HOURS
MAX_POST_AGE_DAYS: int = settings.INSIGHTS_MAX_POST_AGE_DAYS
BATCH_CONCURRENCY: int = settings.INSIGHTS_BATCH_CONCURRENCY
COLLECTION_INTERVAL_SECONDS: int = max(1, RECENT_INTERVAL_HOURS) * 3600

_collector_task: Optional[asyncio.Task] = None

SNAPSHOT_WINDOWS_DAYS: list = settings.INSIGHTS_SNAPSHOT_WINDOWS_DAYS
CANONICAL_WINDOW_DAYS: int = settings.INSIGHTS_CANONICAL_WINDOW_DAYS


def _post_age_days(published_at) -> float:
    """Return a post's age in fractional days, treating naive datetimes as UTC and None as 0."""
    if published_at is None:
        return 0.0
    aware = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - aware).total_seconds() / 86400.0


async def create_initial_insight_rows(
    platform: str,
    feed_id: str,
    contactid: str,
    userprofileid: Optional[str],
    media_id: str,
    media_type: Optional[str] = None,
) -> None:
    """Insert a zeroed insight snapshot row per configured window for a post, leaving existing rows untouched."""
    query = """
        INSERT INTO etown_social_insights_snapshots
            (platform, feed_id, contactid, userprofileid, media_id, media_type,
             caption_link_clicks, captured_at, period_days)
        SELECT $1, $2, $3, $4, $5, $6, 0, NOW(), w
        FROM unnest($7::int[]) AS w
        ON CONFLICT (platform, feed_id, period_days) DO NOTHING
    """
    try:
        await Database.execute(
            query, platform, feed_id, contactid, userprofileid, media_id, media_type,
            SNAPSHOT_WINDOWS_DAYS,
        )
    except Exception as exc:
        logger.error("INSIGHTS_INIT_ROWS_ERROR | platform=%s feed_id=%s | %s", platform, feed_id, exc, exc_info=True)


async def _update_windowed_snapshots(
    platform: str,
    feed_id: str,
    contactid: str,
    userprofileid: Optional[str],
    media_id: str,
    insights: dict,
    age_days: float,
) -> None:
    """Write fresh metrics to every snapshot row whose window still covers the post's age, logging failures."""
    query = """
        UPDATE etown_social_insights_snapshots SET
            contactid          = $3,
            userprofileid      = $4,
            media_id           = $5,
            media_type         = $6,
            impressions        = $7,
            reach              = $8,
            likes              = $9,
            comments           = $10,
            shares             = $11,
            saved              = $12,
            total_interactions = $13,
            views              = $14,
            profile_visits     = $15,
            follows            = $16,
            link_clicks        = $17,
            reactions          = $18,
            clicks             = $19,
            video_views        = $20,
            caption_link_clicks = $21,
            captured_at        = NOW()
        WHERE platform = $1 AND feed_id = $2 AND period_days >= $22
    """
    try:
        await Database.execute(
            query,
            platform,
            feed_id,
            contactid,
            userprofileid,
            media_id,
            insights.get("media_type"),
            insights.get("impressions"),
            insights.get("reach"),
            insights.get("likes"),
            insights.get("comments"),
            insights.get("shares"),
            insights.get("saved"),
            insights.get("total_interactions"),
            insights.get("views"),
            insights.get("profile_visits"),
            insights.get("follows"),
            insights.get("link_clicks"),
            insights.get("reactions"),
            insights.get("clicks"),
            insights.get("video_views"),
            insights.get("caption_link_clicks", 0),
            age_days,
        )
    except Exception as exc:
        logger.warning("social insights: windowed snapshot update failed", exc_info=True)


async def _fetch_due_posts(platform: str, table: str, media_column: str) -> list:
    """Return posts under the max age whose canonical snapshot is missing or older than their recent or mature interval."""
    now = datetime.now(timezone.utc)
    max_age_cutoff = now - timedelta(days=MAX_POST_AGE_DAYS)
    recent_cutoff = now - timedelta(hours=RECENT_WINDOW_HOURS)

    query = f"""
        SELECT p.feed_id, p.contactid, p.userprofileid, p.published_at, p.{media_column} AS media_id
        FROM {table} p
        LEFT JOIN etown_social_insights_snapshots s
               ON s.feed_id = p.feed_id AND s.platform = $1 AND s.period_days = $6
        WHERE p.published_at >= $2
          AND (
                s.captured_at IS NULL
                OR (p.published_at >= $3 AND s.captured_at <= NOW() - (INTERVAL '1 hour' * $4))
                OR (p.published_at <  $3 AND s.captured_at <= NOW() - (INTERVAL '1 hour' * $5))
              )
        ORDER BY p.published_at DESC
    """
    rows = await Database.fetch(
        query,
        platform,
        max_age_cutoff,
        recent_cutoff,
        RECENT_INTERVAL_HOURS,
        MATURE_INTERVAL_HOURS,
        CANONICAL_WINDOW_DAYS,
    )
    return [dict(r) for r in rows]


async def collect_instagram_insights(semaphore: asyncio.Semaphore) -> tuple:
    """Fetch and store insights for all due Instagram posts and return (collected, skipped) counts."""
    from app.etown.instagram.instagram_utils import get_post_insights

    rows = await _fetch_due_posts("instagram", "etown_instagram_posts", "instagram_media_id")
    if not rows:
        return 0, 0

    collected = 0
    skipped = 0

    async def _process(row: dict) -> None:
        nonlocal collected, skipped
        async with semaphore:
            feed_id = row["feed_id"]
            try:
                insights = await get_post_insights(feed_id=feed_id, contactid=row["contactid"])
                if (
                    insights.get("status") == "publishing"
                    or insights.get("post_deleted")
                    or "error" in insights
                ):
                    skipped += 1
                    return
                age_days = _post_age_days(row.get("published_at"))
                await create_initial_insight_rows(
                    "instagram", feed_id, row["contactid"], row.get("userprofileid"),
                    row["media_id"], insights.get("media_type"),
                )
                await _update_windowed_snapshots(
                    platform="instagram",
                    feed_id=feed_id,
                    contactid=row["contactid"],
                    userprofileid=row.get("userprofileid"),
                    media_id=row["media_id"],
                    insights=insights,
                    age_days=age_days,
                )
                collected += 1
            except Exception as exc:
                logger.warning("social insights: skipping feed %s", feed_id, exc_info=True)
                skipped += 1

    await asyncio.gather(*[_process(dict(r)) for r in rows])
    return collected, skipped


async def collect_facebook_insights(semaphore: asyncio.Semaphore) -> tuple:
    """Fetch and store insights for all due Facebook posts and return (collected, skipped) counts."""
    from app.etown.facebook.facebook_utils import get_post_insights as fb_get_post_insights

    rows = await _fetch_due_posts("facebook", "etown_facebook_posts", "facebook_post_id")
    if not rows:
        return 0, 0

    collected = 0
    skipped = 0

    async def _process(row: dict) -> None:
        nonlocal collected, skipped
        async with semaphore:
            feed_id = row["feed_id"]
            try:
                insights = await fb_get_post_insights(feed_id=feed_id, contactid=row["contactid"])
                if (
                    insights.get("status") == "publishing"
                    or insights.get("post_deleted")
                    or "error" in insights
                ):
                    skipped += 1
                    return
                age_days = _post_age_days(row.get("published_at"))
                await create_initial_insight_rows(
                    "facebook", feed_id, row["contactid"], row.get("userprofileid"),
                    row["media_id"], insights.get("media_type"),
                )
                await _update_windowed_snapshots(
                    platform="facebook",
                    feed_id=feed_id,
                    contactid=row["contactid"],
                    userprofileid=row.get("userprofileid"),
                    media_id=row["media_id"],
                    insights=insights,
                    age_days=age_days,
                )
                collected += 1
            except Exception as exc:
                logger.warning("social insights: skipping feed %s", feed_id, exc_info=True)
                skipped += 1

    await asyncio.gather(*[_process(dict(r)) for r in rows])
    return collected, skipped


async def run_collection_job() -> None:
    """Run one insights collection cycle for Instagram then Facebook under a shared concurrency semaphore."""
    semaphore = asyncio.Semaphore(BATCH_CONCURRENCY)
    start = asyncio.get_event_loop().time()

    ig_collected, ig_skipped = await collect_instagram_insights(semaphore)
    fb_collected, fb_skipped = await collect_facebook_insights(semaphore)

    elapsed = asyncio.get_event_loop().time() - start


async def _collector_loop() -> None:
    """Run the collection job forever, logging failures and sleeping COLLECTION_INTERVAL_SECONDS between cycles."""
    while True:
        try:
            await run_collection_job()
        except Exception as exc:
            logger.error("social insights: collection job failed", exc_info=True)
        await asyncio.sleep(COLLECTION_INTERVAL_SECONDS)


async def start_social_insights_collector() -> None:
    """Start the social insights collector loop as a background task."""
    global _collector_task
    _collector_task = asyncio.create_task(_collector_loop())


async def stop_social_insights_collector() -> None:
    """Cancel and await the social insights collector task."""
    global _collector_task
    if _collector_task and not _collector_task.done():
        _collector_task.cancel()
        try:
            await _collector_task
        except asyncio.CancelledError:
            logger.debug("social insights collector cancelled during shutdown")
    _collector_task = None
