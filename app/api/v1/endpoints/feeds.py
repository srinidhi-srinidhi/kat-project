"""Feeds Endpoints - HTTP routes for the social feed, posts, likes and comments.

HTTP layer for /api/feeds: serves the personalized feed in Public, My Circle, Trending and Offers modes, and handles post, like and comment writes through feed_utils; only POST /create checks the shared bearer token, and every write is mirrored to Dataverse in the background.

Features:
    - Social feeds: Personalized, paginated content aggregation across four modes
    - Geographic filtering: Haversine distance sorting via geopy when lat/long are supplied
    - Performance monitoring: Per-stage query timing breakdown returned in every GET / response
    - Trending analysis: Detects nearby trending posts to surface the Trending category
    - Offers integration: Surfaces the Offers category when boosted-provider offers exist nearby
    - Circle filtering: My Circle mode filters to the caller's member/provider relations
    - Like summary: Per-feed "liked by X and N others" details, prioritising My Circle likers
    - Multilingual categories: Category labels translated across English/Hindi/Tamil/Malayalam/Telugu
    - Media upload: Up to eight base64 assets (images/video/thumbnail) uploaded to Azure Blob on create
    - Plan caps: create and edit return 402 on a plan breach, checked before uploading
    - Provider badges: Etown_providertype carries the poster's paid-tier badge label
    - Engagement pushes: likes and comments trigger notify_feed_like / notify_feed_comment
    - Cross-posting: create_feed can auto-publish to Instagram and/or Facebook via social_media flag
    - Share-to-offer: create_feed can also insert a matching etown_offers row when sharetooffer is set
    - Dataverse mirroring: Every write is mirrored to Dataverse via a background task

Endpoints:
    GET /: Retrieve personalized social media feed with performance metrics
    POST /edit-post: Edit the text of an existing feed post
    DELETE /feed: Delete a feed post and its comments/likes
    GET /detailsbyid: Get feed details, counts, or comments by feed_id
    GET /feed_likes_list: Get the total like count for a feed post
    GET /feed_likes: List users who liked a feed post (name, IDs, time)
    GET /feed_comments_list: List comments for a feed post
    POST /create: Create a new feed post (media upload + notifications + optional cross-post/offer)
    POST /feedcomments: Add, update, or delete a feed comment
    POST /likes: Like or dislike a feed post

Classes:
    EditPostRequest(): Body for POST /edit-post — feed_id and new feed_text
    DeletePostRequest(): Body for DELETE /feed — feed_id to remove
    FeedCommentRequest(): Body for POST /feedcomments — add/update/delete switched on action_mode
    FeedLikeRequest(): Body for POST /likes — feed_id, liked_by, and like_mode (1=like, 2=dislike)

Functions:
    _resolve_create_feed_profile_id(): Pick the caller's live userprofile for the posting role
    translate_mode(): Translate a localized mode string to its English/canonical equivalent
    get_feeds_endpoint(): Main feed retrieval with comprehensive filtering and monitoring
    edit_post(): Update a feed post's text and mirror the change to Dataverse
    delete_feed_endpoint(): Delete a feed post plus its comments and likes
    get_feed_details_by_id(): Return feed counts, comments, or full details depending on query flags
    feed_likes_list(): Return the total like count for a feed post
    feed_likes_users(): Return the list of users who liked a feed post, with connection status
    feed_comments_list(): Return all comments for a feed post
    _upload_media_or_none(): Decode base64 media, detect its MIME type, and upload it to blob storage
    _row_to_feed_item(): Map a get_feed_by_id() row to the EtownFeedItem response model
    _share_feed_to_offer(): Insert a matching offer from a feed post when sharetooffer is set
    create_feed_endpoint(): Create a feed post, upload its media, and queue notifications/cross-posts
    feedcomments(): Add, update, or delete a feed comment
    feed_likes(): Like or dislike a feed post and mirror the change to Dataverse

Dependencies:
    - asyncio: Parallel execution of feed/trending/offers queries and media uploads
    - datetime, pytz: Timezone handling for IST-formatted timestamps
    - geopy: Haversine distance calculations for geographic sorting
    - fastapi: API framework, background tasks, and query parameters
    - app.db.client / app.etown.feed.feed_utils: Feed queries, timing wrappers, and post CRUD
    - app.etown.feed.feed_syncing: Dataverse mirroring for feed/comment/like writes
    - app.etown.feed.feed_notifications: Circle notification and boosted-broadcast fan-out
    - app.etown.offers.offers_utils: Boosted-provider check and the shared offer-creation workflow
    - app.etown.notifications.preferences: Feed-open/last-seen tracking for governance
    - app.etown.notifications.triggers: Like and comment push notifications
    - app.etown.plans.plans_utils: Plan-limit errors and provider badge resolution
    - app.services.api_activity: API call tracking
    - app.etown.instagram.instagram_utils / app.etown.facebook.facebook_utils: Auto cross-posting
    - app.utils.blob_storage: Azure Blob upload, MIME detection, and container/name helpers
    - app.schemas.api: Feed request/response models and validation

Updates:
    23-Feb-2026: Enhanced documentation with complete feature coverage
    10-Jul-2026: Added GET /feed_likes to list who liked a post
    14-Jul-2026: create_feed passes poster userprofile and is_text_post to the push fan-out.
    30-Jul-2026: GET / returns feedlikedetails per feed, prioritising My Circle likers.
    18-Aug-2026: /create and /edit-post return 402 on a plan breach; edit accepts audience and images.
    19-Aug-2026: Pre-upload cap check runs for every poster; a video-less thumbnail counts against the photo cap.
    20-Aug-2026: Feed, detailsbyid, create, feed_likes and feed_comments_list carry Etown_providertype.
    24-Aug-2026: Share-to-offer skips quietly on a plan breach.
    31-Aug-2026: Etown_providertype reports the paid tier's badge label via premium_display.
    09-Sep-2026: /create and /edit-post take audience as "mycircle" or "public"; dropped the mycircle boolean.

Notes:
    Includes comprehensive performance metrics for database query optimization.
    Supports geographic filtering with Haversine formula for distance calculations.
    Integrates trending analysis and offers for enhanced user engagement.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Literal
import pytz

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from geopy.distance import geodesic
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.core.security import verify_token
from app.schemas.api import (
    FullFeedResponse,
    FeedExecutionLogs,
    EtownFeedItem,
    FeedCommentItem,
    FeedLikeDetails,
    FeedLikeUserItem,
    CreateFeedRequest,
)
from app.db.client import Database
from app.etown.feed.feed_utils import (
    timed_main_feed,
    timed_trending_check,
    timed_offers_check,
    edit_feed_post,
    create_feed_post,
    enforce_feed_plan_limits,
    resolve_audience,
    Database as FeedDatabase,
)
from app.etown.feed.feed_syncing import (
    update_feed_in_dataverse,
    insert_feed_to_dataverse,
    delete_feed_in_dataverse,
    insert_feed_comment_to_dataverse,
    update_feed_comment_in_dataverse,
    delete_feed_comment_in_dataverse,
    insert_feed_like_to_dataverse,
)
from app.etown.feed.feed_notifications import notify_circle_on_new_feed, send_boosted_feed_broadcast
from app.etown.offers.offers_utils import (
    is_provider_boosted,
    create_offer_full,
    OfferRateLimitError,
)
from app.etown.plans.plans_utils import PlanLimitError, premium_display, resolve_provider_badges
from app.etown.notifications.preferences import touch_feed_last_seen as _touch_feed_last_seen
from app.etown.notifications.preferences import record_feed_open as _record_feed_open
from app.services.api_activity import track_api_call
from app.etown.instagram.instagram_utils import auto_publish_feed_to_instagram
from app.etown.facebook.facebook_utils import auto_publish_feed_to_facebook
from app.utils.blob_storage import (
    normalize_container_name,
    to_bytes,
    upload_blob_bytes,
    current_timestamp_prefix,
    detect_mime_type,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feeds"])


DEFAULT_FEED_LIMIT = 20
ALLOWED_AI_CATEGORIES = ["Service", "Product", "Job", "Event", "News", "Other"]

MODE_TRANSLATIONS = {
    "Public": {
        "English": "Public",
        "Hindi": "सार्वजनिक",
        "Tamil": "பொது பதிவு",
        "Malayalam": "പൊതു",
        "Telugu": "పబ్లిక్"
    },
    "My Circle": {
        "English": "My Circle",
        "Hindi": "मेरा सर्कल",
        "Tamil": "எனது வட்டம்",
        "Malayalam": "എന്റെ വലം",
        "Telugu": "నా వలయం"
    },
    "Offers": {
        "English": "Offers",
        "Hindi": "ऑफ़र",
        "Tamil": "சலுகைகள்",
        "Malayalam": "ഓഫറുകൾ",
        "Telugu": "ఆఫర్లు"
    },
    "Trending": {
        "English": "Trending",
        "Hindi": "प्रवृत्तियां",
        "Tamil": "பிரபலமான",
        "Malayalam": "ട്രെൻഡിംഗ്",
        "Telugu": "ట్రెండింగ్"
    }
}

CIRCLE_ROLE_MAP = {
    0: "Provider", 
    1: "Family", 
    2: "Relatives", 
    3: "Friends", 
    4: "Customer"
}
IST = pytz.timezone('Asia/Kolkata')


async def _resolve_create_feed_profile_id(
    contact_id: str,
    requested_profile_id: str,
    role: int,
) -> str:
    """Return the contact's active profile ID for the role, preferring the requested one, else raise 400."""
    profile_id = await Database.fetchval(
        """
        SELECT userprofileid
        FROM public.etown_userprofile
        WHERE contactid = $1
          AND role = $2
          AND deleted IS DISTINCT FROM 'true'
        ORDER BY (userprofileid = $3) DESC, createdon ASC NULLS LAST, userprofileid
        LIMIT 1
        """,
        contact_id,
        role,
        requested_profile_id,
    )
    if profile_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The contact does not have an active profile for the requested role.",
        )
    return str(profile_id)


def translate_mode(input_mode: str) -> str:
    """Return the canonical English feed mode for a localized mode string, defaulting to Public."""
    if not input_mode:
        logger.warning("Mode not provided, defaulting to 'Public'")
        return "Public"
    if input_mode in MODE_TRANSLATIONS:
        return input_mode
    for canonical, translations in MODE_TRANSLATIONS.items():
        if input_mode in translations.values():
            return canonical
    logger.warning(f"Unrecognized mode '{input_mode}', defaulting to 'Public'")
    return "Public"


@router.get("", response_model=FullFeedResponse)
async def get_feeds_endpoint(
    current_contact_id: str = Query(..., description="The contact ID of the user requesting the feed."),
    current_user_profile_id: str = Query(..., description="The user profile ID of the user requesting the feed."),
    mode: str = Query("Public", description="Set the feed mode (can be in any supported language)."),
    limit: int = Query(DEFAULT_FEED_LIMIT, ge=1, le=100, description="Number of feeds to return per page."),
    category: Optional[Literal["Service", "Product", "Job", "Event", "News", "Other"]] = Query(None, description="Filter feeds by a specific category."),
    offset: int = Query(1, ge=1, description="Page number for pagination."),
    latitude: Optional[float] = Query(None, description="User's latitude for distance-based filtering."),
    longitude: Optional[float] = Query(None, description="User's longitude for distance-based filtering."),
    isMyProfile: bool = Query(False, description="Set to true to fetch posts for the current user's profile."),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Handle GET /api/feeds: return a page of feeds for the mode with localized categories and timing logs."""
    start_time = time.time()
    track_api_call(background_tasks, "/api/feeds", current_contact_id, current_user_profile_id)
    translated_mode = translate_mode(mode)
    logger.info(f"Received mode: '{mode}', translated to: '{translated_mode}'")
    if translated_mode == "My Circle" and current_user_profile_id:
        background_tasks.add_task(_touch_feed_last_seen, current_user_profile_id)
    if current_user_profile_id and offset == 1 and not isMyProfile:
        background_tasks.add_task(_record_feed_open, current_user_profile_id)
    
    try:
        timing_logs = {
            "main_feed_query_time_ms": 0,
            "trending_check_time_ms": 0,
            "offers_check_time_ms": 0,

            "parallel_tasks_time_ms": 0,
            "category_processing_time_ms": 0,
            "localization_time_ms": 0
        }

        parallel_start_time = time.time()
        
        results = await asyncio.gather(
            timed_main_feed(
                current_contact_id=current_contact_id,
                current_user_profile_id=current_user_profile_id,
                limit=limit,
                offset=offset,
                mode=translated_mode,
                category=category,
                latitude=latitude,
                longitude=longitude,
                isMyProfile=isMyProfile,
                timing_logs=timing_logs
            ),
            timed_trending_check(
                latitude=latitude,
                longitude=longitude,
                timing_logs=timing_logs
            ),
            timed_offers_check(
                current_user_profile_id=current_user_profile_id,
                current_contact_id=current_contact_id,
                latitude=latitude,
                longitude=longitude,
                timing_logs=timing_logs
            ),
            return_exceptions=True
        )
        
        parallel_end_time = time.time()
        timing_logs["parallel_tasks_time_ms"] = (parallel_end_time - parallel_start_time) * 1000

        (paginated_feed_data, _), has_trending_posts, has_offers_posts = results

        formatted_feeds = []
        user_location = (latitude, longitude) if latitude is not None and longitude is not None else None
        badge_map = await resolve_provider_badges(
            row.get('userprofileid') for row in paginated_feed_data
        )
        for row in paginated_feed_data:
            created_on_str = None
            db_created_on = row.get('createdon')
            if db_created_on and isinstance(db_created_on, datetime):
                utc_time = db_created_on.replace(tzinfo=pytz.utc)
                ist_time = utc_time.astimezone(IST)
                created_on_str = ist_time.strftime("%m/%d/%Y %I:%M %p")
            feed_lat = None
            feed_lon = None
            if row.get('role') == 1:
                feed_lat = row.get('provider_latitude')
                feed_lon = row.get('provider_longitude')
            else:
                feed_lat = row.get('contact_latitude')
                feed_lon = row.get('contact_longitude')
            role_code = row.get('mycirclerole_code')
            role_name = CIRCLE_ROLE_MAP.get(role_code)
            my_circle_val = "false" if role_name is None else role_name
            my_circle_role_val = "" if role_name is None else role_name
            invitation_code = row.get('invitation')
            invitation_val = "" if invitation_code is None else invitation_code
            distance_km = None
            distance_meters = None
            if user_location and feed_lat is not None and feed_lon is not None:
                try:
                    feed_location = (feed_lat, feed_lon)
                    calculated_distance = geodesic(user_location, feed_location)
                    distance_km = round(calculated_distance.km, 2)
                    distance_meters = int(calculated_distance.meters)
                except Exception as e:
                    logger.warning(f"Failed to calculate distance for feed {row.get('feedid')}: {e}")
            feed_item = EtownFeedItem(
                Etown_feedid=row.get('feedid'),
                Etown_feedfilelocationurl=row.get('feedfilelocationurl'),
                Etown_feedfilelocationurl2=row.get('feedfilelocation2'),
                Etown_feedfilelocationurl3=row.get('feedfilelocation3'),
                Etown_feedfilelocationurl4=row.get('feedfilelocation4'),
                Etown_feedfilelocationurl5=row.get('feedfilelocation5'),
                Etown_feedvideofilelocation=row.get('feedvideofilelocation'),
                Etown_thumbnailfileurl=row.get('thumbnailfileurl'),
                Etown_feedtext=row.get('feedtext'),
                Etown_servicename=row.get('servicename'),
                Etown_userprofileid=row.get('userprofileid'),
                Etown_contactid=row.get('contactid'),
                Etown_role=row.get('role'),
                Etown_profileimage=row.get('author_profileimage'),
                Etown_ets_serviceimageurl=row.get('ets_serviceimageurl'),
                Etown_commentscount=str(row.get('comments_count', 0)),
                Etown_feedlikecount=str(row.get('likes_count', 0)),
                Etown_feedlikecontactperson=row.get('has_liked'),
                Etown_likedPerson=row.get('feedlikecontactperson', ''),
                Etown_createdon=created_on_str,
                Etown_name=row.get('author_name'),
                Etown_address=row.get('address'),
                Etown_statecode=row.get('statecode'),
                Etown_city=row.get('city'),
                Etown_mycircle=my_circle_val,
                Etown_mycirclerole=my_circle_role_val,
                Etown_invitation=invitation_val,
                Etown_businesscategory=row.get('businesscategory'),
                Etown_providertype=premium_display(
                    row.get('providertype'),
                    (badge_map.get(row.get('userprofileid')) or {}).get("premium_label"),
                ),
                Etown_is_liked_by_circle=row.get('is_liked_by_circle'),
                Etown_latitude=feed_lat,
                Etown_longitude=feed_lon,
                Etown_distance_km=distance_km,
                Etown_distance_meters=distance_meters,
                Etown_orientation=row.get('orientation'),
                Etown_is_trending=False,
                feedlikedetails=FeedLikeDetails(**(row.get('like_details') or {}))
            )
            formatted_feeds.append(feed_item)
        category_start_time = time.time()
        
        base_categories = ["Public", "My Circle"]
        found_categories = set()

        if has_trending_posts:
            found_categories.add("Trending")
        if has_offers_posts:
            found_categories.add("Offers")
        
        final_categories = base_categories + sorted(list(found_categories))
        
        category_end_time = time.time()
        timing_logs["category_processing_time_ms"] = (category_end_time - category_start_time) * 1000

        localization_start_time = time.time()
        
        localized_feeds = {
            "English": [],
            "Hindi": [],
            "Tamil": [],
            "Malayalam": [],
            "Telugu": []
        }
        for canonical_mode in final_categories:
            for lang, translations in MODE_TRANSLATIONS[canonical_mode].items():
                localized_feeds[lang].append(translations)

        localization_end_time = time.time()
        timing_logs["localization_time_ms"] = (localization_end_time - localization_start_time) * 1000

        total_duration = time.time() - start_time
        timing_logs["total_execution_time_ms"] = total_duration * 1000
        
        database_total_time = (
            timing_logs["main_feed_query_time_ms"] + 
            timing_logs["trending_check_time_ms"] + 
            timing_logs["offers_check_time_ms"]
        )

        performance_breakdown = {
            "database_percentage": round((database_total_time / timing_logs["total_execution_time_ms"]) * 100, 2),
            "processing_percentage": round(((timing_logs["category_processing_time_ms"] + timing_logs["localization_time_ms"]) / timing_logs["total_execution_time_ms"]) * 100, 2),
            "main_feed_percentage": round((timing_logs["main_feed_query_time_ms"] / timing_logs["total_execution_time_ms"]) * 100, 2),
            "trending_percentage": round((timing_logs["trending_check_time_ms"] / timing_logs["total_execution_time_ms"]) * 100, 2),
            "offers_percentage": round((timing_logs["offers_check_time_ms"] / timing_logs["total_execution_time_ms"]) * 100, 2),
            "parallel_efficiency": round((database_total_time / timing_logs["parallel_tasks_time_ms"]) * 100, 2) if timing_logs["parallel_tasks_time_ms"] > 0 else 0
        }

        execution_logs = FeedExecutionLogs(
            total_execution_time_ms=timing_logs["total_execution_time_ms"],
            main_feed_query_time_ms=timing_logs["main_feed_query_time_ms"],
            trending_check_time_ms=timing_logs["trending_check_time_ms"],
            offers_check_time_ms=timing_logs["offers_check_time_ms"],
            parallel_tasks_time_ms=timing_logs["parallel_tasks_time_ms"],
            category_processing_time_ms=timing_logs["category_processing_time_ms"],
            localization_time_ms=timing_logs["localization_time_ms"],
            database_total_time_ms=database_total_time,
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_profile_id=current_user_profile_id,
            mode=translated_mode,
            feeds_count=len(formatted_feeds),
            database_queries_count=4,
            performance_breakdown=performance_breakdown
        )


        return {
            "feeds": formatted_feeds,
            "feed_categorize": final_categories,
            "feed_categorize_English": localized_feeds["English"],
            "feed_categorize_Hindi": localized_feeds["Hindi"],
            "feed_categorize_Tamil": localized_feeds["Tamil"],
            "feed_categorize_Malayalam": localized_feeds["Malayalam"],
            "feed_categorize_Telugu": localized_feeds["Telugu"],
            "logs": execution_logs
        }
        
    except Exception as e:
        end_time = time.time()
        duration = end_time - start_time
        error_duration_ms = duration * 1000
        
        error_logs = FeedExecutionLogs(
            total_execution_time_ms=error_duration_ms,
            main_feed_query_time_ms=0,
            trending_check_time_ms=0,
            offers_check_time_ms=0,
            parallel_tasks_time_ms=0,
            category_processing_time_ms=0,
            localization_time_ms=0,
            database_total_time_ms=0,
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_profile_id=current_user_profile_id,
            mode=mode,
            feeds_count=0,
            database_queries_count=0,
            performance_breakdown={"error": "Request failed before completion"}
        )
        
        logger.error(
            f"Feeds query failed after {duration:.2f}s ({error_duration_ms:.1f}ms): {e} - "
            f"User: {current_user_profile_id}, Mode: {mode}", 
            exc_info=True
        )
        
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=500, 
            detail="An error occurred while fetching the feed."
        )


class EditPostRequest(BaseModel):
    feed_id: str
    feed_text: str
    audience: Optional[str] = Field(
        None,
        description=(
            'New audience: "mycircle" (the author\'s circle only) or "public" (the public '
            'feed). Omit to leave the post\'s audience as it stands.'
        ),
        examples=["mycircle", "public"],
    )
    feedfilelocationurl: Optional[str] = None
    feedfilelocation2: Optional[str] = None
    feedfilelocation3: Optional[str] = None
    feedfilelocation4: Optional[str] = None
    feedfilelocation5: Optional[str] = None

    @field_validator("audience", mode="before")
    @classmethod
    def _audience_to_text(cls, value):
        """Coerce an integer audience value to its string form."""
        return str(value) if isinstance(value, int) else value


class DeletePostRequest(BaseModel):
    feed_id: str


@router.post("/edit-post")
async def edit_post(body: EditPostRequest, background_tasks: BackgroundTasks):
    """Handle POST /api/feeds/edit-post: update a post's text, audience and images, then mirror to Dataverse.

    Raises:
        HTTPException: 422 for a bad audience, 402 over the plan limit, 500 on failure.
    """
    try:
        audiencename, audience_code = resolve_audience(body.audience)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        await edit_feed_post(
            feed_id=body.feed_id,
            feed_text=body.feed_text,
            audience=audiencename,
            feedfilelocationurl=body.feedfilelocationurl,
            feedfilelocation2=body.feedfilelocation2,
            feedfilelocation3=body.feedfilelocation3,
            feedfilelocation4=body.feedfilelocation4,
            feedfilelocation5=body.feedfilelocation5,
        )
    except PlanLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"edit_post: failed for feed_id={body.feed_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while updating the post.")

    background_tasks.add_task(
        update_feed_in_dataverse,
        body.feed_id,
        body.feed_text,
        audience_code,
        body.feedfilelocationurl,
        body.feedfilelocation2,
        body.feedfilelocation3,
        body.feedfilelocation4,
        body.feedfilelocation5,
    )

    return {"response": "Post Update Successfully"}


@router.delete("/feed")
async def delete_feed_endpoint(body: DeletePostRequest, background_tasks: BackgroundTasks):
    """Handle DELETE /api/feeds/feed: delete a post and its comments and likes, then mirror the delete to Dataverse."""
    try:
        await FeedDatabase.delete_feed(feed_id=body.feed_id)
    except Exception as e:
        logger.error(f"delete_feed: failed for feed_id={body.feed_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while deleting the post.")

    background_tasks.add_task(delete_feed_in_dataverse, body.feed_id)

    return {"response": "Post Deleted Successfully"}


@router.get("/detailsbyid", description="Etown - Mobile API - Feed - Get Feed Details By Id API")
async def get_feed_details_by_id(
    feed_id: str = Query(..., description="The unique identifier of the feed post."),
    feed_details: str = Query(None, description="Additional feed details filter."),
    feed_comments: str = Query(None, description="Feed comments filter."),
    contact_id: Optional[str] = Query(None, description="Optional contact ID; used to check whether this contact liked the feed."),
    userprofile_id: Optional[str] = Query(None, description="Optional user profile ID; used to check whether this profile liked the feed."),
):
    """Handle GET /api/feeds/detailsbyid: return a post's counts, its comments, or its details depending on the flags."""
    if (feed_comments or "").lower() == "no" and (feed_details or "").lower() == "no":
        try:
            counts = await FeedDatabase.get_feed_counts(feed_id=feed_id)
        except Exception as e:
            logger.error(f"detailsbyid: get_feed_counts failed for feed_id={feed_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="An error occurred while fetching feed counts.")
        return {
            "Etown_commentscount": counts["comment_count"],
            "Etown_likescount": counts["like_count"],
        }
    elif (feed_comments or "").lower() == "yes":
        try:
            comments = await FeedDatabase.get_feed_comments_simple(feed_id=feed_id)
        except Exception as e:
            logger.error(f"detailsbyid: get_feed_comments_simple failed for feed_id={feed_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="An error occurred while fetching feed comments.")
        return [
            {
                "Etown_commentsid": row["etown_commentsid"],
                "Etown_feedid": row["etown_feedid"],
                "Etown_textcomments": row["etown_textcomments"],
            }
            for row in comments
        ]
    else:
        try:
            row = await FeedDatabase.get_feed_details_simple(
                feed_id=feed_id,
                contact_id=contact_id,
                userprofile_id=userprofile_id,
            )
        except Exception as e:
            logger.error(f"detailsbyid: get_feed_details_simple failed for feed_id={feed_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="An error occurred while fetching feed details.")
        if row is None:
            return {"message": "Feed not found"}
        role = row.get("role")
        city = row.get("contact_city") if role == 2 else (row.get("provider_city") if role == 1 else None)
        statecode = row.get("contact_statecode") if role == 2 else (row.get("provider_statecode") if role == 1 else None)
        created_on_str = None
        db_created_on = row.get("createdon")
        if db_created_on and isinstance(db_created_on, datetime):
            utc_time = db_created_on.replace(tzinfo=pytz.utc) if db_created_on.tzinfo is None else db_created_on.astimezone(pytz.utc)
            ist_time = utc_time.astimezone(IST)
            created_on_str = ist_time.strftime("%m/%d/%Y %I:%M %p")
        premium_label = (
            (await resolve_provider_badges([row.get("userprofileid")])).get(row.get("userprofileid")) or {}
        ).get("premium_label")
        return {
            "Etown_feedid": row.get("feedid"),
            "Etown_feedfilelocationurl": row.get("feedfilelocationurl"),
            "Etown_feedfilelocationurl2": row.get("feedfilelocation2"),
            "Etown_feedfilelocationurl3": row.get("feedfilelocation3"),
            "Etown_feedfilelocationurl4": row.get("feedfilelocation4"),
            "Etown_feedfilelocationurl5": row.get("feedfilelocation5"),
            "Etown_feedvideofilelocation": row.get("feedvideofilelocation"),
            "Etown_orientation": row.get("orientation"),
            "Etown_thumbnailfileurl": row.get("thumbnailfileurl"),
            "Etown_feedtext": row.get("feedtext"),
            "Etown_servicename": row.get("servicename"),
            "Etown_userprofileid": row.get("userprofileid"),
            "Etown_contactid": row.get("contactid"),
            "Etown_role": role,
            "Etown_providertype": premium_display(row.get("providertype"), premium_label),
            "Etown_profileimage": row.get("profileimage"),
            "Etown_ets_serviceimageurl": row.get("serviceimageurl"),
            "Etown_firstname": row.get("firstname"),
            "Etown_createdon": created_on_str,
            "Etown_city": city,
            "Etown_statecode": statecode,
            "Etown_feedlikecontactperson": str(bool(row.get("has_liked"))).lower(),
        }


@router.get("/feed_likes_list")
async def feed_likes_list(
    feed_id: str = Query(..., description="The ID of the feed post to retrieve like count for."),
):
    """Handle GET /api/feeds/feed_likes_list: return the total like count for a post."""
    try:
        count = await FeedDatabase.get_feed_likes_count(feed_id=feed_id)
    except Exception as e:
        logger.error(f"feed_likes_list: failed for feed_id={feed_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while fetching feed likes.")

    return {"Feed like count": count}


@router.get("/feed_likes", response_model=list[FeedLikeUserItem])
async def feed_likes_users(
    feed_id: str = Query(..., description="The ID of the feed post to retrieve likers for."),
    contact_id: Optional[str] = Query(None, description="Optional contact ID to check connection with each liker."),
    userprofile_id: Optional[str] = Query(None, description="Optional user profile ID to check connection with each liker."),
):
    """Handle GET /api/feeds/feed_likes: list a post's likers with badge, connection status and IST like time."""
    try:
        rows = await FeedDatabase.get_feed_likes_list(
            feed_id=feed_id,
            contact_id=contact_id,
            userprofile_id=userprofile_id,
        )
    except Exception as e:
        logger.error(f"feed_likes_users: failed for feed_id={feed_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while fetching feed likes.")

    badge_map = await resolve_provider_badges(row.get("userprofileid") for row in rows)

    likers = []
    for row in rows:
        liked_on_str = None
        db_liked_on = row.get("liked_on")
        if db_liked_on and isinstance(db_liked_on, datetime):
            utc_time = db_liked_on.replace(tzinfo=pytz.utc) if db_liked_on.tzinfo is None else db_liked_on.astimezone(pytz.utc)
            ist_time = utc_time.astimezone(IST)
            liked_on_str = ist_time.strftime("%m/%d/%Y %I:%M %p")
        likers.append(
            FeedLikeUserItem(
                Etown_name=row.get("name"),
                Etown_contactid=row.get("contactid"),
                Etown_userprofileid=row.get("userprofileid"),
                Etown_profileimage=row.get("profileimage"),
                Etown_role=row.get("role"),
                Etown_providertype=premium_display(
                    row.get("providertype"),
                    (badge_map.get(row.get("userprofileid")) or {}).get("premium_label"),
                ),
                Etown_isconnected=row.get("is_connected"),
                Etown_invitation=row.get("invitation"),
                Etown_likedon=liked_on_str,
                Etown_deleted=row.get("liker_deleted"),
                Etown_deactivated=row.get("liker_deactivated"),
            )
        )
    return likers


@router.get("/feed_comments_list", response_model=list[FeedCommentItem])
async def feed_comments_list(
    feed_id: str = Query(..., description="The ID of the feed post to retrieve comments for."),
):
    """Handle GET /api/feeds/feed_comments_list: list a post's comments with commenter details and badges."""
    try:
        rows = await FeedDatabase.get_feed_comments(feed_id=feed_id)
    except Exception as e:
        logger.error(f"feed_comments_list: failed for feed_id={feed_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while fetching feed comments.")

    badge_map = await resolve_provider_badges(
        row.get("commentedbyuserprofileid") for row in rows
    )

    comments = [
        FeedCommentItem(
            Etown_feedcommentsid=row.get("feedcommentsid"),
            Etown_servicename=row.get("provider_businessname"),
            Etown_userprofileid=row.get("commentedbyuserprofileid"),
            Etown_name=row.get("firstname"),
            Etown_textcomment=row.get("textcomments"),
            Etown_role=row.get("userprofile_role"),
            Etown_providertype=premium_display(
                row.get("providertype"),
                (badge_map.get(row.get("commentedbyuserprofileid")) or {}).get("premium_label"),
            ),
            Etown_commentcreateddate=(
                lambda dt: (
                    lambda ist: f"{ist.month}/{ist.day}/{ist.year} {ist.strftime('%I:%M %p').lstrip('0')}"
                )(
                    (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
                    .astimezone(timezone(timedelta(hours=5, minutes=30)))
                )
            )(row["createdon"]) if row.get("createdon") else None,
            Etown_contactid=row.get("contactid") or row.get("commentedbycontactid"),
            Etown_imageurl=row.get("etown_imageurl"),
            Etown_deleted=row.get("commentedby_deleted"),
            Etown_deactivated=row.get("commentedby_deactivated"),
        )
        for row in rows
    ]
    return comments


async def _upload_media_or_none(
    *,
    container: str,
    contact_id: str,
    timestamp: str,
    raw_base64: Optional[str],
    suffix: str,
    content_type: str,
) -> Optional[str]:
    """Decode base64 media, upload it with its sniffed MIME type, and return the blob path, or None when empty."""
    content = to_bytes(raw_base64)
    if not content:
        return None
    detected_type = detect_mime_type(content)
    blob_name = f"{contact_id}/{timestamp}_{suffix}"
    return await upload_blob_bytes(container, blob_name, content, detected_type)


def _row_to_feed_item(row: dict, premium_label: Optional[str] = None) -> EtownFeedItem:
    """Map a get_feed_by_id row and the poster's premium label to an EtownFeedItem."""
    created_on_str = None
    db_created_on = row.get("createdon")
    if db_created_on and isinstance(db_created_on, datetime):
        utc_time = db_created_on.replace(tzinfo=pytz.utc) if db_created_on.tzinfo is None else db_created_on.astimezone(pytz.utc)
        ist_time = utc_time.astimezone(IST)
        created_on_str = ist_time.strftime("%m/%d/%Y %I:%M %p")

    if row.get("role") == 1:
        feed_lat = row.get("provider_latitude")
        feed_lon = row.get("provider_longitude")
    else:
        feed_lat = row.get("contact_latitude")
        feed_lon = row.get("contact_longitude")

    role_code = row.get("mycirclerole_code")
    role_name = CIRCLE_ROLE_MAP.get(role_code)
    my_circle_val = "false" if role_name is None else role_name
    my_circle_role_val = "" if role_name is None else role_name

    return EtownFeedItem(
        Etown_feedid=row.get("feedid"),
        Etown_feedfilelocationurl=row.get("feedfilelocationurl"),
        Etown_feedfilelocationurl2=row.get("feedfilelocation2"),
        Etown_feedfilelocationurl3=row.get("feedfilelocation3"),
        Etown_feedfilelocationurl4=row.get("feedfilelocation4"),
        Etown_feedfilelocationurl5=row.get("feedfilelocation5"),
        Etown_feedvideofilelocation=row.get("feedvideofilelocation"),
        Etown_thumbnailfileurl=row.get("thumbnailfileurl"),
        Etown_feedtext=row.get("feedtext"),
        Etown_servicename=row.get("servicename"),
        Etown_userprofileid=row.get("userprofileid"),
        Etown_contactid=row.get("contactid"),
        Etown_role=row.get("role"),
        Etown_profileimage=row.get("author_profileimage"),
        Etown_ets_serviceimageurl=row.get("ets_serviceimageurl"),
        Etown_commentscount=str(row.get("comments_count", 0)),
        Etown_feedlikecount=str(row.get("likes_count", 0)),
        Etown_feedlikecontactperson=row.get("has_liked"),
        Etown_likedPerson=row.get("feedlikecontactperson", ""),
        Etown_createdon=created_on_str,
        Etown_name=row.get("author_name"),
        Etown_address=row.get("address"),
        Etown_statecode=row.get("statecode"),
        Etown_city=row.get("city"),
        Etown_mycircle=my_circle_val,
        Etown_mycirclerole=my_circle_role_val,
        Etown_invitation=row.get("invitation") or "",
        Etown_businesscategory=row.get("businesscategory"),
        Etown_providertype=premium_display(row.get("providertype"), premium_label),
        Etown_is_liked_by_circle=row.get("is_liked_by_circle"),
        Etown_latitude=feed_lat,
        Etown_longitude=feed_lon,
        Etown_orientation=row.get("orientation"),
        Etown_is_trending=False,
    )


async def _share_feed_to_offer(
    body: CreateFeedRequest,
    *,
    feedfilelocationurl: Optional[str],
    thumbnailfileurl: Optional[str],
    service_image_url: Optional[str],
    feedfilelocation2: Optional[str],
    feedfilelocation3: Optional[str],
    feedfilelocation4: Optional[str],
    feedfilelocation5: Optional[str],
    feedvideofilelocation: Optional[str],
) -> None:
    """Create an offer from a new feed post via create_offer_full, reusing uploaded media and logging any failure."""
    businessname = body.servicename if body.role == 1 else body.firstname
    phonebook_contacts = (
        [{"phone": p.mobilenumber} for p in body.phonebook if p.mobilenumber]
        if body.phonebook
        else None
    )
    groupuniqueid = None
    if body.groups:
        groupuniqueid = next(
            (g.groupid for g in body.groups if g.groupid), None
        )

    try:
        await create_offer_full(
            contact_id=body.contact_id,
            userprofile_id=body.userprofile_id,
            businessname=businessname or "",
            description=body.feed_text or "",
            title=body.servicename,
            thumbnail_file=thumbnailfileurl,
            businessprofileimagefile=service_image_url,
            offerfile1=feedfilelocationurl,
            offerfile2=feedfilelocation2,
            offerfile3=feedfilelocation3,
            offerfile4=feedfilelocation4,
            offerfile5=feedfilelocation5,
            offervideofile=feedvideofilelocation,
            tags=body.tags,
            oauth_token=body.Oauth,
            phonebook_contacts=phonebook_contacts,
            groupuniqueid=groupuniqueid,
        )
        logger.info("create_feed: share-to-offer succeeded for contact_id=%s", body.contact_id)
    except OfferRateLimitError as e:
        logger.info("create_feed: share-to-offer skipped (rate limit) for contact_id=%s: %s", body.contact_id, e)
    except PlanLimitError as e:
        logger.info("create_feed: share-to-offer skipped (plan limit) for contact_id=%s: %s", body.contact_id, e)
    except Exception as e:
        logger.error("create_feed: share-to-offer failed for contact_id=%s: %s", body.contact_id, e, exc_info=True)


@router.post("/create", response_model=EtownFeedItem, status_code=status.HTTP_201_CREATED)
async def create_feed_endpoint(
    body: CreateFeedRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_token),
):
    """Handle POST /api/feeds/create: upload media, insert the post, and queue mirror, notification and share tasks.

    Raises:
        HTTPException: 400 bad media or profile, 402 over the plan limit, 422 bad audience, 500 on failure.
    """
    container = normalize_container_name(settings.AZURE_BLOB_CONTAINER)
    if not container:
        raise HTTPException(
            status_code=500,
            detail="Azure Blob container is not configured.",
        )

    try:
        body.userprofile_id = await _resolve_create_feed_profile_id(
            contact_id=body.contact_id,
            requested_profile_id=body.userprofile_id,
            role=body.role,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("create_feed: profile resolution failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Could not resolve the posting profile.")

    timestamp = current_timestamp_prefix()
    logger.info(
        "create_feed: contact_id=%s userprofile_id=%s role=%s servicename=%s tags=%s audience=%s",
        body.contact_id, body.userprofile_id, body.role, body.servicename, body.tags,
        body.audience,
    )

    try:
        audiencename, audience_code = resolve_audience(body.audience)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        try:
            await enforce_feed_plan_limits(
                body.userprofile_id,
                photo_count=sum(
                    1 for raw in (
                        None if body.videofile else body.feed_file,
                        body.feedfile2, body.feedfile3, body.feedfile4, body.feedfile5,
                        None if body.videofile else body.thumbnail_file,
                    ) if to_bytes(raw)
                ),
                audience=audience_code,
            )
        except PlanLimitError:
            raise
        except Exception as e:
            logger.warning("create_feed: pre-upload plan check skipped: %s", e)

        try:
            (
                feedfilelocationurl,
                thumbnailfileurl,
                service_image_url,
                feedfilelocation2,
                feedfilelocation3,
                feedfilelocation4,
                feedfilelocation5,
                feedvideofilelocation,
            ) = await asyncio.gather(
                _upload_media_or_none(container=container, contact_id=body.contact_id, timestamp=timestamp,
                                      raw_base64=None if body.videofile else body.feed_file, suffix="FeedImg.png", content_type="application/octet-stream"),
                _upload_media_or_none(container=container, contact_id=body.contact_id, timestamp=timestamp,
                                      raw_base64=body.thumbnail_file, suffix="FeedThumbnailImg.png", content_type="application/octet-stream"),
                _upload_media_or_none(container=container, contact_id=body.contact_id, timestamp=timestamp,
                                      raw_base64=body.service_imagefile, suffix="ServiceImg.png", content_type="application/octet-stream"),
                _upload_media_or_none(container=container, contact_id=body.contact_id, timestamp=timestamp,
                                      raw_base64=body.feedfile2, suffix="FeedImg2.png", content_type="application/octet-stream"),
                _upload_media_or_none(container=container, contact_id=body.contact_id, timestamp=timestamp,
                                      raw_base64=body.feedfile3, suffix="FeedImg3.png", content_type="application/octet-stream"),
                _upload_media_or_none(container=container, contact_id=body.contact_id, timestamp=timestamp,
                                      raw_base64=body.feedfile4, suffix="FeedImg4.png", content_type="application/octet-stream"),
                _upload_media_or_none(container=container, contact_id=body.contact_id, timestamp=timestamp,
                                      raw_base64=body.feedfile5, suffix="FeedImg5.png", content_type="application/octet-stream"),
                _upload_media_or_none(container=container, contact_id=body.contact_id, timestamp=timestamp,
                                      raw_base64=body.videofile, suffix="FeedVideo.mp4", content_type="application/octet-stream"),
            )
            uploaded_count = sum(1 for u in (feedfilelocationurl, thumbnailfileurl, service_image_url, feedfilelocation2, feedfilelocation3, feedfilelocation4, feedfilelocation5, feedvideofilelocation) if u)
            logger.info("create_feed: media upload complete — %d file(s) uploaded", uploaded_count)
        except ValueError as e:
            logger.warning(f"create_feed: invalid base64 media payload: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid base64 media payload: {e}")
        except Exception as e:
            logger.error(f"create_feed: media upload failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to upload media.")

        try:
            feed_id = await create_feed_post(
                contact_id=body.contact_id,
                userprofile_id=body.userprofile_id,
                role=body.role,
                feed_text=body.feed_text,
                servicename=body.servicename,
                audience=audiencename,
                feedfilelocationurl=feedfilelocationurl,
                feedfilelocation2=feedfilelocation2,
                feedfilelocation3=feedfilelocation3,
                feedfilelocation4=feedfilelocation4,
                feedfilelocation5=feedfilelocation5,
                feedvideofilelocation=feedvideofilelocation,
                thumbnailfileurl=thumbnailfileurl,
                serviceimageurl=service_image_url,
                orientation=body.orientation,
                sharetooffer=body.sharetooffer,
                groups=[g.model_dump() for g in body.groups] if body.groups else None,
                phonebook=[p.model_dump() for p in body.phonebook] if body.phonebook else None,
            )
            logger.info("create_feed: feed inserted — feed_id=%s", feed_id)
        except PlanLimitError:
            raise
        except Exception as e:
            logger.error(f"create_feed: insert failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to create feed post.")
    except PlanLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(e),
        )

    try:
        row = await FeedDatabase.get_feed_by_id(
            feed_id=feed_id,
            current_contact_id=body.contact_id,
            current_user_profile_id=body.userprofile_id,
        )
    except Exception as e:
        logger.error(f"create_feed: post-insert fetch failed for {feed_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Feed created but could not be read back.")

    if row is None:
        logger.error(f"create_feed: inserted feed {feed_id} not found on read-back")
        raise HTTPException(status_code=500, detail="Feed created but could not be read back.")

    poster_display_name = body.servicename if body.role == 1 else body.firstname
    background_tasks.add_task(insert_feed_to_dataverse, row)

    poster_boosted = body.role == 1 and await is_provider_boosted(body.userprofile_id)
    circle_only = row.get("circle_only") is True

    is_text_post = not (feedfilelocationurl or feedvideofilelocation)

    if poster_boosted and not circle_only:
        logger.info("create_feed: queuing boosted broadcast for feed_id=%s contact_id=%s", feed_id, body.contact_id)
        background_tasks.add_task(
            send_boosted_feed_broadcast,
            oauth_token=body.Oauth,
            poster_contact_id=body.contact_id,
            poster_userprofile_id=body.userprofile_id,
            body=f"{poster_display_name} shared a new post",
            feed_id=feed_id,
            image=thumbnailfileurl or feedfilelocationurl,
            is_text_post=is_text_post,
        )
    else:
        logger.info("create_feed: queuing circle notification for feed_id=%s role=%s", feed_id, body.role)
        background_tasks.add_task(
            notify_circle_on_new_feed,
            oauth_token=body.Oauth,
            poster_contact_id=body.contact_id,
            poster_userprofile_id=body.userprofile_id,
            poster_firstname=poster_display_name,
            feed_id=feed_id,
            image=thumbnailfileurl or feedfilelocationurl,
            is_text_post=is_text_post,
        )

    if body.sharetooffer:
        logger.info("create_feed: queuing share-to-offer for feed_id=%s", feed_id)
        background_tasks.add_task(
            _share_feed_to_offer,
            body,
            feedfilelocationurl=feedfilelocationurl,
            thumbnailfileurl=thumbnailfileurl,
            service_image_url=service_image_url,
            feedfilelocation2=feedfilelocation2,
            feedfilelocation3=feedfilelocation3,
            feedfilelocation4=feedfilelocation4,
            feedfilelocation5=feedfilelocation5,
            feedvideofilelocation=feedvideofilelocation,
        )

    logger.info("create_feed: completed successfully — feed_id=%s", feed_id)

    social_media_lower = [s.lower() for s in (body.social_media or [])]
    logger.info("create_feed: social_media=%s | feed_id=%s", body.social_media, feed_id)
    if "instagram" in social_media_lower:
        extra_imgs = [u for u in (feedfilelocation2, feedfilelocation3, feedfilelocation4, feedfilelocation5) if u]
        background_tasks.add_task(
            auto_publish_feed_to_instagram,
            contact_id=body.contact_id,
            userprofile_id=body.userprofile_id,
            feed_text=body.feed_text,
            image_url=feedfilelocationurl,
            extra_image_urls=extra_imgs,
            video_url=feedvideofilelocation,
            feed_id=feed_id,
        )
    else:
        logger.info("create_feed: Instagram publish skipped — not in social_media list | feed_id=%s", feed_id)

    if "facebook" in social_media_lower:
        extra_imgs_fb = [u for u in (feedfilelocation2, feedfilelocation3, feedfilelocation4, feedfilelocation5) if u]
        background_tasks.add_task(
            auto_publish_feed_to_facebook,
            contact_id=body.contact_id,
            userprofile_id=body.userprofile_id,
            feed_text=body.feed_text,
            image_url=feedfilelocationurl,
            extra_image_urls=extra_imgs_fb,
            video_url=feedvideofilelocation,
            feed_id=feed_id,
        )
    else:
        logger.info("create_feed: Facebook publish skipped — not in social_media list | feed_id=%s", feed_id)

    poster_userprofile = row.get("userprofileid") or body.userprofile_id
    premium_label = (
        (await resolve_provider_badges([poster_userprofile])).get(poster_userprofile) or {}
    ).get("premium_label")
    return _row_to_feed_item(row, premium_label)


class FeedCommentRequest(BaseModel):
    feed_id: Optional[str] = None
    commented_by: Optional[str] = None
    comments: Optional[str] = None
    feed_comment_row_id: Optional[str] = None
    commentedby_userprofile: Optional[str] = None
    action_mode: Optional[str] = None


@router.post("/feedcomments", description="ADD And Update Feed Comments API")
async def feedcomments(body: FeedCommentRequest, background_tasks: BackgroundTasks):
    """Handle POST /api/feeds/feedcomments: add, update or delete a feed comment and mirror it to Dataverse."""
    if (body.action_mode or "").lower() == "delete":
        try:
            await FeedDatabase.delete_feed_comment(feed_comment_row_id=body.feed_comment_row_id)
        except Exception as e:
            logger.error(f"feedcomments: delete failed for feed_comment_row_id={body.feed_comment_row_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="An error occurred while deleting the comment.")
        background_tasks.add_task(delete_feed_comment_in_dataverse, body.feed_comment_row_id)
        return {"message": "Feed Comments deleted successfully"}
    else:
        if not body.feed_comment_row_id:
            try:
                new_comment_id = await FeedDatabase.insert_feed_comment(
                    feed_id=body.feed_id,
                    commented_by=body.commented_by,
                    commentedby_userprofile=body.commentedby_userprofile,
                    comments=body.comments,
                )
                comment_record = await FeedDatabase.get_feed_comment_by_id(feedcommentsid=new_comment_id)
            except Exception as e:
                logger.error(f"feedcomments: insert failed for feed_id={body.feed_id}: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail="An error occurred while adding the comment.")
            background_tasks.add_task(insert_feed_comment_to_dataverse, comment_record)
            if settings.NOTIFICATIONS_GOVERNANCE_ENABLED:
                from app.etown.notifications.triggers import notify_feed_comment
                background_tasks.add_task(
                    notify_feed_comment, body.feed_id, body.commentedby_userprofile
                )
            return {"message": "Feed Comments added successfully", "data": comment_record}
        else:
            try:
                updated_record = await FeedDatabase.update_feed_comment(
                    feed_comment_row_id=body.feed_comment_row_id,
                    commented_by=body.commented_by,
                    feed_id=body.feed_id,
                    comments=body.comments,
                )
            except Exception as e:
                logger.error(f"feedcomments: update failed for feed_comment_row_id={body.feed_comment_row_id}: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail="An error occurred while updating the comment.")
            background_tasks.add_task(
                update_feed_comment_in_dataverse,
                body.feed_comment_row_id,
                body.commented_by,
                body.feed_id,
                body.comments,
                body.commentedby_userprofile,
            )
            return {"message": "Feed Comments updated successfully", "data": updated_record}


class FeedLikeRequest(BaseModel):
    feed_id: str
    liked_by: str
    like_mode: int


@router.post("/likes", description="Etown - Mobile API - Feed Likes - Post Like API")
async def feed_likes(body: FeedLikeRequest, background_tasks: BackgroundTasks):
    """Handle POST /api/feeds/likes: upsert a like or dislike, mirror it, and notify the author on a like."""
    try:
        record = await FeedDatabase.upsert_feed_like(
            feed_id=body.feed_id,
            liked_by=body.liked_by,
            like_mode=body.like_mode,
        )
    except Exception as e:
        logger.error(
            f"feed_likes: upsert failed for feed_id={body.feed_id} liked_by={body.liked_by}: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="An error occurred while processing the like.")

    if record:
        background_tasks.add_task(insert_feed_like_to_dataverse, record)
        if body.like_mode == 1 and settings.NOTIFICATIONS_GOVERNANCE_ENABLED:
            from app.etown.notifications.triggers import notify_feed_like
            background_tasks.add_task(notify_feed_like, body.feed_id, body.liked_by)

    return {"message": "Feed Like updated successfully", "data": record}