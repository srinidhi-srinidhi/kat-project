"""Feed Utilities - Postgres business logic for feed posts, comments, likes and timed feed reads.

Domain layer behind /api/feeds: reads and times the main, trending and offers feeds, creates and edits posts under the poster's plan caps, handles comments and likes, resolves My Circle audiences and propagates display-name changes across etown_feed, etown_feedcomments and etown_feedlikes.

Features:
    - Timed feed queries: Performance-monitored database operations
    - Feed aggregation: Main feed content compilation and filtering
    - Trending analysis: Content popularity and engagement tracking
    - Offers integration: Special offer content in feed streams
    - Geographic filtering: Location-based feed customization
    - Performance metrics: Query execution time tracking
    - Like summary: Per-feed "liked by X and N others", prioritising My Circle likers
    - Feed/Comment/Like CRUD: Create, edit and delete posts and comments; upsert one like per liker
    - Circle Resolution: Expands "My Circle" mode via member + provider relations
    - Plan Caps: Provider posts enforce plan photo, audience and monthly post limits
    - Disabled Providers: Dropped from feed read paths and counts
    - Display Name Propagation: Rewrites frozen name snapshots on feed, like and comment rows

Classes:
    Database(): Feed, comment and like query classmethods extending BaseDatabase

Functions:
    timed_main_feed(): Execute main feed query with timing tracking
    timed_trending_check(): Check trending content with performance monitoring
    timed_offers_check(): Retrieve offers data with timing metrics
    normalize_quill_delta(): Idempotently coerce feed text to a canonical Quill delta
    resolve_audience(): Split a text audience into the (name, code) pair a feed row stores
    enforce_feed_plan_limits(): Apply a provider's plan caps to a post's photos and audience
    create_feed_post(): Insert a post, applying the poster's plan caps and circle-only rule
    edit_feed_post(): Update a post's text, audience and images under the same caps
    propagate_display_name_to_feed(): Rewrite frozen name snapshots after a member/provider rename

Dependencies:
    - datetime: Time-based filtering and tracking
    - geopy: Geographic distance calculations
    - json: Quill-delta normalization and groups/phonebook jsonb (de)serialization
    - time: Query-timing measurements behind the timed_* wrappers
    - uuid: Generates the feedid for a newly created post
    - app.core.logging: Performance and error logging
    - app.db.client: Database operations with retry logic (with_retry, BaseDatabase)
    - app.etown.plans.plans_utils: Subscription plan resolution and limit enforcement
    - asyncpg: UniqueViolationError handling on like upserts

Updates:
    23-Feb-2026: Enhanced module documentation with comprehensive feature list
    30-Jul-2026: Added get_feed_like_details() and attached like_details to every timed_main_feed() row.
    18-Aug-2026: Provider posts now enforce plan photo, audience-targeting and monthly post caps.
    19-Aug-2026: Post caps keyed on the resolved plan; deleted likers and commenters excluded.
    20-Aug-2026: Plan refusals recorded; rows carry author providertype; live display names.
    26-Aug-2026: Silent exception handlers now logged with tracebacks.
    02-Sep-2026: Disabled providers dropped from feed read paths and counts.
    09-Sep-2026: Post audience is text "mycircle"/"public" and drives circle_only; create_feed_post drops the mycircle argument.

Notes:
    All database operations include timing logs for performance monitoring.
    Geographic filtering uses Haversine formula for distance calculations.
"""

from datetime import datetime, timedelta
import json
import time
import uuid
from geopy.distance import geodesic
from typing import Optional, Dict, Tuple, List, Any
from app.core.logging import logger
from app.db.client import with_retry, Database as BaseDatabase
from app.etown.plans.plans_utils import (
    PlanLimitError,
    check_and_consume,
    record_refusal,
    release_consumed,
    resolve_plan,
)

AUDIENCE_MYCIRCLE = 1
AUDIENCE_PUBLIC = 2

AUDIENCE_CODES = {"mycircle": AUDIENCE_MYCIRCLE, "public": AUDIENCE_PUBLIC}
AUDIENCE_NAMES = {code: name for name, code in AUDIENCE_CODES.items()}

OFFER_POST_KEYWORDS = r'\y(offer|discount|sale|deal|promo)|% off'
OFFER_POST_SQL = "(f.role = 1 AND (f.sharetooffer IS TRUE OR f.feedtext ~* {kw}))"
CIRCLE_VISIBILITY_SQL = """(
                  f.circle_only IS NOT TRUE
               OR f.contactid = {viewer}
               OR EXISTS (
                      SELECT 1 FROM public.etown_memberrelation r
                       WHERE (r.from_contactid = f.contactid AND r.to_contactid = {viewer})
                          OR (r.to_contactid = f.contactid AND r.from_contactid = {viewer})
                  )
               OR EXISTS (
                      SELECT 1 FROM public.etown_providerrelation r
                       WHERE (r.fromcontactid = f.contactid AND r.tocontactid = {viewer})
                          OR (r.tocontactid = f.contactid AND r.fromcontactid = {viewer})
                  )
              )"""


def resolve_audience(audience: Optional[str]) -> Tuple[Optional[str], Optional[int]]:
    """Return the (audiencename, code) pair for an audience name or legacy numeric code, or (None, None) for None.

    Raises:
        ValueError: When the value is neither a known audience name nor a known code.
    """
    if audience is None:
        return None, None

    key = str(audience).strip().lower()
    if key in AUDIENCE_CODES:
        return key, AUDIENCE_CODES[key]
    if key.isdigit() and int(key) in AUDIENCE_NAMES:
        code = int(key)
        return AUDIENCE_NAMES[code], code
    raise ValueError(
        f"audience must be one of {sorted(AUDIENCE_CODES)}, got {audience!r}"
    )


async def timed_main_feed(
    current_contact_id: str,
    current_user_profile_id: str,
    limit: int,
    offset: int,
    mode: str,
    category: Optional[str],
    latitude: Optional[float],
    longitude: Optional[float],
    isMyProfile: bool,
    timing_logs: Dict[str, float]
) -> Tuple[List[Any], int]:
    """Fetch a feed page with per-row like_details, recording the elapsed time in timing_logs.

    Args:
        current_contact_id: Viewer's contact ID.
        current_user_profile_id: Viewer's profile ID.
        limit: Number of items per page.
        offset: Page number.
        mode: Feed mode.
        category: Category filter.
        latitude: Viewer's latitude.
        longitude: Viewer's longitude.
        isMyProfile: Whether to fetch only the viewer's own posts.
        timing_logs: Dict that receives main_feed_query_time_ms.

    Returns:
        Tuple of (feed rows, total count).
    """
    db_start = time.time()
    feed_data, total_count = await Database.get_feeds(
        current_contact_id=current_contact_id,
        current_user_profile_id=current_user_profile_id,
        limit=limit,
        offset=offset,
        mode=mode,
        category=category,
        latitude=latitude,
        longitude=longitude,
        isMyProfile=isMyProfile
    )

    if feed_data:
        like_details = await Database.get_feed_like_details(
            feed_ids=[row["feedid"] for row in feed_data if row.get("feedid")],
            current_user_profile_id=current_user_profile_id,
            current_contact_id=current_contact_id,
        )
        for row in feed_data:
            row["like_details"] = like_details.get(row.get("feedid")) or {
                "likedby": None,
                "count": 0,
                "likedbyimage": None,
                "likedbyimage2": None,
            }

    timing_logs["main_feed_query_time_ms"] = (time.time() - db_start) * 1000
    return (feed_data, total_count)


async def timed_trending_check(
    latitude: Optional[float],
    longitude: Optional[float],
    timing_logs: Dict[str, float],
    current_contact_id: Optional[str] = None,
) -> bool:
    """Return whether trending posts exist near the viewer, recording the elapsed time in timing_logs."""
    db_start = time.time()
    result = await Database.check_for_trending_posts(
        latitude=latitude,
        longitude=longitude,
        current_contact_id=current_contact_id,
    )
    timing_logs["trending_check_time_ms"] = (time.time() - db_start) * 1000
    return result


async def timed_offers_check(
    current_user_profile_id: str,
    current_contact_id: str,
    latitude: Optional[float],
    longitude: Optional[float],
    timing_logs: Dict[str, float]
) -> bool:
    """Return whether offer posts exist for the viewer, recording the elapsed time in timing_logs."""
    db_start = time.time()
    result = await Database.check_for_offers_posts(
        current_user_profile_id,
        current_contact_id,
        latitude=latitude,
        longitude=longitude
    )
    timing_logs["offers_check_time_ms"] = (time.time() - db_start) * 1000
    return result

def normalize_quill_delta(feed_text: Optional[str]) -> Optional[str]:
    """Return feed text unchanged if it is already a Quill delta, else wrap it as a single-insert delta JSON string."""
    if feed_text is None:
        return None
    try:
        parsed = json.loads(feed_text)
        if isinstance(parsed, list) and parsed and all(
            isinstance(op, dict) and "insert" in op for op in parsed
        ):
            return feed_text
    except (json.JSONDecodeError, TypeError):
        logger.debug("feed: text is not a Quill delta; returning it unchanged")
    return json.dumps([{"insert": f"{feed_text}\n"}], ensure_ascii=False)


async def enforce_feed_plan_limits(
    userprofile_id: str,
    *,
    photo_count: int,
    audience: Optional[int],
) -> None:
    """Record and raise PlanLimitError when a post exceeds the plan's photo cap or uses an untargetable audience code.

    Args:
        userprofile_id: The posting provider's user profile ID.
        photo_count: Number of image slots the post carries once the write lands.
        audience: The audience code the post carries once the write lands.

    Raises:
        PlanLimitError: When photos exceed max_gallery_photos or a legacy targeted audience lacks targeting.
    """
    plan = await resolve_plan(userprofile_id)
    if plan is None:
        return

    max_photos = plan.get("max_gallery_photos")
    if max_photos is not None and photo_count > max_photos:
        err = PlanLimitError(
            f"Your {plan['plan_name']} plan allows {max_photos} photo(s) per post.",
            plan_code=plan["plan_code"], limit=max_photos, used=photo_count,
            userprofile_id=userprofile_id, limit_column="max_gallery_photos",
        )
        await record_refusal(err)
        raise err

    if not plan.get("post_audience_targeting") and audience not in (
        None, AUDIENCE_MYCIRCLE, AUDIENCE_PUBLIC
    ):
        err = PlanLimitError(
            f"Your {plan['plan_name']} plan does not include audience targeting on posts.",
            plan_code=plan["plan_code"], limit=0, used=0,
            userprofile_id=userprofile_id, limit_column="post_audience_targeting",
        )
        await record_refusal(err)
        raise err


async def edit_feed_post(
    feed_id: str,
    feed_text: str,
    *,
    audience: Optional[str] = None,
    feedfilelocationurl: Optional[str] = None,
    feedfilelocation2: Optional[str] = None,
    feedfilelocation3: Optional[str] = None,
    feedfilelocation4: Optional[str] = None,
    feedfilelocation5: Optional[str] = None,
) -> None:
    """Update a feed post's text and optionally its audience and images, enforcing the stored author's plan limits.

    Args:
        feed_id: The feed record to update.
        feed_text: The new text, normalised to a Quill delta.
        audience: New audience, "mycircle" or "public"; None leaves it unchanged.
        feedfilelocationurl: New primary image path; None leaves the slot unchanged.
        feedfilelocation2: New second image path; None leaves the slot unchanged.
        feedfilelocation3: New third image path; None leaves the slot unchanged.
        feedfilelocation4: New fourth image path; None leaves the slot unchanged.
        feedfilelocation5: New fifth image path; None leaves the slot unchanged.

    Raises:
        PlanLimitError: When the edited post would breach the author's plan limits.
    """
    audiencename, audience_code = resolve_audience(audience)

    slots = (
        feedfilelocationurl, feedfilelocation2, feedfilelocation3,
        feedfilelocation4, feedfilelocation5,
    )
    mycircle: Optional[bool] = None
    circle_only: Optional[bool] = None
    if audience is not None or any(slots):
        row = await Database.fetchrow(
            """
            SELECT userprofileid, audience, feedfilelocationurl, feedfilelocation2,
                   feedfilelocation3, feedfilelocation4, feedfilelocation5
              FROM public.etown_feed
             WHERE feedid = $1
            """,
            feed_id,
        )
        if row:
            stored = (
                row["feedfilelocationurl"], row["feedfilelocation2"], row["feedfilelocation3"],
                row["feedfilelocation4"], row["feedfilelocation5"],
            )
            await enforce_feed_plan_limits(
                row["userprofileid"],
                photo_count=sum(1 for new, old in zip(slots, stored) if new or old),
                audience=audience_code if audience_code is not None else row["audience"],
            )
            if audiencename is not None:
                plan = await resolve_plan(row["userprofileid"])
                mycircle = audiencename == "mycircle"
                circle_only = mycircle or (
                    plan is not None and not plan.get("post_public_feed", True)
                )

    formatted_text = normalize_quill_delta(feed_text)
    await Database.update_feed_post(
        feed_id=feed_id,
        feed_text=formatted_text,
        audiencename=audiencename,
        audience=audience_code,
        mycircle=mycircle,
        circle_only=circle_only,
        feedfilelocationurl=feedfilelocationurl,
        feedfilelocation2=feedfilelocation2,
        feedfilelocation3=feedfilelocation3,
        feedfilelocation4=feedfilelocation4,
        feedfilelocation5=feedfilelocation5,
    )
    logger.info(f"edit_feed_post: feed {feed_id} updated successfully.")


async def propagate_display_name_to_feed(
    *,
    contact_id: Optional[str] = None,
    userprofile_ids: Optional[List[str]] = None,
    new_name: Optional[str] = None,
    role: Optional[int] = None,
) -> Dict[str, int]:
    """Rewrite frozen name snapshots in the feed, likes and comments tables after a provider or member rename.

    Args:
        contact_id: Renamed contact; scopes the contact-keyed columns when role is 2.
        userprofile_ids: Renamed profiles; scopes the profile-keyed columns.
        new_name: The new display name; blank or missing is a no-op.
        role: 1 rewrites etown_feed.servicename, 2 rewrites the person-name columns.

    Returns:
        Dict of 'table.column' to rows written, empty on a no-op.
    """
    counts: Dict[str, int] = {}
    if not new_name or not new_name.strip():
        return counts

    new_name = new_name.strip()
    upids = [u for u in (userprofile_ids or []) if u]

    def _rows(status: str) -> int:
        try:
            return int(str(status).rsplit(" ", 1)[-1])
        except (ValueError, IndexError):
            logger.warning("feed: could not read a row count out of status %r", status)
            return 0

    if role == 1 and upids:
        counts["etown_feed.servicename"] = _rows(await Database.execute(
            """
            UPDATE public.etown_feed
               SET servicename = $2, modifieddate = NOW()
             WHERE userprofileid = ANY($1::text[])
               AND role = 1
               AND servicename IS DISTINCT FROM $2
            """,
            upids, new_name,
        ))

    elif role == 2:
        if upids:
            counts["etown_feed.userprofileidname"] = _rows(await Database.execute(
                """
                UPDATE public.etown_feed
                   SET userprofileidname = $2, modifieddate = NOW()
                 WHERE userprofileid = ANY($1::text[])
                   AND userprofileidname IS DISTINCT FROM $2
                """,
                upids, new_name,
            ))

            counts["etown_feedlikes.likesbyuserprofileidname"] = _rows(await Database.execute(
                """
                UPDATE public.etown_feedlikes
                   SET likesbyuserprofileidname = $2, modifieddate = NOW()
                 WHERE likesbyuserprofileid = ANY($1::text[])
                   AND likesbyuserprofileidname IS DISTINCT FROM $2
                """,
                upids, new_name,
            ))

            counts["etown_feedcomments.commentedbyuserprofileidname"] = _rows(await Database.execute(
                """
                UPDATE public.etown_feedcomments
                   SET commentedbyuserprofileidname = $2, modifieddate = NOW()
                 WHERE commentedbyuserprofileid = ANY($1::text[])
                   AND commentedbyuserprofileidname IS DISTINCT FROM $2
                """,
                upids, new_name,
            ))

        if contact_id:
            counts["etown_feed.contactidname"] = _rows(await Database.execute(
                """
                UPDATE public.etown_feed
                   SET contactidname = $2, modifieddate = NOW()
                 WHERE contactid = $1
                   AND contactidname IS DISTINCT FROM $2
                """,
                contact_id, new_name,
            ))

            counts["etown_feedlikes.likedbycontactidname"] = _rows(await Database.execute(
                """
                UPDATE public.etown_feedlikes
                   SET likedbycontactidname = $2, modifieddate = NOW()
                 WHERE likedbycontactid = $1
                   AND likedbycontactidname IS DISTINCT FROM $2
                """,
                contact_id, new_name,
            ))

            counts["etown_feedcomments.commentedbycontactidname"] = _rows(await Database.execute(
                """
                UPDATE public.etown_feedcomments
                   SET commentedbycontactidname = $2, modifieddate = NOW()
                 WHERE commentedbycontactid = $1
                   AND commentedbycontactidname IS DISTINCT FROM $2
                """,
                contact_id, new_name,
            ))

    written = {k: v for k, v in counts.items() if v}
    logger.info(
        "propagate_display_name_to_feed: role=%s contact_id=%s profiles=%d written=%s",
        role, contact_id, len(upids), written or "nothing",
    )
    return counts


async def create_feed_post(
    *,
    contact_id: str,
    userprofile_id: str,
    role: int,
    feed_text: Optional[str],
    servicename: Optional[str],
    audience: Optional[str],
    feedfilelocationurl: Optional[str],
    feedfilelocation2: Optional[str],
    feedfilelocation3: Optional[str],
    feedfilelocation4: Optional[str],
    feedfilelocation5: Optional[str],
    feedvideofilelocation: Optional[str],
    thumbnailfileurl: Optional[str],
    serviceimageurl: Optional[str],
    orientation: Optional[str] = None,
    sharetooffer: Optional[bool] = False,
    groups: Optional[List[Dict[str, Any]]] = None,
    phonebook: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Insert a feed post after enforcing plan caps and consuming the post allowance, returning the new feedid.

    Raises:
        ValueError: When role is neither 1 nor 2.
        PlanLimitError: When the author's plan caps are breached.
    """
    feed_id = str(uuid.uuid4())
    if role == 1:
        rolename = "Provider"
    elif role == 2:
        rolename = "Member"
    else:
        raise ValueError("Invalid role")

    audiencename, audience_code = resolve_audience(audience)

    plan = await resolve_plan(userprofile_id)
    await enforce_feed_plan_limits(
        userprofile_id,
        photo_count=sum(
            1 for u in (feedfilelocationurl, feedfilelocation2, feedfilelocation3,
                        feedfilelocation4, feedfilelocation5,
                        None if feedvideofilelocation else thumbnailfileurl) if u
        ),
        audience=audience_code,
    )
    mycircle = audiencename == "mycircle"
    circle_only = mycircle or (plan is not None and not plan.get("post_public_feed", True))
    await check_and_consume(userprofile_id, "posts_created")

    try:
        await Database.insert_feed(
            feed_id=feed_id,
            contact_id=contact_id,
            userprofile_id=userprofile_id,
            role=role,
            rolename=rolename,
            feed_text=normalize_quill_delta(feed_text),
            servicename=servicename,
            audience=audience_code,
            audiencename=audiencename,
            feedfilelocationurl=feedfilelocationurl,
            feedfilelocation2=feedfilelocation2,
            feedfilelocation3=feedfilelocation3,
            feedfilelocation4=feedfilelocation4,
            feedfilelocation5=feedfilelocation5,
            feedvideofilelocation=feedvideofilelocation,
            thumbnailfileurl=thumbnailfileurl,
            serviceimageurl=serviceimageurl,
            orientation=orientation,
            mycircle=mycircle,
            sharetooffer=sharetooffer,
            groups=groups,
            phonebook=phonebook,
            circle_only=circle_only,
        )
    except Exception as exc:
        from asyncpg.exceptions import UniqueViolationError
        if not isinstance(exc, UniqueViolationError):
            if plan is not None:
                await release_consumed(userprofile_id, "posts_created")
        raise
    logger.info(f"create_feed_post: feed {feed_id} inserted for contact {contact_id}.")
    return feed_id

class Database(BaseDatabase):
    @classmethod
    @with_retry()
    async def update_feed_post(
        cls,
        feed_id: str,
        feed_text: str,
        audience: Optional[int] = None,
        feedfilelocationurl: Optional[str] = None,
        feedfilelocation2: Optional[str] = None,
        feedfilelocation3: Optional[str] = None,
        feedfilelocation4: Optional[str] = None,
        feedfilelocation5: Optional[str] = None,
        audiencename: Optional[str] = None,
        mycircle: Optional[bool] = None,
        circle_only: Optional[bool] = None,
    ) -> None:
        """Update a feed row's text and any supplied audience, flag or image column, leaving None values unchanged.

        Raises:
            RuntimeError: When the database pool is not initialised or closed.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        async with cls._pool.acquire() as con:
            await con.execute(
                """
                UPDATE public.etown_feed
                   SET feedtext            = $1,
                       audience            = COALESCE($2::int,  audience),
                       feedfilelocationurl = COALESCE($3::text, feedfilelocationurl),
                       feedfilelocation2   = COALESCE($4::text, feedfilelocation2),
                       feedfilelocation3   = COALESCE($5::text, feedfilelocation3),
                       feedfilelocation4   = COALESCE($6::text, feedfilelocation4),
                       feedfilelocation5   = COALESCE($7::text, feedfilelocation5),
                       audiencename        = COALESCE($8::text, audiencename),
                       mycircle            = COALESCE($9::boolean,  mycircle),
                       circle_only         = COALESCE($10::boolean, circle_only),
                       modifieddate        = NOW()
                 WHERE feedid = $11
                """,
                feed_text,
                audience,
                feedfilelocationurl,
                feedfilelocation2,
                feedfilelocation3,
                feedfilelocation4,
                feedfilelocation5,
                audiencename,
                mycircle,
                circle_only,
                feed_id,
            )

    @classmethod
    @with_retry()
    async def insert_feed(
        cls,
        *,
        feed_id: str,
        contact_id: str,
        userprofile_id: str,
        role: int,
        rolename: Optional[str],
        feed_text: Optional[str],
        servicename: Optional[str],
        audience: Optional[int],
        audiencename: Optional[str] = None,
        feedfilelocationurl: Optional[str],
        feedfilelocation2: Optional[str],
        feedfilelocation3: Optional[str],
        feedfilelocation4: Optional[str],
        feedfilelocation5: Optional[str],
        feedvideofilelocation: Optional[str],
        thumbnailfileurl: Optional[str],
        serviceimageurl: Optional[str],
        orientation: Optional[str] = None,
        mycircle: Optional[bool] = False,
        sharetooffer: Optional[bool] = False,
        groups: Optional[List[Dict[str, Any]]] = None,
        phonebook: Optional[List[Dict[str, Any]]] = None,
        circle_only: bool = False,
    ) -> None:
        """Insert a feed row, filling contact and profile names from their tables and storing groups and phonebook as jsonb."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        async with cls._pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO public.etown_feed (
                    feedid, contactid, contactidname,
                    userprofileid, userprofileidname, role, rolename,
                    servicename, audience, feedtext, feedfilelocationurl,
                    feedfilelocation2, feedfilelocation3,
                    feedfilelocation4, feedfilelocation5,
                    feedvideofilelocation, thumbnailfileurl,
                    serviceimageurl, orientation,
                    mycircle, sharetooffer, groups, phonebook,
                    circle_only, audiencename, createdon
                )
                VALUES (
                    $1, $2,
                    (
                        SELECT firstname
                        FROM public.etown_contact
                        WHERE contactid = $2
                        LIMIT 1
                    ),
                    $3,
                    (
                        SELECT name
                        FROM public.etown_userprofile
                        WHERE userprofileid = $3
                        LIMIT 1
                    ),
                    $4, $5, 
                    $6, $7, $8, $9, 
                    $10, $11, $12, $13, 
                    $14, $15, $16, $17, 
                    $18, $19, $20::jsonb, $21::jsonb,
                    $22, $23, NOW()
                )
                """,
                feed_id, contact_id, userprofile_id, role, rolename,
                servicename, audience, feed_text,
                feedfilelocationurl,
                feedfilelocation2, feedfilelocation3,
                feedfilelocation4, feedfilelocation5,
                feedvideofilelocation, thumbnailfileurl,
                serviceimageurl, orientation,
                mycircle, sharetooffer,
                json.dumps(groups) if groups is not None else None,
                json.dumps(phonebook) if phonebook is not None else None,
                circle_only, audiencename,
            )

    @classmethod
    @with_retry()
    async def get_feed_by_id(
        cls,
        feed_id: str,
        current_contact_id: str,
        current_user_profile_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return one feed row in the same joined shape as get_feeds(), or None if no row matches."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
        WITH
        retired_ids AS MATERIALIZED (
            SELECT identity_id FROM public.etown_retired_identity
             WHERE deleted = 'true'
                OR (deactivated = 'true' AND reactivation IS DISTINCT FROM 'true')
        ),
        comment_counts AS (
            SELECT feedid, COUNT(feedcommentsid)::int AS comments_count
            FROM public.etown_feedcomments
            WHERE feedid = $1
            GROUP BY feedid
        ),
        like_counts AS (
            SELECT
                l.feedid,
                COUNT(l.feedlikesid)::int AS likes_count,
                string_agg(l.liker_name, ', ') AS feedlikecontactperson,
                bool_or(l.likesbyuserprofileid = $2) AS has_liked_by_user,
                false AS is_liked_by_circle_user
            FROM (
                SELECT fl.feedid, fl.feedlikesid, fl.likesbyuserprofileid,
                       COALESCE(
                           CASE WHEN lup.role = 1
                                THEN (SELECT NULLIF(btrim(lpv.businessname), '')
                                        FROM public.etown_provider lpv
                                       WHERE lpv.userprofileid = fl.likesbyuserprofileid
                                       ORDER BY lpv.modifieddate DESC NULLS LAST
                                       LIMIT 1)
                                ELSE NULLIF(btrim(lc.firstname), '')
                           END,
                           fl.likesbyuserprofileidname
                       ) AS liker_name
                FROM public.etown_feedlikes fl
                LEFT JOIN public.etown_userprofile lup ON lup.userprofileid = fl.likesbyuserprofileid
                LEFT JOIN public.etown_contact    lc  ON lc.contactid       = fl.likedbycontactid
                WHERE fl.likemodename = 'Like' AND fl.feedid = $1
            ) l
            GROUP BY l.feedid
        )
        SELECT
            f.feedid, f.feedfilelocationurl, f.thumbnailfileurl, f.feedtext,
            f.feedfilelocation2, f.feedfilelocation3, f.feedfilelocation4,
            f.feedfilelocation5, f.feedvideofilelocation,
            CASE WHEN f.role = 1
                 THEN COALESCE(NULLIF(btrim(p.businessname), ''), f.servicename)
                 ELSE f.servicename
            END AS servicename,
            f.userprofileid, f.contactid, f.role, f.createdon,
            f.serviceimageurl AS serviceimageurl,
            f.audience AS audience,
            f.circle_only AS circle_only,
            f.orientation,
            f.mycircle, f.sharetooffer, f.groups, f.phonebook,
            c.firstname AS author_name,
            etown_deleted_flag(f.userprofileid)     AS author_deleted,
            etown_deactivated_flag(f.userprofileid) AS author_deactivated,
            CASE WHEN f.role = 1 THEN p.serviceimageurl ELSE c.profileimage END AS author_profileimage,
            COALESCE(f.serviceimageurl, p.serviceimageurl) AS ets_serviceimageurl,
            p.providertype,
            CASE WHEN f.role = 1 THEN p.address ELSE c.address END AS address,
            CASE WHEN f.role = 1 THEN p.city ELSE c.city END AS city,
            CASE WHEN f.role = 1 THEN p.statecode ELSE c.statecode END AS statecode,
            c.latitude AS contact_latitude, c.longitude AS contact_longitude,
            p.latitude AS provider_latitude, p.longitude AS provider_longitude,
            COALESCE(cc.comments_count, 0) AS comments_count,
            COALESCE(lc.likes_count, 0) AS likes_count,
            lc.feedlikecontactperson,
            COALESCE(lc.has_liked_by_user, false) AS has_liked,
            COALESCE(lc.is_liked_by_circle_user, false) AS is_liked_by_circle,
            ps.businesscategory,
            COALESCE(mr.relationshiptype, pr.relationshiptype) AS mycirclerole_code,
            inv.invitationcode AS invitation
        FROM public.etown_feed f
        LEFT JOIN public.etown_contact c ON f.contactid = c.contactid
        LEFT JOIN public.etown_provider p ON f.userprofileid = p.userprofileid
        LEFT JOIN public.etown_providerservices ps ON f.userprofileid = ps.userprofileid
        LEFT JOIN comment_counts cc ON f.feedid = cc.feedid
        LEFT JOIN like_counts lc ON f.feedid = lc.feedid
        LEFT JOIN public.etown_memberrelation mr ON (mr.from_contactid = f.contactid AND mr.to_contactid = $3) OR (mr.to_contactid = f.contactid AND mr.from_contactid = $3)
        LEFT JOIN public.etown_providerrelation pr ON (pr.fromcontactid = f.contactid AND pr.tocontactid = $3) OR (pr.tocontactid = f.contactid AND pr.fromcontactid = $3)
        LEFT JOIN LATERAL (
            SELECT invitationcode
            FROM public.etown_invitation
            WHERE (invitercontact = f.contactid AND invitecontact = $3)
               OR (invitecontact = f.contactid AND invitercontact = $3)
            ORDER BY COALESCE(modifieddate, createdon) DESC NULLS LAST,
                     createdon DESC NULLS LAST,
                     invitationid DESC
            LIMIT 1
        ) inv ON TRUE
        WHERE f.feedid = $1
          AND NOT EXISTS (
              SELECT 1 FROM retired_ids ri
               WHERE ri.identity_id = f.contactid
                  OR ri.identity_id = f.userprofileid
          )
          AND """ + CIRCLE_VISIBILITY_SQL.format(viewer="$3") + """
        LIMIT 1
        """

        async with cls._pool.acquire() as con:
            row = await con.fetchrow(query, feed_id, current_user_profile_id, current_contact_id)
            return dict(row) if row else None

    @classmethod
    @with_retry()
    async def get_feeds(
        cls,
        current_contact_id: str,
        current_user_profile_id: str,
        limit: int = 20,
        offset: int = 1,
        mode: str = "Public",
        category: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        isMyProfile: bool = False
    ) -> tuple:
        """Return a page of feed rows for the mode and filters, with total_count equal to the rows on this page."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        
        logger.info(f"Fetching feeds for user {current_user_profile_id} with mode {mode}")
        
        page_offset = (offset - 1) * limit
        
        circle_user_profile_ids = []
        if mode == "My Circle":
            circle_user_profile_ids = await cls._get_circle_user_profile_ids(current_user_profile_id)
        
        query = """
        WITH
        retired_ids AS MATERIALIZED (
            SELECT identity_id FROM public.etown_retired_identity
             WHERE deleted = 'true'
                OR (deactivated = 'true' AND reactivation IS DISTINCT FROM 'true')
        ),
        disabled_provider_ids AS MATERIALIZED (
            SELECT userprofileid::text AS identity_id
              FROM public.etown_provider
             WHERE disabled
        ),
        comment_counts AS (
            SELECT fc.feedid, COUNT(fc.feedcommentsid)::int AS comments_count
            FROM public.etown_feedcomments fc
            WHERE NOT EXISTS (
                SELECT 1 FROM disabled_provider_ids dp
                 WHERE dp.identity_id = fc.commentedbyuserprofileid
            )
            GROUP BY fc.feedid
        ),
        like_counts AS (
            SELECT
                l.feedid,
                COUNT(l.feedlikesid)::int AS likes_count,
                string_agg(l.liker_name, ', ') AS feedlikecontactperson,
                bool_or(l.likesbyuserprofileid = $1) AS has_liked_by_user,
                bool_or(l.likesbyuserprofileid = ANY($2::text[])) AS is_liked_by_circle_user
            FROM (
                SELECT fl.feedid, fl.feedlikesid, fl.likesbyuserprofileid,
                       COALESCE(
                           CASE WHEN lup.role = 1
                                THEN (SELECT NULLIF(btrim(lpv.businessname), '')
                                        FROM public.etown_provider lpv
                                       WHERE lpv.userprofileid = fl.likesbyuserprofileid
                                       ORDER BY lpv.modifieddate DESC NULLS LAST
                                       LIMIT 1)
                                ELSE NULLIF(btrim(lc.firstname), '')
                           END,
                           fl.likesbyuserprofileidname
                       ) AS liker_name
                FROM public.etown_feedlikes fl
                LEFT JOIN public.etown_userprofile lup ON lup.userprofileid = fl.likesbyuserprofileid
                LEFT JOIN public.etown_contact    lc  ON lc.contactid       = fl.likedbycontactid
                WHERE fl.likemodename = 'Like'
                  AND NOT EXISTS (
                      SELECT 1 FROM disabled_provider_ids dp
                       WHERE dp.identity_id = fl.likesbyuserprofileid
                  )
            ) l
            GROUP BY l.feedid
        ),
        all_feeds AS (
            SELECT DISTINCT ON (f.feedid)
                f.feedid, f.feedfilelocationurl, f.thumbnailfileurl, f.feedtext,
                f.feedfilelocation2, f.feedfilelocation3, f.feedfilelocation4, 
                f.feedfilelocation5, f.feedvideofilelocation,
                CASE WHEN f.role = 1
                     THEN COALESCE(NULLIF(btrim(p.businessname), ''), f.servicename)
                     ELSE f.servicename
                END AS servicename,
                f.userprofileid, f.contactid, f.role, f.createdon,
                f.orientation,
                c.firstname AS author_name,
            etown_deleted_flag(f.userprofileid)     AS author_deleted,
            etown_deactivated_flag(f.userprofileid) AS author_deactivated,
                CASE WHEN f.role = 1 THEN p.serviceimageurl ELSE c.profileimage END AS author_profileimage,
                p.serviceimageurl AS ets_serviceimageurl,
                CASE WHEN f.role = 1 THEN p.address ELSE c.address END AS address,
                CASE WHEN f.role = 1 THEN p.city ELSE c.city END AS city,
                CASE WHEN f.role = 1 THEN p.statecode ELSE c.statecode END AS statecode,
                c.latitude AS contact_latitude, c.longitude AS contact_longitude,
                p.latitude AS provider_latitude, p.longitude AS provider_longitude,
                p.providertype,
                COALESCE(cc.comments_count, 0) AS comments_count,
                COALESCE(lc.likes_count, 0) AS likes_count,
                lc.feedlikecontactperson,
                COALESCE(lc.has_liked_by_user, false) AS has_liked,
                COALESCE(lc.is_liked_by_circle_user, false) AS is_liked_by_circle,
                ps.businesscategory,
                r.overallrating,
                COALESCE(mr.relationshiptype, pr.relationshiptype) AS mycirclerole_code,
                inv.invitationcode AS invitation
            FROM public.etown_feed f
            LEFT JOIN public.etown_contact c ON f.contactid = c.contactid
            LEFT JOIN public.etown_provider p ON f.userprofileid = p.userprofileid
            LEFT JOIN public.etown_providerservices ps ON f.userprofileid = ps.userprofileid
            LEFT JOIN public.etown_ratings r ON f.userprofileid = r.provideruserprofileid
            LEFT JOIN comment_counts cc ON f.feedid = cc.feedid
            LEFT JOIN like_counts lc ON f.feedid = lc.feedid
            LEFT JOIN public.etown_memberrelation mr ON (mr.from_contactid = f.contactid AND mr.to_contactid = $3) OR (mr.to_contactid = f.contactid AND mr.from_contactid = $3)
            LEFT JOIN public.etown_providerrelation pr ON (pr.fromcontactid = f.contactid AND pr.tocontactid = $3) OR (pr.tocontactid = f.contactid AND pr.fromcontactid = $3)
            LEFT JOIN LATERAL (
                SELECT invitationcode
                FROM public.etown_invitation
                WHERE (invitercontact = f.contactid AND invitecontact = $3)
                   OR (invitecontact = f.contactid AND invitercontact = $3)
                ORDER BY COALESCE(modifieddate, createdon) DESC NULLS LAST,
                         createdon DESC NULLS LAST,
                         invitationid DESC
                LIMIT 1
            ) inv ON TRUE
            WHERE f.contactid IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM retired_ids ri
                   WHERE ri.identity_id = f.contactid
                      OR ri.identity_id = f.userprofileid
              )
              AND NOT EXISTS (
                  SELECT 1 FROM disabled_provider_ids dp
                   WHERE dp.identity_id = f.userprofileid
              )
              AND """ + CIRCLE_VISIBILITY_SQL.format(viewer="$3") + """
        """

        params = [current_user_profile_id, circle_user_profile_ids or [], current_contact_id]
        
        apply_trending_rank = False
        if isMyProfile:
            query += " AND f.userprofileid = $4"
            params.append(current_user_profile_id)
        elif mode == "My Circle" :
            circle_plus_self = list(set(circle_user_profile_ids + [current_user_profile_id]))
            query += f" AND f.userprofileid = ANY(${len(params) + 1}::text[])"
            params.append(circle_plus_self)
        elif mode == "Offers":
            query += " AND f.createdon >= NOW() - INTERVAL '30 days'"
            query += " AND " + OFFER_POST_SQL.format(kw=f"${len(params) + 1}")
            params.append(OFFER_POST_KEYWORDS)
        elif mode == "Trending":
            query += " AND f.createdon >= NOW() - INTERVAL '7 days'"
            query += (
                " AND (COALESCE(lc.likes_count, 0) * 2"
                " + COALESCE(cc.comments_count, 0) * 3) >= 10"
            )
            if latitude is not None and longitude is not None:
                lat_ph = len(params) + 1
                lon_ph = len(params) + 2
                query += f"""
                    AND (
                        (f.role = 1 AND ps.businesscategory = 'product')
                        OR (
                            (CASE WHEN f.role = 1 THEN p.latitude ELSE c.latitude END) IS NOT NULL
                            AND (CASE WHEN f.role = 1 THEN p.longitude ELSE c.longitude END) IS NOT NULL
                            AND 6371 * acos(
                                LEAST(1.0, GREATEST(-1.0,
                                    cos(radians(${lat_ph})) *
                                    cos(radians((CASE WHEN f.role = 1 THEN p.latitude ELSE c.latitude END)::double precision)) *
                                    cos(radians((CASE WHEN f.role = 1 THEN p.longitude ELSE c.longitude END)::double precision) - radians(${lon_ph})) +
                                    sin(radians(${lat_ph})) *
                                    sin(radians((CASE WHEN f.role = 1 THEN p.latitude ELSE c.latitude END)::double precision))
                                ))
                            ) <= 50
                        )
                    )
                """
                params.append(latitude)
                params.append(longitude)
            else:
                query += " AND FALSE"
            apply_trending_rank = True
        
        if category:
            query += f" AND ps.businesscategory = ${len(params) + 1}"
            params.append(category)
        
        if apply_trending_rank:
            query += """
                ORDER BY f.feedid, f.createdon DESC
            )
            SELECT * FROM all_feeds
            ORDER BY (likes_count * 2 + comments_count * 3) DESC, createdon DESC
            """
        else:
            query += """
                ORDER BY f.feedid, f.createdon DESC
            )
            SELECT * FROM all_feeds
            ORDER BY createdon DESC
            """
        
        params.append(limit)
        query += f" LIMIT ${len(params)}"
        params.append(page_offset)
        query += f" OFFSET ${len(params)}"
        
        try:
            async with cls._pool.acquire() as con:
                rows = await con.fetch(query, *params)
                feed_data = [dict(row) for row in rows]
                
                total_count = len(feed_data)
                
                logger.info(f"Fetched {len(feed_data)} feeds for user {current_user_profile_id}")
                return (feed_data, total_count)
                
        except Exception as e:
            logger.error(f"Error fetching feeds: {str(e)}", exc_info=True)
            raise
    
    @classmethod
    async def _get_circle_user_profile_ids(cls, current_user_profile_id: str) -> List[str]:
        """Return the profile ids related to a user through member or provider relations, or [] on error."""
        if not current_user_profile_id:
            return []
        
        member_query = """
        SELECT
            CASE
                WHEN from_userprofile = $1 THEN to_userprofile
                ELSE from_userprofile
            END as circle_upi
        FROM etown_memberrelation
        WHERE from_userprofile = $1 OR to_userprofile = $1
        """
        
        provider_query = """
        SELECT
            CASE
                WHEN fromuserprofileid = $1 THEN touserprofileid
                ELSE fromuserprofileid
            END as circle_upi
        FROM etown_providerrelation
        WHERE fromuserprofileid = $1 OR touserprofileid = $1
        """
        
        try:
            async with cls._pool.acquire() as con:
                member_relations = await con.fetch(member_query, current_user_profile_id)
                provider_relations = await con.fetch(provider_query, current_user_profile_id)
                
                all_circle_upis = set()
                for rel in member_relations:
                    if rel.get('circle_upi'):
                        all_circle_upis.add(rel['circle_upi'])
                        
                for rel in provider_relations:
                    if rel.get('circle_upi'):
                        all_circle_upis.add(rel['circle_upi'])
                        
                return list(all_circle_upis)
        except Exception as e:
            logger.error(f"Error fetching circle user profile IDs: {str(e)}", exc_info=True)
            return []
    
    @classmethod
    @with_retry()
    async def check_for_trending_posts(
        cls,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        current_contact_id: Optional[str] = None,
    ) -> bool:
        """Return True if a trending post is a product post or lies within the trending radius, False otherwise or on error."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        
        if latitude is None or longitude is None:
            return False
        
        logger.info("Checking for trending posts")
        
        TRENDING_TIME_WINDOW_HOURS = 168
        TRENDING_LIKE_WEIGHT = 2
        TRENDING_COMMENT_WEIGHT = 3
        TRENDING_SCORE_THRESHOLD = 10
        MAX_DISTANCE_KM_TRENDING = 50
        
        time_threshold = datetime.now() - timedelta(hours=TRENDING_TIME_WINDOW_HOURS)
        
        query = f"""
        WITH scored_feeds AS (
            SELECT
                f.feedid,
                f.userprofileid,
                ((COALESCE(lc.count, 0) * $2) + (COALESCE(cc.count, 0) * $3)) AS score
            FROM public.etown_feed f
            LEFT JOIN (
                SELECT feedid, COUNT(feedlikesid) as count
                FROM public.etown_feedlikes WHERE likemodename = 'Like' GROUP BY feedid
            ) lc ON f.feedid = lc.feedid
            LEFT JOIN (
                SELECT feedid, COUNT(feedcommentsid) as count
                FROM public.etown_feedcomments GROUP BY feedid
            ) cc ON f.feedid = cc.feedid
            WHERE f.createdon >= $1
              AND {CIRCLE_VISIBILITY_SQL.format(viewer="$5::text")}
        )
        SELECT
            sf.feedid,
            u.role,
            c.latitude AS contact_lat, c.longitude AS contact_lon,
            p.latitude AS provider_lat, p.longitude AS provider_lon,
            ps.businesscategory
        FROM scored_feeds sf
        JOIN public.etown_userprofile u ON sf.userprofileid = u.userprofileid
        LEFT JOIN public.etown_contact c ON u.contactid = c.contactid
        LEFT JOIN public.etown_provider p ON sf.userprofileid = p.userprofileid
        LEFT JOIN public.etown_providerservices ps ON sf.userprofileid = ps.userprofileid
        WHERE sf.score >= $4
          AND NOT EXISTS (
              SELECT 1 FROM public.etown_retired_identity ri
               WHERE (ri.identity_id = u.contactid
                   OR ri.identity_id = sf.userprofileid)
                 AND (ri.deleted = 'true'
                   OR (ri.deactivated = 'true'
                       AND ri.reactivation IS DISTINCT FROM 'true'))
          )
          AND NOT EXISTS (
              SELECT 1 FROM public.etown_provider dp
               WHERE dp.disabled
                 AND dp.userprofileid = sf.userprofileid
          )
        LIMIT 100
        """
        
        try:
            async with cls._pool.acquire() as con:
                params = [
                    time_threshold,
                    TRENDING_LIKE_WEIGHT,
                    TRENDING_COMMENT_WEIGHT,
                    TRENDING_SCORE_THRESHOLD,
                    current_contact_id,
                ]
                trending_posts = await con.fetch(query, *params)
                
                if not trending_posts:
                    return False
                
                user_location = (latitude, longitude)
                
                for post in trending_posts:
                    is_product_post = (
                        post.get('role') == 1 and
                        post.get('businesscategory') == 'product'
                    )
                    
                    if is_product_post:
                        logger.info(f"Found trending product post {post['feedid']} (distance exempt)")
                        return True
                    
                    post_lat = post.get('provider_lat') if post.get('role') == 1 else post.get('contact_lat')
                    post_lon = post.get('provider_lon') if post.get('role') == 1 else post.get('contact_lon')
                    
                    if post_lat is not None and post_lon is not None:
                        try:
                            distance_km = geodesic(user_location, (post_lat, post_lon)).km
                            if distance_km <= MAX_DISTANCE_KM_TRENDING:
                                logger.info(f"Found trending feed {post['feedid']} within {distance_km:.2f}km")
                                return True
                        except Exception:
                            logger.debug("feed: dropping a trending candidate with unusable coordinates", exc_info=True)
                            continue
                
                return False
                
        except Exception as e:
            logger.error(f"Error checking for trending posts: {str(e)}", exc_info=True)
            return False
    
    @classmethod
    @with_retry()
    async def check_for_offers_posts(
        cls,
        user_profile_id: str,
        contact_id: str,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None
    ) -> bool:
        """Return True when a circle-visible offer post is in range or is a product post, False otherwise or on error."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        if latitude is None or longitude is None:
            return False

        MAX_DISTANCE_KM_OFFERS = 50

        query = f"""
            SELECT 1
            FROM public.etown_feed f
            JOIN public.etown_provider p
              ON LOWER(TRIM(f.userprofileid)) = LOWER(TRIM(p.userprofileid))
            WHERE {OFFER_POST_SQL.format(kw="$1")}
              AND f.createdon >= NOW() - INTERVAL '30 days'
              AND NOT etown_is_retired_ci(f.contactid)
              AND NOT etown_is_retired_ci(f.userprofileid)
              AND p.disabled IS NOT TRUE
              AND {CIRCLE_VISIBILITY_SQL.format(viewer="$4::text")}
              AND (
                    EXISTS (
                        SELECT 1 FROM public.etown_providerservices ps
                         WHERE LOWER(TRIM(ps.userprofileid)) = LOWER(TRIM(f.userprofileid))
                           AND ps.businesscategory = 'product'
                    )
                 OR (
                        p.latitude IS NOT NULL AND p.longitude IS NOT NULL
                    AND 6371 * acos(LEAST(1.0, GREATEST(-1.0,
                            cos(radians($2)) * cos(radians(p.latitude))
                            * cos(radians(p.longitude) - radians($3))
                            + sin(radians($2)) * sin(radians(p.latitude))
                        ))) <= {MAX_DISTANCE_KM_OFFERS}
                    )
              )
            LIMIT 1
        """

        try:
            async with cls._pool.acquire() as con:
                found = await con.fetchval(
                    query, OFFER_POST_KEYWORDS, latitude, longitude, contact_id
                )
                return found is not None
        except Exception as e:
            logger.error(f"Error checking for offers posts: {str(e)}", exc_info=True)
            return False

    @classmethod
    @with_retry()
    async def get_feed_likes_count(cls, feed_id: str) -> int:
        """Return a post's like count, excluding deleted likers and disabled providers.

        Raises:
            RuntimeError: When the database pool is not initialized or is closed.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT
                COUNT(fl.feedid) AS count
            FROM etown_feedlikes fl
            WHERE
                fl.feedid = $1
                AND fl.likemode = 1

                AND NOT etown_is_deleted(fl.likesbyuserprofileid)
                AND NOT etown_is_deleted(fl.likedbycontactid)

                AND NOT EXISTS (
                      SELECT 1 FROM etown_provider dp
                       WHERE dp.disabled
                         AND dp.userprofileid = fl.likesbyuserprofileid
                    );
"""

        async with cls._pool.acquire() as con:
            row = await con.fetchrow(query, feed_id)
            return int(row["count"]) if row else 0

    @classmethod
    @with_retry()
    async def get_feed_likes_list(
        cls,
        feed_id: str,
        contact_id: Optional[str] = None,
        userprofile_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return a post's likers newest first, with name, image, role, connection and invitation status toward the given user.

        Args:
            feed_id: The feed post whose likers to list.
            contact_id: Optional contact to compute is_connected and invitation against.
            userprofile_id: Optional profile to compute is_connected against.

        Returns:
            List of liker dicts.

        Raises:
            RuntimeError: When the database pool is not initialized or is closed.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT
                CASE
                    WHEN up.role = 1 THEN pv.businessname
                    WHEN up.role = 2 THEN c.firstname
                    ELSE NULL
                END                         AS name,
                CASE WHEN EXISTS (
                    SELECT 1 FROM public.etown_retired_identity r
                     WHERE (r.identity_id = fl.likesbyuserprofileid
                         OR r.identity_id = fl.likedbycontactid)
                       AND r.deleted = 'true'
                ) THEN 'true' ELSE 'false' END AS liker_deleted,
                CASE WHEN EXISTS (
                    SELECT 1 FROM public.etown_retired_identity r
                     WHERE (r.identity_id = fl.likesbyuserprofileid
                         OR r.identity_id = fl.likedbycontactid)
                       AND r.deactivated = 'true'
                       AND r.reactivation IS DISTINCT FROM 'true'
                ) THEN 'true' ELSE 'false' END AS liker_deactivated,
                fl.likedbycontactid         AS contactid,
                fl.likesbyuserprofileid     AS userprofileid,
                CASE
                    WHEN up.role = 1 THEN pv.serviceimageurl
                    ELSE c.profileimage
                END                         AS profileimage,
                up.role                     AS role,
                pv.providertype             AS providertype,
                COALESCE(
                    EXISTS (
                        SELECT 1 FROM etown_memberrelation mr
                        WHERE (mr.from_contactid = fl.likedbycontactid AND mr.to_contactid = $2)
                           OR (mr.to_contactid = fl.likedbycontactid AND mr.from_contactid = $2)
                           OR (mr.from_userprofile = fl.likesbyuserprofileid AND mr.to_userprofile = $3)
                           OR (mr.to_userprofile = fl.likesbyuserprofileid AND mr.from_userprofile = $3)
                    )
                    OR EXISTS (
                        SELECT 1 FROM etown_providerrelation pr
                        WHERE (pr.fromcontactid = fl.likedbycontactid AND pr.tocontactid = $2)
                           OR (pr.tocontactid = fl.likedbycontactid AND pr.fromcontactid = $2)
                           OR (pr.fromuserprofileid = fl.likesbyuserprofileid AND pr.touserprofileid = $3)
                           OR (pr.touserprofileid = fl.likesbyuserprofileid AND pr.fromuserprofileid = $3)
                    ),
                    false
                ) AS is_connected,
                (
                    SELECT inv.invitationcode
                    FROM etown_invitation inv
                    WHERE (inv.invitercontact = fl.likedbycontactid AND inv.invitecontact = $2)
                       OR (inv.invitecontact = fl.likedbycontactid AND inv.invitercontact = $2)
                          ORDER BY COALESCE(inv.modifieddate, inv.createdon) DESC NULLS LAST,
                                      inv.createdon DESC NULLS LAST,
                                      inv.invitationid DESC
                    LIMIT 1
                ) AS invitation,
                fl.createdond               AS liked_on
            FROM etown_feedlikes fl
            LEFT JOIN etown_contact c ON c.contactid = fl.likedbycontactid
            LEFT JOIN etown_userprofile up ON up.userprofileid = fl.likesbyuserprofileid
            LEFT JOIN etown_provider pv ON pv.userprofileid = fl.likesbyuserprofileid
                                       AND pv.contactid = fl.likedbycontactid
            WHERE fl.feedid = $1
              AND fl.likemode = 1

              AND NOT etown_is_deleted(fl.likesbyuserprofileid)
              AND NOT etown_is_deleted(fl.likedbycontactid)

              AND NOT EXISTS (
                    SELECT 1 FROM etown_provider dp
                     WHERE dp.disabled
                       AND dp.userprofileid = fl.likesbyuserprofileid
                  )
            ORDER BY fl.createdond DESC;
"""

        async with cls._pool.acquire() as con:
            rows = await con.fetch(query, feed_id, contact_id, userprofile_id)
            return [dict(row) for row in rows]

    @classmethod
    @with_retry()
    async def get_feed_like_details(
        cls,
        feed_ids: List[str],
        current_user_profile_id: Optional[str] = None,
        current_contact_id: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Build "liked by X and N others" summaries for feed posts, featuring the newest circle liker and ranking self-likes last.

        Args:
            feed_ids: Feed GUIDs to summarise, typically one page of the feed.
            current_user_profile_id: Requester's profile ID, for circle matching and self-detection.
            current_contact_id: Requester's contact ID, for circle matching and self-detection.

        Returns:
            Dict keyed by feedid with likedby, count, likedbyimage and likedbyimage2.

        Raises:
            RuntimeError: When the database pool is not initialized or is closed.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        if not feed_ids:
            return {}

        query = """
            WITH likers AS (
                SELECT
                    fl.feedid,

                    CASE
                        WHEN ($2::text IS NOT NULL AND fl.likesbyuserprofileid = $2::text)
                          OR ($3::text IS NOT NULL AND fl.likedbycontactid = $3::text)
                            THEN 'You'
                        ELSE CASE
                            WHEN up.role = 1 THEN pv.businessname
                            ELSE c.firstname
                        END
                    END AS name,
                    NULLIF(
                        CASE
                            WHEN up.role = 1 THEN pv.serviceimageurl
                            ELSE c.profileimage
                        END,
                        ''
                    ) AS image,
                    (
                        EXISTS (
                            SELECT 1 FROM public.etown_memberrelation mr
                            WHERE (mr.from_userprofile = fl.likesbyuserprofileid AND mr.to_userprofile = $2::text)
                               OR (mr.to_userprofile = fl.likesbyuserprofileid AND mr.from_userprofile = $2::text)
                               OR (mr.from_contactid = fl.likedbycontactid AND mr.to_contactid = $3::text)
                               OR (mr.to_contactid = fl.likedbycontactid AND mr.from_contactid = $3::text)
                        )
                        OR EXISTS (
                            SELECT 1 FROM public.etown_providerrelation pr
                            WHERE (pr.fromuserprofileid = fl.likesbyuserprofileid AND pr.touserprofileid = $2::text)
                               OR (pr.touserprofileid = fl.likesbyuserprofileid AND pr.fromuserprofileid = $2::text)
                               OR (pr.fromcontactid = fl.likedbycontactid AND pr.tocontactid = $3::text)
                               OR (pr.tocontactid = fl.likedbycontactid AND pr.fromcontactid = $3::text)
                        )
                    ) AS in_circle,
                    (
                        ($2::text IS NOT NULL AND fl.likesbyuserprofileid = $2::text)
                        OR ($3::text IS NOT NULL AND fl.likedbycontactid = $3::text)
                    ) AS is_self,
                    fl.createdond
                FROM public.etown_feedlikes fl
                LEFT JOIN public.etown_userprofile up ON up.userprofileid = fl.likesbyuserprofileid
                LEFT JOIN public.etown_provider pv ON pv.userprofileid = fl.likesbyuserprofileid
                                                  AND pv.contactid = fl.likedbycontactid
                LEFT JOIN public.etown_contact c ON c.contactid = fl.likedbycontactid
                WHERE fl.feedid = ANY($1::text[])
                  AND fl.likemodename = 'Like'
                  AND NOT EXISTS (
                      SELECT 1 FROM public.etown_provider dp
                       WHERE dp.disabled
                         AND dp.userprofileid = fl.likesbyuserprofileid
                  )
            ),
            ranked AS (
                SELECT
                    feedid,
                    name,
                    image,
                    is_self,
                    COUNT(*) OVER (PARTITION BY feedid)::int AS liker_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY feedid
                        ORDER BY is_self, in_circle DESC, createdond DESC NULLS LAST
                    ) AS rn
                FROM likers
            )
            SELECT
                feedid,
                MAX(liker_count) AS liker_count,
                MAX(name) FILTER (WHERE rn = 1) AS likedby,
                MAX(image) FILTER (WHERE rn = 1) AS likedbyimage,
                (
                    array_agg(image ORDER BY rn)
                        FILTER (WHERE rn > 1 AND image IS NOT NULL AND NOT is_self)
                )[1] AS likedbyimage2
            FROM ranked
            GROUP BY feedid;
"""

        async with cls._pool.acquire() as con:
            rows = await con.fetch(query, feed_ids, current_user_profile_id, current_contact_id)

        return {
            row["feedid"]: {
                "likedby": row["likedby"],
                "count": max(int(row["liker_count"]) - 1, 0),
                "likedbyimage": row["likedbyimage"],
                "likedbyimage2": row["likedbyimage2"],
            }
            for row in rows
        }

    @classmethod
    @with_retry()
    async def get_feed_counts(cls, feed_id: str) -> Dict[str, int]:
        """Return a post's comment_count and like_count, counting every like row regardless of mode."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT 'comment_count' AS count_type,
                   COUNT(feedcommentsid) AS total_count
            FROM etown_feedcomments
            WHERE feedid = $1

            UNION ALL

            SELECT 'like_count' AS count_type,
                   COUNT(feedlikesid) AS total_count
            FROM etown_feedlikes
            WHERE feedid = $1;
        """

        async with cls._pool.acquire() as con:
            rows = await con.fetch(query, feed_id)
            result = {row["count_type"]: int(row["total_count"]) for row in rows}
            return {
                "comment_count": result.get("comment_count", 0),
                "like_count": result.get("like_count", 0),
            }

    @classmethod
    @with_retry()
    async def get_feed_details_simple(
        cls,
        feed_id: str,
        contact_id: Optional[str] = None,
        userprofile_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return a visible post's feed, contact and provider fields with has_liked for the viewer, or None.

        Args:
            feed_id: The feed post to fetch.
            contact_id: Optional viewer contact, used for has_liked and circle visibility.
            userprofile_id: Optional viewer profile, used for has_liked.

        Returns:
            Dict of feed, contact and provider fields, or None if not found or not visible.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT
                f.contactid,
                f.userprofileid,
                f.role,
                f.feedtext,
                f.audience,
                f.thumbnailfileurl,
                f.feedfilelocationurl,
                f.feedfilelocation2,
                f.feedfilelocation3,
                f.feedfilelocation4,
                f.feedfilelocation5,
                f.feedvideofilelocation,
                f.orientation,
                CASE WHEN f.role = 1
                     THEN COALESCE(NULLIF(btrim(p.businessname), ''), f.servicename)
                     ELSE f.servicename
                END AS servicename,
                f.serviceimageurl,
                f.feedid,
                f.createdon,

                c.profileimage,
                c.city          AS contact_city,
                c.statecode     AS contact_statecode,
                c.firstname,
                etown_deleted_flag(f.userprofileid)     AS author_deleted,
                etown_deactivated_flag(f.userprofileid) AS author_deactivated,

                p.city          AS provider_city,
                p.statecode     AS provider_statecode,
                p.providerid,
                p.providertype,

                COALESCE((
                    SELECT bool_or(true)
                    FROM etown_feedlikes fl
                    WHERE fl.feedid = f.feedid
                      AND fl.likemodename = 'Like'
                      AND (
                          ($2::text IS NOT NULL AND fl.likedbycontactid = $2)
                          OR ($3::text IS NOT NULL AND fl.likesbyuserprofileid = $3)
                      )
                ), false) AS has_liked

            FROM etown_feed f
            INNER JOIN etown_contact c ON c.contactid = f.contactid
            LEFT JOIN etown_provider p ON p.userprofileid = f.userprofileid
            WHERE f.feedid = $1
              AND NOT EXISTS (
                  SELECT 1 FROM public.etown_retired_identity ri
                   WHERE (ri.identity_id = f.contactid
                       OR ri.identity_id = f.userprofileid)
                     AND (ri.deleted = 'true'
                       OR (ri.deactivated = 'true'
                           AND ri.reactivation IS DISTINCT FROM 'true'))
              )
              AND """ + CIRCLE_VISIBILITY_SQL.format(viewer="$2::text") + """
            ORDER BY f.feedid, c.contactid, p.providerid;
        """

        async with cls._pool.acquire() as con:
            row = await con.fetchrow(query, feed_id, contact_id, userprofile_id)
            return dict(row) if row else None

    @classmethod
    @with_retry()
    async def get_feed_comments_simple(cls, feed_id: str) -> List[Dict[str, Any]]:
        """Return the id, feed id and text of every comment on a post."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT
                feedcommentsid AS etown_commentsid,
                feedid         AS etown_feedid,
                textcomments   AS etown_textcomments
            FROM etown_feedcomments
            WHERE feedid = $1;
        """

        async with cls._pool.acquire() as con:
            rows = await con.fetch(query, feed_id)
            return [dict(row) for row in rows]

    @classmethod
    @with_retry()
    async def get_feed_comments(cls, feed_id: str) -> List[Dict[str, Any]]:
        """Return a post's comments with commenter details newest first, excluding deleted commenters and disabled providers."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT
    f.feedcommentsid,
    f.commentedbyuserprofileid AS commentedbyuserprofileid,
    f.commentedbycontactid AS contactid,
    p.businessname AS provider_businessname,
    c.firstname,

    CASE WHEN EXISTS (
        SELECT 1 FROM public.etown_retired_identity r
         WHERE (r.identity_id = f.commentedbyuserprofileid
             OR r.identity_id = f.commentedbycontactid)
           AND r.deleted = 'true'
    ) THEN 'true' ELSE 'false' END AS commentedby_deleted,
    CASE WHEN EXISTS (
        SELECT 1 FROM public.etown_retired_identity r
         WHERE (r.identity_id = f.commentedbyuserprofileid
             OR r.identity_id = f.commentedbycontactid)
           AND r.deactivated = 'true'
           AND r.reactivation IS DISTINCT FROM 'true'
    ) THEN 'true' ELSE 'false' END AS commentedby_deactivated,
    f.textcomments,
    up.role AS userprofile_role,
    p.providertype,
    f.createdon,
    CASE
        WHEN up.role = 2 THEN c.profileimage
        WHEN up.role = 1 THEN p.serviceimageurl
        ELSE NULL
    END AS etown_imageurl
FROM
    etown_feedcomments f
INNER JOIN
    etown_contact c ON f.commentedbycontactid = c.contactid
LEFT JOIN
    etown_userprofile up ON f.commentedbyuserprofileid = up.userprofileid
LEFT JOIN
    etown_provider p ON f.commentedbyuserprofileid = p.userprofileid
WHERE
    f.feedid = $1

    AND NOT etown_is_deleted(f.commentedbyuserprofileid)
    AND NOT etown_is_deleted(f.commentedbycontactid)

    AND NOT EXISTS (
        SELECT 1 FROM etown_provider dp
         WHERE dp.disabled
           AND dp.userprofileid = f.commentedbyuserprofileid
    )
ORDER BY
    f.createdon DESC;
"""

        async with cls._pool.acquire() as con:
            rows = await con.fetch(query, feed_id)
            return [dict(row) for row in rows]

    @classmethod
    @with_retry()
    async def delete_feed(cls, feed_id: str) -> None:
        """Delete a feed post together with its comments and likes in one statement."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            WITH deleted_comments AS (
                DELETE FROM public.etown_feedcomments
                WHERE feedid = $1
            ),
            deleted_likes AS (
                DELETE FROM public.etown_feedlikes
                WHERE feedid = $1
            )
            DELETE FROM public.etown_feed
            WHERE feedid = $1;
        """

        async with cls._pool.acquire() as con:
            await con.execute(query, feed_id)

    @classmethod
    @with_retry()
    async def insert_feed_comment(
        cls,
        feed_id: str,
        commented_by: str,
        commentedby_userprofile: str,
        comments: str,
    ) -> str:
        """Insert a comment on a feed post with the commenter's names filled in and return the new feedcommentsid."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            INSERT INTO public.etown_feedcomments
            (
                feedcommentsid,
                commentedbycontactid,
                commentedbycontactidname,
                commentedbyuserprofileid,
                commentedbyuserprofileidname,
                feedid,
                textcomments,
                createdon
            )
            VALUES
            (
                gen_random_uuid()::text,
                $1,
                (SELECT firstname FROM public.etown_contact WHERE contactid = $1),
                $2,
                (SELECT name FROM public.etown_userprofile WHERE userprofileid = $2),
                $3,
                $4,
                NOW()
            )
            RETURNING feedcommentsid;
        """

        async with cls._pool.acquire() as con:
            row = await con.fetchrow(query, commented_by, commentedby_userprofile, feed_id, comments)
            return row["feedcommentsid"]

    @classmethod
    @with_retry()
    async def get_feed_comment_by_id(cls, feedcommentsid: str) -> Optional[Dict[str, Any]]:
        """Return one feed comment's ids, text and commenter ids, or None if not found."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT
                feedid,
                textcomments,
                feedcommentsid,
                commentedbycontactid,
                commentedbyuserprofileid
            FROM public.etown_feedcomments
            WHERE feedcommentsid = $1;
        """

        async with cls._pool.acquire() as con:
            row = await con.fetchrow(query, feedcommentsid)
            return dict(row) if row else None

    @classmethod
    @with_retry()
    async def update_feed_comment(
        cls,
        feed_comment_row_id: str,
        commented_by: str,
        feed_id: str,
        comments: str,
    ) -> Optional[Dict[str, Any]]:
        """Update a feed comment's commenter, feed and text and return the updated row, or None if no row matched."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            UPDATE public.etown_feedcomments
            SET
                commentedbycontactid = $1,
                commentedbycontactidname = (SELECT firstname FROM public.etown_contact WHERE contactid = $1),
                feedid = $2,
                textcomments = $3,
                modifieddate = NOW()
            WHERE feedcommentsid = $4
            RETURNING
                feedcommentsid,
                commentedbycontactid,
                commentedbycontactidname,
                feedid,
                textcomments;
        """

        async with cls._pool.acquire() as con:
            row = await con.fetchrow(query, commented_by, feed_id, comments, feed_comment_row_id)
            return dict(row) if row else None

    @classmethod
    @with_retry()
    async def delete_feed_comment(cls, feed_comment_row_id: str) -> None:
        """Delete a single feed comment by its ID."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            DELETE FROM public.etown_feedcomments
            WHERE feedcommentsid = $1;
        """

        async with cls._pool.acquire() as con:
            await con.execute(query, feed_comment_row_id)

    @classmethod
    @with_retry()
    async def upsert_feed_like(
        cls,
        feed_id: str,
        liked_by: str,
        like_mode: int,
    ) -> Dict[str, Any]:
        """Update the existing like row for a feed and profile, or insert one, and return the resulting row.

        Args:
            feed_id: The feed post GUID.
            liked_by: The user profile ID of the person liking or disliking.
            like_mode: 1 for Like, 2 for Dislike.

        Returns:
            Dict of the like row, or {} if nothing was returned.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        select_query = """
            SELECT feedlikesid
            FROM etown_feedlikes
            WHERE feedid = $1
              AND likesbyuserprofileid = $2
            LIMIT 1;
        """

        update_query = """
            UPDATE etown_feedlikes
            SET
                feedid = $1,
                likemode = $2,
                likemodename = CASE
                    WHEN $2 = 1 THEN 'Like'
                    WHEN $2 = 2 THEN 'Dislike'
                    ELSE NULL
                END,
                likesbyuserprofileid = $3,
                likesbyuserprofileidname = (
                    SELECT name
                    FROM etown_userprofile
                    WHERE userprofileid = $3
                    LIMIT 1
                ),
                modifieddate = NOW()
            WHERE feedlikesid = $4
            RETURNING
                feedid,
                feedlikesid,
                likesbyuserprofileid,
                likesbyuserprofileidname,
                likedbycontactid,
                likedbycontactidname,
                likemode,
                likemodename;
        """

        insert_query = """
            INSERT INTO etown_feedlikes (
    feedlikesid,
    feedid,
    likemode,
    likemodename,
    likesbyuserprofileid,
    likesbyuserprofileidname,
    likedbycontactid,
    likedbycontactidname,
    createdond
)
SELECT
    gen_random_uuid()::text,
    $1,
    $2,
    CASE
        WHEN $2 = 1 THEN 'Like'
        WHEN $2 = 2 THEN 'Dislike'
        ELSE NULL
    END,
    up.userprofileid,
    up.name,
    up.contactid,
    up.contactidname,
    NOW()
FROM etown_userprofile up
WHERE up.userprofileid = $3
RETURNING
    feedid,
    feedlikesid,
    likesbyuserprofileid,
    likesbyuserprofileidname,
    likedbycontactid,
    likedbycontactidname,
    likemode,
    likemodename;
        """

        async with cls._pool.acquire() as con:
            existing = await con.fetchrow(select_query, feed_id, liked_by)
            if existing:
                feed_like_row_id = existing["feedlikesid"]
                row = await con.fetchrow(update_query, feed_id, like_mode, liked_by, feed_like_row_id)
            else:
                row = await con.fetchrow(insert_query, feed_id, like_mode, liked_by)
            return dict(row) if row else {}