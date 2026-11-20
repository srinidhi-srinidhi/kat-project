"""MyTown Endpoints - HTTP routes for town discovery, collections, town images and community needs.

HTTP layer for /api/mytown: checks the shared bearer token and delegates town suggestions and search, verified providers, collections, favourite and coverage towns, area promotion, town images and community needs to mytown_utils, scheduling Dataverse mirrors and notifications as background tasks.

Features:
    - Town suggestions: Geographic location and provider recommendations
    - Area verification: Validated geographic boundaries and availability
    - City search: Intelligent city name parsing and discovery
    - Provider collections: Organized service provider groupings, with full CRUD on collections and items
    - Town presence: Availability checking for cities and areas
    - Coordinate management: GPS coordinate to town resolution
    - Favourite towns: Favourite (create) or unfavourite (update) a town for a user
    - Collection sharing: Push a shared-collection notification to a list of recipients
    - Town image generation: LLM-enhanced prompt → AI image → blob storage for a listing-card image
    - Town image upload and verification: Store member photos; approve or reject them
    - Area promotion: Give an area its own town profile and move its providers across
    - Community needs: post a requirement against a town and let every provider covering it quote
    - Community likes and comments: like, dislike and comment on any post, with counts on the feed
    - Community views: every onclick and one public view per viewer per IST day, with view_count on the feed
    - Community review: updates and events reach the town feed only once approved in Dataverse
    - Community search: GET /community/search, a typo-tolerant match on post text and poster name
    - Geographic filtering: Location-based result customization
    - Multilingual support: Tamil, Hindi, and English city names
    - State mapping: Comprehensive Indian state identification

Endpoints:
    GET /mytown_suggestions: Get location-based provider suggestions
    GET /verified_providers: Fast retrieval of verified providers by city or coordinates
    GET /get_verified_areas: List all verified geographic areas
    GET /search: Search cities and areas with intelligent parsing
    GET /townsearch: Paginated partial and typo-tolerant town-name search with town suggestion records
    GET /collections: Retrieve organized provider collections
    GET /check_town_presence: Verify town availability and status
    GET /town_management: Resolve coordinates to town information
    GET /view_count: Retrieve town view counts and viewer names for a date
    POST /CrudCollections: Create, update, or delete a collection
    POST /CrudCollectionItems: Add, update, or soft-delete providers within a collection
    POST /AddUpdateFavouriteTown: Favourite (create) or unfavourite (update) a town
    POST /CoverageTown: Add or remove a provider's coverage towns in one call (402 when max_towns is used up)
    POST /promote_area: Promote an area to its own town profile and move its providers
    POST /share_collection: Push a shared-collection notification to recipients
    POST /verify_townimage: Approve or reject a member-uploaded town image
    POST /generate_town_image: Generate a town listing-card image, grounded on a verified Google photo
    POST /upload_townimage: Store one or more member-uploaded town photos
    POST /request_townimage: Record a pending etown_townreview request, mirror it to Dataverse and alert the reviewers
    POST /pin_category: Pin or unpin a category for a user in a town and area, or at a popular-providers location
    POST /community: Post a need, update or event against one or more towns, one record per town, or save drafts
    POST /{needid}/publish: Turn one of the caller's own drafts (needid, updateid or eventid) into a post
    GET /community: List a town's posted needs, updates and events, or the caller's own posts or drafts
    GET /community/search: Fuzzy-search a town's posted needs, updates and events by text or poster name
    GET /community/post: Read one need, update or event by post_type and postid in the feed shape
    POST /community/action: Edit or delete one of the caller's own needs, updates or events
    POST /community/respond: Respond to a need, messaging its poster the need's title and the text
    GET /community/responses: List each person's latest response to a need with name and image
    POST /community/like: Like or dislike a posted need, update or event
    GET /community/likes: List a post's likers with name and image
    POST /community/view: Count views of needs, updates and events; public ones once per post per IST day
    POST /community/comment: Add, edit or delete a comment on a post
    GET /community/comments: List a post's comments with commenter name and image
    POST /community/review: Receive an update or event's review decision; an approved one pushes its poster and town

Classes:
    CrudCollectionsRequest(): Body for POST /CrudCollections — collection create/update/delete fields
    CollectionItemInput(): One provider entry within a CrudCollectionItems request (providerid, isFavourite)
    CrudCollectionItemsRequest(): Body for POST /CrudCollectionItems — actionmode + list of CollectionItemInput
    FavouriteTownRequest(): Body for POST /AddUpdateFavouriteTown — actionmode-driven favourite upsert
    CoverageTownDetail(): One town within a CoverageTown request (name, townprofileid)
    ProviderCoverageTownRequest(): Body for POST /CoverageTown — actionmode + list of CoverageTownDetail
    PromoteAreaRequest(): Body for POST /promote_area — townprofileid, areaname, optional statecode
    VerifyTownImageRequest(): Body for POST /verify_townimage — townimageid and approve/reject actionmode
    VerifyTownImageResponse(): Response for POST /verify_townimage — new and previous verification state
    GenerateTownImageRequest(): Body for POST /generate_town_image — town name/description + location grounding
    GenerateTownImageResponse(): Response for POST /generate_town_image — image URL, blob path, and prompt used

Functions:
    validate_coordinates(): Validate latitude and longitude coordinates
    mytown_suggestions(): Process geographic suggestion requests
    get_verified_providers(): Retrieve verified providers by city or coordinates
    get_verified_areas(): Return all verified geographic areas
    mytown_search(): Handle intelligent city, area, and provider search
    townsearch(): Return a page of towns matching a full, partial or typo-similar town name
    mytown_collections(): Fetch organized provider collections, a single collection, or provider details
    check_town_presence(): Verify town existence and availability
    town_management(): Convert coordinates to town details
    get_town_view_count_endpoint(): Retrieve town view counts and viewer names for a date
    crud_collections(): Create, update, or delete a collection
    crud_collection_items(): Add, update, or soft-delete providers within a collection
    add_update_favourite_town(): Favourite or unfavourite a town
    provider_coverage_town(): Add or remove a provider's coverage towns
    promote_area_to_town(): Promote an area to its own town and move its providers
    share_collection_endpoint(): Push a shared-collection notification to recipients
    verify_townimage(): Approve or reject a member-uploaded town image
    generate_town_image(): Generate a town listing-card image from a name and description
    upload_townimage(): Store one or more member-uploaded town photos
    request_townimage(): Record a pending etown_townreview request, mirror it to Dataverse and alert the reviewers
    pin_category(): Pin or unpin a category for a user in a town and area, or at a popular-providers location
    post_need(): Validate, upload files, store the need, update or event, and fan a need out to providers
    publish_draft_need(): Publish a draft and its town copies, fanning a need out to providers
    list_needs(): Page a town's posted needs, updates and events, or the caller's own posts or drafts
    search_community(): Fuzzy-search a town's posted needs, updates and events, best match first
    get_community_post_detail(): Read one need, update or event by id, 404 when missing or hidden from the viewer
    community_post_action(): Edit the fields and towns of, or soft-delete, the caller's own post and mirror it
    respond_to_community_need(): Store a need response and deliver it to the poster in Messages
    list_community_need_responses(): Page each responder's latest response to a need
    like_community_post(): Like or dislike a post and return the new like count
    list_community_post_likes(): Page a post's likers
    view_community_posts(): Count each post's view, public ones once per viewer per IST day, and return the counts
    comment_community_post(): Add, edit or delete a comment and return the new comment count
    list_community_post_comments(): Page a post's comments
    review_community_post(): Acknowledge a review decision and push the poster and town of an approved update or event

Dependencies:
    - fastapi: API framework and query parameter handling
    - pydantic: Request and response body models
    - app.core.security: Shared bearer token verification
    - app.core.logging: Centralized logging infrastructure
    - app.etown.mytown.mytown_utils: Geographic, collection, town-image and community-need utilities
    - app.etown.mytown.mytown_syncing: Town-image, town-review and community post, like and comment Dataverse mirrors
    - app.etown.notifications.triggers: notify_collection_shared(), notify_need_posted(), notify_community_like(), notify_community_comment(), notify_community_post_submitted(), notify_town_image_requested(), notify_need_response(), notify_post_approved() and notify_post_live_in_town()
    - app.core.config: NOTIFICATIONS_GOVERNANCE_ENABLED, gating the community like and comment pushes
    - app.utils.ist_clock: now_ist() stamping a user's community visit
    - app.etown.plans.plans_utils: PlanLimitError for coverage-town caps
    - app.schemas.api: Geographic API request and response models
    - app.services.api_activity: track_api_call activity tracking
    - app.api.v1.endpoints.services: location and geocoding helpers for check_town_presence

Updates:
    23-Feb-2026: Enhanced documentation with complete geographic feature set
    25-Feb-2026: Added coordinate validation to prevent invalid location queries
    02-Mar-2026: Added GET /verified_providers endpoint for verified provider retrieval
    13-Jul-2026: Added POST /share_collection and POST /CrudCollectionItems; isFavourite accepts numeric 0/1.
    14-Jul-2026: Added POST /AddUpdateFavouriteTown - actionmode favourite upsert or unfavourite.
    20-Jul-2026: Added POST /generate_town_image with location grounding and a per-town seed.
    18-Aug-2026: Added POST /CoverageTown to add or drop a provider's coverage towns.
    25-Aug-2026: CoverageTown takes a town_details list for multi-town add/remove
    26-Aug-2026: Silent exception handlers now logged with tracebacks.
    31-Aug-2026: Smaller town image defaults; town cards stored as JPEG.
    01-Sep-2026: generate_town_image grounds on a vision-verified Google photo and stores the card; added POST /verify_townimage.
    02-Sep-2026: Verified Google photo stored as the card; /verified_providers hides the caller's own listing.
    03-Sep-2026: Added POST /promote_area; plan changes prune coverage towns; provider payloads carry Etown_background_imageUrl.
    08-Sep-2026: /upload_townimage accepts an array of images.
    15-Sep-2026: Absorbed the needs router as /mytown/community and /mytown/{needid}/publish.
    05-Oct-2026: /community stores needs, updates and events in separate tables by post_type; adds location name, need-only budget and event-only schedule and details; the date field is needed_at, with time still accepted; added community like and comment APIs, which take post_type; updates and events show only once their review is approved; likes and comments push the post's owner; updates, events, likes and comments are mirrored to Dataverse in the background, like needs; feed adds all, need, event and update counts; posting or publishing an update or event WhatsApps and emails the support reviewers; needed_at is optional.
    06-Oct-2026: GET /community takes is_sameuser to list only one member's own needs and skips posts whose contact is deleted; added POST /community/action to edit or delete a post; needs carry need_status, open on create; added POST /community/respond, which notifies the poster by push and in-app, and GET /community/responses; added POST /community/review for an update or event's review decision, pushing poster, providers and nearby members on approval; added GET /community/search for fuzzy search; GET /community returns open and closed need counts for is_sameuser; GET /community/search takes is_sameuser to search one member's own needs; added GET /community/post to read one post by id.
    07-Oct-2026: GET /community takes is_provider to read only needs and store the provider's visit time in IST; community attachments are images or videos with optional thumbnails and no filename; community posts carry mycircle and invitationstatus; GET /community takes contactid to skip the provider's own needs.
    08-Oct-2026: GET /community returns the town's townimage, provider_count and view_count when townprofileid is given; added POST /request_townimage, which WhatsApps and emails the support reviewers and mirrors the request to ets_townreview.
    09-Oct-2026: POST /request_townimage takes filename and returns 409 when the same filename is already pending or is the town's image.
    12-Oct-2026: Added POST /community/view counting every onclick and one public view per post per user per IST day; community posts carry view_count; GET /mytown_suggestions takes type=dashboard|insidetown to omit the lists that view does not use and no longer returns town_images.
    14-Oct-2026: POST /community takes a town list instead of townname and townprofileid, storing one post per town.
    16-Oct-2026: GET /mytown_suggestions returns a categories list, omitted for type=dashboard, and takes category_id, one or many comma-separated ids, to return only those categories' providers; added POST /pin_category, which takes pin as the string "true" or "false"; for type=insidetown the city block carries the cityname's new need, update and event counts.
    20-Oct-2026: POST /community stores event_status on events; editing an event's schedule recomputes it; GET /community returns each event's event_status; GET /community stores the last visit per post type for any userprofileid on town feed reads; GET /community takes post_type=all; GET /community skips the visit for the guest profile; POST /community/view mirrors counted update and event views to ets_viewcount; POST /pin_category mirrors the pin to ets_categorypin; GET /mytown_suggestions takes language=en|ta for category display names.
    21-Oct-2026: POST /community notifies the support reviewers once for an update or event across all its towns; is_sameuser reads return one item per post with all its towns, other reads all towns on the caller's own posts; /community/action edits, moves and removes towns on every town copy; publish posts every town's draft copy; event_status follows start time and end time whenever each is set; GET /community, /community/search and /community/post correct a wrong event_status and store it in the background.
    26-Oct-2026: POST /pin_category takes action_mode; popular_provider pins at latitude and longitude and skips the Dataverse mirror; GET /mytown_suggestions type=dashboard returns compressed town images.
    27-Oct-2026: GET /community and /community/search with is_sameuser take post_type need, update, event or all, defaulting to need; community posts return update_status instead of review_status, and an unapproved event's event_status is pending or rejected.

Notes:
    Supports multilingual city names and intelligent parsing of search terms.
    Includes comprehensive Indian geography with state code mappings.
    Optimized for performance with caching and efficient database queries.
"""

import asyncio

from fastapi import APIRouter, BackgroundTasks, Query, Depends, HTTPException, Path, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from typing import Optional, List, Union, Dict, Any, Literal
from uuid import UUID

from app.core.config import settings
from app.core.security import verify_token
from app.core.logging import logger
from app.etown.mytown.mytown_utils import (
    ALLOWED_ATTACHMENT_TYPES,
    MAX_ATTACHMENTS,
    MAX_VIDEO_BYTES,
    NEED_STATUSES,
    POST_TYPES,
    NeedValidationError,
    create_posts,
    list_needs as list_needs_util,
    get_community_post,
    correct_event_statuses,
    store_event_statuses,
    record_community_visit,
    get_community_town_header,
    publish_post,
    edit_or_delete_post,
    respond_to_need,
    list_need_responses,
    set_community_like,
    list_community_likes,
    save_community_comment,
    list_community_comments,
    record_community_views,
    find_reviewed_post,
    upload_need_attachments,
    handle_suggestions,
    handle_get_all_verified_areas,
    handle_search,
    handle_townsearch,
    handle_collections,
    handle_check_town_presence,
    handle_town_management,
    handle_verified_providers,
    handle_town_view_count,
    handle_crud_collections,
    handle_crud_collectionitems,
    handle_favourite_town,
    handle_provider_coverage_towns,
    handle_promote_area_to_town,
    generate_town_card,
    update_town_profile_image,
    handle_verify_town_image,
    handle_request_town_image,
    handle_category_pin,
)
from app.etown.mytown.mytown_syncing import (
    sync_town_profile_image_to_dataverse,
    sync_town_image_verification_to_dataverse,
    sync_post_to_dataverse,
    sync_post_viewcounts_to_dataverse,
    sync_community_like_to_dataverse,
    sync_community_comment_to_dataverse,
    sync_town_review_to_dataverse,
    sync_category_pin_to_dataverse,
)
from app.etown.plans.plans_utils import PlanLimitError
from app.schemas.api import (
    MyTownResponse,
    TownPresenceResponse,
    TownManagementResponse,
    VerifiedAreasResponse,
    MyTownSearchResponse,
    TownSearchResponse,
    CollectionItem,
    VerifiedProvidersResponse,
    TownViewCountResponse,
    ShareCollectionRequest,
    ShareCollectionResponse,
    UploadTownImageRequest,
    UploadTownImageResponse,
    RequestTownImageRequest,
    RequestTownImageResponse,
    CategoryPinRequest,
    CategoryPinResponse,
    NeedListResponse,
    CommunityPostDetailResponse,
    PostNeedRequest,
    PostNeedResponse,
    PublishNeedRequest,
    PublishNeedResponse,
    CommunityPostActionRequest,
    CommunityPostActionResponse,
    NeedResponseRequest,
    NeedResponseResponse,
    NeedResponseListResponse,
    CommunityLikeRequest,
    CommunityLikeResponse,
    CommunityLikeListResponse,
    CommunityCommentRequest,
    CommunityCommentResponse,
    CommunityCommentListResponse,
    CommunityReviewRequest,
    CommunityReviewResponse,
    CommunityViewRequest,
    CommunityViewResponse,
)
from app.etown.notifications.triggers import (
    notify_collection_shared,
    notify_community_comment,
    notify_community_like,
    notify_community_post_submitted,
    notify_town_image_requested,
    notify_post_approved,
    notify_post_live_in_town,
    notify_need_posted,
    notify_need_response,
)
from app.services.api_activity import track_api_call
from app.utils.ist_clock import now_ist

router = APIRouter()


class CrudCollectionsRequest(BaseModel):
    """Request body for the CrudCollections endpoint. All fields are optional."""
    user_contactid: Optional[str] = None
    user_userprofileid: Optional[str] = None
    collection_type: Optional[str] = None
    collectionname: Optional[str] = None
    isMytown: Optional[str] = None
    actionmode: Optional[str] = None
    collectionid: Optional[str] = None


class CollectionItemInput(BaseModel):
    """A single provider entry within a CrudCollectionItems request.

    isFavourite accepts the numeric choice form (0/1) the client sends, matching
    PowerApps, as well as the text labels ('favourite'/'unfavourite').
    """
    providerid: str
    isFavourite: Optional[Union[str, int]] = None


class CrudCollectionItemsRequest(BaseModel):
    """Request body for the CrudCollectionItems endpoint."""
    user_contactid: Optional[str] = None
    user_userprofileid: Optional[str] = None
    actionmode: Optional[str] = None
    collectionid: Optional[str] = None
    collectionitems: List[CollectionItemInput] = []


class FavouriteTownRequest(BaseModel):
    """Request body for the AddUpdateFavouriteTown endpoint.

    Mirrors the PowerApps trigger inputs. isFavourite is accepted for
    compatibility but ignored — the flag is driven entirely by actionmode.
    """
    user_contactid: Optional[str] = None
    user_userprofileid: Optional[str] = None
    isFavourite: Optional[Union[str, int]] = None
    actionmode: Optional[str] = None
    favouritetownid: Optional[str] = None
    townname: Optional[str] = None


class CoverageTownDetail(BaseModel):
    """One town inside a CoverageTown request.

    A town picked from suggestions carries its townprofileid, which is used as-is; a
    free-typed town carries only a name, which is resolved (and created if new) on add.
    """
    name: Optional[str] = None
    townprofileid: Optional[str] = None


class ProviderCoverageTownRequest(BaseModel):
    """Request body for the CoverageTown endpoint.

    town_details holds every town the call acts on, so one request adds or drops several
    coverage towns. provider_id is still accepted as the id key.
    """
    model_config = ConfigDict(populate_by_name=True)

    providerid: str = Field(..., validation_alias=AliasChoices("providerid", "provider_id"))
    actionmode: str
    business_name: Optional[str] = None
    town_details: List[CoverageTownDetail] = []


class PromoteAreaRequest(BaseModel):
    """Request body for the promote_area endpoint.

    Names the area to promote and the town it currently sits in. statecode only
    disambiguates the geocode when the area name is ambiguous across states.
    """
    townprofileid: str
    areaname: str
    statecode: Optional[str] = None


class VerifyTownImageRequest(BaseModel):
    """Request body for the verify_townimage endpoint.

    actionmode follows the convention the other MyTown write endpoints use; it takes
    'approve' or 'reject' (the resulting state name is accepted too).
    """
    townimageid: str
    actionmode: str
    userprofileid: Optional[str] = None


class VerifyTownImageResponse(BaseModel):
    """Response for the verify_townimage endpoint."""
    success: bool
    townimageid: str
    townprofileid: Optional[str] = None
    verification: str
    previous_verification: Optional[str] = None


class GenerateTownImageRequest(BaseModel):
    """Request body for the generate_town_image endpoint.

    The town name, description, and any geographic grounding (city/district, state,
    country, coordinates) drive an LLM prompt-enhancement step before the image is
    generated. Defaults are 1280x848 -- the widest near-3:2 landscape card the model server accepts, since a side above its IMAGE_MAX_SIDE=1280 is rejected with 422. Passing the real location context anchors the image to the actual
    town — preventing generic, templated, or geographically wrong results.

    Passing townprofileid additionally stores the generated URL on that town profile and
    mirrors it to the CRM; leaving it unset keeps the endpoint read-only as before.
    use_google_reference=False forces the description-only prompt, skipping the Google
    photo lookup entirely.
    """
    town_name: str
    town_description: str
    city_name: Optional[str] = None
    state: Optional[str] = None
    country: str = "India"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    townprofileid: Optional[str] = None
    use_google_reference: bool = True
    width: int = 1280
    height: int = 848
    steps: Optional[int] = None
    seed: Optional[int] = None


class GenerateTownImageResponse(BaseModel):
    """Response for the generate_town_image endpoint.

    image_source records which branch produced the image: 'google_photo' when a real Google photo of the town passed the vision check and was stored as the card itself, or
    'description_only' when no usable photo was found and one was generated from the stored description. enhanced_prompt is null on the photo path, which composes no prompt.
    """
    success: bool
    image_url: str
    blob_path: Optional[str] = None
    town_name: str
    enhanced_prompt: Optional[str] = None
    width: int
    height: int
    seed: Optional[int] = None
    image_source: str = "description_only"
    reference_photo_url: Optional[str] = None
    reference_verdict: Optional[str] = None
    persisted: bool = False


def validate_coordinates(latitude: float, longitude: float) -> None:
    """Raise a 400 for out-of-range or placeholder coordinates and log a warning when they fall outside India."""
    if (latitude == 0.0 and longitude == 0.0) or not (-90.0 <= latitude <= 90.0) or not (-180.0 <= longitude <= 180.0):
        raise HTTPException(
            status_code=400, 
            detail="Invalid coordinates : Please provide valid GPS coordinates."
        )
    
    invalid_coords = [
        (90.0, 0.0),
        (-90.0, 0.0),
        (0.0, 180.0),
        (0.0, -180.0),
    ]
    
    for invalid_lat, invalid_lon in invalid_coords:
        if abs(latitude - invalid_lat) < 0.0001 and abs(longitude - invalid_lon) < 0.0001:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid coordinates: ({latitude}, {longitude}) appears to be a placeholder or invalid location."
            )
    
    if not (0.0 <= latitude <= 45.0 and 60.0 <= longitude <= 105.0):
        logger.warning(f"Coordinates ({latitude}, {longitude}) are outside expected region (near India)")


@router.get("/mytown_suggestions")
async def mytown_suggestions(
    latitude: float = Query(..., description="User's current latitude."),
    longitude: float = Query(..., description="User's current longitude."),
    verified: Optional[bool] = Query(False, description="If true, return only verified providers."),
    areaname: Optional[str] = Query(None, description="User's current area name."),
    cityname: Optional[str] = Query(None, description="User's current city name."),
    contact_id: Optional[str] = Query(None, description="Current user's contact ID."),
    userprofile_id: Optional[str] = Query(None, description="Current user's profile ID."),
    token: str = Depends(verify_token),
    page: int = Query(1, ge=1, description="Page number for paginated providers, areas, and available towns"),
    limit: int = Query(20, ge=1, description="Number of providers, areas, and available towns per page"),
    suggestion_type: Optional[Literal["dashboard", "insidetown"]] = Query(
        None,
        alias="type",
        description="dashboard: omits nearby_areas, verified_providers and categories, and town images are the compressed WebP copy when one is current; insidetown: omits available_cities, favourite_cities, favourite_collection and saved_providers.",
    ),
    category_id: Optional[List[str]] = Query(None, description="etown_category ids, comma-separated or repeated; when given, the response holds only verified_providers in any of those categories, whatever the type."),
    language: Literal["en", "ta"] = Query("en", description="en or ta; ta returns each category's Tamil display name, falling back to English when none is stored."),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Return city info, paginated available towns, verified areas and nearby providers, with timing data."""
    import time as _time
    t0 = _time.time()

    track_api_call(
        background_tasks, "/api/mytown/mytown_suggestions", contact_id, userprofile_id
    )

    validate_coordinates(latitude, longitude)

    category_ids: List[str] = []
    for raw in category_id or []:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                parsed = str(UUID(part))
            except ValueError:
                raise HTTPException(status_code=422, detail=f"Invalid category_id: {part}")
            if parsed not in category_ids:
                category_ids.append(parsed)

    try:
        from fastapi.responses import JSONResponse
        from fastapi.encoders import jsonable_encoder

        result = await handle_suggestions(latitude, longitude, areaname, cityname, contact_id, userprofile_id, verified, page, limit, suggestion_type, category_ids or None, language)

        data = jsonable_encoder(result)
        
        if "available_cities" in data and isinstance(data["available_cities"], list):
            for city in data["available_cities"]:
                if isinstance(city, dict) and "favouritetownid" in city:
                    del city["favouritetownid"]

        for key in ("available_cities", "favourite_cities"):
            for city in data.get(key) or []:
                if isinstance(city, dict):
                    city.pop("town_images", None)

        if category_ids:
            data = {"verified_providers": data.get("verified_providers") or []}
            omitted_keys = ()
        elif suggestion_type == "dashboard":
            omitted_keys = ("nearby_areas", "verified_providers", "categories")
        elif suggestion_type == "insidetown":
            omitted_keys = ("available_cities", "favourite_cities", "favourite_collection", "saved_providers")
        else:
            omitted_keys = ()
        for key in omitted_keys:
            data.pop(key, None)

        data["performance"] = {
            "response_time_seconds": round(_time.time() - t0, 3),
            "endpoint_name": "mytown_mytown_suggestions",
            "cache_hit": False,
        }

        return JSONResponse(content=data, headers={"X-Performance-Injected": "1"})

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in /mytown_suggestions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@router.get("/verified_providers", response_model=VerifiedProvidersResponse)
async def get_verified_providers(
    cityname: Optional[str] = Query(None, description="City name to filter verified providers."),
    latitude: Optional[float] = Query(None, description="User's current latitude for distance sorting."),
    longitude: Optional[float] = Query(None, description="User's current longitude for distance sorting."),
    page: int = Query(0, description="Page number for pagination", ge=0),
    limit: int = Query(20, description="Number of items per page", ge=1, le=100),
    contact_id: Optional[str] = Query(None, description="Current user's contact ID; their own providers are excluded."),
    userprofile_id: Optional[str] = Query(None, description="Current user's profile ID; their own providers are excluded."),
    token: str = Depends(verify_token)
):
    """Handle GET /api/mytown/verified_providers: page verified providers by city or coordinates, excluding the caller's own."""
    if latitude is not None and longitude is not None:
        validate_coordinates(latitude, longitude)
    elif (latitude is None) != (longitude is None):
        raise HTTPException(
            status_code=400, 
            detail="Both latitude and longitude must be provided together, or neither."
        )
    
    if not cityname and latitude is None:
        raise HTTPException(
            status_code=400,
            detail="Either cityname or coordinates (latitude + longitude) must be provided."
        )
    
    try:
        return await handle_verified_providers(
            latitude=latitude,
            longitude=longitude,
            cityname=cityname,
            page=page,
            limit=limit,
            contact_id=contact_id,
            userprofile_id=userprofile_id
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in /verified_providers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.get("/get_verified_areas", response_model=VerifiedAreasResponse)
async def get_verified_areas(
    latitude: Optional[float] = Query(None, description="User's current latitude."),
    longitude: Optional[float] = Query(None, description="User's current longitude."),
    contact_id: Optional[str] = Query(None, description="Current user's contact ID."),
    userprofile_id: Optional[str] = Query(None, description="Current user's profile ID."),
    token: str = Depends(verify_token),
    page: int = Query(1, description="Page number for pagination"),
    limit: int = Query(200, description="Number of items per page")
):
    """Handle GET /api/mytown/get_verified_areas: page unique verified provider areas, sorted by distance when coordinates are given."""
    try:
        return await handle_get_all_verified_areas(latitude, longitude, contact_id, userprofile_id, page, limit)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in /get_verified_areas: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.get("/search", response_model=MyTownSearchResponse)
async def mytown_search(
    search_term: str = Query(..., description="The term to search for providers, areas, and cities."),
    city: Optional[str] = Query(None, description="Filter results to a specific city. Search within this city only."),
    latitude: Optional[float] = Query(None, description="User's latitude for sorting providers by distance"),
    longitude: Optional[float] = Query(None, description="User's longitude for sorting providers by distance"),
    page: int = Query(1, description="Page number for pagination", ge=1),
    limit: int = Query(20, description="Number of items per category", ge=1, le=50),
    token: str = Depends(verify_token)
):
    """Handle GET /api/mytown/search: search providers, areas and cities for a term, optionally within one city."""
    try:
        return await handle_search(search_term, latitude, longitude, page, limit, city)
    except Exception as e:
        logger.error(f"Error in /mytownsearch: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.get(
    "/townsearch",
    response_model=TownSearchResponse,
    response_model_exclude={"towns": {"__all__": {"favouritetownid"}}},
)
async def townsearch(
    search_term: str = Query(..., min_length=1, description="Town name or partial town name to search for."),
    latitude: Optional[float] = Query(None, description="Optional latitude for nearest-first sorting."),
    longitude: Optional[float] = Query(None, description="Optional longitude for nearest-first sorting."),
    contact_id: Optional[str] = Query(None, description="Current user's contact ID."),
    userprofile_id: Optional[str] = Query(None, description="Current user's profile ID."),
    page: int = Query(1, ge=1, description="Page number, starting at 1."),
    limit: int = Query(20, ge=1, le=100, description="Number of towns per page (maximum 100)."),
    token: str = Depends(verify_token),
):
    """Handle GET /api/mytown/townsearch: return a page of matching town records."""
    if (latitude is None) != (longitude is None):
        raise HTTPException(
            status_code=400,
            detail="Both latitude and longitude must be provided together, or neither.",
        )
    if latitude is not None and longitude is not None:
        validate_coordinates(latitude, longitude)

    try:
        return await handle_townsearch(
            search_term=search_term,
            latitude=latitude,
            longitude=longitude,
            contact_id=contact_id,
            userprofile_id=userprofile_id,
            page=page,
            limit=limit,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /townsearch: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")

@router.get("/collections", response_model=Any)
async def mytown_collections(
    contact_id: Optional[str] = Query(None, description="The contact ID of the user."),
    user_profile_id: Optional[str] = Query(None, description="The user profile ID of the user."),
    collection_id: Optional[str] = Query(None, description="Optional: ID of a specific collection to fetch."),
    collection_unique_id: Optional[str] = Query(None, description="Optional: Unique ID of a specific collection to fetch."),
    provider_id: Optional[str] = Query(None, description="Optional: ID of a specific provider to fetch details for."),
    page: int = Query(1, description="Page number for pagination", ge=1),
    limit: int = Query(20, description="Number of collections/items to return", ge=1, le=50),
    token: str = Depends(verify_token),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Handle GET /api/mytown/collections: return one provider's details, one collection, or the user's collection list."""
    track_api_call(background_tasks, "/api/mytown/collections", contact_id, user_profile_id)
    try:
        result = await handle_collections(
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            collection_id=collection_id,
            provider_id=provider_id,
            collection_unique_id=collection_unique_id,
            page=page,
            limit=limit
        )
        
        if provider_id is None:
            success = isinstance(result, list) and len(result) > 0
            return {
                "data": result,
                "success": success
            }
        else:
            return result
            
    except Exception as e:
        logger.error(f"Error in /mytowncollections: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.get("/check_town_presence", response_model=TownPresenceResponse)
async def check_town_presence(
    cityname: str = Query(..., description="The name of the city to check."),
    townname: str = Query(..., description="The name of the town to check."),
    statecode: str = Query(..., description="The state code associated with the city."),
    token: str = Depends(verify_token)
):
    """Handle GET /api/mytown/check_town_presence: geocode the city and report whether the town exists, with its profile."""
    try:
        from app.api.v1.endpoints.services import get_location_details_logic
        parsed_components = None
        latitude = longitude = None
        try:
            from app.api.v1.endpoints.services import get_geocoding_logic
            geocode_result = await get_geocoding_logic(f"{cityname}, {statecode}")
            latitude = geocode_result.latitude
            longitude = geocode_result.longitude
            location_details = await get_location_details_logic(latitude, longitude)
            parsed_components = location_details.parsed_components if hasattr(location_details, 'parsed_components') else None
        except Exception as geo_ex:
            logger.warning(f"Could not get parsed_components for check_town_presence: {geo_ex}")
            parsed_components = None

        return await handle_check_town_presence(
            townname, cityname, statecode, parsed_components, latitude, longitude
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in /check_town_presence: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.get("/town_management", response_model=TownManagementResponse)
async def town_management(
    latitude: float = Query(..., description="Latitude to check."),
    longitude: float = Query(..., description="Longitude to check."),
    token: str = Depends(verify_token)
):
    """Handle GET /api/mytown/town_management: resolve area, city, town and state code for a coordinate pair."""
    try:
        return await handle_town_management(latitude, longitude)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in /town_management: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.get("/view_count", response_model=TownViewCountResponse)
async def get_town_view_count_endpoint(
    target_date: str = Query(..., description="The date to fetch view counts for (e.g., '2026-06-18')"),
    page: int = Query(1, description="Page number for pagination", ge=1),
    limit: int = Query(20, description="Number of items per page", ge=1, le=100),
    token: str = Depends(verify_token)
):
    """Handle GET /api/mytown/view_count: page town view records with town and viewer names for a YYYY-MM-DD date."""
    try:
        from datetime import datetime
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")
            
        return await handle_town_view_count(target_date, page, limit)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in /view_count: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.post("/CrudCollections", response_model=Any)
async def crud_collections(
    payload: CrudCollectionsRequest,
    token: str = Depends(verify_token)
):
    """Handle POST /api/mytown/CrudCollections: create, update or delete a user's collection by actionmode."""
    try:
        return await handle_crud_collections(
            user_contactid=payload.user_contactid,
            user_userprofileid=payload.user_userprofileid,
            collection_type=payload.collection_type,
            collectionname=payload.collectionname,
            isMytown=payload.isMytown,
            actionmode=payload.actionmode,
            collectionid=payload.collectionid,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in /CrudCollections: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@router.post("/CrudCollectionItems", response_model=Any)
async def crud_collection_items(
    payload: CrudCollectionItemsRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/mytown/CrudCollectionItems: insert, update or soft-delete providers in a collection, mirrored to Dataverse."""
    try:
        return await handle_crud_collectionitems(
            user_contactid=payload.user_contactid,
            user_userprofileid=payload.user_userprofileid,
            actionmode=payload.actionmode,
            collectionid=payload.collectionid,
            collectionitems=[item.model_dump() for item in payload.collectionitems],
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in /CrudCollectionItems: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@router.post("/AddUpdateFavouriteTown", response_model=Any)
async def add_update_favourite_town(
    payload: FavouriteTownRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/mytown/AddUpdateFavouriteTown: favourite a town on create or unfavourite it on update, mirrored to Dataverse."""
    try:
        return await handle_favourite_town(
            user_contactid=payload.user_contactid,
            user_userprofileid=payload.user_userprofileid,
            actionmode=payload.actionmode,
            favouritetownid=payload.favouritetownid,
            townname=payload.townname,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in /AddUpdateFavouriteTown: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@router.post("/CoverageTown", response_model=Any)
async def provider_coverage_town(
    payload: ProviderCoverageTownRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/mytown/CoverageTown: add or remove a provider's non-home coverage towns, returning 402 at the plan cap."""
    try:
        return await handle_provider_coverage_towns(
            provider_id=payload.providerid,
            actionmode=payload.actionmode,
            town_details=[town.model_dump() for town in payload.town_details],
            business_name=payload.business_name,
        )
    except PlanLimitError as e:
        raise HTTPException(status_code=402, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /CoverageTown: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@router.post("/promote_area", response_model=Any)
async def promote_area_to_town(
    payload: PromoteAreaRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/mytown/promote_area: create a town profile for an area and repoint its providers' town links."""
    try:
        return await handle_promote_area_to_town(
            townprofileid=payload.townprofileid,
            areaname=payload.areaname,
            statecode=payload.statecode,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /promote_area: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@router.post(
    "/share_collection",
    response_model=ShareCollectionResponse,
    status_code=200,
    summary="Share Collection",
    description=(
        "Send a push notification to a list of recipients when a user shares a "
        "collection. Notifies the latest device of each recipient with the body "
        "'<collection owner first name> shared a collection', routing to /collectionview. "
        "Migrated from the PowerApps ShareCollection flow."
    ),
    responses={
        200: {"description": "Share notification dispatched", "model": ShareCollectionResponse},
        500: {"description": "Internal server error"},
    },
)
async def share_collection_endpoint(
    request: ShareCollectionRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/mytown/share_collection: push a shared-collection notification to the listed recipients."""
    try:
        recipient_ids = [r.userprofileid for r in request.shared_userprofileids if r.userprofileid]
        delivered = await notify_collection_shared(
            collectionid=request.collectionid,
            shared_userprofile_ids=recipient_ids,
            oauth_token=request.Oauth,
        )
        logger.info(
            f"share_collection - collection={request.collectionid} "
            f"recipients={len(recipient_ids)} delivered={delivered}"
        )
        return ShareCollectionResponse(
            success=True,
            recipients=len(recipient_ids),
            delivered=delivered,
        )
    except Exception as e:
        logger.error(f"share_collection - failed collection={request.collectionid} error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to share collection")

@router.post(
    "/verify_townimage",
    response_model=VerifyTownImageResponse,
    status_code=200,
    summary="Approve or Reject an Uploaded Town Image",
    description="Move an uploaded town image to approved or rejected. Only approved images are public.",
    responses={
        200: {"description": "Verification updated", "model": VerifyTownImageResponse},
        400: {"description": "actionmode was not approve or reject"},
        404: {"description": "No such town image"},
    },
)
async def verify_townimage(
    payload: VerifyTownImageRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token),
):
    """Handle POST /api/mytown/verify_townimage: approve or reject a town image and schedule the verification mirror."""
    try:
        result = await handle_verify_town_image(
            townimageid=payload.townimageid,
            actionmode=payload.actionmode,
            reviewer_userprofileid=payload.userprofileid,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"verify_townimage - failed townimageid={payload.townimageid} error={e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to update town image verification")

    background_tasks.add_task(
        sync_town_image_verification_to_dataverse, payload.townimageid, result["verification"]
    )
    return VerifyTownImageResponse(
        success=True,
        townimageid=result["townimageid"],
        townprofileid=result.get("townprofileid"),
        verification=result["verification"],
        previous_verification=result.get("previous_verification"),
    )


@router.post(
    "/generate_town_image",
    response_model=GenerateTownImageResponse,
    status_code=200,
    summary="Generate Town Listing-Card Image",
    description=("Generate a town listing-card image from a name and description, optionally grounded with city/state/country/coordinates. "),
    responses={
        200: {"description": "Image generated", "model": GenerateTownImageResponse},
        502: {"description": "Image model unavailable or returned no image"},
    },
)
async def generate_town_image(
    payload: GenerateTownImageRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token),
):
    """Handle POST /api/mytown/generate_town_image: build a town card from a verified Google photo or a generated image, optionally persisting it."""
    town_name = (payload.town_name or "").strip()
    town_description = (payload.town_description or "").strip()
    if not town_name or not town_description:
        raise HTTPException(
            status_code=400,
            detail="Both town_name and town_description are required.",
        )
    try:
        card = await generate_town_card(
            town_name=town_name,
            town_description=town_description,
            city_name=payload.city_name,
            state=payload.state,
            country=payload.country,
            latitude=payload.latitude,
            longitude=payload.longitude,
            use_google_reference=payload.use_google_reference,
            width=payload.width,
            height=payload.height,
            steps=payload.steps,
            seed=payload.seed,
        )
    except Exception as e:
        logger.error(f"generate_town_image - failed town='{town_name}' error={e}", exc_info=True)
        raise HTTPException(status_code=502, detail="Failed to generate town image.")

    if not card:
        logger.warning(f"generate_town_image - no image produced town='{town_name}'")
        raise HTTPException(status_code=502, detail="Town image could not be generated. Please try again.")

    reference = card.get("reference")
    image_url = card["image_url"]
    persisted = False
    if payload.townprofileid and not image_url.startswith("data:"):
        persisted = await update_town_profile_image(payload.townprofileid, image_url)
        if persisted:
            background_tasks.add_task(
                sync_town_profile_image_to_dataverse, payload.townprofileid, image_url
            )

    return GenerateTownImageResponse(
        success=True,
        image_url=image_url,
        blob_path=card.get("blob_path"),
        town_name=town_name,
        enhanced_prompt=card.get("enhanced_prompt"),
        width=card["width"],
        height=card["height"],
        seed=card["seed"],
        image_source=card.get("image_source", "description_only"),
        reference_photo_url=(reference or {}).get("photo_url"),
        reference_verdict=(reference or {}).get("verdict"),
        persisted=persisted,
    )

@router.post(
    "/upload_townimage",
    response_model=UploadTownImageResponse,
    description="Upload one or more town images for a town profile by a member."
)
async def upload_townimage(
    payload: UploadTownImageRequest,
    current_user: str = Depends(verify_token)
):
    """Handle POST /api/mytown/upload_townimage: store a member's base64 town images in Azure Blob Storage."""
    try:
        from app.etown.mytown.mytown_utils import handle_upload_town_image
        return await handle_upload_town_image(payload)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in upload_townimage: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/request_townimage",
    response_model=RequestTownImageResponse,
    status_code=200,
    summary="Request a Town Image",
    description=(
        "Submit one base64 town image with its filename, and an optional description, for review. The image is "
        "uploaded to Azure Blob at townreview/{townreviewid}/{filename}, keeping the exact filename, and "
        "recorded in etown_townreview with its filename, the town's name, the contact's first name "
        "and status pending. If the same contact and userprofile already have a pending request for this town "
        "with the same filename, or their approved image with that filename is already the town's image, "
        "nothing is uploaded, inserted or sent and 409 is returned. "
        "The support reviewers in COMMUNITY_REVIEW_WHATSAPP_RECIPIENTS and "
        "COMMUNITY_REVIEW_EMAIL_RECIPIENTS are then sent a WhatsApp and an email with the image in the background, "
        "and the request is mirrored to ets_townreview in Dataverse in the background."
    ),
    responses={
        200: {"description": "Request recorded", "model": RequestTownImageResponse},
        400: {"description": "filename contains a slash, or image is not valid base64 or not an image"},
        404: {"description": "No such town profile or contact"},
        409: {"description": "The same filename is already pending, or is already the town's image, for this person"},
    },
)
async def request_townimage(
    payload: RequestTownImageRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(verify_token),
):
    """Handle POST /api/mytown/request_townimage: record a pending etown_townreview request and alert the reviewers."""
    try:
        result = await handle_request_town_image(payload)
        background_tasks.add_task(sync_town_review_to_dataverse, result.model_dump())
        background_tasks.add_task(
            notify_town_image_requested,
            result.townreviewid, result.townprofileid, result.townname, result.contactidname, result.imageurl,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"request_townimage - failed townprofileid={payload.townprofileid} error={e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to submit town image request")


@router.post(
    "/pin_category",
    response_model=CategoryPinResponse,
    status_code=200,
    summary="Pin or Unpin a Category",
    description=(
        "pin \"true\" pins and pin \"false\" unpins an etown_category; unpinning sets ispinned to false and keeps the row. "
        "action_mode \"town\" (the default) pins for a user in a town and, optionally, an area in etown_category_pin, "
        "with cityname and areaname stored trimmed and lowercased, so each user has one row per town, area and category; "
        "the pin and its category are mirrored to ets_categorypin and ets_category in Dataverse in the background. "
        "action_mode \"popular_provider\" pins for a user at latitude and longitude, rounded to 4 decimals, in "
        "etown_popular_category_pin, which GET /api/services/popular_providers reads for Etown_pinned; it is not mirrored."
    ),
    responses={
        200: {"description": "Pin state saved", "model": CategoryPinResponse},
        400: {"description": "cityname is blank for town, or latitude or longitude is missing for popular_provider"},
        404: {"description": "No such contact and user profile, or no such category"},
        422: {"description": "category_id or pin is missing or invalid"},
    },
)
async def pin_category(
    payload: CategoryPinRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(verify_token),
):
    """Handle POST /api/mytown/pin_category: pin or unpin a category for a user in a town and area, or at a location."""
    try:
        result = await handle_category_pin(payload)
        if result.action_mode == "town":
            background_tasks.add_task(
                sync_category_pin_to_dataverse,
                {**result.model_dump(), "contactid": payload.contact_id, "userprofileid": payload.userprofile_id},
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"pin_category - failed category_id={payload.category_id} pin={payload.pin} "
            f"userprofile_id={payload.userprofile_id} error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to save the pin")


@router.post(
    "/community",
    response_model=PostNeedResponse,
    status_code=200,
    summary="Post A Need, Update Or Event",
    description=(
        "Post a need, update or event against one or more towns. town is a list of "
        "{townname, townprofileid}; one record is stored per town (a repeated townprofileid "
        "once), all in one statement, sharing every other field and the attachments. The id "
        "fields and need describe the first town's post and posts lists them all. "
        "post_type (need by default) picks the "
        "table: a need is stored in etown_need under a new needid, an update in etown_update "
        "under an updateid and an event in etown_event under an eventid; the response sets only "
        "that id. JSON body: each attachment is an image or video sent as {data, thumbnail} in the "
        "attachments array, data as base64 or a full data URL, which is the same encoding "
        "every other media field on this API accepts; no filename is needed. Up to "
        f"{MAX_ATTACHMENTS} are allowed "
        f"({', '.join(sorted(ALLOWED_ATTACHMENT_TYPES))}); images have no size limit and a video "
        f"may be up to {MAX_VIDEO_BYTES // (1024 * 1024)}MB once decoded. thumbnail is optional "
        "(base64 or data-URL JPEG, PNG or WebP, no size limit), stored beside the file and "
        "returned as thumbnail_path and thumbnail_url. "
        "Coordinates and the date take either name - lat/latitude, long/longitude and "
        "needed_at (time is still accepted). A date sent without an offset is read as IST, and every "
        "timestamp comes back in IST. location_name is optional text on every post_type. "
        "budget is stored only on needs, and every new need starts with need_status=open. start_date/end_date (YYYY-MM-DD), start_time/end_time "
        "(HH:MM), venue, organizer_name, organizer_number and entry_info are stored only on "
        "events. Sending a field for the wrong post_type is a 422. Every event stores "
        "event_status in IST: upcoming before start_date at start_time (midnight when no start_time), "
        "then live until it ends, then completed. It ends at end_date (or start_date) at end_time; with no "
        "end_time at the end of end_date; with neither, EVENT_OPEN_ENDED_HOURS (default 48) after the start. "
        "It is null when neither date is sent. A worker moves "
        "stored statuses nightly at 00:00 IST, and community reads correct a wrong upcoming or live status. "
        "For a posted need, every provider covering the town is pushed it in the background, "
        "so a 200 means the need is stored, not that anyone has been reached yet; a posted update "
        "or event instead WhatsApps and emails the support reviewers in the background. Every post, draft or posted, is mirrored to Dataverse in the "
        "background. Send "
        "status=draft to save without posting - a draft is stored, excluded from the town "
        "feed, and notifies nobody."
    ),
    responses={
        200: {"description": "Need recorded"},
        400: {"description": "An attachment breaks the type, size or count rules"},
        422: {"description": "A required field is missing or malformed"},
        500: {"description": "The need could not be stored"},
    },
)
async def post_need(
    request: PostNeedRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_token),
) -> PostNeedResponse:
    """Handle POST /api/mytown/community: store one post per town with shared attachments, schedule each mirror and notify if posted."""
    towns = [t.model_dump() for t in request.town]
    townprofileid = ",".join(t["townprofileid"] for t in towns)
    userprofileid = request.userprofileid.strip()
    contactid = request.contactid.strip()
    title = request.title
    description = request.description
    category = (request.category or "").strip() or None
    need_status = request.status

    logger.info(
        "NEEDS | POST_RECEIVED | towns=%d | townprofileids=%s | user=%s | status=%s | "
        "files=%d | category=%s | budget=%s | post_type=%s | start=%s %s | "
        "end=%s %s | title_len=%d | desc_len=%d",
        len(towns), townprofileid, userprofileid, need_status,
        len(request.attachments), category, request.budget,
        request.post_type, request.start_date, request.start_time, request.end_date,
        request.end_time, len(title), len(description),
    )

    def _reject(detail: str, code: int = status.HTTP_422_UNPROCESSABLE_ENTITY) -> HTTPException:
        """Log the refusal and return the exception to raise, so no 4xx goes unlogged."""
        logger.warning(
            "NEEDS | POST_REJECTED | code=%d | user=%s | townprofileids=%s | reason=%s",
            code, userprofileid, townprofileid, detail,
        )
        return HTTPException(status_code=code, detail=detail)

    if not towns or not userprofileid or not contactid:
        raise _reject("town, userprofileid and contactid are all required")
    if need_status not in NEED_STATUSES:
        raise _reject(f"status must be one of {list(NEED_STATUSES)}")

    try:
        attachments = await upload_need_attachments(
            [a.model_dump() for a in request.attachments], userprofileid
        )
    except NeedValidationError as exc:
        raise _reject(str(exc), status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        logger.error(
            "NEEDS | ATTACHMENT_UPLOAD_FAILED | user=%s | %s",
            userprofileid, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Your files could not be uploaded. Please try again.",
        )

    post_type = request.post_type
    try:
        posts = await create_posts(post_type, {
            "userprofileid": userprofileid,
            "contactid": contactid,
            "title": title,
            "description": description,
            "latitude": request.lat,
            "longitude": request.long,
            "needed_at": request.needed_at,
            "category": category,
            "budget": request.budget,
            "need_status": "open",
            "start_date": request.start_date,
            "start_time": request.start_time,
            "end_date": request.end_date,
            "end_time": request.end_time,
            "location_name": request.location_name,
            "venue": request.venue,
            "organizer_name": request.organizer_name,
            "organizer_number": request.organizer_number,
            "entry_info": request.entry_info,
            "status": need_status,
            "attachments": attachments,
        }, towns)
    except Exception as exc:
        logger.error(
            "NEEDS | CREATE_FAILED | post_type=%s | townprofileids=%s | user=%s | %s",
            post_type, townprofileid, userprofileid, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Your {post_type} could not be posted. Please try again.",
        )

    need = posts[0]
    for post in posts:
        background_tasks.add_task(sync_post_to_dataverse, post)

    if post_type != "need":
        for post in posts:
            logger.info(
                "NEEDS | %s_SAVED | post_type=%s | %s=%s | town=%s | status=%s | no provider fan-out",
                post_type.upper(), post_type, f"{post_type}id", post[f"{post_type}id"],
                post["townprofileid"], need_status,
            )
        if need_status == "posted":
            background_tasks.add_task(
                notify_community_post_submitted,
                post_type,
                [{"postid": p[f"{post_type}id"], "townname": p["townname"]} for p in posts],
                title,
                userprofileid,
            )
        return PostNeedResponse(
            success=True,
            post_type=post_type,
            updateid=need["updateid"],
            eventid=need["eventid"],
            status=need["status"],
            need=need,
            posts=posts,
            message=(
                f"Your {post_type} has been submitted for review and will appear in the town feed once approved."
                if need_status == "posted"
                else f"Your {post_type} has been saved as a draft."
            ),
        )

    for post in posts:
        if need_status == "posted":
            background_tasks.add_task(
                notify_need_posted,
                post["needid"],
                post["townname"],
                post["townprofileid"],
                title,
                poster_upid=userprofileid,
                poster_contactid=contactid,
            )
            logger.info(
                "NEEDS | FANOUT_SCHEDULED | needid=%s | townprofileid=%s",
                post["needid"], post["townprofileid"],
            )
        else:
            logger.info(
                "NEEDS | DRAFT_SAVED | needid=%s | user=%s | not notifying anyone",
                post["needid"], userprofileid,
            )

    return PostNeedResponse(
        success=True,
        post_type=post_type,
        needid=need["needid"],
        status=need["status"],
        need=need,
        posts=posts,
        message=(
            f"Your need has been posted. Providers in {', '.join(t['townname'] for t in towns)} "
            "will send you quotes in Messages."
            if need_status == "posted"
            else "Your need has been saved as a draft."
        ),
    )


@router.post(
    "/{needid}/publish",
    response_model=PublishNeedResponse,
    status_code=200,
    summary="Publish A Draft Need, Update Or Event",
    description=(
        "Turn one of your own drafts into a posted post, with its draft copies in every other town. "
        "The path takes a needid, updateid or eventid of any copy; a need also notifies every provider covering the town, while an update or "
        "event WhatsApps and emails the support reviewers. Only the draft's owner may publish it. Retrying is safe: a post that is "
        "already posted returns 200 with published=false and fires no second notification. "
        "Pass needed_at to correct the date on a draft whose original one has passed."
    ),
    responses={
        200: {"description": "Need posted, or already was"},
        403: {"description": "The draft belongs to someone else"},
        404: {"description": "No such need"},
        409: {"description": "The need is closed and cannot be posted"},
        500: {"description": "The publish failed"},
    },
)
async def publish_draft_need(
    background_tasks: BackgroundTasks,
    request: PublishNeedRequest,
    needid: str = Path(..., description="The draft's needid, updateid or eventid"),
    _: str = Depends(verify_token),
) -> PublishNeedResponse:
    """Handle POST /api/mytown/{needid}/publish: post the owner's draft, notifying providers only on a need's first publish."""
    logger.info(
        "NEEDS | PUBLISH_RECEIVED | needid=%s | user=%s | needed_at_override=%s",
        needid, request.userprofileid, request.needed_at is not None,
    )

    try:
        result = await publish_post(
            postid=needid,
            userprofileid=request.userprofileid.strip(),
            needed_at=request.needed_at,
        )
    except Exception as exc:
        logger.error(
            "NEEDS | PUBLISH_FAILED | needid=%s | user=%s | %s",
            needid, request.userprofileid, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Your need could not be posted. Please try again.",
        )

    outcome = result["outcome"]
    _REFUSALS = {
        "not_found": (status.HTTP_404_NOT_FOUND, "No such post."),
        "not_owner": (status.HTTP_403_FORBIDDEN, "This draft belongs to another member."),
        "closed": (status.HTTP_409_CONFLICT, "This post is closed and cannot be posted."),
    }
    if outcome in _REFUSALS:
        code, detail = _REFUSALS[outcome]
        logger.warning(
            "NEEDS | PUBLISH_REFUSED | code=%d | needid=%s | user=%s | outcome=%s",
            code, needid, request.userprofileid, outcome,
        )
        raise HTTPException(status_code=code, detail=detail)

    need = result["need"]
    post_type = need["post_type"]
    published = result["posts"]
    for post in published or [need]:
        background_tasks.add_task(sync_post_to_dataverse, post)

    if post_type != "need":
        logger.info(
            "NEEDS | PUBLISH_OK | post_type=%s | id=%s | outcome=%s | towns=%d | no provider fan-out",
            post_type, needid, outcome, len(published),
        )
        if outcome == "published":
            background_tasks.add_task(
                notify_community_post_submitted,
                post_type,
                [{"postid": p[f"{post_type}id"], "townname": p["townname"]} for p in published],
                need["title"],
                need["userprofileid"],
            )
        return PublishNeedResponse(
            success=True,
            post_type=post_type,
            updateid=need["updateid"],
            eventid=need["eventid"],
            status=need["status"],
            published=outcome == "published",
            need=need,
            message=(
                f"Your {post_type} has been submitted for review and will appear in the town feed once approved."
                if outcome == "published"
                else f"This {post_type} was already posted."
            ),
        )

    if outcome == "published":
        for post in published:
            background_tasks.add_task(
                notify_need_posted,
                post["needid"],
                post["townname"],
                post["townprofileid"],
                post["title"],
                poster_upid=post["userprofileid"],
                poster_contactid=post["contactid"],
            )
            logger.info(
                "NEEDS | FANOUT_SCHEDULED | needid=%s | townprofileid=%s | via=publish",
                post["needid"], post["townprofileid"],
            )
    else:
        logger.info(
            "NEEDS | PUBLISH_NOOP_OK | needid=%s | user=%s | already posted, not re-notifying",
            needid, request.userprofileid,
        )

    return PublishNeedResponse(
        success=True,
        post_type=post_type,
        needid=need["needid"],
        status=need["status"],
        published=outcome == "published",
        need=need,
        message=(
            f"Your need has been posted. Providers in {', '.join(p['townname'] for p in published)} "
            "will send you quotes in Messages."
            if outcome == "published"
            else "This need was already posted."
        ),
    )


@router.get(
    "/community",
    response_model=NeedListResponse,
    status_code=200,
    summary="List Needs, Updates And Events",
    description=(
        "Reads etown_need, etown_update and etown_event together, newest first across all "
        "three. With status=posted, updates and events appear only once their review column "
        "is approved (set by an admin in Dataverse; 'approved' or '1'); needs need no approval. Across all "
        "three; each item's post_type says which it is and only its matching id is set. Pass "
        "post_type=need, update or event to read one type only. Every event is returned whatever its "
        "status, carrying its event_status (upcoming, live or completed once its review is approved, "
        "else the review's pending or rejected); an upcoming or live event's "
        "status is checked against the current IST time on every read, and only a wrong one is corrected "
        "in the response and stored in the background. "
        "status=posted (the default) returns one town's posted posts, newest first - "
        "townprofileid is required and townname is an optional extra case-insensitive "
        "filter. is_sameuser=true instead returns only userprofileid's own posts across "
        "every town (townprofileid optional, narrowing to one town), pending and rejected updates "
        "and events included, an update's update_status and an unapproved event's event_status "
        "showing pending or rejected; post_type defaults to need there, and "
        "update, event or all reads those instead; open and closed split the need count by "
        "need_status (anything not closed counts as open), and each need carries response_count, the "
        "unique people who responded to it. status=draft returns only the caller's own drafts: userprofileid is "
        "required (the bearer token is shared, so the caller states who they are) and "
        "townprofileid becomes optional, narrowing the drafts to one town when supplied. "
        "With is_sameuser=true a post made in several towns is one item, counted once, whose "
        "towns lists every town it was made in with that town's postid. With is_sameuser=false, "
        "items whose poster is userprofileid carry the same full towns list, and every other "
        "item's towns holds only its own town. "
        "Deleted needs, and posts whose poster's contact is deleted, are never returned or counted, and one member's drafts are never visible to "
        "another. poster_name and poster_image are the provider's businessname and "
        "serviceimageurl when the poster's role is 1, and the contact's firstname and "
        "profileimage when it is 2. Every item carries like_count and comment_count, and "
        "is_liked tells whether userprofileid has liked it, and mycircle whether the poster is in "
        "userprofileid's MyCircle (a member or provider relation either way), with invitationstatus Connected, Requested "
        "(a pending invitation either way) or null. With is_provider=true (userprofileid "
        "required), only needs are read (update and event count 0, and post_type may only be need) "
        "and needs posted by contactid, when given, are left out of the items and counts; "
        "with is_provider=false (the default) needs, updates and events are read. "
        "Whenever a town feed is read with userprofileid (status=posted, is_sameuser=false), the "
        "current IST time (never for GUEST_USER_PROFILE_ID) is stored in the background as that user's last visit to the post types "
        "read: post_type's own type, all three when post_type is omitted or all, and need alone with "
        "is_provider=true. The provider dashboard's per-town new_need_count and the MyTown "
        "new_need_count, new_update_count and new_event_count each count from their own type's visit. "
        "all, need, event and update are tab "
        "counts that ignore post_type; total and total_pages follow it. When townprofileid is given, "
        "townimage, provider_count and view_count describe the town exactly as "
        "/mytown_suggestions does (contactid's own providers left out of provider_count); they are "
        "null without it. Attachment URLs come back fully "
        "resolved. Paged on page (1-based) and "
        "limit, with total_pages in the response."
    ),
    responses={
        200: {"description": "Needs retrieved"},
        422: {"description": "status invalid, or the identifier that status requires is missing"},
        500: {"description": "The read failed"},
    },
)
async def list_needs(
    background_tasks: BackgroundTasks,
    need_status: str = Query(
        "posted",
        alias="status",
        description="posted for the town feed, draft for the caller's own drafts",
    ),
    townprofileid: Optional[str] = Query(
        None,
        description="etown_townprofile id; required for status=posted, optional for status=draft",
    ),
    townname: Optional[str] = Query(None, description="Optional extra filter on the town name"),
    userprofileid: Optional[str] = Query(
        None,
        description="Draft owner, required for status=draft; for either status, the viewer whose "
                    "like sets is_liked on each item",
    ),
    post_type: Optional[str] = Query(
        None, description="need, update or event to read one type; all for all three; omitted reads "
                          "all three, or only need with is_sameuser=true"
    ),
    is_sameuser: bool = Query(
        False,
        description="true to read only userprofileid's own posts instead of the town feed; "
                    "false (the default) for the town feed",
    ),
    is_provider: bool = Query(
        False,
        description="true to read only needs (userprofileid required); false (the default) for "
                    "needs, updates and events",
    ),
    contactid: Optional[str] = Query(
        None,
        description="The caller's etown_contact id; their own providers are left out of provider_count, "
                    "and with is_provider=true, needs posted by this contact are left out of the items and counts",
    ),
    page: int = Query(1, ge=1, description="Page number for pagination (starts from 1)"),
    limit: int = Query(10, ge=1, le=100, description="Rows per page (default 10, max 100)"),
    _: str = Depends(verify_token),
) -> NeedListResponse:
    """Handle GET /api/mytown/community: page a town's posted posts, or the caller's own posts or drafts, newest first."""
    need_status = (need_status or "posted").strip().lower()
    post_type = (post_type or "").strip().lower() or ("need" if is_sameuser else None)
    if post_type == "all":
        post_type = None

    def _reject(detail: str) -> HTTPException:
        """Log the refused read and return the 422 to raise, so no 4xx goes unlogged."""
        logger.warning(
            "NEEDS | LIST_REJECTED | code=422 | status=%s | townprofileid=%s | "
            "userprofileid=%s | reason=%s",
            need_status, townprofileid, userprofileid, detail,
        )
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)

    if need_status not in ("posted", "draft"):
        raise _reject("status must be posted or draft")
    if post_type and post_type not in POST_TYPES:
        raise _reject(f"post_type must be one of {list(POST_TYPES)}")
    if is_sameuser and not (userprofileid or "").strip():
        raise _reject("userprofileid is required when is_sameuser=true")
    if need_status == "posted" and not is_sameuser and not (townprofileid or "").strip():
        raise _reject("townprofileid is required when status=posted")
    if need_status == "draft" and not (userprofileid or "").strip():
        raise _reject("userprofileid is required when status=draft")
    if is_provider and post_type and post_type != "need":
        raise _reject("post_type must be need when is_provider=true")
    if is_provider and not (userprofileid or "").strip():
        raise _reject("userprofileid is required when is_provider=true")

    viewer = (userprofileid or "").strip()
    visited_at = now_ist()

    town_id = (townprofileid or "").strip() or None

    async def _no_header() -> Dict[str, Any]:
        """Stand in for the town header when no townprofileid was given."""
        return {"townimage": None, "provider_count": None, "view_count": None}

    try:
        result, header = await asyncio.gather(
            list_needs_util(
                status=need_status,
                townprofileid=town_id,
                townname=townname,
                userprofileid=viewer or None,
                post_type=post_type,
                same_user=is_sameuser,
                needs_only=is_provider,
                exclude_contactid=contactid if is_provider else None,
                page=page,
                limit=limit,
            ),
            get_community_town_header(town_id, contactid)
            if town_id else _no_header(),
        )
    except Exception as exc:
        logger.error(
            "NEEDS | LIST_FAILED | status=%s | townprofileid=%s | userprofileid=%s | "
            "page=%d | limit=%d | %s",
            need_status, townprofileid, userprofileid, page, limit, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not read these needs.",
        )

    is_guest = bool(settings.GUEST_USER_PROFILE_ID) and viewer == settings.GUEST_USER_PROFILE_ID
    if viewer and not is_guest and need_status == "posted" and not is_sameuser:
        visited_types = ("need",) if is_provider else ((post_type,) if post_type else POST_TYPES)
        background_tasks.add_task(record_community_visit, viewer, visited_at, visited_types)
    moved = correct_event_statuses(result["needs"])
    if moved:
        background_tasks.add_task(store_event_statuses, moved)

    if not result["needs"]:
        logger.info(
            "NEEDS | LIST_EMPTY | status=%s | townprofileid=%s | townname=%s | "
            "userprofileid=%s | is_sameuser=%s | page=%d | limit=%d | total=%d",
            need_status, townprofileid, townname, userprofileid, is_sameuser, page, limit,
            result["total"],
        )
    else:
        logger.info(
            "NEEDS | LIST_OK | status=%s | townprofileid=%s | userprofileid=%s | is_sameuser=%s | "
            "returned=%d | total=%d | page=%d/%d | limit=%d | counts=%s",
            need_status, townprofileid, userprofileid, is_sameuser,
            len(result["needs"]), result["total"], page, result["total_pages"], limit,
            result["counts"],
        )

    return NeedListResponse(
        success=True,
        all=result["counts"]["all"],
        need=result["counts"]["need"],
        event=result["counts"]["event"],
        update=result["counts"]["update"],
        open=result["counts"]["open"],
        closed=result["counts"]["closed"],
        total=result["total"],
        page=page,
        limit=limit,
        total_pages=result["total_pages"],
        townimage=header["townimage"],
        provider_count=header["provider_count"],
        view_count=header["view_count"],
        needs=result["needs"],
    )


@router.get(
    "/community/search",
    response_model=NeedListResponse,
    status_code=200,
    summary="Search Needs, Updates And Events",
    description=(
        "Fuzzy-searches one town's posted needs and approved updates and events, under the same "
        "visibility rules as GET /community. search is lower-cased, punctuation is dropped and "
        "up to 8 words of 2+ characters are kept; a post matches when at least half the words "
        "match its post type, title, description, poster_name (provider businessname or member "
        "firstname), category, location, town, venue, organizer or entry info, by word prefix or "
        "trigram similarity, so typos still match. Results come best match first, posts matching "
        "more words and matching in the title or poster name ranking higher, each with "
        "match_score. all, need, event and update count matching posts ignoring post_type; total "
        "and total_pages follow it. is_sameuser=true instead searches only userprofileid's own "
        "posted posts across every town (userprofileid required, townprofileid optional, narrowing "
        "to one town; post_type defaults to need, and update, event or all searches those instead), "
        "with open and closed splitting the need count by need_status, and each need carrying response_count. "
        "Paged on page (1-based) and limit."
    ),
    responses={
        200: {"description": "Search results retrieved"},
        422: {
            "description": "post_type invalid, search has no word of 2+ letters or digits, or the "
                           "identifier that is_sameuser requires is missing"
        },
        500: {"description": "The search failed"},
    },
)
async def search_community(
    background_tasks: BackgroundTasks,
    search: str = Query(..., min_length=1, max_length=100, description="Words to search for"),
    townprofileid: Optional[str] = Query(
        None,
        description="etown_townprofile id of the town to search; required unless is_sameuser=true",
    ),
    townname: Optional[str] = Query(None, description="Optional extra filter on the town name"),
    userprofileid: Optional[str] = Query(
        None,
        description="The viewer whose like sets is_liked on each item; with is_sameuser=true, "
                    "the member whose own needs are searched",
    ),
    post_type: Optional[str] = Query(
        None, description="need, update or event to return one type; all for all three; omitted returns "
                          "all three, or only need with is_sameuser=true"
    ),
    is_sameuser: bool = Query(
        False,
        description="true to search only userprofileid's own posts instead of the town feed; "
                    "false (the default) for the town feed",
    ),
    page: int = Query(1, ge=1, description="Page number for pagination (starts from 1)"),
    limit: int = Query(10, ge=1, le=50, description="Rows per page (default 10, max 50)"),
    _: str = Depends(verify_token),
) -> NeedListResponse:
    """Handle GET /api/mytown/community/search: fuzzy-search a town's posted posts, or the caller's own needs, best match first."""
    post_type = (post_type or "").strip().lower() or ("need" if is_sameuser else None)
    if post_type == "all":
        post_type = None
    townprofileid = (townprofileid or "").strip() or None
    userprofileid = (userprofileid or "").strip() or None
    detail = None
    if post_type and post_type not in POST_TYPES:
        detail = f"post_type must be one of {list(POST_TYPES)}"
    elif is_sameuser and not userprofileid:
        detail = "userprofileid is required when is_sameuser=true"
    elif not is_sameuser and not townprofileid:
        detail = "townprofileid is required unless is_sameuser=true"
    else:
        try:
            result = await list_needs_util(
                status="posted",
                townprofileid=townprofileid,
                townname=townname,
                userprofileid=userprofileid,
                post_type=post_type,
                same_user=is_sameuser,
                search=search,
                page=page,
                limit=limit,
            )
        except ValueError as exc:
            detail = str(exc)
        except Exception as exc:
            logger.error(
                "NEEDS | SEARCH_FAILED | townprofileid=%s | userprofileid=%s | is_sameuser=%s | "
                "search=%r | page=%d | limit=%d | %s",
                townprofileid, userprofileid, is_sameuser, search, page, limit, exc, exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not search these posts.",
            )
    if detail:
        logger.warning(
            "NEEDS | SEARCH_REJECTED | code=422 | townprofileid=%s | userprofileid=%s | "
            "is_sameuser=%s | search=%r | reason=%s",
            townprofileid, userprofileid, is_sameuser, search, detail,
        )
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)

    moved = correct_event_statuses(result["needs"])
    if moved:
        background_tasks.add_task(store_event_statuses, moved)
    return NeedListResponse(
        success=True,
        all=result["counts"]["all"],
        need=result["counts"]["need"],
        event=result["counts"]["event"],
        update=result["counts"]["update"],
        open=result["counts"]["open"],
        closed=result["counts"]["closed"],
        total=result["total"],
        page=page,
        limit=limit,
        total_pages=result["total_pages"],
        needs=result["needs"],
    )


@router.get(
    "/community/post",
    response_model=CommunityPostDetailResponse,
    status_code=200,
    summary="Get One Need, Update Or Event",
    description=(
        "Reads one post by post_type and postid (its needid, updateid or eventid) with the same "
        "fields as a GET /community item: poster_name and poster_image, like_count, "
        "comment_count, is_liked for userprofileid, update_status on updates, resolved attachment URLs, and "
        "on needs need_status and response_count. Deleted posts and posts whose poster's "
        "contact is deleted are never returned. Drafts, and updates and events not yet approved, "
        "are returned only when userprofileid is their owner; anyone else gets 404."
    ),
    responses={
        200: {"description": "Post retrieved"},
        404: {"description": "No such post, or it is not visible to userprofileid"},
        422: {"description": "post_type invalid or postid missing"},
        500: {"description": "The read failed"},
    },
)
async def get_community_post_detail(
    background_tasks: BackgroundTasks,
    post_type: str = Query(..., min_length=1, description="need, update or event"),
    postid: str = Query(..., min_length=1, description="needid, updateid or eventid of the post"),
    userprofileid: Optional[str] = Query(
        None,
        description="The viewer whose like sets is_liked; the owner also sees their drafts and unapproved posts",
    ),
    _: str = Depends(verify_token),
) -> CommunityPostDetailResponse:
    """Handle GET /api/mytown/community/post: read one need, update or event by id in the feed shape."""
    post_type = post_type.strip().lower()
    postid = postid.strip()
    userprofileid = (userprofileid or "").strip() or None
    if post_type not in POST_TYPES or not postid:
        detail = f"post_type must be one of {list(POST_TYPES)}" if postid else "postid is required"
        logger.warning(
            "NEEDS | POST_GET_REJECTED | code=422 | post_type=%s | postid=%s | reason=%s",
            post_type, postid, detail,
        )
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)

    try:
        post = await get_community_post(post_type=post_type, postid=postid, userprofileid=userprofileid)
    except Exception as exc:
        logger.error(
            "NEEDS | POST_GET_FAILED | post_type=%s | postid=%s | userprofileid=%s | %s",
            post_type, postid, userprofileid, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not read this post.",
        )

    if not post:
        logger.warning(
            "NEEDS | POST_GET_NOT_FOUND | code=404 | post_type=%s | postid=%s | userprofileid=%s",
            post_type, postid, userprofileid,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")

    moved = correct_event_statuses([post])
    if moved:
        background_tasks.add_task(store_event_statuses, moved)
    logger.info(
        "NEEDS | POST_GET_OK | post_type=%s | postid=%s | userprofileid=%s",
        post_type, postid, userprofileid,
    )
    return CommunityPostDetailResponse(success=True, post_type=post_type, postid=postid, need=post)


@router.post(
    "/community/action",
    response_model=CommunityPostActionResponse,
    status_code=200,
    summary="Edit Or Delete A Need, Update Or Event",
    description=(
        "action_mode=edit changes one of the caller's own needs, updates or events: only the "
        "fields present in the body are written, a blank optional field clears it, and title, "
        "description, lat and long cannot be blanked. attachments adds files (base64 or data URL, "
        "each with an optional thumbnail, under the same type and size rules as POST /community) "
        "and remove_attachments drops stored ones, with their thumbnails, by path, keeping at most "
        f"{MAX_ATTACHMENTS} in all. budget and need_status (open or closed) are need-only, "
        "and the schedule, venue, organizer and "
        "entry fields are event-only, as on POST /community. action_mode=delete soft-deletes the "
        "post, which then leaves every feed. A post made in several towns is one copy per town: "
        "postid may be any copy, and edits and deletes apply to every copy. On edit, town is the "
        "full list of towns to keep: a town already there is kept as is, a town left out moves "
        "its copy to a new town in the list (townname and townprofileid rewritten, keeping its "
        "likes, comments and review) or, with no new town left, is deleted, and any further new "
        "town gets a fresh copy. When posted, a need notifies each new town's providers and a "
        "fresh update or event copy goes to the reviewers. "
        "post_type, postid and userprofileid are required, and only the post's owner may change "
        "it. Status is not editable here. Every change is mirrored to Dataverse in the background."
    ),
    responses={
        200: {"description": "Post edited or deleted"},
        400: {"description": "An attachment breaks the type, size or count rules"},
        403: {"description": "The post belongs to someone else"},
        404: {"description": "No such post"},
        422: {"description": "A field is missing, malformed, or leaves the schedule inconsistent"},
        500: {"description": "The change could not be saved"},
    },
)
async def community_post_action(
    request: CommunityPostActionRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_token),
) -> CommunityPostActionResponse:
    """Handle POST /api/mytown/community/action: edit or soft-delete the caller's own post and schedule its mirror."""
    action = request.action_mode
    columns = {
        "title": "title", "description": "description", "category": "category",
        "budget": "budget", "need_status": "need_status", "lat": "latitude", "long": "longitude", "needed_at": "needed_at",
        "location_name": "location_name", "start_date": "start_date", "start_time": "start_time",
        "end_date": "end_date", "end_time": "end_time", "venue": "venue",
        "organizer_name": "organizer_name", "organizer_number": "organizer_number",
        "entry_info": "entry_info",
    }
    changes = {
        column: getattr(request, field)
        for field, column in columns.items()
        if action == "edit" and field in request.model_fields_set
    }
    logger.info(
        "NEEDS | ACTION_RECEIVED | action=%s | post_type=%s | postid=%s | user=%s | fields=%s | "
        "files_added=%d | files_removed=%d",
        action, request.post_type, request.postid, request.userprofileid,
        ",".join(sorted(changes)) or "-", len(request.attachments), len(request.remove_attachments),
    )

    try:
        result = await edit_or_delete_post(
            action=action,
            post_type=request.post_type,
            postid=request.postid,
            userprofileid=request.userprofileid,
            changes=changes,
            new_attachments=[a.model_dump() for a in request.attachments] if action == "edit" else [],
            remove_attachments=request.remove_attachments if action == "edit" else [],
            towns=[t.model_dump() for t in request.town] if action == "edit" and request.town is not None else None,
        )
    except NeedValidationError as exc:
        logger.warning(
            "NEEDS | ACTION_REJECTED | code=400 | postid=%s | user=%s | reason=%s",
            request.postid, request.userprofileid, exc,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error(
            "NEEDS | ACTION_FAILED | action=%s | post_type=%s | postid=%s | user=%s | %s",
            action, request.post_type, request.postid, request.userprofileid, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Your {request.post_type} could not be saved. Please try again.",
        )

    refusals = {
        "not_found": (status.HTTP_404_NOT_FOUND, "No such post."),
        "not_owner": (status.HTTP_403_FORBIDDEN, "This post belongs to another member."),
        "invalid": (status.HTTP_422_UNPROCESSABLE_ENTITY, result["reason"]),
    }
    if result["outcome"] in refusals:
        code, detail = refusals[result["outcome"]]
        logger.warning(
            "NEEDS | ACTION_REFUSED | code=%d | action=%s | postid=%s | user=%s | outcome=%s",
            code, action, request.postid, request.userprofileid, result["outcome"],
        )
        raise HTTPException(status_code=code, detail=detail)

    post = result["need"]
    post_type = request.post_type
    for touched in result["posts"]:
        background_tasks.add_task(sync_post_to_dataverse, touched)
    added = result["added"]
    new_town_posts = result["moved"] + added
    if new_town_posts and new_town_posts[0]["status"] == "posted":
        if post_type == "need":
            for new_post in new_town_posts:
                background_tasks.add_task(
                    notify_need_posted,
                    new_post["needid"],
                    new_post["townname"],
                    new_post["townprofileid"],
                    new_post["title"],
                    poster_upid=new_post["userprofileid"],
                    poster_contactid=new_post["contactid"],
                )
        elif added:
            background_tasks.add_task(
                notify_community_post_submitted,
                post_type,
                [{"postid": p[f"{post_type}id"], "townname": p["townname"]} for p in added],
                added[0]["title"],
                request.userprofileid,
            )
        logger.info(
            "NEEDS | ACTION_TOWNS_CHANGED | post_type=%s | postid=%s | moved=%s | added=%s | notifying",
            post_type, request.postid,
            ",".join(p["townprofileid"] for p in result["moved"]) or "-",
            ",".join(p["townprofileid"] for p in added) or "-",
        )
    return CommunityPostActionResponse(
        success=True,
        action_mode=action,
        post_type=request.post_type,
        postid=request.postid,
        need=post if action == "edit" else None,
        message=(
            f"Your {request.post_type} has been updated."
            if action == "edit"
            else f"Your {request.post_type} has been deleted."
        ),
    )


@router.post(
    "/community/respond",
    response_model=NeedResponseResponse,
    status_code=200,
    summary="Respond To A Need",
    description=(
        "userprofileid responds to a posted, open need with a message. The response is stored in "
        "etown_needresponse and delivered in Messages: the conversation between the responder and "
        "the need's poster is opened (or reused), then two messages are sent from the responder - "
        "the need's title, then the message. Pass oauth_token to push the poster the message. "
        "The poster also gets a push and an in-app notification in the background. "
        "Responding to your own need, or to a deleted, draft or closed one, is refused."
    ),
    responses={
        200: {"description": "Response stored and sent"},
        400: {"description": "The responder is the need's poster"},
        404: {"description": "No such posted need, or an unknown userprofile"},
        409: {"description": "The need is closed"},
        422: {"description": "A required field is missing"},
        500: {"description": "The response could not be sent"},
    },
)
async def respond_to_community_need(
    request: NeedResponseRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_token),
) -> NeedResponseResponse:
    """Handle POST /api/mytown/community/respond: store a response and message the need's poster its title and the text."""
    logger.info(
        "NEEDS | RESPOND_RECEIVED | needid=%s | user=%s | message_len=%d | push=%s",
        request.needid, request.userprofileid, len(request.message), bool(request.oauth_token),
    )
    try:
        result = await respond_to_need(
            needid=request.needid,
            userprofileid=request.userprofileid,
            contactid=request.contactid or None,
            message=request.message,
            oauth_token=request.oauth_token,
        )
    except HTTPException as exc:
        logger.warning(
            "NEEDS | RESPOND_REJECTED | code=%d | needid=%s | user=%s | reason=%s",
            exc.status_code, request.needid, request.userprofileid, exc.detail,
        )
        raise
    except Exception as exc:
        logger.error(
            "NEEDS | RESPOND_FAILED | needid=%s | user=%s | %s",
            request.needid, request.userprofileid, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Your response could not be sent. Please try again.",
        )

    refusals = {
        "not_found": (status.HTTP_404_NOT_FOUND, "No such need."),
        "need_closed": (status.HTTP_409_CONFLICT, "This need is closed and no longer takes responses."),
        "own_need": (status.HTTP_400_BAD_REQUEST, "You cannot respond to your own need."),
    }
    if result["outcome"] in refusals:
        code, detail = refusals[result["outcome"]]
        logger.warning(
            "NEEDS | RESPOND_REFUSED | code=%d | needid=%s | user=%s | outcome=%s",
            code, request.needid, request.userprofileid, result["outcome"],
        )
        raise HTTPException(status_code=code, detail=detail)

    background_tasks.add_task(
        notify_need_response,
        request.needid, result["response"]["needresponseid"], request.userprofileid,
        result["response"].get("conversation_id"),
    )
    return NeedResponseResponse(
        success=True,
        response=result["response"],
        message="Your response has been sent to the need's poster in Messages.",
    )


@router.get(
    "/community/responses",
    response_model=NeedResponseListResponse,
    status_code=200,
    summary="List A Need's Responses",
    description=(
        "Page the responses to a need, one per person: each responder's latest response by "
        "created_at, with the most recent responders first. Each responder is named and "
        "pictured by userprofile role: a provider (role 1) by businessname and serviceimageurl, "
        "a member (role 2) by contact firstname and profileimage. total is the number of unique "
        "responders. Deleted responses and deleted accounts are left out."
    ),
    responses={
        200: {"description": "Responses retrieved"},
        422: {"description": "needid missing"},
        500: {"description": "The read failed"},
    },
)
async def list_community_need_responses(
    needid: str = Query(..., min_length=1, description="The need whose responses are listed"),
    page: int = Query(1, ge=1, description="Page number for pagination (starts from 1)"),
    limit: int = Query(20, ge=1, le=100, description="Rows per page (default 20, max 100)"),
    _: str = Depends(verify_token),
) -> NeedResponseListResponse:
    """Handle GET /api/mytown/community/responses: page each responder's latest response to a need, newest first."""
    try:
        result = await list_need_responses(needid=needid.strip(), page=page, limit=limit)
    except Exception as exc:
        logger.error("NEEDS | RESPONSES_LIST_FAILED | needid=%s | %s", needid, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not read the responses to this need.",
        )

    logger.info(
        "NEEDS | RESPONSES_LIST_OK | needid=%s | returned=%d | total=%d | page=%d/%d",
        needid, len(result["responses"]), result["total"], page, result["total_pages"],
    )
    return NeedResponseListResponse(
        success=True,
        needid=needid.strip(),
        count=len(result["responses"]),
        total=result["total"],
        page=page,
        limit=limit,
        total_pages=result["total_pages"],
        responses=result["responses"],
    )


@router.post(
    "/community/like",
    response_model=CommunityLikeResponse,
    status_code=200,
    summary="Like Or Dislike A Community Post",
    description=(
        "Like (like_mode=1, stored as Like) or dislike (like_mode=2, stored as Dislike) a posted "
        "need, update or event, sent as post_type plus its needid, "
        "updateid or eventid. One like is kept per post and userprofileid, so repeating a like is "
        "harmless. post_type is stored on the like. A like (not a dislike) pushes the post's "
        "owner a notification, unless they liked their own post. Returns the post's like count after the change. "
        "A post that is not posted in that post_type's table - a draft, a deleted post or the wrong "
        "post_type - is a 404."
    ),
    responses={
        200: {"description": "Like recorded"},
        404: {"description": "No posted post of that post_type has this id"},
        422: {"description": "post_type, postid or userprofileid missing or invalid, or like_mode not 1 or 2"},
        500: {"description": "The like could not be saved"},
    },
)
async def like_community_post(
    request: CommunityLikeRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_token),
) -> CommunityLikeResponse:
    """Handle POST /api/mytown/community/like: like or dislike a post and return its new like count."""
    try:
        result = await set_community_like(
            post_type=request.post_type,
            postid=request.postid,
            userprofileid=request.userprofileid,
            contactid=request.contactid or None,
            like_mode=request.like_mode,
        )
    except Exception as exc:
        logger.error(
            "COMMUNITY | LIKE_FAILED | postid=%s | user=%s | %s",
            request.postid, request.userprofileid, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Your like could not be saved. Please try again.",
        )

    if result["outcome"] == "post_not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such post.")

    background_tasks.add_task(sync_community_like_to_dataverse, result["like"])

    liked = result["outcome"] == "liked"
    if liked and settings.NOTIFICATIONS_GOVERNANCE_ENABLED:
        background_tasks.add_task(
            notify_community_like, request.post_type, request.postid, request.userprofileid
        )
    return CommunityLikeResponse(
        success=True,
        postid=request.postid,
        post_type=result["post_type"],
        like_mode=request.like_mode,
        likemodename=result["like"]["likemodename"],
        is_liked=liked,
        like_count=result["like_count"],
        message="Liked." if liked else "Disliked.",
    )


@router.get(
    "/community/likes",
    response_model=CommunityLikeListResponse,
    status_code=200,
    summary="List A Community Post's Likes",
    description=(
        "Page the current likers of a need, update or event, most recent first. Each liker is "
        "named and pictured by userprofile role: a provider (role 1) by businessname and "
        "serviceimageurl, a member (role 2) by contact firstname and profileimage. Deleted "
        "accounts are left out of both the list and the total."
    ),
    responses={
        200: {"description": "Likes retrieved"},
        422: {"description": "postid missing"},
        500: {"description": "The read failed"},
    },
)
async def list_community_post_likes(
    postid: str = Query(..., min_length=1, description="needid, updateid or eventid of the post"),
    page: int = Query(1, ge=1, description="Page number for pagination (starts from 1)"),
    limit: int = Query(20, ge=1, le=100, description="Rows per page (default 20, max 100)"),
    _: str = Depends(verify_token),
) -> CommunityLikeListResponse:
    """Handle GET /api/mytown/community/likes: page a post's likers, most recent first."""
    try:
        result = await list_community_likes(postid=postid.strip(), page=page, limit=limit)
    except Exception as exc:
        logger.error("COMMUNITY | LIKES_LIST_FAILED | postid=%s | %s", postid, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not read the likes on this post.",
        )

    return CommunityLikeListResponse(
        success=True,
        postid=postid.strip(),
        count=len(result["likes"]),
        total=result["total"],
        page=page,
        limit=limit,
        total_pages=result["total_pages"],
        likes=result["likes"],
    )


@router.post(
    "/community/view",
    response_model=CommunityViewResponse,
    status_code=200,
    summary="Record Views Of Community Posts",
    description=(
        "Record that userprofileid viewed one or more posts, sent as post_type: a list of "
        "{type, id} items. Each item of type need, update or event whose post exists and is not "
        "deleted adds one view to the post's view_count. view_type onclick (the post was opened) "
        "counts on every call; view_type public (the default, the post was shown in the feed) "
        "counts at most once per userprofileid per post per IST day, so repeating it the same "
        "day is harmless. Items of any other type, or with an id that is not a post of that type, "
        "are skipped with view_count null. Returns each item's view count after the call; "
        "GET /community returns the same view_count on every post."
    ),
    responses={
        200: {"description": "Views recorded"},
        422: {"description": "userprofileid or post_type missing, an item without type or id, or view_type not onclick or public"},
        500: {"description": "The views could not be saved"},
    },
)
async def view_community_posts(
    request: CommunityViewRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_token),
) -> CommunityViewResponse:
    """Handle POST /api/mytown/community/view: count each post's view, public ones once per viewer per IST day, and return the counts."""
    try:
        result = await record_community_views(
            userprofileid=request.userprofileid,
            posts=[p.model_dump() for p in request.post_type],
            view_type=request.view_type,
        )
    except Exception as exc:
        logger.error(
            "COMMUNITY | VIEW_FAILED | user=%s | items=%d | %s",
            request.userprofileid, len(request.post_type), exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Your views could not be saved. Please try again.",
        )

    background_tasks.add_task(sync_post_viewcounts_to_dataverse, result["posts"])
    return CommunityViewResponse(
        success=True,
        userprofileid=request.userprofileid,
        view_type=request.view_type,
        view_date=result["view_date"],
        counted=result["counted"],
        posts=result["posts"],
        message=f"{result['counted']} view(s) recorded.",
    )


@router.post(
    "/community/comment",
    response_model=CommunityCommentResponse,
    status_code=200,
    summary="Add, Edit Or Delete A Community Comment",
    description=(
        "action_mode=add comments on a posted need, update or event (post_type, postid and comment "
        "required; post_type is stored on the comment, and the post's owner is pushed a "
        "notification unless they commented on their own post). "
        "action_mode=update edits and action_mode=delete soft-deletes one of the caller's own "
        "comments (commentid required). Returns the post's live comment count after the change."
    ),
    responses={
        200: {"description": "Comment saved"},
        403: {"description": "The comment belongs to someone else"},
        404: {"description": "No such post, or no such comment"},
        422: {"description": "A field the action needs is missing"},
        500: {"description": "The comment could not be saved"},
    },
)
async def comment_community_post(
    request: CommunityCommentRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_token),
) -> CommunityCommentResponse:
    """Handle POST /api/mytown/community/comment: add, edit or soft-delete a comment and return the new count."""
    action = request.action_mode
    try:
        result = await save_community_comment(
            action=action,
            post_type=request.post_type or None,
            postid=request.postid or None,
            commentid=request.commentid or None,
            userprofileid=request.userprofileid,
            contactid=request.contactid or None,
            comment=request.comment or None,
        )
    except Exception as exc:
        logger.error(
            "COMMUNITY | COMMENT_FAILED | action=%s | postid=%s | commentid=%s | user=%s | %s",
            action, request.postid, request.commentid, request.userprofileid, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Your comment could not be saved. Please try again.",
        )

    refusals = {
        "post_not_found": (status.HTTP_404_NOT_FOUND, "No such post."),
        "comment_not_found": (status.HTTP_404_NOT_FOUND, "No such comment."),
        "not_owner": (status.HTTP_403_FORBIDDEN, "This comment belongs to another member."),
    }
    if result["outcome"] in refusals:
        code, detail = refusals[result["outcome"]]
        raise HTTPException(status_code=code, detail=detail)

    saved = result["comment"]
    background_tasks.add_task(
        sync_community_comment_to_dataverse,
        saved or {"commentid": request.commentid, "deleted": "true"},
    )
    if result["outcome"] == "added" and settings.NOTIFICATIONS_GOVERNANCE_ENABLED:
        background_tasks.add_task(
            notify_community_comment, request.post_type, result["postid"], request.userprofileid
        )
    return CommunityCommentResponse(
        success=True,
        action_mode=action,
        commentid=saved["commentid"] if saved else request.commentid,
        postid=result["postid"],
        comment_count=result["comment_count"],
        comment=saved,
        message={"added": "Comment added.", "updated": "Comment updated.", "deleted": "Comment deleted."}[
            result["outcome"]
        ],
    )


@router.get(
    "/community/comments",
    response_model=CommunityCommentListResponse,
    status_code=200,
    summary="List A Community Post's Comments",
    description=(
        "Page the live comments on a need, update or event, newest first. Each commenter is "
        "named and pictured by userprofile role: a provider (role 1) by businessname and "
        "serviceimageurl, a member (role 2) by contact firstname and profileimage. Deleted "
        "comments and deleted accounts are left out of both the list and the total."
    ),
    responses={
        200: {"description": "Comments retrieved"},
        422: {"description": "postid missing"},
        500: {"description": "The read failed"},
    },
)
async def list_community_post_comments(
    postid: str = Query(..., min_length=1, description="needid, updateid or eventid of the post"),
    page: int = Query(1, ge=1, description="Page number for pagination (starts from 1)"),
    limit: int = Query(20, ge=1, le=100, description="Rows per page (default 20, max 100)"),
    _: str = Depends(verify_token),
) -> CommunityCommentListResponse:
    """Handle GET /api/mytown/community/comments: page a post's live comments, newest first."""
    try:
        result = await list_community_comments(postid=postid.strip(), page=page, limit=limit)
    except Exception as exc:
        logger.error("COMMUNITY | COMMENTS_LIST_FAILED | postid=%s | %s", postid, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not read the comments on this post.",
        )

    return CommunityCommentListResponse(
        success=True,
        postid=postid.strip(),
        count=len(result["comments"]),
        total=result["total"],
        page=page,
        limit=limit,
        total_pages=result["total_pages"],
        comments=result["comments"],
    )


@router.post(
    "/community/review",
    response_model=CommunityReviewResponse,
    status_code=200,
    summary="Receive A Review Decision On A Community Update Or Event",
    description=(
        "Receives an admin's review decision on an update or event. The body is postid (the "
        "updateid or eventid), post_type (update or event, picking the table postid is looked up "
        "in) and status, a free-text field. Nothing is written to Postgres or "
        "Dataverse: the review column is maintained outside this API. The response echoes the "
        "decision received alongside the review state currently stored on the post. When "
        "status is approved (any case), the update's or event's poster is pushed that it is live in its "
        "town's community, and, if the post is posted, the providers covering its town and the "
        "members within about 10 km of it are pushed that it is new; each person gets each push "
        "at most once per post. Needs are not reviewed."
    ),
    responses={
        200: {"description": "Review decision received"},
        404: {"description": "No post of this post_type has this id"},
        422: {"description": "postid, post_type or status missing or not recognised"},
        500: {"description": "The post could not be read"},
    },
)
async def review_community_post(
    request: CommunityReviewRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_token),
) -> CommunityReviewResponse:
    """Handle POST /api/mytown/community/review: acknowledge the decision and push the poster of an approved update or event."""
    logger.info(
        "COMMUNITY | REVIEW_RECEIVED | post_type=%s | postid=%s | status=%s",
        request.post_type, request.postid, request.status,
    )
    try:
        result = await find_reviewed_post(postid=request.postid, post_type=request.post_type)
    except Exception as exc:
        logger.error(
            "COMMUNITY | REVIEW_FAILED | post_type=%s | postid=%s | status=%s | %s",
            request.post_type, request.postid, request.status, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The post could not be read.",
        )

    if not result:
        logger.info(
            "COMMUNITY | REVIEW_NOT_FOUND | post_type=%s | postid=%s",
            request.post_type, request.postid,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {request.post_type} with id {request.postid}.",
        )

    stored = result["review_status"]
    notify_poster = request.status.lower() == "approved" and not result["deleted"]
    notify_town = notify_poster and result["status"] == "posted"
    if notify_poster:
        background_tasks.add_task(notify_post_approved, result["post_type"], request.postid)
    if notify_town:
        background_tasks.add_task(notify_post_live_in_town, result["post_type"], request.postid)
    logger.info(
        "COMMUNITY | REVIEW_ACKNOWLEDGED | post_type=%s | postid=%s | received=%s | stored=%s | "
        "poster_notified=%s | town_notified=%s",
        result["post_type"], request.postid, request.status, stored, notify_poster, notify_town,
    )
    return CommunityReviewResponse(
        success=True,
        postid=result["postid"],
        post_type=result["post_type"],
        review_status=request.status,
        stored_review_status=stored,
        visible_in_feed=stored == "approved" and result["status"] == "posted" and not result["deleted"],
        poster_notified=notify_poster,
        town_notified=notify_town,
        message=f"Review decision '{request.status}' received for the {result['post_type']}.",
    )
