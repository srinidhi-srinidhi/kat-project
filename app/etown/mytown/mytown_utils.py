"""MyTown Utilities - Postgres logic for town discovery, collections, town views and community needs.

Domain layer behind /api/mytown: resolves and searches towns, serves suggestions and verified providers, records town views, manages collections and favourite towns with a Dataverse mirror, assigns providers to plan-capped towns, and stores town photos and community needs, updates and events.

Features:
    - City search: Intelligent city name parsing and token extraction
    - Geographic lookup: Google Maps-first coordinate-based city resolution, nearest Etown town when the place is not one
    - Provider discovery: Service provider search within towns
    - Verified provider optimization: Fast (<1s) verified provider retrieval with specialized queries
    - Town presence: Availability checking for cities and areas
    - User tracking: Validated user profile tracking with normalized IDs
    - View counting: Every town fetch is recorded with dedicated table structure and hybrid sync/async tracking
    - Daily view counting: Repeated views by the same user on the same day each increment the cumulative count
    - Collection management: Organized provider groupings by category
    - City descriptions: AI-generated city information and metadata
    - State mapping: Indian state code to name conversions
    - Metro city identification: Major city classification and handling
    - Area verification: Validated geographic boundaries
    - Cache optimization: HTTP client reuse and data caching
    - City deduplication: SQL-level unique town names across the whole result set, keeping highest provider_count
    - Multi-town coverage: a provider holds one home town plus plan-capped coverage towns, discoverable in each
    - Real town photos: the best Google photo at TOWN_PHOTO_MIN_RELEVANCE (default 80) is stored as the card; generation only fills the gap
    - Wide photo search: town, attractions and landmark Places queries near the town's coordinates, distant places dropped
    - Town photo moderation: only approved uploads are public; a member still sees their own at any state
    - Town image requests: stored as pending under the exact filename; a filename already pending or already the town's image is refused
    - Community needs: a requirement posted at a town reaches every provider covering it
    - Community post types: needs, updates and events live in etown_need, etown_update and etown_event, each keyed by its own id
    - Multi-town posts: one row per town, grouped by poster, created_at and status; is_sameuser reads show one item with all its towns
    - Community likes and comments: etown_communitylike and etown_communitycomment, counted on every feed item
    - Community views: etown_communityview logs every onclick view and one public view per viewer per IST day; each bumps view_count
    - Community review: updates and events reach the feed once their review column is approved in Dataverse
    - Event status: events store upcoming, live or completed in IST, to the minute from start and end time; a wrong one is fixed on read
    - Review-pending exclusion: providers awaiting review are held out of MyTown reads
    - Bounded attachments: up to 5 images (no size cap) or videos (15MB), each with an optional thumbnail
    - Private drafts: status='draft' is a row the town feed excludes, returned only to its owner
    - Retry-safe publish: one owner-guarded UPDATE, so a double-tap cannot fan out to providers twice
    - Community search: pg_trgm fuzzy match of half the words on title, description, poster name and details
    - New posts since last visit: etown_community_lastvisit keeps each user's last feed visit per post type in IST

Classes:
    Database: MyTown-specific database operations for geographic data
    NeedValidationError: A posted need attachment breaks the type, size or count rules

Functions:
    normalize_user_profile_id(): Normalize user IDs for consistent tracking
    is_valid_user_profile_id(): Validate user profile ID format
    extract_city_tokens(): Parse and clean city search terms
    _format_provider_suggestions(): Structure provider data for API responses
    _fetch_provider_collection_map(): Read live which of the caller's collections hold each listed provider
    _get_cached_collections_for_suggestions(): Cache a user's collections for the suggestions endpoint
    _get_cached_city_for_coordinates(): Cache coordinate -> city reverse-lookup results
    _get_cached_provider_count_by_city(): Cache provider/verified/unverified counts for a city and optional area
    _get_cached_town_profile_by_name(): Cache a town's profile metadata by name
    _safe_city_lookup(): Bound the coordinate->city lookup latency for suggestions
    _wait_for_result(): Await a coroutine with a timeout, returning a fallback instead of stalling
    _build_mytown_request_cache_key(): Build the stable cache key for a suggestions request
    _get_last_good_mytown_response(): Fetch the last known-good cached suggestions response
    _set_last_good_mytown_response(): Store a suggestions response as the last known-good fallback
    _is_sparse_mytown_response(): Detect an effectively empty suggestions response
    _schedule_city_description_generation(): Kick off (once per city) background AI description generation
    _normalize_city_candidate(): Trim/validate a candidate city-name string
    _resolve_city_from_phase1_data(): Resolve the response's city from the fastest available phase-1 source
    handle_suggestions(): Main suggestion endpoint logic, trimmed to the lists a dashboard or insidetown type needs
    _track_city_view(): Hybrid sync/async view tracking with accuracy guarantee
    _track_city_view_background(): Background view tracking wrapper
    _fetch_city_image_background(): Background Google Places photo fetch for a city
    get_mytown_keywords(): Generate autocomplete keywords for a search term, optionally city-filtered
    handle_search(): City, area, and provider search functionality
    handle_collections(): Provider collection management (single provider / single collection / list)
    _organize_collections_data(): Assemble collection + item + service rows into the response shape
    handle_check_town_presence(): Town availability verification
    handle_get_all_verified_areas(): Verified area listing
    _generate_and_save_city_description(): Generate and persist an AI city description in the background
    _get_cached_google_maps_data(): Cached Google Maps API responses
    _load_metro_cities(): Metro city data loading from JSON
    handle_verified_providers(): Fast verified provider retrieval with optimized performance
    handle_town_management(): Coordinate-based town resolution
    handle_crud_collections(): Create/update/delete a collection (Postgres + Dataverse mirror)
    _normalize_favourite(): Normalise an isFavourite input to the canonical Postgres text
    _normalize_is_mytown(): Convert numeric MyTown flags to the API's Yes/No values
    handle_crud_collectionitems(): Add/update/soft-delete providers in a collection (Postgres + Dataverse mirror)
    handle_favourite_town(): Favourite/unfavourite a town by actionmode (Postgres + Dataverse mirror)
    handle_town_view_count(): Fetch town view counts for a specific date, paginated
    is_metro_place(): Whether a name identifies a metro, deciding town granularity
    handle_provider_town_assignment(): provider-to-town assignment, capped by the plan's max_towns
    handle_provider_coverage_towns(): add or drop a list of coverage towns (the home town is not removable)
    prune_coverage_towns_to_plan(): drop the coverage towns a downgraded plan no longer covers
    handle_promote_area_to_town(): give an area its own town profile and move its providers across
    handle_upload_town_image(): Store one or more member-uploaded town photos in blob storage and Postgres
    handle_category_pin(): Pin or unpin a category in a town and area, or at a popular-providers location
    handle_request_town_image(): Upload one base64 town image and record it in etown_townreview as pending
    handle_verify_town_image(): Approve or reject an uploaded town photo (Postgres + CRM mirror)
    normalize_verification(): Reduce a stored verification value to pending/approved/rejected
    fetch_google_town_reference(): Find the most relevant real Google photo of a town within a time budget
    _fetch_google_town_reference(): Multi-query Places search, distance filter and batched relevance judging behind that helper
    _judge_town_reference_candidate(): Download one Places photo and return the vision judge's verdict and relevance
    update_town_profile_image(): Write a generated card URL onto etown_townprofile.townimage
    generate_town_card(): Ground, enhance and generate one town listing-card image
    schedule_town_card_generation(): Kick off (once per town) background card generation for a new town
    _generate_and_store_town_card(): Background body writing the card to Postgres and the CRM
    _canonical(): Fold image/jpg into image/jpeg and mov/3gp into video/mp4 for content-type comparison
    upload_need_attachments():Decode, validate and upload a need's base64 files and thumbnails, returning their metadata
    event_status(): Upcoming, live or completed for an event's start and end in IST
    refresh_event_statuses(): Move every stale, not yet completed event to its current event_status
    correct_event_statuses(): Fix listed upcoming or live events whose event_status is wrong at the current IST time
    store_event_statuses(): Store corrected event statuses on etown_event rows still upcoming or live
    _post_columns(): Columns a post type's table stores besides its id
    _sibling_sql(): SQL matching the undeleted town copies of a post row
    _group_lead_gate(): AND clause keeping one row per group of town copies
    _post_select(): One post table's SELECT in the shared feed shape, with all its towns, the owner's, or only the row's own
    create_posts(): Insert one need, update or event per town in one statement and return the stored rows
    _fuzzy_search_sql(): Wrap a feed SELECT with a per-post matched flag and match_score for a search
    list_needs(): Page a town's posted posts, a member's drafts, or own needs one item per post, newest or best match first
    get_community_post(): Read one need, update or event by id in the feed shape, hiding drafts and unapproved posts from non-owners
    record_community_visit(): Upsert a user's last community visit time in IST for the post types read
    get_community_town_header(): Read a town's image, provider count and view count for the community feed
    publish_post(): Flip one of a member's own drafts and its town copies to posted in whichever table holds it
    edit_or_delete_post(): Edit the fields and towns of, or soft-delete, a member's own post across its town copies
    find_reviewed_post(): Read an update or event's status and stored review state by id, without writing
    respond_to_need(): Store a need response and send the poster the need's title and the message in Messages
    list_need_responses(): Page each responder's latest response to a need with name and image
    _review_status_sql(): SQL mapping a review column to pending, approved or rejected
    _approved_gate(): AND clause hiding an update or event until its review column is approved
    _person_columns(): Role, name and image columns for a person, by userprofile role
    _person_joins(): Userprofile, contact and provider joins behind _person_columns
    _with_ist(): Convert every datetime in a row to IST
    _is_live_post(): Whether a post_type's table holds a posted, undeleted post with an id
    set_community_like(): Like or dislike a post once per userprofile and return the new count
    list_community_likes(): Page a post's current likers with name and image
    save_community_comment(): Add, edit or soft-delete a comment on a post
    record_community_views(): Count every onclick view and one public view per user per IST day, returning the counts
    list_community_comments(): Page a post's live comments with commenter name and image
    _row_to_need(): Map an etown_need row to the API shape, decoding attachments and rendering IST

Dependencies:
    - asyncio: Asynchronous geographic operations
    - unicodedata: Punctuation stripping when normalising a community search
    - pg_trgm: word_similarity() behind the community search
    - uuid: UUID generation for new collection/collection-item/favourite-town records
    - async_lru: Caching decorator for performance optimization
    - fastapi: HTTPException raised on invalid input/coordinates across the handlers
    - app.db.client: Database base class and with_retry decorator backing the Database subclass
    - app.agent_infrastructure.infrastructure.llm_clients: gemini_2_5_flash_vision judging town reference photos
    - app.core.logging: Centralized logger used throughout the handlers and DB methods
    - app.agent_infrastructure: AI services for city descriptions
    - app.core.config: Application settings and API keys
    - app.schemas.api: Geographic API response models
    - app.etown.services: Town detail integration
    - app.etown.plans.plans_utils: Subscription plan resolution and the max_towns cap
    - app.utils.http_client: Tracked HTTP client with performance monitoring
    - app.utils.blob_storage: base64 decoding, mime sniffing, upload and public-URL building for need attachments
    - app.utils.ist_clock.IST: the single source of truth for the +05:30 offset on need timestamps
    - app.etown.mytown.mytown_syncing: Dataverse mirror for collections, towns and town images
    - app.etown.provider.provider_review_utils: review_pending_exclusion SQL fragment
    - app.etown.feed.feed_utils: CIRCLE_VISIBILITY_SQL for the collections profile feed
    - app.agent_infrastructure.tools.tools_utils.generate_image: town card image generation
    - app.etown.dataverse_sync.dataverse_sync_utils: refresh_content_vectors after town changes
    - app.etown.message.message_utils: MessageService conversations and sends behind need responses

Updates:
    23-Feb-2026: Enhanced documentation with complete feature inventory
    25-Feb-2026: Added coordinate and user profile validation, a normalized view tracking table; fixed the view-count race.
    02-Mar-2026: Switched to the tracked HTTP client; added handle_verified_providers() with get_verified_providers_optimized.
    03-Mar-2026: Added city deduplication to remove duplicate entries in favourite_cities and available_cities
    20-Apr-2026: Direct Google Maps lookup for coordinate city resolution; reordered Google component priority.
    25-Jun-2026: Favourite towns also appear in available_cities with isfavourite=True.
    13-Jul-2026: Added handle_crud_collectionitems() with Dataverse mirror; normalised isFavourite 0/1 to text.
    14-Jul-2026: Added handle_favourite_town - actionmode favourite upsert or unfavourite.
    16-Jul-2026: Quoted the mixed-case isMytown column in the create path.
    29-Jul-2026: City dedup, empty-town filter and ordering moved into SQL.
    10-Aug-2026: nearby_areas keeps the area named after its own city.
    18-Aug-2026: Plan max_towns cap on town assignment; coverage towns; badge fields on suggestions.
    19-Aug-2026: The collections profile feed applies the circle_only test against the viewer.
    20-Aug-2026: Provider payloads expose Etown_providertype; verified markers report the paid tier's badge label.
    25-Aug-2026: Batched coverage town add/drop; available_cities and favourite_cities carry townprofileid.
    26-Aug-2026: Logged silent exception handlers with tracebacks; rewrote vague log messages.
    27-Aug-2026: MyTown city lists include bulk-loaded town image metadata.
    01-Sep-2026: Vision-verified Google photo town cards; relative town-photo urls; handle_provider_town_assignment() takes sync_link.
    02-Sep-2026: Inactive providers excluded from MyTown reads; nearest-town fallback.
    03-Sep-2026: Ladder-based town assignment; area-to-town promotion; coverage towns pruned to plan.
    08-Sep-2026: handle_upload_town_image() stores image batches; /mytown_suggestions city block carries townprofileid.
    11-Sep-2026: Town view tracking records every fetch; optional per-image captions on town images.
    15-Sep-2026: Absorbed needs_utils - community needs, town feed, drafts and publish.
    17-Sep-2026: Providers awaiting review are held out of MyTown lists, suggestions and area rollups.
    21-Sep-2026: Review-pending exclusion extended to provider details and MyTown keywords.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    05-Oct-2026: Needs, updates and events stored in their own tables with a location name; needs add budget; events add schedule, venue, organizer and entry info; poster name and image follow the poster's role; likes and comments with counts on the feed; likes store likemodename Like or Dislike; likes and comments take post_type from the caller; updates and events show once their review column is approved; the feed returns per-type counts.
    06-Oct-2026: list_needs reads only one member's own needs when same_user is set; added edit_or_delete_post; needs store need_status; added respond_to_need and list_need_responses; list_needs skips posts whose contact is deleted; own-post reads count each need's unique responders; added find_reviewed_post for the update and event review callback; list_needs takes a fuzzy search; own-need reads count open and closed needs; added get_community_post.
    07-Oct-2026: Added record_community_visit to store a provider's last community visit in IST; list_needs takes needs_only; attachments are uncapped images or 15MB videos with uncapped optional thumbnails, sent without a filename; community posts carry the viewer's mycircle flag and invitationstatus; list_needs takes exclude_contactid.
    08-Oct-2026: Added get_community_town_header for the community feed's town image, provider count and view count; added handle_request_town_image storing pending etown_townreview requests; removed LangSmith tracing.
    09-Oct-2026: available_cities includes every town with providers within 100 km; handle_request_town_image stores the exact filename and refuses a pending duplicate or the town's current image.
    12-Oct-2026: /mytown_suggestions city card falls back to the nearest town with providers when the location is not an Etown town; added record_community_views; feed items carry view_count; handle_suggestions takes suggestion_type dashboard or insidetown and no longer fetches town images; Google town photos need TOWN_PHOTO_MIN_RELEVANCE relevance from a multi-query, coordinate-biased search.
    14-Oct-2026: create_post became create_posts, inserting one row per town in a single statement.
    16-Oct-2026: /mytown_suggestions providers carry Etown_category, derived as in popular_providers; added a categories list from etown_category for the town or area; category_ids returns only the providers list, filtered to any of those categories; added handle_category_pin; categories carry the caller's Etown_pinned; handle_category_pin takes a pin string, "true" or "false"; the favourites category lists providers in the caller's collections; categories always open with All and Favourites; /search providers carry Etown_category and also match by category name; categories return categoryimage as a public blob URL; for type=insidetown the city block carries new_need_count, new_update_count and new_event_count for the cityname since the caller's last community visit.
    20-Oct-2026: Events store event_status, set on create and on a schedule edit; added event_status and refresh_event_statuses; record_community_visit stores any user's last visit per post type; new post counts read each type's own visit time and are 0 for the guest profile; handle_suggestions takes language, and ta returns categories' displayname_ta.
    21-Oct-2026: /mytown_suggestions city providecount, verified_count and unverified_count follow areaname and are computed when the town is not in available_cities; same_user reads return one item per post with all its towns, other reads all towns on the viewer's own posts and the row's own town otherwise; publish_post, edit_or_delete_post act on every town copy and edits move, add or remove towns; get_user_favourite_towns dedupes first and counts providers once per town, without retry, under a 4s timeout; provider collection fields come from a live fetch_provider_collection_links lookup; event_status starts at start_date plus start_time and ends at end_time when set; added correct_event_statuses and store_event_statuses for community reads.
    26-Oct-2026: handle_category_pin takes action_mode; popular_provider pins at latitude and longitude in etown_popular_category_pin; handle_suggestions dashboard town images use a current townimage_compressed; provider and saved-provider suggestions add Etown_slideshow from etown_provider.image_stack.
    27-Oct-2026: same_user reads and counts the member's own needs, updates and events; create_posts stores review pending on updates and events; review pending or 0 reads as pending; feed items carry update_status for updates instead of review_status, and an unapproved event's event_status is its review state; /collections provider details and collection items add Etown_category.

Notes:
    Supports multilingual city names (Tamil, Hindi, English). Uses regex
    patterns for intelligent city name cleaning. Includes comprehensive
    Indian state mapping and metro city identification. View tracking uses
    hybrid approach: synchronous for first-time users (accuracy), asynchronous
    for repeat users (performance).
"""

import asyncio
import math
import re
import uuid
import base64
import random
import unicodedata
import zlib
from typing import Optional, Dict, List, Any, Union, Tuple, Sequence
from fastapi import HTTPException
from datetime import date, datetime, time as clock_time, timedelta, timezone
import httpx
from asyncpg.exceptions import UniqueViolationError
from async_lru import alru_cache
from collections import defaultdict
import json
from pathlib import Path
import time
from functools import lru_cache

from app.core.logging import logger
from app.db.client import Database, with_retry
from app.etown.provider.provider_review_utils import review_pending_exclusion
from app.etown.message.message_utils import MessageDatabase, MessageService
from app.schemas.message import CreateConversationRequest, SendMessageRequest

_EXCLUDE_REVIEW_PENDING_P = review_pending_exclusion("p.providerid")
_EXCLUDE_REVIEW_PENDING_BARE = review_pending_exclusion("etown_provider.providerid")
_EXCLUDE_REVIEW_PENDING_AND_P = "AND " + _EXCLUDE_REVIEW_PENDING_P
from app.utils.blob_storage import (
    upload_blob_bytes,
    current_timestamp_prefix,
    detect_mime_type,
    normalize_container_name,
    to_bytes,
    to_public_blob_url,
    to_relative_blob_url,
)
from app.utils.ist_clock import IST
from app.utils.http_client import get_http_client
from app.agent_infrastructure.infrastructure.llm_clients import gpt41, gemini_2_5_flash_vision
from app.agent_infrastructure.prompt_templates.templates import (
    generate_city_description_prompt,
    TOWN_REFERENCE_VISION_PROMPT,
)
from app.core.config import settings
from app.etown.services.services_utils import get_town_details, generate_towndetails_logic
from app.etown.feed.feed_utils import CIRCLE_VISIBILITY_SQL
from app.etown.plans.plans_utils import record_refusal, PlanLimitError, premium_display, resolve_plan, resolve_provider_badges
from app.schemas.api import (
    MyTownResponse, 
    CitySuggestion, 
    AreaSuggestion, 
    ProviderSuggestion,
    CategorySuggestion,
    TownPresenceResponse,
    VerifiedAreasResponse,
    CityItem,
    CollectionItem,
    MyTownSearchResponse,
    TownSearchResponse,
)

_ETOWN_WORD = re.compile(r"^(?:etown|thetown|e[-_. ]?town|etwon|e[-_. ]?twon)$", re.IGNORECASE)
_ETOWN_PREFIX = re.compile(r"^(?:etown|thetown|etwon|e[-_. ]?town|e[-_. ]?twon)+", re.IGNORECASE)

def normalize_user_profile_id(user_profile_id: str) -> str:
    """Return a user profile id trimmed and lowercased, or an empty string when falsy."""
    if not user_profile_id:
        return ""
    return user_profile_id.strip().lower()


def is_valid_user_profile_id(user_profile_id: str) -> bool:
    """Return True when a user profile id is a non-empty string other than null/none/0/undefined/nil."""
    if not user_profile_id or not isinstance(user_profile_id, str):
        return False
        
    normalized_id = user_profile_id.strip()
    invalid_ids = {'null', 'none', '0', '', 'undefined', 'nil'}
    if normalized_id.lower() in invalid_ids:
        return False
    if len(normalized_id) < 1:
        return False
    return True

STATE_CODE_TO_NAME_MAP = {
    "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh", "AS": "Assam", "BR": "Bihar",
    "CG": "Chhattisgarh", "GA": "Goa", "GJ": "Gujarat", "HR": "Haryana", "HP": "Himachal Pradesh",
    "JH": "Jharkhand", "KA": "Karnataka", "KL": "Kerala", "MP": "Madhya Pradesh", "MH": "Maharashtra",
    "MN": "Manipur", "ML": "Meghalaya", "MZ": "Mizoram", "NL": "Nagaland", "OR": "Odisha",
    "PB": "Punjab", "RJ": "Rajasthan", "SK": "Sikkim", "TN": "Tamil Nadu", "TG": "Telangana",
    "TR": "Tripura", "UP": "Uttar Pradesh", "UK": "Uttarakhand", "WB": "West Bengal",
    "AN": "Andaman and Nicobar Islands", "CH": "Chandigarh", "DNHDD": "Dadra and Nagar Haveli and Daman and Diu",
    "DL": "Delhi", "JK": "Jammu and Kashmir", "LA": "Ladakh", "LD": "Lakshadweep", "PY": "Puducherry"
}

MAX_CACHE_SIZE = 1000
CACHE_TTL_SECONDS = 300
HTTP_TIMEOUT = 5.0
DB_QUERY_TIMEOUT = 15.0
GOOGLE_MAPS_TIMEOUT_SECONDS = 4.0

COLLECTIONS_CACHE_TTL_SECONDS = 45
CITY_LOOKUP_CACHE_TTL_SECONDS = 300
CITY_STATS_CACHE_TTL_SECONDS = 120
MYTOWN_CITY_LOOKUP_TIMEOUT_SECONDS = 5.0
MYTOWN_CITY_DETAILS_TIMEOUT_SECONDS = 1.0
MYTOWN_CITY_COORD_PRECISION = 3
MYTOWN_AVAILABLE_CITIES_TIMEOUT_SECONDS = 4.0
MYTOWN_PROVIDERS_TIMEOUT_SECONDS = 4.0
MYTOWN_CATEGORIES_RADIUS_KM = 25.0
FAVOURITES_CATEGORY_NAME = "favourites"
ALL_CATEGORY_NAME = "all"
MYTOWN_AREAS_PHASE_TIMEOUT_SECONDS = 3.0
MYTOWN_FAVOURITES_TIMEOUT_SECONDS = 4.0
MYTOWN_COLLECTIONS_TIMEOUT_SECONDS = 2.0
MYTOWN_RESCUE_TIMEOUT_SECONDS = 8.0
MYTOWN_LAST_GOOD_CACHE_TTL_SECONDS = 180
TOWN_DEDUP_RADIUS_KM = 20.0
TOWN_FALLBACK_MAX_KM = 50.0
TOWN_ABSORB_RADIUS_KM = 10.0
TOWN_ABSORB_RADIUS_METRO_KM = 2.0

VERIFICATION_STATES = ("pending", "approved", "rejected")


def is_metro_place(*names: Optional[str]) -> bool:
    """Return True when any of the given town, city or district names is listed in metro_cities.json."""
    metro_cities = _load_metro_cities()
    return any(name and str(name).strip().lower() in metro_cities for name in names)


def normalize_verification(value: Any) -> str:
    """Map a stored verification word or 0/1/2 choice number to a VERIFICATION_STATES value, defaulting to pending."""
    text = str(value if value is not None else "").strip().lower()
    numeric = {"0": "pending", "1": "approved", "2": "rejected"}
    if text in numeric:
        return numeric[text]
    return text if text in VERIFICATION_STATES else "pending"


_TOWN_MATCH_PREDICATE = """
    townname IS NOT NULL
    AND (
          lower(btrim(townname)) = lower(btrim({name}))
       OR (
            etown_town_canon(townname) <> ''
            AND etown_town_canon(townname) = etown_town_canon({name})
            AND (
                  etown_town_distance_km(townlatitude, townlongitude, {lat}::text, {lon}::text) <= {radius}
               OR (
                    etown_town_distance_km({lat}::text, {lon}::text, {lat}::text, {lon}::text) IS NULL
                    AND (
                          SELECT count(*) FROM etown_townprofile x
                          WHERE x.townname IS NOT NULL
                            AND etown_town_canon(x.townname) = etown_town_canon({name})
                        ) = 1
                  )
                )
          )
    )
"""

_CITY_DESC_TASKS: Dict[str, asyncio.Task] = {}
_MYTOWN_LAST_GOOD_CACHE: Dict[Tuple[Any, ...], Dict[str, Any]] = {}


def extract_city_tokens(raw: str) -> List[str]:
    """Split a raw search string into city tokens, dropping trailing digits and etown prefixes."""
    s = (raw or "").strip().lower()
    s = re.sub(r"[_\-.]+", " ", s)
    tokens = re.split(r"\s+", s)
    out: List[str] = []
    for t in tokens:
        if not t:
            continue
        t = re.sub(r"\d+$", "", t)
        if not t:
            continue
        if _ETOWN_WORD.match(t):
            continue
        t = _ETOWN_PREFIX.sub("", t).strip()
        if not t:
            continue
        out.append(t)
    return out

def _format_provider_suggestions(provider_rows: List[Dict], provider_collection_map: Dict[str, Dict[str, set]] = None, badge_map: Dict[str, Dict[str, Optional[str]]] = None) -> List[ProviderSuggestion]:
    """Convert provider rows into deduplicated ProviderSuggestion objects with favourite, collection and badge fields."""
    providers_list = []
    seen_provider_ids = set()
    
    for row in provider_rows:
        p_id = row.get('providerid') or row.get('Etown_providerid') or row.get('userprofileid')
        if p_id is not None and p_id in seen_provider_ids:
            continue
        if p_id is not None:
            seen_provider_ids.add(p_id)
        
        created_on = row.get('creationdate')
        created_iso = created_on.isoformat() + 'Z' if isinstance(created_on, datetime) else None
        
        p_id = row.get('providerid') or row.get('Etown_providerid') or row.get('userprofileid') 
        
        is_fav = None
        col_ids = None
        col_unic_ids = None
        col_item_ids = None
        
        if provider_collection_map and p_id in provider_collection_map:
            is_fav = "Favourite"
            data = provider_collection_map[p_id]
            if data.get('ids'):
                col_ids = ",".join(sorted(list(data['ids'])))
            if data.get('unic_ids'):
                col_unic_ids = ",".join(sorted(list(data['unic_ids'])))
            if data.get('item_ids'):
                col_item_ids = ",".join(sorted(list(data['item_ids'])))

        badge = (badge_map or {}).get(row.get('userprofileid')) or {}

        formatted_provider = {
            "Etown_providerid": row.get('providerid'),
            "Etown_userprofileid": row.get('userprofileid'),
            "Etown_role": 1,
            "Etown_rolename": "Provider",
            "Etown_description": row.get('description'),
            "Etown_firstname": row.get('businessname'),
            "Etown_contactid": row.get('contactid'),
            "Etown_isconnected": False,
            "Etown_mobilenumber": row.get('mobilephone'),
            "Etown_email": row.get('email'),
            "Etown_profileimage": row.get('Etown_profileimage') or row.get('serviceimageurl'),
            "Etown_serviceimageurl": row.get('Etown_serviceimageurl') or row.get('serviceimageurl'),
            "Etown_background_imageUrl": row.get('background_image'),
            "Etown_slideshow": list(row.get('image_stack') or []),
            "Etown_offerimageurl": row.get('etown_offerimageurl_source'),
            "Etown_createdon": created_iso,
            "Etown_address": row.get('address'),
            "Etown_areaname": row.get('areaname'),
            "Etown_statecode": row.get('statecode'),
            "Etown_city": row.get('city'),
            "Etown_latitude": str(row.get('latitude')) if row.get('latitude') is not None else None,
            "Etown_longitude": str(row.get('longitude')) if row.get('longitude') is not None else None,
            "Etown_publicsearchprovider": row.get('publicsearchprovider'),
            "Etown_memberbookingcount": int(row.get('memberbookingcount', 0)),
            "Etown_ratingcount": int(row.get('ratingcount', 0)),
            "Etown_overallrating": float(row.get('overallrating', 0.0)),
            "Etown_total_members": int(row.get('total_members', 0)),
            "Etown_distance_km": float(row.get('distance_km')) if row.get('distance_km') is not None else None,
            'Etown_resultType': 1,
            "Etown_isfavourite": is_fav,
            "Etown_collectionid": col_ids,
            "Etown_uniquecollectionid": col_unic_ids,
            "Etown_collectionitemsid": col_item_ids,
            "Etown_providertype": badge.get("provider_type") or row.get('providertype'),
            "Etown_badge_label": badge.get("badge_label"),
            "Etown_category": row.get('etown_category'),
        }
            
        providers_list.append(ProviderSuggestion(**formatted_provider))
    return providers_list

async def _fetch_provider_collection_map(
    contact_id: Optional[str], userprofile_id: Optional[str], provider_rows: List[Dict]
) -> Dict[str, Dict[str, set]]:
    """Map each listed provider id to the caller's collection ids, unique collection ids and item ids holding it, read live."""
    provider_ids = list({str(r.get('providerid')) for r in provider_rows if r.get('providerid')})
    if not contact_id or not userprofile_id or not provider_ids:
        return {}
    links = await _wait_for_result(
        Database.fetch_provider_collection_links(contact_id, userprofile_id, provider_ids),
        MYTOWN_COLLECTIONS_TIMEOUT_SECONDS,
        [],
        "MyTown provider collection links query",
    )
    mapping: Dict[str, Dict[str, set]] = {}
    for link in links or []:
        entry = mapping.setdefault(str(link['providerid']), {'ids': set(), 'unic_ids': set(), 'item_ids': set()})
        if link.get('collectionid'):
            entry['ids'].add(str(link['collectionid']))
        if link.get('collectionunicid'):
            entry['unic_ids'].add(str(link['collectionunicid']))
        if link.get('collectionitemsid'):
            entry['item_ids'].add(str(link['collectionitemsid']))
    return mapping


@alru_cache(maxsize=256, ttl=COLLECTIONS_CACHE_TTL_SECONDS)
async def _get_cached_collections_for_suggestions(
    contact_id: str,
    userprofile_id: str,
    page: int,
    limit: int,
) -> List[Dict[str, Any]]:
    """Return the user's collections for suggestions, cached briefly, or an empty list."""
    result = await handle_collections(contact_id, userprofile_id, page=page, limit=limit)
    return result if isinstance(result, list) else []


@alru_cache(maxsize=1024, ttl=CITY_LOOKUP_CACHE_TTL_SECONDS)
async def _get_cached_city_for_coordinates(latitude: float, longitude: float) -> Optional[str]:
    """Return the city name for coordinates, cached."""
    return await Database.get_city_from_coordinates(latitude, longitude)


@alru_cache(maxsize=1024, ttl=CITY_STATS_CACHE_TTL_SECONDS)
async def _get_cached_provider_count_by_city(
    city_name: str, areaname: Optional[str] = None, contact_id: Optional[str] = None, userprofile_id: Optional[str] = None
) -> Dict[str, int]:
    """Return total, verified and unverified provider counts for a city and optional area, without the caller's own, cached."""
    result = await Database.get_provider_count_by_city(city_name, areaname, contact_id, userprofile_id)
    return result if isinstance(result, dict) else {'provider_count': 0, 'verified_count': 0, 'unverified_count': 0}


@alru_cache(maxsize=1024, ttl=CITY_STATS_CACHE_TTL_SECONDS)
async def _get_cached_town_profile_by_name(town_name: str) -> Optional[Dict[str, Any]]:
    """Return the town profile row for a town name, cached."""
    return await Database.get_town_profile_by_name(town_name)


async def _safe_city_lookup(latitude: float, longitude: float) -> Optional[str]:
    """Return the cached city for rounded coordinates, or None on timeout or error."""
    rounded_lat = round(latitude, MYTOWN_CITY_COORD_PRECISION)
    rounded_lon = round(longitude, MYTOWN_CITY_COORD_PRECISION)
    try:
        return await asyncio.wait_for(
            _get_cached_city_for_coordinates(rounded_lat, rounded_lon),
            timeout=MYTOWN_CITY_LOOKUP_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "City lookup timed out after %.2fs for %.6f, %.6f",
            MYTOWN_CITY_LOOKUP_TIMEOUT_SECONDS,
            latitude,
            longitude,
        )
        return None
    except Exception as e:
        logger.warning(f"City lookup failed for {latitude}, {longitude}: {e}")
        return None


async def _wait_for_result(coro, timeout_seconds: float, fallback: Any, label: str) -> Any:
    """Await a coroutine with a timeout, returning the fallback on timeout or error."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning("%s timed out after %.2fs", label, timeout_seconds)
        return fallback
    except Exception as e:
        logger.warning("%s failed: %s", label, e)
        return fallback


def _build_mytown_request_cache_key(
    latitude: float,
    longitude: float,
    areaname: Optional[str],
    cityname: Optional[str],
    contact_id: Optional[str],
    userprofile_id: Optional[str],
    verified: Optional[bool],
    page: int,
    limit: int,
    suggestion_type: Optional[str] = None,
    category_ids: Optional[List[str]] = None,
    language: str = "en",
) -> Tuple[Any, ...]:
    """Build a normalized cache key tuple for a MyTown suggestions request."""
    return (
        round(float(latitude), MYTOWN_CITY_COORD_PRECISION),
        round(float(longitude), MYTOWN_CITY_COORD_PRECISION),
        str(areaname or "").strip().lower(),
        str(cityname or "").strip().lower(),
        str(contact_id or "").strip().lower(),
        str(userprofile_id or "").strip().lower(),
        bool(verified),
        int(page),
        int(limit),
        str(suggestion_type or "").strip().lower(),
        ",".join(sorted(str(c).strip().lower() for c in category_ids or [])),
        str(language or "en").strip().lower(),
    )


def _get_last_good_mytown_response(cache_key: Tuple[Any, ...]) -> Optional[MyTownResponse]:
    """Return the cached last good MyTown response for a key if fresh and still valid, else None."""
    entry = _MYTOWN_LAST_GOOD_CACHE.get(cache_key)
    if not entry:
        return None

    age = time.time() - entry.get("ts", 0)
    if age > MYTOWN_LAST_GOOD_CACHE_TTL_SECONDS:
        _MYTOWN_LAST_GOOD_CACHE.pop(cache_key, None)
        return None

    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None

    try:
        return MyTownResponse(**payload)
    except Exception:
        logger.warning("mytown: cached response no longer matches MyTownResponse; ignoring it", exc_info=True)
        return None


def _set_last_good_mytown_response(cache_key: Tuple[Any, ...], response: MyTownResponse) -> None:
    """Store a MyTown response as the last good one for a key, evicting the oldest past 500 entries."""
    if len(_MYTOWN_LAST_GOOD_CACHE) > 500:
        oldest_key = min(_MYTOWN_LAST_GOOD_CACHE, key=lambda k: _MYTOWN_LAST_GOOD_CACHE[k].get("ts", 0))
        _MYTOWN_LAST_GOOD_CACHE.pop(oldest_key, None)

    _MYTOWN_LAST_GOOD_CACHE[cache_key] = {
        "ts": time.time(),
        "payload": response.model_dump(mode="json"),
    }


def _is_sparse_mytown_response(response: MyTownResponse) -> bool:
    """Return True when a MyTown response has no lists and an empty or unknown city."""
    city_name = ""
    if response.city and response.city.name:
        city_name = response.city.name.strip().lower()

    no_lists = (
        not response.nearby_areas
        and not response.verified_providers
        and not response.categories
        and not response.available_cities
        and not response.favourite_cities
        and not response.favourite_collection
        and not response.saved_providers
    )

    return no_lists and city_name in {"", "unknown"}


def _schedule_city_description_generation(city_name: str) -> None:
    """Start a background city description task unless one is already running for that city."""
    if not city_name:
        return

    key = city_name.strip().lower()
    existing = _CITY_DESC_TASKS.get(key)
    if existing and not existing.done():
        return

    task = asyncio.create_task(_generate_and_save_city_description(city_name))
    _CITY_DESC_TASKS[key] = task

    def _cleanup(_task: asyncio.Task, city_key: str = key) -> None:
        _CITY_DESC_TASKS.pop(city_key, None)

    task.add_done_callback(_cleanup)


def _normalize_city_candidate(value: Optional[str]) -> Optional[str]:
    """Return a stripped non-empty string, or None for blanks and non-strings."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _resolve_city_from_phase1_data(
    city_lookup_result: Optional[str],
    available_cities_data: List[Dict[str, Any]],
    nearby_areas_raw: List[Dict[str, Any]],
    verified_providers_raw: List[Dict[str, Any]],
) -> tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """Return (city, matching town row, source) from the lookup, available cities, areas or providers, in that order."""
    resolved_city = _normalize_city_candidate(city_lookup_result)
    if resolved_city:
        for city in available_cities_data:
            town_name = _normalize_city_candidate(city.get("townname"))
            if town_name and town_name.lower() == resolved_city.lower():
                return resolved_city, city, "city_lookup"
        return resolved_city, None, "city_lookup"

    for city in available_cities_data:
        town_name = _normalize_city_candidate(city.get("townname"))
        provider_count = int(city.get("provider_count", 0) or 0)
        if town_name and provider_count > 0:
            return town_name, city, "available_cities"

    for area in nearby_areas_raw:
        area_city = _normalize_city_candidate(area.get("city")) or _normalize_city_candidate(area.get("city_name"))
        if area_city:
            return area_city, None, "nearby_areas"

    for provider in verified_providers_raw:
        provider_city = _normalize_city_candidate(provider.get("city")) or _normalize_city_candidate(provider.get("Etown_city"))
        if provider_city:
            return provider_city, None, "verified_providers"

    return None, None, None

async def handle_suggestions(
    latitude: float, longitude: float, areaname: Optional[str] = None, cityname: Optional[str] = None, 
    contact_id: Optional[str] = None, userprofile_id: Optional[str] = None, verified: Optional[bool] = False, page: int = 1, limit: int = 20,
    suggestion_type: Optional[str] = None, category_ids: Optional[List[str]] = None, language: str = "en"
) -> MyTownResponse:
    """Build the MyTown suggestions response, limiting the lists to those a dashboard or insidetown type needs."""
    start_time = time.time()
    phase1_duration = 0.0
    collections_wait_duration = 0.0
    city_details_duration = 0.0
    formatting_duration = 0.0
    logger.info(f"Handler: Suggestions for lat={latitude}, lon={longitude}, type={suggestion_type}, category_ids={category_ids}")
    offset = (page - 1) * limit
    provided_city = cityname.strip() if isinstance(cityname, str) and cityname.strip() else None
    is_dashboard = suggestion_type == "dashboard"
    is_insidetown = suggestion_type == "insidetown"
    request_cache_key = _build_mytown_request_cache_key(
        latitude, longitude, areaname, cityname, contact_id, userprofile_id, verified, page, limit, suggestion_type, category_ids, language
    )

    if category_ids:
        category_providers_raw = await _wait_for_result(
            Database.get_providers_by_distance_prioritized(
                latitude, longitude, areaname, cityname, contact_id, userprofile_id, verified, limit, offset, category_ids
            ),
            MYTOWN_PROVIDERS_TIMEOUT_SECONDS,
            [],
            "MyTown category providers query",
        )
        category_providers_raw = category_providers_raw if isinstance(category_providers_raw, list) else []
        category_collection_map, category_badge_map = await asyncio.gather(
            _fetch_provider_collection_map(contact_id, userprofile_id, category_providers_raw),
            resolve_provider_badges([row.get('userprofileid') for row in category_providers_raw]),
        )
        logger.info(
            "Handle suggestions: category_ids=%s returned %d providers in %.2fs",
            category_ids, len(category_providers_raw), time.time() - start_time,
        )
        return MyTownResponse(
            verified_providers=_format_provider_suggestions(
                category_providers_raw, category_collection_map, category_badge_map
            ),
            available_cities=[],
        )

    if cityname and userprofile_id:
        asyncio.create_task(_track_city_view_background(cityname, userprofile_id))
    
    collections_task = asyncio.create_task(
        _get_cached_collections_for_suggestions(contact_id, userprofile_id, page, limit)
        if contact_id and userprofile_id else asyncio.sleep(0, result=[])
    )

    phase1_start = time.time()
    phase1_tasks = {
        "available_cities": _wait_for_result(
            Database.get_available_cities_by_distance(
                latitude, longitude, contact_id, userprofile_id, limit, offset
            ),
            MYTOWN_AVAILABLE_CITIES_TIMEOUT_SECONDS,
            [],
            "MyTown available cities query",
        ),
        "favourite_towns": _wait_for_result(
            Database.get_user_favourite_towns(contact_id, userprofile_id, latitude, longitude),
            MYTOWN_FAVOURITES_TIMEOUT_SECONDS,
            [],
            "MyTown favourite towns query",
        ) if contact_id and userprofile_id else asyncio.sleep(0, result=[]),
        "providers": _wait_for_result(
            Database.get_providers_by_distance_prioritized(
                latitude, longitude, areaname, cityname, contact_id, userprofile_id, verified, limit, offset, category_ids
            ),
            MYTOWN_PROVIDERS_TIMEOUT_SECONDS,
            [],
            "MyTown providers query",
        ) if not is_dashboard else asyncio.sleep(0, result=[]),
        "categories": _wait_for_result(
            Database.get_categories_by_location(
                latitude, longitude, areaname, cityname, contact_id, userprofile_id, verified
            ),
            MYTOWN_PROVIDERS_TIMEOUT_SECONDS,
            [],
            "MyTown categories query",
        ) if not is_dashboard else asyncio.sleep(0, result=[]),
        "city_lookup": asyncio.sleep(0, result=provided_city) if provided_city else _safe_city_lookup(latitude, longitude),
        "areas": _wait_for_result(
            Database.get_areas_prioritized(
                latitude, longitude, contact_id, userprofile_id, limit, offset,
                city_name=provided_city, skip_city_lookup=not bool(provided_city)
            ),
            MYTOWN_AREAS_PHASE_TIMEOUT_SECONDS,
            [],
            "MyTown areas query",
        ) if not is_dashboard else asyncio.sleep(0, result=[]),
    }

    phase1_keys = list(phase1_tasks.keys())
    phase1_values = list(phase1_tasks.values())
    phase1_results = await asyncio.gather(*phase1_values, return_exceptions=True)
    phase1_duration = time.time() - phase1_start
    phase1_map = dict(zip(phase1_keys, phase1_results))

    city_result_raw = phase1_map.get("city_lookup")
    available_cities_raw = phase1_map.get("available_cities")
    favourite_towns_raw = phase1_map.get("favourite_towns")
    verified_providers_raw_task = phase1_map.get("providers")

    nearby_areas_raw = phase1_map.get("areas") if isinstance(phase1_map.get("areas"), list) else []

    available_cities_data = available_cities_raw if isinstance(available_cities_raw, list) else []
    favourite_towns_data  = favourite_towns_raw  if isinstance(favourite_towns_raw,  list) else []
    verified_providers_raw = verified_providers_raw_task if isinstance(verified_providers_raw_task, list) else []
    nearby_areas = [AreaSuggestion(**a) for a in nearby_areas_raw] if isinstance(nearby_areas_raw, list) else []
    categories_raw = phase1_map.get("categories") if isinstance(phase1_map.get("categories"), list) else []
    categories = [
        CategorySuggestion(
            Etown_categoryid=c.get("categoryid"),
            Etown_categoryname=c["categoryname"],
            Etown_displayname=(language == "ta" and c.get("displayname_ta")) or c.get("displayname") or (
                "Other Services" if c["categoryname"] == "other" else c["categoryname"].replace("_", " ").title()
            ),
            Etown_categoryimage=to_public_blob_url(c.get("categoryimage")),
            Etown_providercount=int(c.get("provider_count") or 0),
            Etown_pinned=bool(c.get("ispinned")),
        )
        for c in categories_raw
    ]

    if not available_cities_data and not verified_providers_raw and not nearby_areas_raw:
        logger.warning(
            "MyTown phase1 returned empty payload for contact_id=%s userprofile_id=%s; running rescue retry",
            contact_id,
            userprofile_id,
        )
        rescue_available, rescue_providers, rescue_areas = await asyncio.gather(
            _wait_for_result(
                Database.get_available_cities_by_distance(
                    latitude, longitude, contact_id, userprofile_id, limit, offset
                ),
                MYTOWN_RESCUE_TIMEOUT_SECONDS,
                [],
                "MyTown rescue available cities query",
            ),
            _wait_for_result(
                Database.get_providers_by_distance_prioritized(
                    latitude, longitude, areaname, cityname, contact_id, userprofile_id, verified, limit, offset, category_ids
                ),
                MYTOWN_RESCUE_TIMEOUT_SECONDS,
                [],
                "MyTown rescue providers query",
            ) if not is_dashboard else asyncio.sleep(0, result=[]),
            _wait_for_result(
                Database.get_areas_prioritized(
                    latitude, longitude, contact_id, userprofile_id, limit, offset,
                    city_name=provided_city, skip_city_lookup=not bool(provided_city)
                ),
                MYTOWN_RESCUE_TIMEOUT_SECONDS,
                [],
                "MyTown rescue areas query",
            ) if not is_dashboard else asyncio.sleep(0, result=[]),
            return_exceptions=False,
        )

        if isinstance(rescue_available, list) and rescue_available:
            available_cities_data = rescue_available
        if isinstance(rescue_providers, list) and rescue_providers:
            verified_providers_raw = rescue_providers
        if isinstance(rescue_areas, list) and rescue_areas:
            nearby_areas_raw = rescue_areas
            nearby_areas = [AreaSuggestion(**a) for a in nearby_areas_raw]

    city_result, city_source_row, city_resolution_source = _resolve_city_from_phase1_data(
        city_result_raw if isinstance(city_result_raw, str) else provided_city,
        available_cities_data,
        nearby_areas_raw,
        verified_providers_raw,
    )

    if not city_source_row:
        known_town = await _wait_for_result(
            _get_cached_town_profile_by_name(city_result),
            MYTOWN_CITY_LOOKUP_TIMEOUT_SECONDS,
            True,
            "MyTown town profile check",
        ) if city_result else None
        if not known_town:
            nearest_towns = available_cities_data[:1] if offset == 0 else await _wait_for_result(
                Database.get_available_cities_by_distance(latitude, longitude, contact_id, userprofile_id, 1, 0),
                MYTOWN_AVAILABLE_CITIES_TIMEOUT_SECONDS,
                [],
                "MyTown nearest town query",
            )
            nearest_name = _normalize_city_candidate(nearest_towns[0].get("townname")) if nearest_towns else None
            if nearest_name:
                logger.info("MyTown: %s is not an Etown town; showing nearest town %s", city_result, nearest_name)
                city_result, city_source_row, city_resolution_source = nearest_name, nearest_towns[0], "nearest_town"

    collections_wait_start = time.time()
    try:
        collections_raw = await asyncio.wait_for(
            collections_task,
            timeout=MYTOWN_COLLECTIONS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "MyTown collections query timed out after %.2fs for userprofile_id=%s",
            MYTOWN_COLLECTIONS_TIMEOUT_SECONDS,
            userprofile_id,
        )
        collections_task.cancel()
        collections_raw = []
    except Exception as e:
        logger.error(f"Collections task failed: {e}", exc_info=True)
        collections_raw = []
    collections_wait_duration = time.time() - collections_wait_start

    collections_data = collections_raw if isinstance(collections_raw, list) else []

    logger.info(
        "Got %s available cities and %s favourite towns. Resolved city=%s source=%s",
        len(available_cities_data),
        len(favourite_towns_data),
        city_result,
        city_resolution_source,
    )

    city_tasks = {}
    city_desc_obj = CitySuggestion(name="Unknown", townprofileid=None, description="", image="", apihitcount=0, viewcount=0, providecount=0, verified_count=0, unverified_count=0, isfavourite=False, favouritetownid=None)
    
    if isinstance(city_result, str):
        city_details_start = time.time()
        city_desc_obj.name = city_result
        if city_source_row:
            city_desc_obj.townprofileid = city_source_row.get('townprofileid') or None
            city_desc_obj.providecount = int(city_source_row.get('provider_count', 0) or 0)
            city_desc_obj.verified_count = int(city_source_row.get('verified_count', 0) or 0)
            city_desc_obj.unverified_count = int(city_source_row.get('unverified_count', 0) or 0)
            city_desc_obj.description = city_source_row.get('towndescription') or city_desc_obj.description
            city_desc_obj.image = city_source_row.get('townimage') or city_desc_obj.image
            city_desc_obj.apihitcount = int(city_source_row.get('api_hit_count', 0) or 0)
            city_desc_obj.viewcount = int(city_source_row.get('view_count', 0) or 0)
        actual_provider_counts = {
            'provider_count': city_desc_obj.providecount,
            'verified_count': city_desc_obj.verified_count,
            'unverified_count': city_desc_obj.unverified_count,
        }

        if (areaname and areaname.strip()) or not city_source_row:
            city_tasks["provider_counts"] = _wait_for_result(
                _get_cached_provider_count_by_city(city_result, areaname, contact_id, userprofile_id),
                MYTOWN_PROVIDERS_TIMEOUT_SECONDS,
                None,
                "MyTown city provider counts query",
            )

        needs_town_profile = not (
            city_source_row
            and city_desc_obj.description
            and city_desc_obj.image
        )
        
        try:
            city_result_map: Dict[str, Any] = {}
            if city_tasks:
                city_results = await asyncio.gather(*city_tasks.values(), return_exceptions=True)
                city_result_map = dict(zip(city_tasks.keys(), city_results))

            provider_counts_result = city_result_map.get("provider_counts")
            if isinstance(provider_counts_result, dict):
                actual_provider_counts = provider_counts_result

            town_profile_result = city_result_map.get("town_profile")
            town_profile = town_profile_result if isinstance(town_profile_result, dict) else None
            
            city_desc_obj.providecount = actual_provider_counts.get('provider_count', 0)
            city_desc_obj.verified_count = actual_provider_counts.get('verified_count', 0)
            city_desc_obj.unverified_count = actual_provider_counts.get('unverified_count', 0)
            
            if town_profile:
                city_desc_obj.apihitcount = int(town_profile.get('apihit', 0) or 0)
                
                town_profile_id = town_profile.get("townprofileid")
                if town_profile_id:
                    city_desc_obj.viewcount = int(town_profile.get("view_count", 0) or 0)
                else:
                    viewed_users = town_profile.get("vieweduserprofileid", "") or ""
                    if viewed_users.strip():
                        city_desc_obj.viewcount = len([u.strip() for u in viewed_users.split(',') if u.strip()])
                    else:
                        city_desc_obj.viewcount = 0
                        
                if town_profile.get("towndescription"):
                    city_desc_obj.description = town_profile.get("towndescription")
                if town_profile.get("townimage"):
                    city_desc_obj.image = town_profile.get("townimage")
            
            if not city_desc_obj.description:
                city_desc_obj.description = ""
                _schedule_city_description_generation(city_result)
                    
        except Exception as e:
            logger.warning(f"Failed to get city details for {city_result}: {e}")
        finally:
            city_details_duration = time.time() - city_details_start
        
        city_desc_obj.isfavourite = False
        city_desc_obj.favouritetownid = None

        if favourite_towns_data and isinstance(favourite_towns_data, list):
            favourite_towns_lookup = {
                town.get('townname', '').lower(): town.get('favouritetownid')
                for town in favourite_towns_data
                if town.get('townname') and town.get('favouritetownid')
            }

            city_lower = city_result.lower()
            if city_lower in favourite_towns_lookup:
                city_desc_obj.isfavourite = True
                city_desc_obj.favouritetownid = favourite_towns_lookup[city_lower]

    count_town = provided_city or (city_result if isinstance(city_result, str) else None)
    is_guest = bool(settings.GUEST_USER_PROFILE_ID) and (userprofile_id or "").strip() == settings.GUEST_USER_PROFILE_ID
    if is_insidetown and count_town and is_guest:
        city_desc_obj.new_need_count = city_desc_obj.new_update_count = city_desc_obj.new_event_count = 0
    elif is_insidetown and count_town:
        count_town_id = city_desc_obj.townprofileid if city_desc_obj.name.strip().lower() == count_town.lower() else None
        new_post_where = (
            "WHERE (n.townprofileid = $2::text OR ($2::text IS NULL AND lower(btrim(n.townname)) = lower(btrim($3::text)))) "
            "AND n.status = 'posted' AND COALESCE(n.deleted, 'false') <> 'true' "
            "AND n.userprofileid IS DISTINCT FROM $1::text AND n.created_at > s.{t}_at "
            "AND NOT EXISTS (SELECT 1 FROM public.etown_contact dc WHERE dc.contactid = n.contactid "
            "AND lower(btrim(COALESCE(dc.deleted, ''))) = 'true')"
        )
        new_post_counts = await _wait_for_result(
            Database.fetchrow(
                "WITH since AS (SELECT "
                + ", ".join(
                    f"COALESCE(v.{t}_visited_at, '-infinity'::timestamptz) AS {t}_at" for t in POST_TYPES
                )
                + " FROM (SELECT 1) one LEFT JOIN public.etown_community_lastvisit v "
                "ON v.userprofileid = $1::text) SELECT "
                + ", ".join(
                    f"(SELECT count(*) FROM public.{_POST_TABLES[t][0]} n {new_post_where.format(t=t)}"
                    f"{_approved_gate(t, 'n.review')})::int AS new_{t}_count"
                    for t in POST_TYPES
                )
                + " FROM since s",
                userprofile_id or "", count_town_id, count_town,
            ),
            MYTOWN_PROVIDERS_TIMEOUT_SECONDS,
            None,
            "MyTown new community post counts query",
        )
        if new_post_counts:
            city_desc_obj.new_need_count = int(new_post_counts["new_need_count"] or 0)
            city_desc_obj.new_update_count = int(new_post_counts["new_update_count"] or 0)
            city_desc_obj.new_event_count = int(new_post_counts["new_event_count"] or 0)

    favourite_collection = [CollectionItem(**c) for c in collections_data]
    saved_providers_dicts = []
    for col in collections_data:
        if str(col.get('Etown_isMytown', '')).lower() == 'yes':
            saved_providers_dicts.extend(col.get('items', []))
    saved_providers_dicts.sort(key=lambda x: x.get('Etown_item_createdon') or "", reverse=True)
    saved_providers = []
    for item in saved_providers_dicts:
        try: saved_providers.append(ProviderSuggestion(**item))
        except Exception:
            logger.debug("mytown: a saved-provider item did not match ProviderSuggestion; dropping it", exc_info=True)

    provider_map, badge_map = await asyncio.gather(
        _fetch_provider_collection_map(contact_id, userprofile_id, verified_providers_raw),
        resolve_provider_badges([row.get('userprofileid') for row in verified_providers_raw]),
    )
    verified_providers = _format_provider_suggestions(verified_providers_raw, provider_map, badge_map)

    formatting_start = time.time()
    favourite_towns_lookup = {}
    if favourite_towns_data:
        for fav_town in favourite_towns_data:
            fav_name = str(fav_town.get('townname', '')).strip().lower()
            if fav_name:
                favourite_towns_lookup[fav_name] = fav_town.get('favouritetownid')

    final_available_cities = []
    for city in available_cities_data:
        townname = str(city.get("townname") or "")
        provider_count = int(city.get("provider_count", 0) or 0)
        town_key = townname.strip().lower()
        is_fav = town_key in favourite_towns_lookup
        fav_town_id = favourite_towns_lookup.get(town_key)
        final_available_cities.append(CityItem(
            name=townname.strip(),
            distance_km=round(float(city.get("distance_km", 0)), 2) if city.get("distance_km") is not None else None,
            image=city.get("townimage") or "",
            provider_count=provider_count,
            verified_count=int(city.get("verified_count", 0) or 0),
            unverified_count=int(city.get("unverified_count", 0) or 0),
            latitude=str(city.get("townlatitude")).strip() if city.get("townlatitude") else None,
            longitude=str(city.get("townlongitude")).strip() if city.get("townlongitude") else None,
            description=city.get("towndescription") or "",
            favourite_count=int(city.get("favourite_count", 0) or 0),
            view_count=int(city.get("view_count", 0) or 0),
            api_hit_count=int(city.get("api_hit_count", 0) or 0),
            cityname=city.get("cityname"),
            townprofileid=city.get("townprofileid") or None,
            createdon=None,
            isfavourite=is_fav,
            favouritetownid=fav_town_id,
        ))
    final_favourite_cities = []
    for fav in favourite_towns_data:
        created_on = fav.get('createdon')
        created_iso = created_on.isoformat() + 'Z' if isinstance(created_on, datetime) else None
        dist = fav.get("distance_km")
        if dist is not None:
            try:
                dist = round(float(dist), 2)
            except (ValueError, TypeError):
                logger.debug("mytown: favourite town distance %r is not a number; dropping it", dist)
                dist = None
        sql_provider = int(fav.get("provider_count", 0) or 0)
        sql_verified = int(fav.get("verified_count", 0) or 0)
        final_favourite_cities.append(CityItem(
            name=fav.get("townname") or "",
            distance_km=dist,
            image=fav.get("townimage") or "",
            provider_count=sql_provider,
            verified_count=sql_verified,
            unverified_count=max(sql_provider - sql_verified, 0),
            latitude=str(fav.get("townlatitude")).strip() if fav.get("townlatitude") else None,
            longitude=str(fav.get("townlongitude")).strip() if fav.get("townlongitude") else None,
            townprofileid=fav.get("townprofileid") or None,
            favouritetownid=fav.get("favouritetownid") or None,
            createdon=created_iso,
            description=fav.get("towndescription") or "",
            view_count=int(fav.get("view_count", 0) or 0),
            api_hit_count=int(fav.get("api_hit_count", 0) or 0),
            favourite_count=int(fav.get("favourite_count", 0) or 0),
            cityname=fav.get("cityname") or None,
        ))

    if is_dashboard:
        dashboard_towns = [city_desc_obj, *final_available_cities, *final_favourite_cities]
        dashboard_town_ids = list({town.townprofileid for town in dashboard_towns if town.townprofileid})
        compressed_rows = await _wait_for_result(
            Database.fetch_once(
                "SELECT townprofileid, townimage_compressed FROM public.etown_townprofile "
                "WHERE townprofileid = ANY($1::text[]) "
                "AND townimage_compressed LIKE '%' || md5(townimage) || '.webp'",
                dashboard_town_ids,
            ),
            MYTOWN_CITY_DETAILS_TIMEOUT_SECONDS,
            [],
            "MyTown compressed town images query",
        ) if dashboard_town_ids else []
        compressed_images = {row["townprofileid"]: row["townimage_compressed"] for row in compressed_rows or []}
        for town in dashboard_towns:
            town.image = compressed_images.get(town.townprofileid) or town.image

    formatting_duration = time.time() - formatting_start

    elapsed_time = time.time() - start_time
    logger.info(
        "Handle suggestions timings | phase1=%.3fs collections=%.3fs city_details=%.3fs format=%.3fs total=%.3fs",
        phase1_duration,
        collections_wait_duration,
        city_details_duration,
        formatting_duration,
        elapsed_time,
    )
    logger.info(f"Handle suggestions completed in {elapsed_time:.2f}s")
    
    final_response = MyTownResponse(
        city=city_desc_obj,
        nearby_areas=nearby_areas,
        verified_providers=verified_providers,
        categories=categories,
        available_cities=final_available_cities,
        favourite_cities=[] if is_insidetown else final_favourite_cities,
        favourite_collection=[] if is_insidetown else favourite_collection,
        saved_providers=[] if is_insidetown else saved_providers
    )

    if _is_sparse_mytown_response(final_response):
        cached_response = _get_last_good_mytown_response(request_cache_key)
        if cached_response is not None:
            logger.warning(
                "Serving last-good MyTown response for contact_id=%s userprofile_id=%s due sparse live payload",
                contact_id,
                userprofile_id,
            )
            return cached_response
    else:
        _set_last_good_mytown_response(request_cache_key, final_response)

    return final_response

async def _track_city_view(cityname: str, userprofile_id: str, sync_mode: bool = False) -> tuple[bool, bool]:
    """Record a town view event for a user, returning (success, is_new_view).

    Args:
        cityname: Name of the town being viewed.
        userprofile_id: Viewing user profile id.
        sync_mode: True checks the profile exists in the DB; False only checks its format.

    Returns:
        (success, is_new_view); every successful record counts as a new view.
    """
    try:
        normalized_user_id = normalize_user_profile_id(userprofile_id)
        
        if sync_mode:
            if not await Database.validate_user_profile_exists(normalized_user_id):
                logger.warning(f"Tracking skipped - invalid userprofile_id: {userprofile_id}")
                return False, False
        elif not is_valid_user_profile_id(normalized_user_id):
            logger.warning(f"Tracking skipped - invalid format userprofile_id: {userprofile_id}")
            return False, False
            
        success, is_new_view = await Database.add_town_view_tracking(cityname, normalized_user_id)
        return success, is_new_view
        
    except Exception as e:
        logger.warning(f"City view tracking failed for {cityname}: {e}")
        return False, False

async def _track_city_view_background(cityname: str, userprofile_id: str):
    """Record a town view without the DB profile check, for use as a background task."""
    await _track_city_view(cityname, userprofile_id, sync_mode=False)

async def _fetch_city_image_background(city_name: str, city_obj):
    """Set city_obj.image to the first Google Places photo URL for the city, ignoring failures."""
    try:
        if not settings.google_maps_api_key:
            return
            
        api_key = settings.google_maps_api_key
        city_query = f"{city_name}, India"
        search_url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={city_query}&key={api_key}&language=en"
        
        async with get_http_client() as client:
            search_response = await client.get(search_url)
        search_data = search_response.json()
        
        if search_data.get('status') == 'OK' and search_data.get('results'):
            place = search_data['results'][0]
            if place.get('photos'):
                photo_ref = place['photos'][0]['photo_reference']
                city_obj.image = f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=400&photoreference={photo_ref}&key={api_key}"
    except Exception as e:
        logger.warning(f"Background city image fetch failed for {city_name}: {e}")

async def get_mytown_keywords(search_term: str, city_filter: Optional[str] = None, limit: int = 10) -> List[str]:
    """Return frequent words from provider names and categories that start with the search term, optionally within a town."""
    if not search_term or len(search_term.strip()) < 1:
        return []
        
    query_text = search_term.strip().lower()
    starts_with_pattern = f"{query_text}%"
    contain_pattern = f"%{query_text}%"
    
    base_select = """
    WITH individual_words AS (
        SELECT unnest(regexp_matches(lower(p.businessname), '[a-z0-9]+', 'g')) as word
        FROM etown_provider p
        WHERE p.businessname ILIKE $1
        """ + _EXCLUDE_REVIEW_PENDING_AND_P + """
        {city_condition}

        UNION ALL
        SELECT unnest(regexp_matches(lower(ps.businesscategory), '[a-z0-9]+', 'g')) as word
        FROM etown_providerservices ps
        INNER JOIN etown_provider p ON ps.providerid = p.providerid
        WHERE ps.businesscategory ILIKE $1
        """ + _EXCLUDE_REVIEW_PENDING_AND_P + """
        {city_condition}

        UNION ALL
        SELECT unnest(regexp_matches(lower(ps.servicecategory), '[a-z0-9]+', 'g')) as word
        FROM etown_providerservices ps
        INNER JOIN etown_provider p ON ps.providerid = p.providerid
        WHERE ps.servicecategory ILIKE $1
        """ + _EXCLUDE_REVIEW_PENDING_AND_P + """
        {city_condition}
    )
    SELECT word as keyword, count(*) as frequency, length(word) as word_length
    FROM individual_words
    WHERE word LIKE $2
    AND length(word) > 2
    AND word NOT IN ('and', 'the', 'with', 'for', 'from', 'etown')
    GROUP BY word
    ORDER BY count(*) DESC, length(word) ASC
    LIMIT $3
    """
    
    try:
        city_condition = ""
        if city_filter:
            city_condition = f" AND {Database._build_town_membership_sql('$4')}"
            params = [contain_pattern, starts_with_pattern, limit, city_filter]
        else:
            params = [contain_pattern, starts_with_pattern, limit]
            
        query = base_select.format(city_condition=city_condition)
        
        rows = await Database.fetch(query, *params)
        search_term_lower = search_term.strip().lower()
        keywords = [row['keyword'] for row in rows if row['keyword'].lower() != search_term_lower]
        
        logger.info(f"Generated {len(keywords)} keywords for '{search_term}' with city filter '{city_filter}'")
        return keywords
        
    except Exception as e:
        logger.error(f"Error generating MyTown keywords: {e}", exc_info=True)
        return []

async def handle_search(search_term: str, latitude: Optional[float] = None, longitude: Optional[float] = None, page: int = 1, limit: int = 20, city: Optional[str] = None) -> MyTownSearchResponse:
    """Search providers, areas, towns and keywords matching a term, optionally scoped to a town."""
    logger.info(f"Handler: Search term '{search_term}', city='{city}', lat={latitude}, lon={longitude}")
    offset = (page - 1) * limit
    clean_term = search_term.strip()
    clean_term_basic = re.sub(r'^etown[._-]?', '', clean_term, flags=re.IGNORECASE)
    clean_term_basic = re.sub(r'\d+$', '', clean_term_basic)
    search_pattern_basic = f"%{clean_term_basic}%"
    city_tokens = extract_city_tokens(clean_term)
    valid_city_tokens = [t for t in city_tokens if len(t) >= 4]

    normalized_city = city.strip().lower() if city and city.strip() else None

    if normalized_city:
        keywords_task = get_mytown_keywords(search_term, normalized_city, limit=10)
    else:
        keywords_task = get_mytown_keywords(search_term, None, limit=10)

    tasks = [
        Database.search_mytown_providers(search_pattern_basic, limit, offset, latitude, longitude, normalized_city),
        Database.search_mytown_areas(search_pattern_basic, limit, offset, normalized_city),
        keywords_task
    ]
    if not normalized_city and valid_city_tokens:
        tasks.append(Database.search_mytown_cities(valid_city_tokens, limit, offset))
    else:
        tasks.append(asyncio.sleep(0, result=[]))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    provider_rows = results[0] if isinstance(results[0], list) else []
    location_rows = results[1] if isinstance(results[1], list) else []
    keywords_list = results[2] if isinstance(results[2], list) else []
    city_rows = results[3] if isinstance(results[3], list) else []
    logger.info(f"Found {len(provider_rows)} providers, {len(location_rows)} areas, {len(city_rows)} cities, {len(keywords_list)} keywords")
    logger.info(f"Found {len(provider_rows)} providers, {len(location_rows)} areas, {len(city_rows)} cities")

    providers_list = _format_provider_suggestions(
        provider_rows,
        badge_map=await resolve_provider_badges(
            [row.get('userprofileid') for row in provider_rows]
        ),
    )
    
    location_list = []
    for row in location_rows:
        location_list.append(AreaSuggestion(
            area_name=row.get('areaname'),
            latitude=str(row.get('latitude')) if row.get('latitude') else None,
            provider_count=row.get('provider_count', 0),
            verified_count=row.get('verified_count', 0),
            unverified_count=row.get('unverified_count', 0),
            longitude=str(row.get('longitude')) if row.get('longitude') else None,
            city_name=row.get('city'),
            state_code=row.get('statecode')
        ))
    
    cities_list = []
    for city in city_rows:
         cities_list.append(CityItem(
            name=city.get("city") or "",
            distance_km=None,
            image=city.get("townimage") or "",
            provider_count=city.get("provider_count", 0),
            verified_count=city.get("verified_count", 0),
            unverified_count=city.get("unverified_count", 0),
            latitude=str(city.get("latitude")).strip() if city.get("latitude") is not None else None,
            longitude=str(city.get("longitude")).strip() if city.get("longitude") is not None else None,
            description=city.get("towndescription") or "",
            favourite_count=city.get("favourite_count", 0)
        ))

    return MyTownSearchResponse(
        providers=providers_list,
        areas=location_list,
        cities=cities_list,
        keywords=keywords_list
    )

async def handle_townsearch(
    search_term: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    contact_id: Optional[str] = None,
    userprofile_id: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
) -> TownSearchResponse:
    """Return a page of available towns whose names match, with typo-tolerant fallback."""
    clean_term = search_term.strip()
    if not clean_term:
        raise HTTPException(status_code=400, detail="search_term must not be empty.")

    async def fetch_towns(fuzzy_search: bool = False, page_number: Optional[int] = None):
        return await Database.get_available_cities_by_distance(
            latitude=latitude,
            longitude=longitude,
            contact_id=contact_id,
            userprofile_id=userprofile_id,
            search_term=clean_term,
            search_page=page if page_number is None else page_number,
            limit=limit,
            fuzzy_search=fuzzy_search,
        )

    towns = await fetch_towns()
    similar_results = False
    if not towns and len(clean_term) >= 3:
        has_direct_matches = (
            bool(await fetch_towns(page_number=1))
            if page > 1
            else False
        )
        if not has_direct_matches:
            towns = await fetch_towns(fuzzy_search=True)
            similar_results = bool(towns)

    favourite_towns = await (
        Database.get_user_favourite_towns(contact_id, userprofile_id, latitude, longitude)
        if contact_id and userprofile_id
        else asyncio.sleep(0, result=[])
    )

    favourite_ids = {
        str(town.get("townname") or "").strip().lower(): town.get("favouritetownid")
        for town in favourite_towns
    }

    townprofile_ids = [town.get("townprofileid") for town in towns if town.get("townprofileid")]
    town_images_map: Dict[str, List[Dict[str, Any]]] = {}
    if townprofile_ids:
        try:
            town_images_rows = await Database.fetch_once(
                "SELECT townprofileid, townimageurl, name, caption, createdon, verification, userprofileid "
                "FROM public.etown_townimages "
                "WHERE townprofileid = ANY($1) "
                "  AND (lower(btrim(COALESCE(verification, ''))) IN ('approved', '1') "
                "       OR ($2 <> '' AND userprofileid = $2))",
                townprofile_ids,
                userprofile_id or "",
            )
            for row in town_images_rows:
                createdon = row.get("createdon")
                town_images_map.setdefault(row["townprofileid"], []).append({
                    "imageurl": to_relative_blob_url(row.get("townimageurl")) or "",
                    "name": row.get("name") or "",
                    "caption": row.get("caption"),
                    "createdon": createdon.isoformat() if hasattr(createdon, "isoformat") else createdon,
                    "townprofileid": row.get("townprofileid"),
                    "verification": normalize_verification(row.get("verification")),
                    "is_own_upload": bool(userprofile_id) and row.get("userprofileid") == userprofile_id,
                })
        except Exception as e:
            logger.warning("Failed to fetch town images for town search: %s", e, exc_info=True)

    result = []
    for town in towns:
        town_name = str(town.get("townname") or "").strip()
        town_key = town_name.lower()
        result.append(CityItem(
            name=town_name,
            distance_km=round(float(town["distance_km"]), 2) if town.get("distance_km") is not None else None,
            image=town.get("townimage") or "",
            provider_count=int(town.get("provider_count", 0) or 0),
            verified_count=int(town.get("verified_count", 0) or 0),
            unverified_count=int(town.get("unverified_count", 0) or 0),
            latitude=str(town.get("townlatitude")).strip() if town.get("townlatitude") else None,
            longitude=str(town.get("townlongitude")).strip() if town.get("townlongitude") else None,
            description=town.get("towndescription") or "",
            favourite_count=int(town.get("favourite_count", 0) or 0),
            view_count=int(town.get("view_count", 0) or 0),
            api_hit_count=int(town.get("api_hit_count", 0) or 0),
            cityname=town.get("cityname"),
            townprofileid=town.get("townprofileid") or None,
            isfavourite=town_key in favourite_ids,
            town_images=town_images_map.get(town.get("townprofileid"), []),
        ))

    total = int(towns[0].get("total_count", 0) or 0) if towns else 0
    return TownSearchResponse(
        towns=result,
        page=page,
        limit=limit,
        total=total,
        total_pages=(total + limit - 1) // limit,
        similar_results=similar_results,
    )

async def handle_collections(
    contact_id: Optional[str] = None, 
    user_profile_id: Optional[str] = None, 
    collection_id: Optional[str] = None, 
    provider_id: Optional[str] = None, 
    collection_unique_id: Optional[str] = None,
    page: int = 1, 
    limit: int = 20
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Return a provider profile, a single collection, or the user's paged collections, depending on the ids given."""
    logger.info(f"Handler: Collections. User={user_profile_id}, Prov={provider_id}, Col={collection_id}")
    offset = (page - 1) * limit

    if collection_unique_id:
        resolved_collection_id = await Database.fetch_collection_id_by_unique_id(collection_unique_id)
        if resolved_collection_id:
            collection_id = resolved_collection_id
        else:
            logger.warning(f"Collection unique ID {collection_unique_id} not found.")
            return {}

    if provider_id:
        provider_details = await Database.fetch_single_provider_details(provider_id)
        if not provider_details:
            return {}

        p_contact_id = provider_details.get('contactid')
        p_user_profile_id = provider_details.get('userprofileid')
        p_premium_label = (
            (await resolve_provider_badges([p_user_profile_id])).get(p_user_profile_id) or {}
        ).get("premium_label")

        tasks = {
            "services": Database.fetch_services_bulk([provider_id]),
            "collections": Database.fetch_collections_list(contact_id, user_profile_id, limit=100, offset=0),
            "feeds": Database.fetch_provider_feeds(p_user_profile_id, limit=limit, offset=offset, viewer_contact_id=contact_id) if p_user_profile_id else asyncio.sleep(0),
            "completed_reqs": Database.get_provider_completed_requests_count(p_user_profile_id) if p_user_profile_id else asyncio.sleep(0),
            "relations": Database.get_provider_relation_stats(contact_id, user_profile_id, p_contact_id, p_user_profile_id) if (p_contact_id and p_user_profile_id) else asyncio.sleep(0)
        }

        results = await asyncio.gather(*tasks.values())
        data = dict(zip(tasks.keys(), results))

        is_favourite = False
        collection_ids_str = ""
        unique_ids_str = ""
        
        collections_list = data["collections"]
        if collections_list and isinstance(collections_list, list):
            c_ids = [c['collectionid'] for c in collections_list]
            if c_ids:
                items = await Database.fetch_collection_items_bulk(c_ids)
                matched_cols = []
                col_map = {str(c['collectionid']): c for c in collections_list}
                
                for item in items:
                    if str(item.get('providerid')) == str(provider_id):
                        cid = str(item.get('collectionid'))
                        if cid in col_map:
                            matched_cols.append(col_map[cid])
                
                if matched_cols:
                    is_favourite = True
                    matched_c_ids = sorted(list(set(str(c['collectionid']) for c in matched_cols)))
                    matched_u_ids = sorted(list(set(str(c['collectionunicid']) for c in matched_cols if c.get('collectionunicid'))))
                    collection_ids_str = ",".join(matched_c_ids)
                    unique_ids_str = ",".join(matched_u_ids)

        formatted_services = []
        for svc in data["services"]:
            formatted_services.append({
                "Etown_providerservicesid": svc.get('providerservicesid'),
                "Etown_servicename": svc.get('servicename'),
                "Etown_businesscategory": svc.get('businesscategory'), 
                "Etown_servicecategory": svc.get('servicecategory'),   
                "Etown_serviceimageurl": svc.get('serviceimageurl'),
                "Etown_serviceimageurl1": svc.get('serviceimageurl1'),
                "Etown_serviceimageurl2": svc.get('serviceimageurl2'),
                "Etown_serviceimageurl3": svc.get('serviceimageurl3'),
                "Etown_pricerange": svc.get('pricerange'),
                "Etown_servicedescription": svc.get('servicedescription')
            })

        feed_details = []
        feeds_raw = data["feeds"]
        if isinstance(feeds_raw, list):
            ist_tz = timezone(timedelta(hours=5, minutes=30))
            for feed in feeds_raw:
                d = feed.get("createdon")
                fmt_time = ""
                if d and isinstance(d, datetime):
                    if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
                    fmt_time = d.astimezone(ist_tz).strftime("%d/%m/%Y %I:%M %p")
                
                feed_details.append({
                    "Etown_feedid": feed.get("feedid"),
                    "Etown_feedfilelocationurl": feed.get("feedfilelocationurl"),
                    "Etown_feedfilelocationurl2": feed.get("feedfilelocation2"),
                    "Etown_feedfilelocationurl3": feed.get("feedfilelocation3"),
                    "Etown_feedfilelocationurl4": feed.get("feedfilelocation4"),
                    "Etown_feedfilelocationurl5": feed.get("feedfilelocation5"),
                    "Etown_feedvideofilelocation": feed.get("feedvideofilelocation"),
                    "Etown_thumbnailfileurl": feed.get("thumbnailfileurl"),
                    "Etown_feedtext": feed.get("feedtext"),
                    "Etown_commentscount": str(feed.get("comments_count", 0)),
                    "Etown_feedlikecount": str(feed.get("likes_count", 0)),
                    "Etown_createdon": fmt_time
                })

        rel_data = data["relations"] if isinstance(data["relations"], dict) else {"is_connected": False, "connection_count": 0}
        completed_count = data["completed_reqs"] if isinstance(data["completed_reqs"], int) else 0

        created_date = provider_details.get('creationdate')
        fmt_created = created_date.strftime("%d/%m/%Y") if isinstance(created_date, datetime) else str(created_date)

        return {
            "Etown_providerid": provider_details.get('providerid'),
            "Etown_contactid": provider_details.get('contactid'),
            "Etown_userprofileid": provider_details.get('userprofileid'),
            "Etown_businessname": provider_details.get('businessname'),
            "Etown_firstname": provider_details.get('userprofileidname'),
            "Etown_description": provider_details.get('description'),
            "Etown_serviceimageurl": provider_details.get('serviceimageurl') or "null",
            "Etown_background_imageUrl": provider_details.get('background_image'),
            "Etown_address": provider_details.get('address'),
            "Etown_city": provider_details.get('city'),
            "Etown_statecode": provider_details.get('statecode'),
            "Etown_providertype": premium_display(provider_details.get('providertype'), p_premium_label),
            "Etown_category": provider_details.get('etown_category'),
            "Etown_publicsearchprovider": provider_details.get('publicsearchprovider'),
            "Etown_mobilenumber": provider_details.get('mobilenumber'),
            "Etown_email": provider_details.get('email'),
            "Etown_createdon": fmt_created,
            "Etown_overallrating": provider_details.get('overallrating'),
            "Etown_ratingcount": provider_details.get('ratingcount', 0),
            "Etown_memberbookingcount": provider_details.get('memberbookingcount', 0),
            "Etown_completedservices": int(completed_count),
            "Etown_mycircle": bool(rel_data["is_connected"]),
            "Etown_mycirclecount": int(rel_data["connection_count"]),
            "Etown_favouritecheck": bool(is_favourite),
            "Etown_collectionid": collection_ids_str,
            "Etown_uniquecollectionid": unique_ids_str,
            "services": formatted_services,
            "feeds": feed_details
        }

    if collection_id:
        meta = await Database.fetch_collection_metadata(collection_id)
        if not meta: return {}
        items = await Database.fetch_collection_items_bulk([collection_id])
        
        provider_ids = [i['providerid'] for i in items if i.get('providerid')]
        services = []
        if provider_ids:
            services = await Database.fetch_services_bulk(provider_ids)
            
        result_list = _organize_collections_data(
            [meta], items, services,
            await resolve_provider_badges(i.get('userprofileid') for i in items),
        )
        return result_list[0] if result_list else {}

    collections = await Database.fetch_collections_list(contact_id, user_profile_id, limit, offset)
    if not collections: return []
    
    collection_ids = [c['collectionid'] for c in collections]
    items = await Database.fetch_collection_items_bulk(collection_ids)
    
    provider_ids = list(set(i['providerid'] for i in items if i.get('providerid')))
    services = []
    if provider_ids:
        services = await Database.fetch_services_bulk(provider_ids)
        
    return _organize_collections_data(
        collections, items, services,
        await resolve_provider_badges(i.get('userprofileid') for i in items),
    )

async def handle_check_town_presence(townname: str, cityname: str, statecode: str, parsed_components: Optional[Dict[str, Any]] = None, latitude: Optional[float] = None, longitude: Optional[float] = None) -> TownPresenceResponse:
    """Check whether a town exists by name, else resolve its admin level 3 via components or Google geocoding and check that.

    Raises:
        HTTPException: 400 when townname or cityname is missing.
    """
    if not townname or not cityname:
        raise HTTPException(status_code=400, detail="Both town name and city name are required")

    logger.info(f"Handler: Checking town presence for townname='{townname}', cityname='{cityname}', state='{statecode}'")

    row = await Database.check_town_presence_in_db(townname, cityname)

    if row:
        return TownPresenceResponse(
            townnamepresent=True,
            administartivelevel3present=False,
            administartivelevel3value=None,
            townprofileid=row.get('townprofileid'),
            townname=row.get('townname'),
            cityname=row.get('cityname'),
            townstatus=row.get('townstatus')
        )

    admin_level_3_value = None
    if parsed_components and isinstance(parsed_components, dict):
        admin_level_3_value = parsed_components.get("administrative_area_level_3")

    if not admin_level_3_value and settings.google_maps_api_key:
        try:
            api_key = settings.google_maps_api_key
            query_address = f"{cityname}, {statecode}" if statecode else cityname
            url = f"https://maps.googleapis.com/maps/api/geocode/json?address={query_address}&key={api_key}"

            async with get_http_client() as client:
                response = await client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    results = data.get('results', [])
                    if results:
                        address_components = results[0].get('address_components', [])
                        for comp in address_components:
                            if 'administrative_area_level_3' in comp.get('types', []):
                                admin_level_3_value = comp.get('long_name')
                                break
        except Exception as e:
            logger.error(f"Error fetching Google Maps data for {cityname}: {e}", exc_info=True)

    if not admin_level_3_value and latitude is not None and longitude is not None:
        place = await _get_cached_google_maps_data(latitude, longitude)
        for comp in (place or {}).get("address_components", []):
            if "administrative_area_level_3" in comp.get("types", []):
                admin_level_3_value = comp.get("long_name")
                logger.info(
                    "check_town_presence: '%s' yielded no admin L3; reverse geocode of "
                    "(%s, %s) resolved '%s' for town '%s'",
                    cityname, latitude, longitude, admin_level_3_value, townname,
                )
                break

    if not admin_level_3_value:
        return TownPresenceResponse(
            townnamepresent=False,
            administartivelevel3present=False,
            administartivelevel3value=None,
            townprofileid=None,
            townname=None,
            townstatus=None
        )

    row = await Database.check_town_presence_in_db(admin_level_3_value, None)

    if row:
        return TownPresenceResponse(
            townnamepresent=False,
            administartivelevel3present=True,
            administartivelevel3value=admin_level_3_value,
            townprofileid=row.get('townprofileid'),
            townname=row.get('townname'),
            cityname=row.get('cityname'),
            townstatus=row.get('townstatus')
        )
    else:
        return TownPresenceResponse(
            townnamepresent=False,
            administartivelevel3present=False,
            administartivelevel3value=admin_level_3_value,
            townprofileid=None,
            townname=None,
            townstatus=None
        )

def _organize_collections_data(collections: List[Dict], items: List[Dict], services: List[Dict] = None, badge_map: Dict[str, Dict[str, Optional[str]]] = None) -> List[Dict]:
    """Assemble collection rows, their provider items and services into the Etown_ collections response list."""
    services_map = defaultdict(list)
    if services:
        for svc in services:
            pid = svc.get('providerid') or svc.get('userprofileid')
            if pid:
                services_map[pid].append({
                    "Etown_providerservicesid": svc.get('providerservicesid'),
                    "Etown_servicename": svc.get('servicename'),
                    "Etown_businesscategory": svc.get('businesscategory'), 
                    "Etown_servicecategory": svc.get('servicecategory'),   
                    "Etown_serviceimageurl": svc.get('serviceimageurl')
                })

    items_map = defaultdict(list)
    for item in items:
        cid = item.get('collectionid')
        if cid:
            created = item.get('creationdate')
            created_iso = created.isoformat() + 'Z' if isinstance(created, datetime) else str(created) if created else None
            item_created = item.get('item_createdon')
            item_created_iso = item_created.isoformat() + 'Z' if isinstance(item_created, datetime) else str(item_created) if item_created else None
            
            p_id = item.get('providerid')
            
            p_data = {
                "Etown_userprofileid": item.get('userprofileid'),
                "Etown_providerid": p_id,
                "Etown_isfavourite": item.get('isfavourite'),
                "Etown_collectionid": cid,
                "Etown_uniquecollectionid": item.get('collectionunicid'),
                "Etown_collectionitemsid": item.get('collectionitemsid'),
                "Etown_role": 1,
                "Etown_rolename": "Provider",
                "Etown_description": item.get('description'),
                "Etown_firstname": item.get('businessname'),
                "Etown_contactid": item.get('contactid'),
                "Etown_isconnected": False,
                "Etown_mobilenumber": item.get('mobilenumber'),
                "Etown_email": item.get('email'),
                "Etown_profileimage": item.get('serviceimageurl'),
                "Etown_serviceimageurl": item.get('serviceimageurl'),
                "Etown_background_imageUrl": item.get('background_image'),
                "Etown_slideshow": list(item.get('image_stack') or []),
                "Etown_createdon": created_iso,
                "Etown_item_createdon": item_created_iso,
                "Etown_address": item.get('address'),
                "Etown_statecode": item.get('statecode'),
                "Etown_city": item.get('city'),
                "Etown_areaname": item.get('areaname'),
                "Etown_latitude": str(item.get('latitude')) if item.get('latitude') else None,
                "Etown_longitude": str(item.get('longitude')) if item.get('longitude') else None,
                "Etown_publicsearchprovider": item.get('publicsearchprovider'),
                "Etown_providertype": premium_display(
                    item.get('providertype'),
                    ((badge_map or {}).get(item.get('userprofileid')) or {}).get("premium_label"),
                ),
                "Etown_category": item.get('etown_category'),
                "services": services_map.get(p_id, [])
            }
            items_map[cid].append(p_data)

    result = []
    for col in collections:
        cid = col.get('collectionid')
        created = col.get('createdon')
        created_iso = created.isoformat() + 'Z' if isinstance(created, datetime) else str(created) if created else None
        c_unic = col.get('collectionunicid')
        collection_items = items_map.get(cid, [])
        collection_items.sort(key=lambda x: x.get('Etown_item_createdon') or "", reverse=True)    
        for it in collection_items:
            it['Etown_uniquecollectionid'] = c_unic
        result.append({
            "Etown_collectionid": cid,
            "Etown_uniquecollectionid": c_unic,
            "Etown_collectionname": col.get('collectionname'),
            "Etown_collectiontype": col.get('collectiontype'),
            "Etown_isMytown": _normalize_is_mytown(col.get('isMytown')),
            "Etown_createdon": created_iso,
            "Etown_collectioncount": len(collection_items),
            "items": collection_items
        })
    return result

async def handle_get_all_verified_areas(
    latitude: Optional[float] = None, 
    longitude: Optional[float] = None, 
    contact_id: Optional[str] = None, 
    userprofile_id: Optional[str] = None, 
    page: int = 1,
    limit: int = 20
) -> VerifiedAreasResponse:
    """Return paged verified areas and nearby available towns for a location, raising 500 on failure."""
    logger.info(f"Handler: Verified Areas. Lat={latitude}, Lon={longitude}")
    try:
        offset = (page - 1) * limit
        
        area_task = Database.get_all_unique_verified_areas(latitude, longitude, contact_id, userprofile_id, limit=limit, offset=offset)
        cities_task = Database.get_available_cities_by_distance(latitude, longitude, contact_id, userprofile_id, limit=limit, offset=offset)
        
        area_results_raw, cities_results_raw = await asyncio.gather(area_task, cities_task)
        
        if isinstance(area_results_raw, Exception):
            raise area_results_raw
        area_results = [AreaSuggestion(**area) for area in area_results_raw]
        
        available_cities = []
        if not isinstance(cities_results_raw, Exception) and isinstance(cities_results_raw, list):
             for city in cities_results_raw:
                available_cities.append(CityItem(
                    name=city.get("townname"),
                    distance_km=round(float(city.get("distance_km", 0)), 2) if city.get("distance_km") else None,
                    image=city.get("townimage"),
                    provider_count=int(city.get("provider_count", 0)),
                    verified_count=int(city.get("verified_count", 0)),
                    unverified_count=int(city.get("unverified_count", 0)),
                    latitude=str(city.get("townlatitude")),
                    longitude=str(city.get("townlongitude")),
                    description=city.get("towndescription"),
                    favourite_count=int(city.get("favourite_count", 0)),
                ))
            
        return VerifiedAreasResponse(
            verified_areas=area_results,
            available_cities=available_cities
        )
        
    except Exception as e:
        logger.error(f"Error in handle_get_all_verified_areas: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

_METRO_CITIES_CACHE = set()
_CACHE_LOADED_TIME = 0

async def _generate_and_save_city_description(city_name: str) -> None:
    """Generate a town description with gpt41 and save it on the matching etown_townprofile row."""
    try:
        from app.db.client import Database
        prompt = generate_city_description_prompt(city_name)
        result = await gpt41.ainvoke(prompt)
        description = result.content.strip() if result and result.content else None
        
        if description:
            query = "UPDATE etown_townprofile SET towndescription = $1 WHERE TRIM(LOWER(townname)) = $2"
            await Database.execute(query, description, city_name.strip().lower())
            logger.info(f"Generated and saved AI description for city: {city_name}")
    except Exception as e:
        logger.error(f"Error generating description for {city_name}: {e}", exc_info=True)

@alru_cache(maxsize=MAX_CACHE_SIZE, ttl=CACHE_TTL_SECONDS)
async def _get_cached_google_maps_data(latitude: float, longitude: float) -> Optional[Dict]:
    """Return the first Google reverse-geocode result for coordinates, cached, or None."""
    if not settings.google_maps_api_key:
        return None
        
    try:
        api_key = settings.google_maps_api_key
        url = f"https://maps.googleapis.com/maps/api/geocode/json?latlng={latitude},{longitude}&key={api_key}&language=en"
        
        async with get_http_client() as client:
            response = await client.get(url)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "OK" and data.get("results"):
                return data["results"][0]
        return None
    except Exception as e:
        logger.error(f"Error fetching Google Maps data: {e}", exc_info=True)
        return None

@lru_cache(maxsize=1)
def _load_metro_cities():
    """Return the lowercased metro city names from metro_cities.json, cached."""
    global _METRO_CITIES_CACHE, _CACHE_LOADED_TIME
    current_time = time.time()

    if _METRO_CITIES_CACHE and (current_time - _CACHE_LOADED_TIME) < 1800:
        return _METRO_CITIES_CACHE
    
    try:
        file_path = Path(__file__).resolve().parent / "metro_cities.json"
        
        if file_path.exists():
            with open(file_path, "r", encoding='utf-8') as f:
                data = json.load(f)
                _METRO_CITIES_CACHE = {city.lower().strip() for city in data.get("metro_cities", [])}
                _CACHE_LOADED_TIME = current_time
                logger.info(f"Loaded {len(_METRO_CITIES_CACHE)} metro cities from cache.")
        else:
            logger.error(f"metro_cities.json not found at {file_path}")
    except Exception as e:
        logger.error(f"Error loading metro_cities.json: {e}", exc_info=True)
    
    return _METRO_CITIES_CACHE


async def handle_verified_providers(latitude: Optional[float] = None, longitude: Optional[float] = None, cityname: Optional[str] = None, page: int = 0, limit: int = 20, contact_id: Optional[str] = None, userprofile_id: Optional[str] = None) -> Dict[str, Any]:
    """Return a zero-based page of verified providers near a point or in a town, excluding the caller's own.

    Raises:
        HTTPException: 400 on invalid coordinates, 500 when the query fails.
    """
    logger.info(f"Starting verified providers request - City: {cityname}, Coords: {latitude}, {longitude}, Page: {page}, Limit: {limit}")
    
    if latitude is not None and longitude is not None:
        if (latitude == 0.0 and longitude == 0.0) or not (-90.0 <= latitude <= 90.0) or not (-180.0 <= longitude <= 180.0):
            raise HTTPException(status_code=400, detail="Invalid coordinates provided")
    
    offset = page * limit
    
    try:
        providers_data = await Database.get_verified_providers_optimized(
            latitude=latitude,
            longitude=longitude,
            cityname=cityname,
            limit=limit,
            offset=offset,
            contact_id=contact_id,
            userprofile_id=userprofile_id
        )
        
        providers = _format_provider_suggestions(
            providers_data,
            badge_map=await resolve_provider_badges(
                [row.get('userprofileid') for row in providers_data]
            ),
        )
        
        logger.info(f"Retrieved {len(providers)} verified providers")
        return {"providers": providers}
        
    except Exception as e:
        logger.error(f"Error handling verified providers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve verified providers")


async def handle_town_management(latitude: float, longitude: float) -> Dict[str, Optional[str]]:
    """Reverse-geocode coordinates into areaname, cityname, townname and statecode using metro/non-metro rules.

    Raises:
        HTTPException: 500 when the Google key is missing or the geocode call fails.
    """
    if not settings.google_maps_api_key:
        raise HTTPException(status_code=500, detail="Google Maps API Key is not configured.")

    api_key = settings.google_maps_api_key
    url = f"https://maps.googleapis.com/maps/api/geocode/json?latlng={latitude},{longitude}&key={api_key}"
    
    components = {}
    
    try:
        async with get_http_client() as client:
            response = await client.get(url)
            data = response.json()
            
            if data.get("status") != "OK" or not data.get("results"):
                logger.warning(f"Google Maps Geocoding failed for {latitude},{longitude}: {data.get('status')}")
                return {
                    "areaname": None, "cityname": None, "townname": None, "statecode": None
                }

            address_components = data["results"][0].get("address_components", [])
            for comp in address_components:
                types = comp.get("types", [])
                if "locality" in types:
                    components["locality"] = comp.get("long_name")
                elif "sublocality_level_1" in types:
                    components["sublocality"] = comp.get("long_name")
                elif "neighborhood" in types:
                    components["neighborhood"] = comp.get("long_name")
                elif "administrative_area_level_3" in types:
                    components["level3"] = comp.get("long_name")
                elif "administrative_area_level_1" in types:
                    components["statecode"] = comp.get("short_name")

    except Exception as e:
        logger.error(f"Error in handle_town_management Google API call: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing location data: {str(e)}")

    locality = components.get("locality")
    sublocality = components.get("sublocality") or components.get("neighborhood")
    level3 = components.get("level3")
    statecode = components.get("statecode")

    metro_cities = _load_metro_cities()
    
    areaname = None
    cityname = None
    townname = None

    if locality:
        is_metro = locality.lower().strip() in metro_cities
        logger.info(f"Locality: {locality}, Is Metro: {is_metro}")
        
        if is_metro:
            logger.info(f"Metro city detected -> {locality}")
            cityname = locality
            townname = sublocality if sublocality else locality
            areaname = sublocality if sublocality else locality
        else:
            logger.info(f"Non-metro town detected -> {locality}")
            cityname = level3 if level3 else locality
            townname = locality
            
            if sublocality:
                areaname = sublocality
                logger.info(f"Sublocality used as area -> {sublocality}")
            else:
                areaname = locality
                logger.info("Sublocality missing -> using locality as area")
    else:
        logger.info("Locality missing — fallback applied")
        cityname = level3
        townname = sublocality
        areaname = sublocality

    return {
        "areaname": areaname.title() if areaname else None,
        "cityname": cityname.title() if cityname else None,
        "townname": townname.title() if townname else None,
        "statecode": statecode
    }


async def handle_crud_collections(
    user_contactid: Optional[str] = None,
    user_userprofileid: Optional[str] = None,
    collection_type: Optional[str] = None,
    collectionname: Optional[str] = None,
    isMytown: Optional[str] = None,
    actionmode: Optional[str] = None,
    collectionid: Optional[str] = None,
) -> Dict[str, Any]:
    """Create, update or delete a collection per actionmode and mirror the change to Dataverse in the background.

    Raises:
        HTTPException: 400 on missing actionmode/collectionid or unsupported action, 404 when an update target is absent.
    """
    from app.etown.mytown.mytown_syncing import (
        sync_collection_to_dataverse,
        sync_collection_delete_from_dataverse,
    )

    logger.info(f"Handler: CrudCollections. actionmode={actionmode}, user={user_userprofileid}")

    if not actionmode:
        raise HTTPException(status_code=400, detail="actionmode is required.")

    action = actionmode.lower()

    if action == "create":
        new_collectionid = str(uuid.uuid4())
        collectionunicid = f"ETCL{random.randint(1, 999999)}"

        usercontactidname = None
        if user_contactid:
            usercontactidname = await Database.fetchval(
                "SELECT contactidname FROM etown_userprofile WHERE contactid = $1 LIMIT 1",
                user_contactid,
            )

        useruserprofileidname = None
        if user_userprofileid:
            useruserprofileidname = await Database.fetchval(
                "SELECT name FROM etown_userprofile WHERE userprofileid = $1 LIMIT 1",
                user_userprofileid,
            )

        insert_query = """
            INSERT INTO etown_collections (
                collectionid,
                collectionunicid,
                collectionname,
                collectiontype,
                usercontactid,
                useruserprofileid,
                createdon,
                usercontactidname,
                useruserprofileidname,
                "isMytown"
            )
            VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8, $9)
            RETURNING *
        """

        row = await Database.fetchrow(
            insert_query,
            new_collectionid,
            collectionunicid,
            collectionname,
            collection_type,
            user_contactid,
            user_userprofileid,
            usercontactidname,
            useruserprofileidname,
            isMytown,
        )

        asyncio.create_task(sync_collection_to_dataverse(
            collectionid=new_collectionid,
            collectionname=collectionname,
            collectiontype=collection_type,
            collectionunicid=collectionunicid,
            ismytown=isMytown,
            usercontactid=user_contactid,
            useruserprofileid=user_userprofileid,
        ))

        return {"success": True, **(dict(row) if row else {})}

    if action == "update":
        if not collectionid:
            raise HTTPException(status_code=400, detail="collectionid is required for update.")

        update_query = """
            UPDATE etown_collections
            SET collectionname = $2,
                collectiontype = $3,
                modifieddate = NOW()
            WHERE collectionid = $1
            RETURNING *
        """

        row = await Database.fetchrow(
            update_query,
            collectionid,
            collectionname,
            collection_type,
        )

        if not row:
            raise HTTPException(status_code=404, detail="Collection not found.")

        asyncio.create_task(sync_collection_to_dataverse(
            collectionid=collectionid,
            collectionname=collectionname,
            collectiontype=collection_type,
        ))

        return {"success": True, **dict(row)}

    if action == "delete":
        if not collectionid:
            raise HTTPException(status_code=400, detail="collectionid is required for delete.")

        await Database.execute(
            """
            WITH deleted_items AS (
                DELETE FROM etown_collectionitems WHERE collectionid = $1
            )
            DELETE FROM etown_collections WHERE collectionid = $1
            """,
            collectionid,
        )

        asyncio.create_task(sync_collection_delete_from_dataverse(collectionid))

        return {"message": "Collection deleted successfully"}

    raise HTTPException(status_code=400, detail=f"Unsupported actionmode: {actionmode}")


def _normalize_favourite(value: Any) -> str:
    """Return 'Unfavourite' for 0/unfavourite/false/no inputs and 'Favourite' for anything else."""
    return "Unfavourite" if str(value).strip().lower() in ("0", "unfavourite", "false", "no") else "Favourite"


def _normalize_is_mytown(value: Any) -> Any:
    """Convert numeric MyTown flags to the API's Yes/No values."""
    normalized = str(value).strip()
    if normalized == "0":
        return "No"
    if normalized == "1":
        return "Yes"
    return value


async def handle_crud_collectionitems(
    user_contactid: Optional[str] = None,
    user_userprofileid: Optional[str] = None,
    actionmode: Optional[str] = None,
    collectionid: Optional[str] = None,
    collectionitems: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Insert, update or unfavourite collection items per actionmode and mirror each to Dataverse in the background.

    Args:
        user_contactid: Owning contact UUID.
        user_userprofileid: Owning user-profile UUID.
        actionmode: insert (or create) | edit | update | delete.
        collectionid: Collection the items belong to.
        collectionitems: List of {providerid, isFavourite} dicts to act on.

    Returns:
        {success, actionmode, collectionid, results} with a per-provider operation.

    Raises:
        HTTPException: 400 when required inputs are missing or actionmode is unsupported.
    """
    from app.etown.mytown.mytown_syncing import sync_collectionitem_to_dataverse

    logger.info(
        f"Handler: CrudCollectionItems. actionmode={actionmode}, collection={collectionid}, user={user_userprofileid}"
    )

    if not actionmode:
        raise HTTPException(status_code=400, detail="actionmode is required.")
    if not collectionid:
        raise HTTPException(status_code=400, detail="collectionid is required.")
    if not collectionitems:
        raise HTTPException(status_code=400, detail="collectionitems must contain at least one item.")

    action = actionmode.lower()
    if action == "create":
        action = "insert"
    if action not in ("insert", "edit", "update", "delete"):
        raise HTTPException(status_code=400, detail=f"Unsupported actionmode: {actionmode}")

    match_query = """
        UPDATE etown_collectionitems
           SET isfavourite = $5,
               modifieddate = NOW()
         WHERE usercontactid = $1
           AND useruserprofileid = $2
           AND providerid = $3
           AND collectionid = $4
        RETURNING collectionitemsid
    """
    usercontactidname = None
    useruserprofileidname = None
    if action == "insert":
        if user_contactid:
            usercontactidname = await Database.fetchval(
                "SELECT contactidname FROM etown_userprofile WHERE contactid = $1 LIMIT 1",
                user_contactid,
            )
        if user_userprofileid:
            useruserprofileidname = await Database.fetchval(
                "SELECT name FROM etown_userprofile WHERE userprofileid = $1 LIMIT 1",
                user_userprofileid,
            )

    results: List[Dict[str, Any]] = []

    for item in collectionitems:
        providerid = item.get("providerid")
        if not providerid:
            continue
        raw_favourite = item.get("isFavourite")
        if action == "delete":
            fav_value = "Unfavourite"
        elif raw_favourite is None:
            fav_value = "Favourite"
        else:
            fav_value = _normalize_favourite(raw_favourite)

        if action == "insert":
            row = await Database.fetchrow(
                match_query,
                user_contactid, user_userprofileid, providerid, collectionid, fav_value,
            )
            if row:
                collectionitemsid = row["collectionitemsid"]
                operation = "updated"
                asyncio.create_task(sync_collectionitem_to_dataverse(
                    collectionitemsid=collectionitemsid,
                    isfavourite=fav_value,
                ))
            else:
                collectionitemsid = str(uuid.uuid4())
                await Database.execute(
                    """
                    INSERT INTO etown_collectionitems (
                        collectionitemsid, collectionid, providerid, isfavourite,
                        usercontactid, useruserprofileid, createdon,
                        usercontactidname, useruserprofileidname
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8)
                    """,
                    collectionitemsid, collectionid, providerid, fav_value,
                    user_contactid, user_userprofileid,
                    usercontactidname, useruserprofileidname,
                )
                operation = "inserted"
                asyncio.create_task(sync_collectionitem_to_dataverse(
                    collectionitemsid=collectionitemsid,
                    isfavourite=fav_value,
                    collectionid=collectionid,
                    providerid=providerid,
                    usercontactid=user_contactid,
                    useruserprofileid=user_userprofileid,
                ))
        else:
            row = await Database.fetchrow(
                match_query,
                user_contactid, user_userprofileid, providerid, collectionid, fav_value,
            )
            if not row:
                results.append({"providerid": providerid, "operation": "not_found"})
                continue
            collectionitemsid = row["collectionitemsid"]
            operation = "removed" if action == "delete" else "updated"
            asyncio.create_task(sync_collectionitem_to_dataverse(
                collectionitemsid=collectionitemsid,
                isfavourite=fav_value,
            ))

        results.append({
            "providerid": providerid,
            "collectionitemsid": collectionitemsid,
            "operation": operation,
        })

    return {
        "success": True,
        "actionmode": action,
        "collectionid": collectionid,
        "results": results,
    }


async def handle_favourite_town(
    user_contactid: Optional[str] = None,
    user_userprofileid: Optional[str] = None,
    actionmode: Optional[str] = None,
    favouritetownid: Optional[str] = None,
    townname: Optional[str] = None,
) -> Dict[str, Any]:
    """Favourite a town (create, reusing an existing row) or unfavourite one (update), mirroring to Dataverse.

    Args:
        user_contactid: Owning contact UUID (required for create).
        user_userprofileid: Owning user-profile UUID (required for create).
        actionmode: create | update.
        favouritetownid: Favourite-town row UUID; update falls back to townname plus user ids.
        townname: Town being favourited (required for create).

    Returns:
        dict: {success, actionmode, operation, data} where data is the affected row.

    Raises:
        HTTPException: 400 on missing/invalid inputs, 404 when an update target is absent.
    """
    from app.etown.mytown.mytown_syncing import sync_favouritetown_to_dataverse

    logger.info(
        f"Handler: FavouriteTown. actionmode={actionmode}, town={townname}, user={user_userprofileid}"
    )

    if not actionmode:
        raise HTTPException(status_code=400, detail="actionmode is required.")

    action = actionmode.lower()

    if action == "create":
        if not townname:
            raise HTTPException(status_code=400, detail="townname is required for create.")
        if not user_contactid or not user_userprofileid:
            raise HTTPException(
                status_code=400,
                detail="user_contactid and user_userprofileid are required for create.",
            )

        row = await Database.fetchrow(
            """
            UPDATE etown_favouritetown
               SET isfavourite = 'Favourite',
                   modifieddate = NOW()
             WHERE usercontactid = $1
               AND useruserprofileid = $2
               AND LOWER(TRIM(townname)) = LOWER(TRIM($3))
            RETURNING *
            """,
            user_contactid, user_userprofileid, townname,
        )

        if row:
            operation = "reactivated"
        else:
            new_id = str(uuid.uuid4())
            usercontactidname = await Database.fetchval(
                "SELECT contactidname FROM etown_userprofile WHERE contactid = $1 LIMIT 1",
                user_contactid,
            )
            useruserprofileidname = await Database.fetchval(
                "SELECT name FROM etown_userprofile WHERE userprofileid = $1 LIMIT 1",
                user_userprofileid,
            )
            row = await Database.fetchrow(
                """
                INSERT INTO etown_favouritetown (
                    favouritetownid, townname, isfavourite,
                    usercontactid, useruserprofileid, createdon,
                    usercontactidname, useruserprofileidname
                )
                VALUES ($1, $2, 'Favourite', $3, $4, NOW(), $5, $6)
                RETURNING *
                """,
                new_id, townname, user_contactid, user_userprofileid,
                usercontactidname, useruserprofileidname,
            )
            operation = "inserted"

        asyncio.create_task(sync_favouritetown_to_dataverse(
            favouritetownid=row["favouritetownid"],
            isfavourite="Favourite",
            townname=townname,
            usercontactid=user_contactid,
            useruserprofileid=user_userprofileid,
        ))
        return {"success": True, "actionmode": action, "operation": operation, "data": dict(row)}

    if action == "update":
        can_match_by_town = bool(townname and user_contactid and user_userprofileid)
        if not favouritetownid and not can_match_by_town:
            raise HTTPException(
                status_code=400,
                detail="favouritetownid (or townname with user_contactid and user_userprofileid) is required for update.",
            )

        row = None

        if favouritetownid:
            row = await Database.fetchrow(
                """
                UPDATE etown_favouritetown
                   SET isfavourite = 'unfavourite',
                       modifieddate = NOW()
                 WHERE LOWER(TRIM(favouritetownid::text)) = LOWER(TRIM($1))
                RETURNING *
                """,
                favouritetownid,
            )

        if not row and can_match_by_town:
            row = await Database.fetchrow(
                """
                UPDATE etown_favouritetown
                   SET isfavourite = 'unfavourite',
                       modifieddate = NOW()
                 WHERE usercontactid = $1
                   AND useruserprofileid = $2
                   AND LOWER(TRIM(townname)) = LOWER(TRIM($3))
                   AND isfavourite = 'Favourite'
                RETURNING *
                """,
                user_contactid, user_userprofileid, townname,
            )

        if not row:
            raise HTTPException(status_code=404, detail="Favourite town not found.")

        asyncio.create_task(sync_favouritetown_to_dataverse(
            favouritetownid=row["favouritetownid"],
            isfavourite="unfavourite",
        ))
        return {"success": True, "actionmode": action, "operation": "unfavourited", "data": dict(row)}

    raise HTTPException(status_code=400, detail=f"Unsupported actionmode: {actionmode}")


class Database(Database):
    @staticmethod
    def _build_distance_sql(lat_col: str, lon_col: str, lat_param: str, lon_param: str) -> str:
        """Return a haversine SQL expression aliased distance_km between the columns and parameter point."""
        return f"""
        ( 6371 * acos( LEAST(1.0, GREATEST(-1.0,
            cos(radians({lat_param})) * cos(radians(CAST({lat_col} AS DOUBLE PRECISION)))
            * cos(radians(CAST({lon_col} AS DOUBLE PRECISION)) - radians({lon_param}))
            + sin(radians({lat_param})) * sin(radians(CAST({lat_col} AS DOUBLE PRECISION)))
        )))) AS distance_km
        """

    @staticmethod
    def _build_lat_lon_validation_sql(lat_col: str, lon_col: str) -> str:
        """Return a SQL predicate requiring both columns to be numeric and within latitude/longitude range."""
        return f"""
        {lat_col} IS NOT NULL AND {lon_col} IS NOT NULL
        AND CAST({lat_col} AS TEXT) ~ E'^[-+]?[0-9]*\\.?[0-9]+([eE][-+]?[0-9]+)?$'
        AND CAST({lon_col} AS TEXT) ~ E'^[-+]?[0-9]*\\.?[0-9]+([eE][-+]?[0-9]+)?$'
        AND CAST({lat_col} AS DOUBLE PRECISION) BETWEEN -90 AND 90
        AND CAST({lon_col} AS DOUBLE PRECISION) BETWEEN -180 AND 180
        """

    @staticmethod
    def _build_town_membership_sql(town_param: str, provider_alias: str = "p") -> str:
        """Return a SQL predicate: provider linked to the town, or city matches when it holds no live town link.

        Args:
            town_param: Placeholder for the lowercased, trimmed town name, e.g. "$3".
            provider_alias: Alias of etown_provider in the enclosing query.

        Returns:
            A boolean SQL expression safe to AND into a WHERE clause.
        """
        return f"""(
            EXISTS (
                SELECT 1 FROM public.etown_etownprovider et
                 WHERE et.providerid = {provider_alias}.providerid
                   AND COALESCE(et.deleted, 'false') <> 'true'
                   AND TRIM(LOWER(et.townname)) = {town_param}
            )
            OR (
                TRIM(LOWER({provider_alias}.city)) = {town_param}
                AND NOT EXISTS (
                    SELECT 1 FROM public.etown_etownprovider et2
                     WHERE et2.providerid = {provider_alias}.providerid
                       AND COALESCE(et2.deleted, 'false') <> 'true'
                       AND COALESCE(et2.townprofileid, '') <> ''
                )
            )
        )"""

    @staticmethod
    def _build_distance_sql_numeric(lat_col: str, lon_col: str, lat_param: str, lon_param: str) -> str:
        """Distance SQL for numeric lat/lon columns (no CAST overhead)."""
        return f"""
        ( 6371 * acos( LEAST(1.0, GREATEST(-1.0,
            cos(radians({lat_param})) * cos(radians({lat_col}))
            * cos(radians({lon_col}) - radians({lon_param}))
            + sin(radians({lat_param})) * sin(radians({lat_col}))
        )))) AS distance_km
        """

    @staticmethod
    def _build_numeric_lat_lon_validation_sql(lat_col: str, lon_col: str) -> str:
        """Fast validation for numeric columns."""
        return f"""
        {lat_col} IS NOT NULL AND {lon_col} IS NOT NULL
        AND {lat_col} BETWEEN -90 AND 90
        AND {lon_col} BETWEEN -180 AND 180
        """
    
    @classmethod
    @with_retry()
    async def get_city_from_coordinates(cls, latitude: float, longitude: float) -> Optional[str]:
        """Return a place name for coordinates from Google reverse geocoding: named premise, locality, admin L3, then sublocality."""
        logger.info(f"Resolving city via Google Maps for coordinates: {latitude}, {longitude}")
        if settings.google_maps_api_key:
            try:
                google_data = await asyncio.wait_for(
                    _get_cached_google_maps_data(latitude, longitude),
                    timeout=GOOGLE_MAPS_TIMEOUT_SECONDS
                )
                if google_data:
                    address_components = google_data.get("address_components", [])
                    for component in address_components:
                        types = component.get("types", [])
                        if "premise" in types:
                            premise_name = (component.get("long_name") or "").strip()
                            is_premise_like = (
                                bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", premise_name)) or
                                bool(re.search(r"[A-Za-z]+\d+|[A-Za-z]*\d+[/-]", premise_name)) or
                                bool(re.search(r"\b(unit|apt|apartment|block|bldg|building|floor|level|suite|room)\b", premise_name, re.IGNORECASE)) or
                                (len(premise_name) <= 8 and bool(re.search(r"[A-Za-z].*\d|\d.*[A-Za-z]", premise_name)))
                            )
                            if premise_name and not is_premise_like and len(premise_name) > 3:
                                logger.info(f"Google Maps found premise: {premise_name}")
                                return premise_name
                    for component in address_components:
                        types = component.get("types", [])
                        if "locality" in types:
                            city_name = component.get("long_name")
                            logger.info(f"Google Maps found city: {city_name}")
                            return city_name
                    for component in address_components:
                        types = component.get("types", [])
                        if "administrative_area_level_3" in types:
                            city_name = component.get("long_name")
                            logger.info(f"Google Maps found admin area level 3: {city_name}")
                            return city_name
                    for component in address_components:
                        types = component.get("types", [])
                        if "sublocality" in types:
                            city_name = component.get("long_name")
                            logger.info(f"Google Maps found sublocality: {city_name}")
                            return city_name
            except asyncio.TimeoutError:
                logger.warning(f"Google Maps API call timed out after {GOOGLE_MAPS_TIMEOUT_SECONDS:.2f}s for {latitude}, {longitude}")
            except Exception as e:
                logger.error(f"Google Maps API call failed: {e}", exc_info=True)
        else:
            logger.warning("Google Maps API key not configured")
        logger.info("No city found via Google Maps for the given coordinates.")
        return None

    @classmethod
    @with_retry()
    async def get_town_profile_by_name(cls, town_name: str) -> Optional[Dict]:
        """Return the first etown_townprofile row matching a town name case-insensitively, with its view count."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        if not town_name: return None
        
        query = """
            SELECT tp.*, get_town_view_count(tp.townprofileid) as view_count
            FROM etown_townprofile tp 
            WHERE TRIM(tp.townname) ILIKE $1 
            LIMIT 1
        """
        row = await cls.fetchrow(query, town_name.strip())
        return dict(row) if row else None

    @classmethod
    @with_retry()
    async def validate_user_profile_exists(cls, user_profile_id: str) -> bool:
        """Return True when a well-formed user profile id exists in etown_userprofile, False otherwise or on error."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool not initialized")
            
        if not is_valid_user_profile_id(user_profile_id):
            return False
            
        try:
            result = await cls.fetchval(
                "SELECT 1 FROM etown_userprofile WHERE TRIM(LOWER(userprofileid)) = $1 LIMIT 1",
                normalize_user_profile_id(user_profile_id)
            )
            return result is not None
            
        except Exception as e:
            logger.error(f"Error validating user profile {user_profile_id}: {e}", exc_info=True)
            return False
    
    @classmethod
    @with_retry()
    async def add_town_view_tracking(cls, town_name: str, user_profile_id: str) -> tuple[bool, bool]:
        """Record a town view via add_town_view and bump the town's apihit, returning (success, add_town_view result)."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool not initialized")
            
        if not town_name or not user_profile_id:
            return False, False
            
        try:
            town_profile = await cls.get_town_profile_by_name(town_name)
            if not town_profile:
                logger.warning(f"Town profile not found for: {town_name}")
                return False, False
                
            town_profile_id = town_profile.get('townprofileid')
            if not town_profile_id:
                logger.warning(f"Town profile ID not found for: {town_name}")
                return False, False
            
            result = await cls.fetchval(
                "SELECT add_town_view($1, $2)",
                town_profile_id,
                user_profile_id
            )

            await cls.execute(
                "UPDATE etown_townprofile SET apihit = COALESCE(apihit, 0) + 1 WHERE townprofileid = $1",
                town_profile_id
            )
            
            return True, result
            
        except Exception as e:
            logger.error(f"Error adding town view tracking for {town_name}, {user_profile_id}: {e}", exc_info=True)
            return False, False
    
    @classmethod
    @with_retry()
    async def update_town_view_tracking(cls, town_name: str, user_profile_id: str) -> None:
        """Increment a town's apihit and add the user to its comma-separated vieweduserprofileid list."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        if not town_name or not user_profile_id: return
        
        current_town = await cls.get_town_profile_by_name(town_name)
        if not current_town:
            logger.warning(f"Town profile not found for: {town_name}")
            return
        
        existing_viewers = current_town.get('vieweduserprofileid', '') or ''
        viewer_list = [v.strip() for v in existing_viewers.split(',') if v.strip()] if existing_viewers else []
        
        if user_profile_id not in viewer_list:
            viewer_list.append(user_profile_id)
        
        current_apihit = int(current_town.get('apihit', 0) or 0)
        new_apihit = current_apihit + 1
        
        new_viewers = ','.join(viewer_list)
        
        query = """
        UPDATE etown_townprofile 
        SET apihit = $1, vieweduserprofileid = $2
        WHERE TRIM(townname) ILIKE $3
        """
        
        try:
            await cls.execute(query, new_apihit, new_viewers, town_name.strip())
            logger.info(f"Updated town view count for {town_name}: apihits ={new_apihit}, viewers ={len(viewer_list)}")
        except Exception as e:
            logger.error(f"Error updating town view tracking for {town_name}: {e}", exc_info=True)

    @classmethod
    @with_retry()
    async def get_available_cities_by_distance(
        cls, 
        latitude: Optional[float] = None, 
        longitude: Optional[float] = None, 
        contact_id: str = None, 
        userprofile_id: str = None, 
        limit: int = 20, 
        offset: int = 0,
        all_towns: bool = False,
        search_term: Optional[str] = None,
        search_page: Optional[int] = None,
        fuzzy_search: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return towns with at least one provider, nearest first, with provider and favourite counts."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")

        logger.info(f"DB: Fetching available cities strictly from townprofile. Lat: {latitude}, Lon: {longitude}")
        validation_sql = cls._build_lat_lon_validation_sql('tp.townlatitude', 'tp.townlongitude')
        params: List[Any] = []
        where_clauses = []
        search_param = None

        if latitude is not None and longitude is not None:
            distance_sql = cls._build_distance_sql('tp.townlatitude', 'tp.townlongitude', '$1', '$2')
            params.extend([latitude, longitude])
            where_clauses.append(validation_sql)
            candidate_order_by = "ORDER BY distance_km ASC"
            final_order_by = "ORDER BY dt.distance_km ASC"
        else:
            distance_sql = "NULL AS distance_km"
            candidate_order_by = "ORDER BY tp.townname ASC"
            final_order_by = "ORDER BY dt.townname ASC"

        if search_term:
            clean_search_term = search_term.strip()
            params.append(clean_search_term if fuzzy_search else f"%{clean_search_term}%")
            search_param = f"${len(params)}"
            if fuzzy_search:
                where_clauses.append(f"word_similarity({search_param}, COALESCE(tp.townname, '')) >= 0.3")
            else:
                where_clauses.append(f"tp.townname ILIKE {search_param}")

        result_order_by = final_order_by
        if fuzzy_search and search_param:
            distance_order = "dt.distance_km ASC" if latitude is not None and longitude is not None else "dt.townname ASC"
            result_order_by = f"ORDER BY word_similarity({search_param}, dt.townname) DESC, {distance_order}"

        pagination_sql = ""
        if search_page is not None:
            params.extend([limit, (search_page - 1) * limit])
            limit_pos = len(params) - 1
            offset_pos = len(params)
            pagination_sql = f"LIMIT ${limit_pos} OFFSET ${offset_pos}"
        elif not all_towns:
            params.extend([limit, offset])
            limit_pos = len(params) - 1
            offset_pos = len(params)
            pagination_sql = f"LIMIT ${limit_pos} OFFSET ${offset_pos}"

        exclude_own_and = ""
        if contact_id:
            params.append(contact_id)
            exclude_own_and = f"AND p.contactid != ${len(params)}"

        query = f"""
        WITH TownCandidates AS (
            SELECT
                tp.townprofileid,
                tp.townname,
                tp.townlatitude,
                tp.townlongitude,
                tp.townimage,
                tp.towndescription,
                tp.cityname,
                {distance_sql},
                COALESCE(tp.apihit, 0) as api_hit_count
            FROM etown_townprofile tp
            WHERE {" AND ".join(where_clauses) if where_clauses else "1=1"}
            {candidate_order_by}
        ),
        MappedProviders AS (
            SELECT DISTINCT p.providerid, tc.townprofileid, p.providertype
            FROM TownCandidates tc
            JOIN etown_etownprovider ep ON ep.townprofileid = tc.townprofileid
            JOIN etown_provider p ON p.providerid = ep.providerid
            WHERE p.deleted IS DISTINCT FROM 'true'
              AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
              AND p.disabled IS NOT TRUE
              {exclude_own_and}

            UNION

            SELECT DISTINCT p.providerid, tc.townprofileid, p.providertype
            FROM TownCandidates tc
            JOIN etown_provider p ON LOWER(TRIM(p.city)) = LOWER(TRIM(tc.townname))
            WHERE NOT EXISTS (
                SELECT 1 FROM public.etown_etownprovider et
                WHERE et.providerid = p.providerid
                  AND COALESCE(et.townprofileid, '') <> ''
            )
              AND p.deleted IS DISTINCT FROM 'true'
              AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
              AND p.disabled IS NOT TRUE
              {exclude_own_and}
        ),
        ProviderCounts AS (
            SELECT
                townprofileid,
                COUNT(DISTINCT providerid)::int AS provider_count,
                COUNT(DISTINCT CASE WHEN providertype = 'etown_verified' THEN providerid END)::int AS verified_count,
                COUNT(DISTINCT CASE WHEN providertype != 'etown_verified' OR providertype IS NULL THEN providerid END)::int AS unverified_count
            FROM MappedProviders
            GROUP BY townprofileid
        ),
        FavouriteCounts AS (
            SELECT townname, COUNT(*)::int AS favourite_count
            FROM etown_favouritetown
            WHERE isfavourite = 'Favourite'
            GROUP BY townname
        ),
        DedupedTowns AS (
            SELECT DISTINCT ON (LOWER(TRIM(tc.townname)))
                tc.townprofileid,
                tc.townname,
                tc.townlatitude,
                tc.townlongitude,
                tc.townimage,
                tc.towndescription,
                tc.cityname,
                tc.distance_km,
                tc.api_hit_count,
                COALESCE(pc.provider_count, 0) AS provider_count,
                COALESCE(pc.verified_count, 0) AS verified_count,
                COALESCE(pc.unverified_count, 0) AS unverified_count,
                COALESCE(fc.favourite_count, 0) AS favourite_count
            FROM TownCandidates tc
            LEFT JOIN ProviderCounts pc ON pc.townprofileid = tc.townprofileid
            LEFT JOIN FavouriteCounts fc ON fc.townname = tc.townname
            WHERE tc.townname IS NOT NULL
              AND TRIM(tc.townname) <> ''
              AND COALESCE(pc.provider_count, 0) > 0
            ORDER BY
                LOWER(TRIM(tc.townname)),
                COALESCE(pc.provider_count, 0) DESC,
                tc.distance_km ASC NULLS LAST,
                tc.townprofileid
        )
        SELECT
            dt.townprofileid,
            dt.townname,
            dt.townlatitude,
            dt.townlongitude,
            dt.townimage,
            dt.towndescription,
            dt.cityname,
            dt.distance_km,
            dt.provider_count,
            dt.verified_count,
            dt.unverified_count,
            dt.favourite_count,
            dt.api_hit_count,
            COALESCE(get_town_view_count(dt.townprofileid), 0) AS view_count
            {", COUNT(*) OVER() AS total_count" if search_page is not None else ""}
        FROM DedupedTowns dt
        {result_order_by}
        {pagination_sql};
        """

        try:
            results = await cls.fetch(query, *params)
            return [dict(row) for row in results]
        except Exception as e:
            logger.error(f"Error fetching available cities: {e}", exc_info=True)
            return []

    @classmethod
    @with_retry()
    async def get_user_favourite_towns(cls, contact_id: str, user_profile_id: str, latitude: Optional[float] = None, longitude: Optional[float] = None) -> List[Dict[str, Any]]:
        """Return the user's favourited towns that have providers, deduplicated by name, newest first, with counts and distance."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        if not contact_id or not user_profile_id: return []
        
        params = [contact_id, user_profile_id]
        distance_col = "NULL as distance_km"
        
        if latitude is not None and longitude is not None:
             distance_sql = cls._build_distance_sql('tp.townlatitude', 'tp.townlongitude', '$3', '$4')
             distance_col = f"{distance_sql}"
             params.extend([latitude, longitude])

        query = f"""
        WITH UserFavourites AS MATERIALIZED (
            SELECT DISTINCT ON (LOWER(TRIM(ft.townname)))
                ft.favouritetownid, ft.townname, ft.isfavourite, ft.createdon,
                LOWER(TRIM(ft.townname)) AS town_key
            FROM etown_favouritetown ft
            WHERE ft.usercontactid = $1
              AND ft.useruserprofileid = $2
              AND ft.isfavourite = 'Favourite'
              AND ft.townname IS NOT NULL
              AND TRIM(ft.townname) <> ''
            ORDER BY LOWER(TRIM(ft.townname)), ft.createdon DESC NULLS LAST, ft.favouritetownid
        ),
        TownProviders AS (
            SELECT TRIM(LOWER(et.townname)) AS town_key, et.providerid
            FROM public.etown_etownprovider et
            WHERE COALESCE(et.deleted, 'false') <> 'true'
              AND TRIM(LOWER(et.townname)) IN (SELECT town_key FROM UserFavourites)
            UNION
            SELECT LOWER(TRIM(p.city)), p.providerid
            FROM public.etown_provider p
            WHERE p.city IS NOT NULL
              AND LOWER(TRIM(p.city)) IN (SELECT town_key FROM UserFavourites)
              AND NOT EXISTS (
                  SELECT 1 FROM public.etown_etownprovider et2
                   WHERE et2.providerid = p.providerid
                     AND COALESCE(et2.deleted, 'false') <> 'true'
                     AND COALESCE(et2.townprofileid, '') <> ''
              )
        ),
        ProviderCounts AS (
            SELECT tpv.town_key,
                   COUNT(DISTINCT p.providerid)::int AS provider_count,
                   (COUNT(DISTINCT p.providerid) FILTER (WHERE p.providertype = 'etown_verified'))::int AS verified_count
            FROM TownProviders tpv
            JOIN public.etown_provider p ON p.providerid = tpv.providerid
            WHERE p.contactid != $1
              AND p.deleted IS DISTINCT FROM 'true'
              AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
              AND p.disabled IS NOT TRUE
            GROUP BY tpv.town_key
        )
        SELECT
            uf.favouritetownid,
            tp.townprofileid,
            uf.townname,
            uf.isfavourite,
            uf.createdon,
            tp.townimage,
            tp.towndescription,
            tp.townlatitude,
            tp.townlongitude,
            tp.cityname,
            {distance_col},
            (SELECT COUNT(*) FROM etown_favouritetown WHERE townname = uf.townname AND isfavourite = 'Favourite') as favourite_count,
            COALESCE(tp.apihit, 0) as api_hit_count,
            COALESCE((SELECT get_town_view_count(tp.townprofileid)), 0) as view_count,
            pc.provider_count,
            pc.verified_count
        FROM UserFavourites uf
        JOIN ProviderCounts pc ON pc.town_key = uf.town_key
        LEFT JOIN LATERAL (
            SELECT t.townprofileid, t.townimage, t.towndescription, t.townlatitude, t.townlongitude, t.cityname, t.apihit
            FROM etown_townprofile t
            WHERE uf.town_key ILIKE LOWER(TRIM(t.townname)) || '%'
            ORDER BY LENGTH(t.townname) DESC
            LIMIT 1
        ) tp ON TRUE
        ORDER BY uf.createdon DESC NULLS LAST
        """

        try:
            results = await cls.fetch_once(query, *params)
            return [dict(r) for r in results]
        except Exception as e:
            logger.error(f"Error fetching favourite towns: {e}", exc_info=True)
            return []

    @classmethod
    @with_retry()
    async def get_areas_prioritized(
        cls,
        latitude: float,
        longitude: float,
        contact_id: str = None,
        userprofile_id: str = None,
        limit: int = 20,
        offset: int = 0,
        city_name: str = None,
        skip_city_lookup: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return a page of provider areas near a point, verified-first then by distance, with provider counts."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        if city_name:
            determined_city = city_name
        elif skip_city_lookup:
            determined_city = None
        else:
            determined_city = await cls.get_city_from_coordinates(latitude, longitude)
        logger.info(f"DB: Fetching prioritized areas for {latitude}, {longitude} Limit: {limit}. Detected City: {determined_city}")

        distance_sql = cls._build_distance_sql_numeric('latitude', 'longitude', '$1', '$2')
        validation_sql = cls._build_numeric_lat_lon_validation_sql('latitude', 'longitude')

        params = [latitude, longitude]
        where_conditions = [
            validation_sql,
            "areaname IS NOT NULL AND areaname != ''",
            "city IS NOT NULL AND city != ''",
            "deleted IS DISTINCT FROM 'true'",
            "(deactivated IS DISTINCT FROM 'true' OR reactivation = 'true')",
            "disabled IS NOT TRUE",
            _EXCLUDE_REVIEW_PENDING_BARE,
        ]

        if contact_id:
            params.append(contact_id)
            where_conditions.append(f"(contactid != ${len(params)} OR contactid IS NULL)")
        if userprofile_id:
            params.append(userprofile_id)
            where_conditions.append(f"(userprofileid != ${len(params)} OR userprofileid IS NULL)")
        if determined_city:
            params.append(determined_city.strip().lower())
            where_conditions.append(f"TRIM(LOWER(city)) = ${len(params)}")

        params.extend([limit, offset])

        query = f"""
        WITH ProviderDistances AS (
            SELECT areaname, latitude, longitude, city, statecode, providertype, {distance_sql}
            FROM public.etown_provider
            WHERE {" AND ".join(where_conditions)}
        ),
        AreaStats AS (
            SELECT 
                LOWER(areaname) AS area_lower, 
                LOWER(city) AS city_lower, 
                COUNT(*)::int AS provider_count,
                BOOL_OR(providertype = 'etown_verified') as has_verified_provider,
                MIN(distance_km) as min_distance
            FROM ProviderDistances
            GROUP BY LOWER(areaname), LOWER(city)
        ),
        RankedAreas AS (
            SELECT DISTINCT ON (LOWER(areaname), LOWER(city))
                areaname, latitude, longitude, distance_km, city, statecode
            FROM ProviderDistances
            ORDER BY LOWER(areaname), LOWER(city), distance_km ASC
        )
        SELECT r.areaname, r.latitude, r.longitude, r.distance_km, r.city, r.statecode, 
            s.provider_count, s.has_verified_provider
        FROM RankedAreas r
        JOIN AreaStats s ON LOWER(r.areaname) = s.area_lower AND LOWER(r.city) = s.city_lower
        ORDER BY (CASE WHEN s.has_verified_provider THEN 0 ELSE 1 END) ASC, r.distance_km ASC 
        LIMIT ${len(params)-1} OFFSET ${len(params)};
        """
        
        try:
            query = f"""
            WITH ProviderDistances AS (
                SELECT areaname, latitude, longitude, city, statecode, providertype, {distance_sql}
                FROM public.etown_provider
                WHERE {" AND ".join(where_conditions)}
                ORDER BY distance_km ASC  -- Pre-sort for better performance
                LIMIT 1000  -- Limit processing for performance
            ),
            AreaStats AS (
                SELECT 
                    LOWER(areaname) AS area_lower, 
                    LOWER(city) AS city_lower, 
                    COUNT(*)::int AS provider_count,
                    SUM(CASE WHEN providertype = 'etown_verified' THEN 1 ELSE 0 END)::int as verified_count,
                    SUM(CASE WHEN providertype != 'etown_verified' OR providertype IS NULL THEN 1 ELSE 0 END)::int as unverified_count,
                    BOOL_OR(providertype = 'etown_verified') as has_verified_provider,
                    MIN(distance_km) as min_distance
                FROM ProviderDistances
                GROUP BY LOWER(areaname), LOWER(city)
            ),
            RankedAreas AS (
                SELECT DISTINCT ON (LOWER(areaname), LOWER(city))
                    areaname, latitude, longitude, distance_km, city, statecode
                FROM ProviderDistances
                ORDER BY LOWER(areaname), LOWER(city), distance_km ASC
            )
            SELECT r.areaname, r.latitude, r.longitude, r.distance_km, r.city, r.statecode, 
                s.provider_count, s.verified_count, s.unverified_count, s.has_verified_provider
            FROM RankedAreas r
            JOIN AreaStats s ON LOWER(r.areaname) = s.area_lower AND LOWER(r.city) = s.city_lower
            ORDER BY (CASE WHEN s.has_verified_provider THEN 0 ELSE 1 END) ASC, r.distance_km ASC 
            LIMIT ${len(params)-1} OFFSET ${len(params)};
            """
            
            results = await asyncio.wait_for(
                cls.fetch(query, *params),
                timeout=DB_QUERY_TIMEOUT
            )
            return [{
                "area_name": row['areaname'],
                "latitude": str(row.get('latitude')),
                "longitude": str(row.get('longitude')),
                "distance_km": round(row.get('distance_km', 0), 2),
                "city_name": row.get('city'),
                "state_code": row.get('statecode'),
                "provider_count": row.get('provider_count', 0),
                "verified_count": row.get('verified_count', 0),
                "unverified_count": row.get('unverified_count', 0)
            } for row in results]
            
        except asyncio.TimeoutError:
            logger.warning(f"Prioritized areas query timed out for {latitude}, {longitude}")
            return []
        except Exception as e:
            logger.error(f"Error fetching prioritized areas: {e}", exc_info=True)
            return []

    @classmethod
    @with_retry()
    async def get_provider_relation_stats(cls, contact_id: str, user_profile_id: str, provider_contact_id: str, provider_user_profile_id: str) -> Dict[str, Any]:
        """Return whether the viewer and provider are connected and the provider's total connection count."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        
        is_connected_sql = """
            SELECT 1 FROM etown_providerrelation
            WHERE (fromcontactid = $1 AND fromuserprofileid = $2 AND tocontactid = $3 AND touserprofileid = $4)
               OR (fromcontactid = $3 AND fromuserprofileid = $4 AND tocontactid = $1 AND touserprofileid = $2)
            LIMIT 1
        """
        count_sql = """
            SELECT COUNT(*) as count FROM etown_providerrelation
            WHERE (fromcontactid = $1 AND fromuserprofileid = $2) OR (tocontactid = $1 AND touserprofileid = $2)
        """
        is_connected_row = await cls.fetchrow(is_connected_sql, contact_id, user_profile_id, provider_contact_id, provider_user_profile_id)
        count_row = await cls.fetchrow(count_sql, provider_contact_id, provider_user_profile_id)
        conn_count = count_row['count'] if count_row else 0
        
        return {
            "is_connected": bool(is_connected_row),
            "connection_count": conn_count
        }

    @classmethod
    @with_retry()
    async def get_providers_by_distance_prioritized(cls, latitude: float, longitude: float, areaname: Optional[str] = None, cityname: Optional[str] = None, contact_id: str = None, userprofile_id: str = None, verified: Optional[bool] = False, limit: int = 20, offset: int = 0, category_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Return a page of active providers near a point, verified-first then by distance, optionally filtered by area, town or any of several categories."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")

        logger.info(f"DB: Fetching prioritized providers. Limit: {limit}")
        distance_sql = cls._build_distance_sql_numeric('p.latitude', 'p.longitude', '$1', '$2')
        validation_sql = cls._build_numeric_lat_lon_validation_sql('p.latitude', 'p.longitude')
        
        params = [latitude, longitude]
        where_conditions = [
            validation_sql,
            "p.deleted IS DISTINCT FROM 'true'",
            "(p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')",
            "p.disabled IS NOT TRUE",
            _EXCLUDE_REVIEW_PENDING_P,
        ]

        if contact_id:
            params.append(contact_id)
            where_conditions.append(f"(p.contactid != ${len(params)} OR p.contactid IS NULL)")
        if userprofile_id:
            params.append(userprofile_id)
            where_conditions.append(f"(p.userprofileid != ${len(params)} OR p.userprofileid IS NULL)")

        if areaname:
            params.append(areaname.strip().lower())
            where_conditions.append(
                f"(TRIM(LOWER(p.areaname)) = ${len(params)} OR EXISTS ("
                "SELECT 1 FROM public.etown_etownprovider ea WHERE ea.providerid = p.providerid "
                f"AND TRIM(LOWER(ea.areaname)) = ${len(params)}))"
            )
        if cityname:
            params.append(cityname.strip().lower())
            where_conditions.append(cls._build_town_membership_sql(f"${len(params)}"))

        if verified:
            where_conditions.append("p.providertype = 'etown_verified'")

        if category_ids:
            params.extend([list(category_ids), contact_id or "", userprofile_id or ""])
            cat_p, contact_p, user_p = (f"${len(params) - i}" for i in (2, 1, 0))
            where_conditions.append(
                f"EXISTS (SELECT 1 FROM public.etown_category cf WHERE cf.categoryid = ANY({cat_p}::uuid[]) AND ("
                f"cf.categoryname = '{ALL_CATEGORY_NAME}' OR "
                f"(cf.categoryname = '{FAVOURITES_CATEGORY_NAME}' AND EXISTS ("
                "SELECT 1 FROM public.etown_collectionitems ci "
                "JOIN public.etown_collections col ON col.collectionid = ci.collectionid "
                f"WHERE ci.providerid = p.providerid AND col.usercontactid = {contact_p} AND col.useruserprofileid = {user_p} "
                "AND (ci.isfavourite IS NULL OR LOWER(ci.isfavourite) != 'unfavourite')))"
                f" OR (cf.categoryname NOT IN ('{ALL_CATEGORY_NAME}', '{FAVOURITES_CATEGORY_NAME}') AND EXISTS ("
                "SELECT 1 FROM public.etown_providerservices psf "
                "WHERE psf.userprofileid = p.userprofileid AND cf.categoryname = "
                "COALESCE(NULLIF(BTRIM(psf.normalized_category), ''), NULLIF(BTRIM(psf.servicecategory), ''), 'other')))"
                "))"
            )

        params.extend([limit, offset])
        
        logger.info(f"Querying prioritized providers for coords: {latitude}, {longitude}, limit: {limit}, offset: {offset}")
        
        query = f"""
        WITH CandidateProviders AS (
            SELECT
                p.providerid, p.userprofileid, p.contactid, p.businessname, p.userprofileidname, p.description,
                p.address, p.statecode, p.providertype, p.publicsearchprovider, p.mobilenumber, p.email,
                p.creationdate, p.latitude, p.longitude, p.serviceimageurl, p.background_image, p.image_stack,
                COALESCE(ep.townname, p.city) as city,
                COALESCE(ep.areaname, p.areaname) as areaname,
                c.mobilephone,
                COALESCE(r.total_bookings, 0) AS memberbookingcount,
                COALESCE(r.total_ratings, 0) AS ratingcount,
                COALESCE(r.avg_overall_rating, 0.0) AS overallrating,
                {distance_sql}
            FROM public.etown_provider p
            LEFT JOIN public.etown_contact c ON p.contactid = c.contactid
            LEFT JOIN LATERAL (
                SELECT townname, areaname
                FROM public.etown_etownprovider
                WHERE providerid = p.providerid
                ORDER BY is_primary DESC
                LIMIT 1
            ) ep ON TRUE
            LEFT JOIN (
                SELECT provideruserprofileid,
                    MAX(memberbookingcount) as total_bookings,
                    MAX(ratingcount) as total_ratings,
                    MAX(overallrating) as avg_overall_rating
                FROM etown_ratings
                GROUP BY provideruserprofileid
            ) r ON p.userprofileid = r.provideruserprofileid
            WHERE {" AND ".join(where_conditions)}
            ORDER BY (CASE WHEN p.providertype = 'etown_verified' THEN 0 ELSE 1 END) ASC, distance_km ASC
            LIMIT ${len(params)-1} OFFSET ${len(params)}
        ),
        ProviderServicesImages AS (
            SELECT
                ps.userprofileid,
                MAX(ps.serviceimageurl) AS Etown_profileimage,
                MAX(COALESCE(ps.serviceimageurl1, ps.serviceimageurl2, ps.serviceimageurl3)) AS Etown_serviceimageurl,
                MAX(ps.serviceimageurl1) AS Etown_offerimageurl_source,
                STRING_AGG(DISTINCT COALESCE(NULLIF(BTRIM(ps.normalized_category), ''), NULLIF(BTRIM(ps.servicecategory), ''), 'other'), ', ') AS etown_category
            FROM public.etown_providerservices ps
            WHERE ps.userprofileid IN (SELECT userprofileid FROM CandidateProviders)
            GROUP BY ps.userprofileid
        ),
        RequestCounts AS (
            SELECT
                rsl.serviceprovideruserprofileid,
                COUNT(*)::int AS total_members
            FROM public.etown_allrequests ar
            JOIN public.etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid
            WHERE ar.requeststatus = 'completed'
              AND rsl.serviceprovideruserprofileid IN (SELECT userprofileid FROM CandidateProviders)
            GROUP BY rsl.serviceprovideruserprofileid
        )
        SELECT
            cp.providerid, cp.userprofileid, cp.contactid, cp.businessname, cp.userprofileidname, cp.description,
            cp.address, cp.statecode, cp.providertype, cp.publicsearchprovider, cp.mobilenumber, cp.email,
            cp.creationdate, cp.latitude, cp.longitude, cp.serviceimageurl, cp.background_image, cp.image_stack,
            cp.city, cp.areaname, cp.mobilephone,
            psi.Etown_profileimage, psi.Etown_serviceimageurl, psi.Etown_offerimageurl_source, psi.etown_category,
            cp.memberbookingcount, cp.ratingcount, cp.overallrating, cp.distance_km,
            COALESCE(rc.total_members, 0) AS total_members
        FROM CandidateProviders cp
        LEFT JOIN ProviderServicesImages psi ON cp.userprofileid = psi.userprofileid
        LEFT JOIN RequestCounts rc ON cp.userprofileid = rc.serviceprovideruserprofileid
        ORDER BY (CASE WHEN cp.providertype = 'etown_verified' THEN 0 ELSE 1 END) ASC, cp.distance_km ASC
        """
        try:
            results = await cls.fetch(query, *params)
            return [dict(row) for row in results]
        except Exception as e:
            logger.error(f"Error fetching prioritized providers: {e}", exc_info=True)
            return []

    @classmethod
    @with_retry()
    async def get_categories_by_location(cls, latitude: float, longitude: float, areaname: Optional[str] = None, cityname: Optional[str] = None, contact_id: str = None, userprofile_id: str = None, verified: Optional[bool] = False) -> List[Dict[str, Any]]:
        """Return All, Favourites, then the etown_category rows of the providers get_providers_by_distance_prioritized would list, most providers first.

        Args:
            latitude: Requester's latitude.
            longitude: Requester's longitude.
            areaname: Area the providers must serve, if given.
            cityname: Town the providers must belong to, if given.
            contact_id: Caller's contact id, whose own providers are left out.
            userprofile_id: Caller's profile id, whose own providers are left out.
            verified: True keeps only etown_verified providers.

        Returns:
            Rows with categoryid, categoryname, displayname, displayname_ta, categoryimage, provider_count and the caller's ispinned.
        """
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")

        distance_sql = cls._build_distance_sql_numeric('p.latitude', 'p.longitude', '$1', '$2')
        params = [latitude, longitude]
        where_conditions = [
            cls._build_numeric_lat_lon_validation_sql('p.latitude', 'p.longitude'),
            "p.deleted IS DISTINCT FROM 'true'",
            "(p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')",
            "p.disabled IS NOT TRUE",
            _EXCLUDE_REVIEW_PENDING_P,
        ]

        if contact_id:
            params.append(contact_id)
            where_conditions.append(f"(p.contactid != ${len(params)} OR p.contactid IS NULL)")
        if userprofile_id:
            params.append(userprofile_id)
            where_conditions.append(f"(p.userprofileid != ${len(params)} OR p.userprofileid IS NULL)")

        if areaname:
            params.append(areaname.strip().lower())
            where_conditions.append(
                f"(TRIM(LOWER(p.areaname)) = ${len(params)} OR EXISTS ("
                "SELECT 1 FROM public.etown_etownprovider ea WHERE ea.providerid = p.providerid "
                f"AND TRIM(LOWER(ea.areaname)) = ${len(params)}))"
            )
        if cityname:
            params.append(cityname.strip().lower())
            where_conditions.append(cls._build_town_membership_sql(f"${len(params)}"))

        if verified:
            where_conditions.append("p.providertype = 'etown_verified'")

        radius_filter = ""
        if not areaname and not cityname:
            params.append(MYTOWN_CATEGORIES_RADIUS_KM)
            radius_filter = f"WHERE distance_km <= ${len(params)}"

        params.extend([contact_id or "", userprofile_id or "", (cityname or "").strip().lower(), (areaname or "").strip().lower()])
        contact_p, user_p, city_p, area_p = (f"${len(params) - i}" for i in (3, 2, 1, 0))
        reserved = f"('{ALL_CATEGORY_NAME}', '{FAVOURITES_CATEGORY_NAME}')"

        query = f"""
        WITH LocationProviders AS (
            SELECT p.userprofileid, p.providerid, {distance_sql}
            FROM public.etown_provider p
            WHERE {" AND ".join(where_conditions)}
        ),
        ScopedProviders AS (
            SELECT userprofileid, providerid FROM LocationProviders {radius_filter}
        ),
        ProviderCategories AS (
            SELECT DISTINCT
                ps.userprofileid,
                COALESCE(NULLIF(BTRIM(ps.normalized_category), ''), NULLIF(BTRIM(ps.servicecategory), ''), 'other') AS categoryname
            FROM public.etown_providerservices ps
            WHERE ps.userprofileid IN (SELECT userprofileid FROM ScopedProviders)
        ),
        CategoryCounts AS (
            SELECT categoryname, COUNT(*)::int AS provider_count
            FROM ProviderCategories
            WHERE categoryname NOT IN {reserved}
            GROUP BY categoryname
            UNION ALL
            SELECT '{ALL_CATEGORY_NAME}', (SELECT COUNT(*)::int FROM ScopedProviders)
            UNION ALL
            SELECT '{FAVOURITES_CATEGORY_NAME}', (
                SELECT COUNT(DISTINCT ci.providerid)::int
                FROM public.etown_collectionitems ci
                JOIN public.etown_collections col ON col.collectionid = ci.collectionid
                WHERE col.usercontactid = {contact_p} AND col.useruserprofileid = {user_p}
                  AND (ci.isfavourite IS NULL OR LOWER(ci.isfavourite) != 'unfavourite')
                  AND ci.providerid IN (SELECT providerid FROM ScopedProviders)
            )
        )
        SELECT
            c.categoryid::text AS categoryid,
            cc.categoryname,
            c.displayname,
            c.displayname_ta,
            c.categoryimage,
            cc.provider_count,
            BOOL_OR(cpin.categorypinid IS NOT NULL) AS ispinned
        FROM CategoryCounts cc
        LEFT JOIN public.etown_category c ON c.categoryname = cc.categoryname
        LEFT JOIN public.etown_category_pin cpin ON cpin.categoryid = c.categoryid
            AND cpin.userprofileid = {user_p} AND cpin.cityname = {city_p}
            AND cpin.areaname = {area_p} AND cpin.ispinned
        GROUP BY c.categoryid, cc.categoryname, c.displayname, c.displayname_ta, c.categoryimage, cc.provider_count
        ORDER BY
            CASE cc.categoryname WHEN '{ALL_CATEGORY_NAME}' THEN 0 WHEN '{FAVOURITES_CATEGORY_NAME}' THEN 1 ELSE 2 END,
            cc.provider_count DESC, cc.categoryname ASC
        """
        try:
            results = await cls.fetch(query, *params)
            return [dict(row) for row in results]
        except Exception as e:
            logger.error(
                "mytown categories: query failed, returning an empty list | city=%s area=%s | %s: %s",
                cityname, areaname, type(e).__name__, e, exc_info=True,
            )
            return []

    @classmethod
    @with_retry()
    async def get_verified_providers_optimized(cls, latitude: Optional[float] = None, longitude: Optional[float] = None, cityname: Optional[str] = None, limit: int = 20, offset: int = 0, contact_id: Optional[str] = None, userprofile_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return a page of active etown_verified providers by distance or rating, optionally in a town, excluding the caller's own."""
        if cls._pool is None or cls._pool._closed: 
            raise RuntimeError("Database pool not initialized")
        
        logger.info("verified_providers: fetching etown_verified providers | limit=%s", limit)
        
        params = []
        where_conditions = [
            "p.providertype = 'etown_verified'",
            "p.deleted IS DISTINCT FROM 'true'",
            "(p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')",
            "p.disabled IS NOT TRUE",
            _EXCLUDE_REVIEW_PENDING_P,
        ]

        if latitude is not None and longitude is not None:
            params.extend([latitude, longitude])
            distance_sql = cls._build_distance_sql('p.latitude', 'p.longitude', '$1', '$2')
            validation_sql = cls._build_lat_lon_validation_sql('p.latitude', 'p.longitude')
            where_conditions.append(validation_sql)
            order_clause = "ORDER BY distance_km ASC"
        else:
            distance_sql = "NULL AS distance_km"
            order_clause = "ORDER BY COALESCE(r.avg_overall_rating, 0.0) DESC, COALESCE(r.total_bookings, 0) DESC"
        
        if cityname:
            params.append(cityname.strip().lower())
            where_conditions.append(cls._build_town_membership_sql(f"${len(params)}"))

        if contact_id:
            params.append(contact_id)
            where_conditions.append(f"(p.contactid != ${len(params)} OR p.contactid IS NULL)")
        if userprofile_id:
            params.append(userprofile_id)
            where_conditions.append(f"(p.userprofileid != ${len(params)} OR p.userprofileid IS NULL)")

        params.extend([limit, offset])

        query = f"""
        WITH ProviderServicesImages AS (
            SELECT userprofileid, MAX(serviceimageurl) AS Etown_profileimage,
                MAX(COALESCE(serviceimageurl1, serviceimageurl2, serviceimageurl3)) AS Etown_serviceimageurl
            FROM public.etown_providerservices GROUP BY userprofileid
        )
        SELECT 
            p.providerid, p.userprofileid, p.contactid, p.businessname, p.userprofileidname, p.description, 
            p.address, p.statecode, p.providertype, p.publicsearchprovider, p.mobilenumber, p.email,
            p.creationdate, p.latitude, p.longitude, p.serviceimageurl, p.background_image, p.image_stack,
            COALESCE(ep.townname, p.city) as city,
            COALESCE(ep.areaname, p.areaname) as areaname,
            c.mobilephone, c.email, ps.Etown_profileimage, ps.Etown_serviceimageurl,
            COALESCE(r.total_bookings, 0) AS memberbookingcount,
            COALESCE(r.total_ratings, 0) AS ratingcount,
            COALESCE(r.avg_overall_rating, 0.0) AS overallrating,
            {distance_sql},
            (SELECT COUNT(DISTINCT rsl.serviceprovideruserprofileid) FROM public.etown_allrequests ar JOIN public.etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid WHERE rsl.serviceprovideruserprofileid = p.userprofileid AND ar.requeststatus = 'completed') AS total_members
        FROM public.etown_provider p
        LEFT JOIN public.etown_contact c ON p.contactid = c.contactid
        LEFT JOIN ProviderServicesImages ps ON p.userprofileid = ps.userprofileid
        LEFT JOIN LATERAL (
            SELECT townname, areaname
            FROM public.etown_etownprovider
            WHERE providerid = p.providerid
            ORDER BY is_primary DESC
            LIMIT 1
        ) ep ON TRUE
        LEFT JOIN (
            SELECT provideruserprofileid,
                MAX(memberbookingcount) as total_bookings,
                MAX(ratingcount) as total_ratings, 
                MAX(overallrating) as avg_overall_rating
            FROM etown_ratings GROUP BY provideruserprofileid
        ) r ON p.userprofileid = r.provideruserprofileid
        WHERE {" AND ".join(where_conditions)}
        {order_clause}
        LIMIT ${len(params)-1} OFFSET ${len(params)};
        """
        
        try:
            if latitude is not None and longitude is not None:
                logger.info(f"Querying verified providers WITH distance for coords: {latitude}, {longitude}, limit: {limit}")
            else:
                logger.info(f"Querying verified providers WITHOUT distance, limit: {limit}")
                
            results = await cls.fetch(query, *params)
            return [dict(row) for row in results]
        except Exception as e:
            logger.error(
                "verified_providers: query failed, returning an empty list | limit=%s | %s: %s",
                limit, type(e).__name__, e, exc_info=True,
            )
            return []

    @classmethod
    @with_retry()
    async def get_all_unique_verified_areas(cls, latitude: Optional[float] = None, longitude: Optional[float] = None, contact_id: Optional[str] = None, userprofile_id: Optional[str] = None, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        """Return a page of distinct areas with verified providers, nearest first when coordinates are given."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")

        logger.info(f"DB: Fetching unique verified areas. Limit: {limit}")
        params = []
        if latitude is not None and longitude is not None:
            params = [latitude, longitude]
            distance_select = f", {cls._build_distance_sql_numeric('latitude', 'longitude', '$1', '$2')}"
            order_by = "ORDER BY ca.distance_km ASC"
            closest_order = ", distance_km ASC"
        else:
            distance_select = ", NULL AS distance_km"
            order_by = "ORDER BY ca.city ASC, ca.areaname ASC"
            closest_order = ""

        validation = cls._build_numeric_lat_lon_validation_sql('latitude', 'longitude')
        where_conds = ["providertype = 'etown_verified'", "areaname IS NOT NULL AND areaname != ''", "city IS NOT NULL AND city != ''", validation,
                       "deleted IS DISTINCT FROM 'true'",
                       "(deactivated IS DISTINCT FROM 'true' OR reactivation = 'true')",
                       "disabled IS NOT TRUE",
                       _EXCLUDE_REVIEW_PENDING_BARE]

        if contact_id:
            params.append(contact_id)
            where_conds.append(f"contactid != ${len(params)}")
        if userprofile_id:
            params.append(userprofile_id)
            where_conds.append(f"userprofileid != ${len(params)}")
        
        params.extend([limit, offset])

        query = f"""
        WITH VerifiedProviderDistances AS (
            SELECT areaname, city, statecode, latitude, longitude {distance_select}
            FROM public.etown_provider WHERE {" AND ".join(where_conds)}
        ),
        ProviderCounts AS (
            SELECT LOWER(areaname) AS area_lower, LOWER(city) AS city_lower, COUNT(*)::int AS provider_count
            FROM VerifiedProviderDistances GROUP BY LOWER(areaname), LOWER(city)
        ),
        ClosestArea AS (
            SELECT DISTINCT ON (LOWER(areaname), LOWER(city))
                areaname, city, statecode, latitude, longitude, distance_km
            FROM VerifiedProviderDistances
            ORDER BY LOWER(areaname), LOWER(city) {closest_order}
        )
        SELECT ca.*, pc.provider_count
        FROM ClosestArea ca
        JOIN ProviderCounts pc ON LOWER(ca.areaname) = pc.area_lower AND LOWER(ca.city) = pc.city_lower
        {order_by} LIMIT ${len(params)-1} OFFSET ${len(params)};
        """
        try:
            if latitude is not None:
                logger.info(f"Querying all unique verified areas WITH distance for coords: {latitude}, {longitude}")
            else:
                logger.info("Querying all unique verified areas WITHOUT distance.")
                
            results = await cls.fetch(query, *params)
            return [{
                "area_name": row['areaname'],
                "latitude": str(row['latitude']),
                "longitude": str(row['longitude']),
                "distance_km": round(row.get('distance_km', 2)) if row.get('distance_km') else None,
                "city_name": row.get('city'),
                "state_code": row.get('statecode'),
                "provider_count": row.get('provider_count', 0)
            } for row in results]
        except Exception as e:
            logger.error(f"Error fetching all unique verified areas: {e}", exc_info=True)
            return []

    @classmethod
    @with_retry()
    async def get_provider_completed_requests_count(cls, provider_user_profile_id: str) -> int:
        """Return the number of completed requests served by a provider profile."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        query = """
            SELECT COUNT(*) as count
            FROM etown_allrequests ar
            JOIN etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid
            WHERE rsl.serviceprovideruserprofileid = $1
              AND ar.requeststatus = 'completed'
        """
        row = await cls.fetchrow(query, provider_user_profile_id)
        return row['count'] if row else 0

    @classmethod
    @with_retry()
    async def search_mytown_providers(cls, search_pattern: str, limit: int, offset: int, latitude: Optional[float] = None, longitude: Optional[float] = None, city_filter: Optional[str] = None) -> List[Dict]:
        """Return a page of active providers matching by name or service category, name matches first, optionally in a town."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        base_select = """
            SELECT
                p.providerid, p.userprofileid, p.contactid, p.businessname, p.userprofileidname, p.description, 
                p.address, p.statecode, p.latitude, p.longitude, 
                p.publicsearchprovider, p.serviceimageurl, p.background_image, p.image_stack, p.creationdate,
                p.mobilenumber, p.email,
                COALESCE(ep.townname, p.city) as city, 
                COALESCE(ep.areaname, p.areaname) as areaname,
                (SELECT COUNT(*) FROM etown_allrequests ar JOIN etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid WHERE rsl.serviceprovideruserprofileid = p.userprofileid AND ar.requeststatus = 'completed') as completedservices,
                (SELECT STRING_AGG(DISTINCT COALESCE(NULLIF(BTRIM(ps.normalized_category), ''), NULLIF(BTRIM(ps.servicecategory), ''), 'other'), ', ')
                   FROM public.etown_providerservices ps WHERE ps.userprofileid = p.userprofileid) AS etown_category
        """
        params = [search_pattern]
        name_match = "(TRIM(p.businessname) ILIKE $1 OR TRIM(p.userprofileidname) ILIKE $1)"
        where_clauses = [
            f"({name_match} OR EXISTS ("
            "SELECT 1 FROM public.etown_providerservices pss WHERE pss.userprofileid = p.userprofileid AND ("
            "REPLACE(COALESCE(NULLIF(BTRIM(pss.normalized_category), ''), NULLIF(BTRIM(pss.servicecategory), '')), '_', ' ') ILIKE $1 "
            "OR BTRIM(pss.servicecategory) ILIKE $1)))",
            "p.deleted IS DISTINCT FROM 'true'",
            "(p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')",
            "p.disabled IS NOT TRUE",
            _EXCLUDE_REVIEW_PENDING_P,
        ]

        if city_filter:
            params.append(city_filter)
            where_clauses.append(cls._build_town_membership_sql(f"${len(params)}"))

        order_clause = f"ORDER BY {name_match} DESC, p.creationdate DESC"
        if latitude is not None and longitude is not None:
            distance_sql = cls._build_distance_sql('p.latitude', 'p.longitude', f"${len(params)+1}", f"${len(params)+2}")
            base_select += f", {distance_sql}"
            order_clause = f"ORDER BY {name_match} DESC, distance_km ASC"
            params.extend([latitude, longitude])
        else:
            base_select += ", NULL as distance_km"
        params.extend([limit, offset])        
        query = f"""
            {base_select}
            FROM public.etown_provider p
            LEFT JOIN LATERAL (
                SELECT townname, areaname
                FROM public.etown_etownprovider
                WHERE providerid = p.providerid
                ORDER BY is_primary DESC
                LIMIT 1
            ) ep ON TRUE
            WHERE {' AND '.join(where_clauses)}
            {order_clause}
            LIMIT ${len(params)-1} OFFSET ${len(params)}
        """
        results = await cls.fetch(query, *params)
        return [dict(r) for r in results]

    @classmethod
    @with_retry()
    @alru_cache(maxsize=200, ttl=1800)
    async def search_mytown_areas(cls, search_pattern: str, limit: int, offset: int, city_filter: Optional[str] = None) -> List[Dict]:
        """Return a cached page of areas matching a pattern with provider counts, falling back to a simpler query on error."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        search_start = time.time()
        
        params = [search_pattern]
        where_clauses = ["TRIM(COALESCE(ep.areaname, p.areaname)) ILIKE $1",
                        "p.latitude IS NOT NULL AND p.longitude IS NOT NULL",
                        "p.deleted IS DISTINCT FROM 'true'",
                        "(p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')",
                        "p.disabled IS NOT TRUE",
                        _EXCLUDE_REVIEW_PENDING_P]
        
        if city_filter:
            params.append(city_filter)
            where_clauses.append(
                f"(LOWER(TRIM(ep.townname)) = ${len(params)} "
                f"OR (ep.providerid IS NULL AND LOWER(TRIM(p.city)) = ${len(params)}))"
            )

        params.extend([limit, offset])

        query = f"""
            SELECT
                COALESCE(ep.areaname, p.areaname) as areaname,
                COALESCE(ep.townname, p.city) as city,
                p.statecode,
                MAX(p.latitude) as latitude,
                MAX(p.longitude) as longitude,
                COUNT(DISTINCT p.providerid)::int as provider_count,
                COUNT(DISTINCT CASE WHEN p.providertype = 'etown_verified' THEN p.providerid END)::int as verified_count,
                COUNT(DISTINCT CASE WHEN p.providertype != 'etown_verified' OR p.providertype IS NULL THEN p.providerid END)::int as unverified_count
            FROM public.etown_provider p
            LEFT JOIN public.etown_etownprovider ep ON p.providerid = ep.providerid
            WHERE {' AND '.join(where_clauses)}
            GROUP BY 1, 2, 3
            ORDER BY provider_count DESC
            LIMIT ${len(params)-1} OFFSET ${len(params)}
        """
        try:
            results = await cls.fetch(query, *params)
            search_duration = time.time() - search_start
            if search_duration > 0.5:
                logger.warning(f"Slow MyTown area search: {search_duration:.3f}s for pattern '{search_pattern}' with city filter '{city_filter}'")
            return [dict(r) for r in results]
        except Exception as e:
            logger.error(f"Error in search_mytown_areas: {e}", exc_info=True)
            fallback_query = """
                SELECT areaname, city, statecode, latitude, longitude, 
                       COUNT(*)::int as provider_count,
                       SUM(CASE WHEN providertype = 'etown_verified' THEN 1 ELSE 0 END)::int as verified_count,
                       SUM(CASE WHEN providertype != 'etown_verified' OR providertype IS NULL THEN 1 ELSE 0 END)::int as unverified_count
                FROM etown_provider
                WHERE areaname ILIKE $1 AND latitude IS NOT NULL AND longitude IS NOT NULL
                  AND deleted IS DISTINCT FROM 'true'
                  AND (deactivated IS DISTINCT FROM 'true' OR reactivation = 'true')
                  AND disabled IS NOT TRUE
                GROUP BY 1, 2, 3
                LIMIT $2 OFFSET $3
            """
            results = await cls.fetch(fallback_query, search_pattern, limit, offset)
            return [dict(r) for r in results]

    @classmethod
    @with_retry()
    async def search_mytown_cities(cls, search_tokens: List[str], limit: int, offset: int) -> List[Dict]:
        """Return towns matching any token with provider counts, trying a pg_trgm fuzzy match when none match."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        
        search_start = time.time()
        
        search_tokens = tuple(search_tokens) if isinstance(search_tokens, list) else search_tokens
        search_params = [f"%{token}%" for token in search_tokens]
        
        query = """
            SELECT
                COALESCE(ep.townname, p.city) as city,
                p.statecode,
                MAX(COALESCE(CASE WHEN tp.townlatitude ~ '^[+-]?([0-9]*[.])?[0-9]+$' 
                                  THEN tp.townlatitude::double precision 
                                  ELSE NULL END, p.latitude)) as latitude,
                MAX(COALESCE(CASE WHEN tp.townlongitude ~ '^[+-]?([0-9]*[.])?[0-9]+$' 
                                  THEN tp.townlongitude::double precision 
                                  ELSE NULL END, p.longitude)) as longitude,
                COUNT(DISTINCT p.providerid)::int as provider_count,
                COUNT(DISTINCT CASE WHEN p.providertype = 'etown_verified' THEN p.providerid END)::int as verified_count,
                COUNT(DISTINCT CASE WHEN p.providertype != 'etown_verified' OR p.providertype IS NULL THEN p.providerid END)::int as unverified_count,
                MAX(tp.towndescription) as towndescription,
                MAX(tp.townimage) as townimage,
                COALESCE(MAX(fc.fav_count), 0) as favourite_count
            FROM public.etown_provider p
            LEFT JOIN public.etown_etownprovider ep ON p.providerid = ep.providerid
            LEFT JOIN public.etown_townprofile tp ON LOWER(TRIM(tp.townname)) = LOWER(TRIM(COALESCE(ep.townname, p.city)))
            LEFT JOIN (
                SELECT townname, COUNT(*) as fav_count
                FROM etown_favouritetown
                WHERE isfavourite = 'Favourite'
                GROUP BY townname
            ) fc ON COALESCE(ep.townname, p.city) = fc.townname
            WHERE COALESCE(ep.townname, p.city) ILIKE ANY($1::text[])
              AND p.deleted IS DISTINCT FROM 'true'
              AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
              AND p.disabled IS NOT TRUE
            GROUP BY 1, 2
            ORDER BY provider_count DESC
            LIMIT $2 OFFSET $3
        """
        
        try:
            results = await cls.fetch(query, search_params, limit, offset)
            search_duration = time.time() - search_start
            if search_duration > 0.5:
                logger.warning(f"Slow MyTown city search: {search_duration:.3f}s for tokens {search_tokens}")
            
            if not results and search_tokens:
                try:
                    fuzzy_query = """
                        SELECT
                            COALESCE(ep.townname, p.city) as city,
                            p.statecode,
                            MAX(COALESCE(CASE WHEN tp.townlatitude ~ '^[+-]?([0-9]*[.])?[0-9]+$' 
                                              THEN tp.townlatitude::double precision 
                                              ELSE NULL END, p.latitude)) as latitude,
                            MAX(COALESCE(CASE WHEN tp.townlongitude ~ '^[+-]?([0-9]*[.])?[0-9]+$' 
                                              THEN tp.townlongitude::double precision 
                                              ELSE NULL END, p.longitude)) as longitude,
                            COUNT(DISTINCT p.providerid)::int as provider_count,
                            COUNT(DISTINCT CASE WHEN p.providertype = 'etown_verified' THEN p.providerid END)::int as verified_count,
                            COUNT(DISTINCT CASE WHEN p.providertype != 'etown_verified' OR p.providertype IS NULL THEN p.providerid END)::int as unverified_count,
                            MAX(tp.towndescription) as towndescription,
                            MAX(tp.townimage) as townimage,
                            COALESCE(MAX(fc.fav_count), 0) as favourite_count,
                            MAX(word_similarity($1, COALESCE(ep.townname, p.city))) as trgm_score
                        FROM public.etown_provider p
                        LEFT JOIN public.etown_etownprovider ep ON p.providerid = ep.providerid
                        LEFT JOIN public.etown_townprofile tp ON LOWER(TRIM(tp.townname)) = LOWER(TRIM(COALESCE(ep.townname, p.city)))
                        LEFT JOIN (
                            SELECT townname, COUNT(*) as fav_count
                            FROM etown_favouritetown
                            WHERE isfavourite = 'Favourite'
                            GROUP BY townname
                        ) fc ON COALESCE(ep.townname, p.city) = fc.townname
                        WHERE word_similarity($1, COALESCE(ep.townname, p.city)) > 0.3
                          AND p.deleted IS DISTINCT FROM 'true'
                          AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
                          AND p.disabled IS NOT TRUE
                        GROUP BY 1, 2
                        HAVING MAX(word_similarity($1, COALESCE(ep.townname, p.city))) > 0.3
                        ORDER BY trgm_score DESC, provider_count DESC
                        LIMIT $2 OFFSET $3
                    """
                    raw_token = search_tokens[0] if isinstance(search_tokens, (list, tuple)) else str(search_tokens)
                    fuzzy_results = await cls.fetch(fuzzy_query, raw_token, limit, offset)
                    if fuzzy_results:
                        logger.info(
                            "pg_trgm fuzzy city match found %d results for token '%s'",
                            len(fuzzy_results), raw_token,
                        )
                        results = fuzzy_results
                except Exception as fuzzy_err:
                    logger.debug("pg_trgm fuzzy city search failed (extension may not be available): %s", fuzzy_err)
            
            return [dict(r) for r in results]
        except Exception as e:
            logger.error(f"Error in search_mytown_cities: {e}", exc_info=True)
            fallback_query = """
                SELECT city, statecode, latitude, longitude, 
                       COUNT(*)::int as provider_count,
                       SUM(CASE WHEN providertype = 'etown_verified' THEN 1 ELSE 0 END)::int as verified_count,
                       SUM(CASE WHEN providertype != 'etown_verified' OR providertype IS NULL THEN 1 ELSE 0 END)::int as unverified_count,
                       NULL as towndescription, NULL as townimage, 0 as favourite_count
                FROM etown_provider
                WHERE city ILIKE ANY($1::text[]) AND latitude IS NOT NULL AND longitude IS NOT NULL
                  AND deleted IS DISTINCT FROM 'true'
                  AND (deactivated IS DISTINCT FROM 'true' OR reactivation = 'true')
                  AND disabled IS NOT TRUE
                GROUP BY 1, 2, 3, 4
                ORDER BY provider_count DESC
                LIMIT $2 OFFSET $3
            """
            results = await cls.fetch(fallback_query, search_params, limit, offset)
            return [dict(r) for r in results]

    @classmethod
    @with_retry()
    async def fetch_collections_list(cls, contact_id: str, user_profile_id: str, limit: int = 20, offset: int = 0) -> List[Dict]:
        """Return a page of the user's collections, private MyTown first, then private, then newest."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        query = """
            SELECT collectionid, collectionunicid, collectionname, collectiontype, "isMytown", createdon
            FROM etown_collections WHERE usercontactid = $1 AND useruserprofileid = $2
            ORDER BY
                CASE WHEN LOWER(collectiontype) = 'private' AND LOWER("isMytown") = 'yes' THEN 1
                     WHEN LOWER(collectiontype) = 'private' THEN 2 ELSE 3 END,
                createdon DESC LIMIT $3 OFFSET $4
        """
        results = await cls.fetch(query, contact_id, user_profile_id, limit, offset)
        return [dict(r) for r in results]

    @classmethod
    @with_retry()
    async def fetch_collection_items_bulk(cls, collection_ids: List[str]) -> List[Dict]:
        """Return non-unfavourited items of the given collections joined to their active provider rows."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        if not collection_ids: return []
        query = """
            SELECT
                ci.collectionid, ci.collectionitemsid, ci.isfavourite, ci.providerid, ci.createdon as item_createdon,
                p.businessname, p.serviceimageurl, p.background_image, p.image_stack, p.city, p.providertype, p.userprofileid,
                p.contactid, p.description, p.mobilenumber, p.email, p.creationdate,
                p.address, p.statecode, p.areaname, p.latitude, p.longitude, p.publicsearchprovider,
                (SELECT STRING_AGG(DISTINCT COALESCE(NULLIF(BTRIM(ps.normalized_category), ''), NULLIF(BTRIM(ps.servicecategory), ''), 'other'), ', ')
                   FROM public.etown_providerservices ps WHERE ps.userprofileid = p.userprofileid) AS etown_category
            FROM etown_collectionitems ci
            LEFT JOIN etown_provider p ON ci.providerid = p.providerid
            WHERE ci.collectionid = ANY($1::text[]) AND (ci.isfavourite IS NULL OR LOWER(ci.isfavourite) != 'unfavourite')
              AND p.deleted IS DISTINCT FROM 'true'
              AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
              AND p.disabled IS NOT TRUE
        """
        results = await cls.fetch(query, collection_ids)
        return [dict(r) for r in results]

    @classmethod
    async def fetch_provider_collection_links(cls, contact_id: str, user_profile_id: str, provider_ids: List[str]) -> List[Dict]:
        """Return the user's non-unfavourited collection items holding any of the providers, with their collection ids."""
        if not provider_ids: return []
        query = """
            SELECT ci.providerid, ci.collectionid, ci.collectionitemsid, col.collectionunicid
            FROM etown_collectionitems ci
            JOIN etown_collections col ON col.collectionid = ci.collectionid
            WHERE col.usercontactid = $1 AND col.useruserprofileid = $2
              AND ci.providerid = ANY($3::text[])
              AND (ci.isfavourite IS NULL OR LOWER(ci.isfavourite) != 'unfavourite')
        """
        results = await cls.fetch_once(query, contact_id, user_profile_id, provider_ids)
        return [dict(r) for r in results]

    @classmethod
    @with_retry()
    async def fetch_collection_metadata(cls, collection_id: str) -> Optional[Dict]:
        """Return the metadata row of one collection, or None."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        query = "SELECT collectionid, collectionunicid, collectionname, collectiontype, \"isMytown\", createdon FROM etown_collections WHERE collectionid = $1"
        row = await cls.fetchrow(query, collection_id)
        return dict(row) if row else None

    @classmethod
    @with_retry()
    async def fetch_collection_id_by_unique_id(cls, unique_id: str) -> Optional[str]:
        """Return the collection id for a collectionunicid, or None."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        query = "SELECT collectionid FROM etown_collections WHERE collectionunicid = $1"
        row = await cls.fetchrow(query, unique_id)
        return row['collectionid'] if row else None

    @classmethod
    @with_retry()
    async def fetch_single_provider_details(cls, provider_id: str) -> Optional[Dict]:
        """Return an enabled, non-review-pending provider row with its ratings and booking counts, or None."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        query = """
            SELECT
                p.providerid, p.businessname, p.userprofileidname, p.description, 
                p.address, p.city, p.statecode, p.providertype, p.publicsearchprovider,
                p.mobilenumber, p.email, p.serviceimageurl, p.background_image, p.contactid, p.userprofileid, p.creationdate,
                COALESCE(r.overallrating, 0.0) as overallrating,
                COALESCE(r.ratingcount, 0) as ratingcount,
                COALESCE(r.memberbookingcount, 0) as memberbookingcount,
                (SELECT STRING_AGG(DISTINCT COALESCE(NULLIF(BTRIM(ps.normalized_category), ''), NULLIF(BTRIM(ps.servicecategory), ''), 'other'), ', ')
                   FROM public.etown_providerservices ps WHERE ps.userprofileid = p.userprofileid) AS etown_category
            FROM public.etown_provider p
            LEFT JOIN etown_ratings r ON p.userprofileid = r.provideruserprofileid
            WHERE p.providerid = $1
              AND p.disabled IS NOT TRUE
              AND """ + _EXCLUDE_REVIEW_PENDING_P + """
        """
        row = await cls.fetchrow(query, provider_id)
        return dict(row) if row else None

    @classmethod
    @with_retry()
    async def fetch_services_bulk(cls, provider_ids: List[str]) -> List[Dict]:
        """Return the service rows of the given providers."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        if not provider_ids: return []
        query = """
            SELECT
                providerservicesid, services AS servicename, businesscategory, servicecategory,
                serviceimageurl, serviceimageurl1, serviceimageurl2, serviceimageurl3,
                pricerange, servicedescription, providerid
            FROM public.etown_providerservices WHERE providerid = ANY($1::text[])
        """
        results = await cls.fetch(query, provider_ids)
        return [dict(r) for r in results]
    
    @classmethod
    @with_retry()
    async def fetch_provider_feeds(
        cls,
        provider_user_profile_id: str,
        limit: int = 10,
        offset: int = 0,
        viewer_contact_id: Optional[str] = None,
    ) -> List[Dict]:
        """Return a page of a provider's feed posts visible to the viewer under the circle-only rule."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        query = """
            SELECT
                f.feedid, f.feedfilelocationurl, f.feedfilelocation2, f.feedfilelocation3,
                f.feedfilelocation4, f.feedfilelocation5, f.feedvideofilelocation,
                f.thumbnailfileurl, f.feedtext, f.createdon,
                (SELECT COUNT(*) FROM etown_feedcomments WHERE feedid = f.feedid) as comments_count,
                (SELECT COUNT(*) FROM etown_feedlikes WHERE feedid = f.feedid) as likes_count
            FROM etown_feed f
            WHERE f.userprofileid = $1
              AND """ + CIRCLE_VISIBILITY_SQL.format(viewer="$4::text") + """
            ORDER BY f.createdon DESC
            LIMIT $2 OFFSET $3
        """
        results = await cls.fetch(query, provider_user_profile_id, limit, offset, viewer_contact_id)
        return [dict(r) for r in results]
    
    @classmethod
    @with_retry()
    async def check_town_presence_in_db(cls, town_name: str, city_name: Optional[str] = None) -> Optional[Dict]:
        """Return the town profile matching a name case-insensitively, preferring a matching city_name, or None."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")

        query = """
            SELECT townprofileid, townname, cityname, townstatus
            FROM etown_townprofile
            WHERE lower(btrim(townname)) = lower(btrim($1))
            ORDER BY
                CASE WHEN $2::text IS NOT NULL
                      AND lower(btrim(cityname)) = lower(btrim($2)) THEN 0 ELSE 1 END ASC,
                CASE WHEN townprofileid != selfreferencingid THEN 0 ELSE 1 END ASC,
                createdon ASC NULLS LAST
            LIMIT 1
        """
        row = await cls.fetchrow(query, town_name.strip(), city_name.strip() if city_name else None)
        return dict(row) if row else None

    @classmethod
    @with_retry()
    async def get_provider_count_by_city(cls, city_name: str, areaname: Optional[str] = None, contact_id: Optional[str] = None, userprofile_id: Optional[str] = None) -> dict:
        """Return total, verified and unverified active provider counts for a town, optionally one area, leaving out the caller's own providers.

        Args:
            city_name: Town the providers must belong to under the town membership rule.
            areaname: Area the providers must serve, if given.
            contact_id: Caller's contact id, whose own providers are left out.
            userprofile_id: Caller's profile id, whose own providers are left out.

        Returns:
            Dict with provider_count, verified_count and unverified_count.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool not initialized")

        params: List[Any] = [city_name.strip().lower()]
        where_conditions = [
            cls._build_town_membership_sql("$1"),
            "p.deleted IS DISTINCT FROM 'true'",
            "(p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')",
            "p.disabled IS NOT TRUE",
            _EXCLUDE_REVIEW_PENDING_P,
        ]
        if contact_id:
            params.append(contact_id)
            where_conditions.append(f"(p.contactid != ${len(params)} OR p.contactid IS NULL)")
        if userprofile_id:
            params.append(userprofile_id)
            where_conditions.append(f"(p.userprofileid != ${len(params)} OR p.userprofileid IS NULL)")
        if areaname and areaname.strip():
            params.append(areaname.strip().lower())
            where_conditions.append(
                f"(TRIM(LOWER(p.areaname)) = ${len(params)} OR EXISTS ("
                "SELECT 1 FROM public.etown_etownprovider ea WHERE ea.providerid = p.providerid "
                f"AND TRIM(LOWER(ea.areaname)) = ${len(params)}))"
            )

        query = f"""
        SELECT
            COUNT(DISTINCT p.providerid)::int as provider_count,
            COUNT(DISTINCT CASE WHEN p.providertype = 'etown_verified' THEN p.providerid END)::int as verified_count,
            COUNT(DISTINCT CASE WHEN p.providertype != 'etown_verified' OR p.providertype IS NULL THEN p.providerid END)::int as unverified_count
        FROM public.etown_provider p
        WHERE {" AND ".join(where_conditions)}
        """
        try:
            result = await cls.fetchrow(query, *params)
            if result:
                return {
                    'provider_count': int(result['provider_count']) if result['provider_count'] is not None else 0,
                    'verified_count': int(result['verified_count']) if result['verified_count'] is not None else 0,
                    'unverified_count': int(result['unverified_count']) if result['unverified_count'] is not None else 0
                }
            return {'provider_count': 0, 'verified_count': 0, 'unverified_count': 0}
        except Exception as e:
            logger.error(f"Error getting provider count for city {city_name}: {e}", exc_info=True)
            return {'provider_count': 0, 'verified_count': 0, 'unverified_count': 0}

    @classmethod
    @with_retry()
    async def get_town_view_count(cls, target_date, limit: int, offset: int) -> dict:
        """Return the total and a page of town view events for an IST date, with town and viewer names."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool not initialized")
            
        count_query = """
            SELECT COUNT(*) 
            FROM public.etown_town_view_tracking v
            WHERE DATE(v.created_on AT TIME ZONE 'UTC' AT TIME ZONE 'IST') = $1::date
        """
        
        data_query = """
            SELECT 
                tp.townname AS town_name,
                COALESCE(up.name, c.firstname) AS viewer_name,
                v.created_on AS view_time
            FROM public.etown_town_view_tracking v
            LEFT JOIN public.etown_townprofile tp 
                ON v.townprofileid = tp.townprofileid
            LEFT JOIN public.etown_userprofile up 
                ON v.userprofileid = up.userprofileid
            LEFT JOIN public.etown_contact c
                ON up.contactid = c.contactid
            WHERE DATE(v.created_on AT TIME ZONE 'UTC' AT TIME ZONE 'IST') = $1::date
            ORDER BY v.created_on DESC
            LIMIT $2 OFFSET $3
        """
        
        try:
            total_count = await cls.fetchval(count_query, target_date)
            records = await cls.fetch(data_query, target_date, limit, offset)
            
            return {
                "total_count": total_count or 0,
                "data": [dict(r) for r in records]
            }
        except Exception as e:
            logger.error(f"Error executing get_town_view_count for {target_date}: {e}", exc_info=True)
            raise

    @classmethod
    @with_retry()
    async def insert_town_profile_in_db(
        cls,
        townprofileid: str,
        townname: str,
        cityname: str,
        selfreferencingid: str,
        towndescription: Optional[str],
        townlatitude: str,
        townlongitude: str,
        townimage: Optional[str],
        townstatus: str = "2",
    ) -> Tuple[str, bool]:
        """Insert a town profile unless a matching town exists within the dedup radius, else reuse the match.

        Args:
            townprofileid: UUID to use if this call creates the row.
            townname: Title-cased town name, the deduplication key.
            cityname: Parent district or city.
            selfreferencingid: townprofileid for admin level 3 roots, else the admin level 3 townprofileid.
            towndescription: AI-generated description.
            townlatitude: Latitude as text.
            townlongitude: Longitude as text.
            townimage: Optional image URL.
            townstatus: Status code string, default "2".

        Returns:
            (effective townprofileid, True only if this call inserted it).

        Raises:
            RuntimeError: When the row was neither inserted nor found.
        """
        query = f"""
            INSERT INTO etown_townprofile
                (townprofileid, townname, cityname, selfreferencingid, towndescription,
                 townlatitude, townlongitude, townimage, townstatus, createdon)
            SELECT $1, $2, $3, $4, $5, $6, $7, $8, $9, NOW()
            WHERE NOT EXISTS (
                SELECT 1 FROM etown_townprofile
                WHERE {_TOWN_MATCH_PREDICATE.format(
                    name='$2', lat='$6', lon='$7', radius='$10')}
            )
            RETURNING townprofileid
        """
        try:
            inserted = await cls.fetchval(
                query,
                townprofileid, townname, cityname, selfreferencingid, towndescription,
                townlatitude, townlongitude, townimage, townstatus,
                TOWN_DEDUP_RADIUS_KM,
            )
        except UniqueViolationError:
            logger.info("mytown: a town profile already exists within the dedup radius; not inserting")
            inserted = None

        if inserted:
            return inserted, True

        existing = await cls.find_matching_town(townname, townlatitude, townlongitude)
        if existing and existing.get("townprofileid"):
            logger.info(
                "[town_assignment] Town '%s' already exists as '%s' (%s) — reusing instead of "
                "inserting %s",
                townname, existing.get("townname"), existing["townprofileid"], townprofileid,
            )
            return existing["townprofileid"], False

        raise RuntimeError(f"Town profile for '{townname}' was neither inserted nor found")

    @classmethod
    @with_retry()
    async def find_nearest_town_by_distance(
        cls,
        latitude: float,
        longitude: float,
        radius_km: float = TOWN_FALLBACK_MAX_KM,
    ) -> Optional[Dict[str, Any]]:
        """Return the nearest town profile with parseable coordinates within radius_km, with its distance, or None."""
        row = await cls.fetchrow(
            """
            SELECT townprofileid, townname, cityname,
                   etown_town_distance_km(townlatitude, townlongitude, $1::text, $2::text) AS distance_km
            FROM etown_townprofile
            WHERE townname IS NOT NULL
              AND etown_town_distance_km(townlatitude, townlongitude, $1::text, $2::text) IS NOT NULL
              AND etown_town_distance_km(townlatitude, townlongitude, $1::text, $2::text) <= $3
            ORDER BY 4 ASC, createdon ASC NULLS LAST
            LIMIT 1
            """,
            str(latitude), str(longitude), float(radius_km),
        )
        return dict(row) if row else None

    @classmethod
    @with_retry()
    async def find_matching_town(
        cls,
        townname: str,
        townlatitude: Optional[str] = None,
        townlongitude: Optional[str] = None,
        radius_km: float = TOWN_DEDUP_RADIUS_KM,
    ) -> Optional[Dict]:
        """Return the town matching a name exactly or canonically within radius_km, exact then nearest first, or None.

        Args:
            townname: Town name to resolve.
            townlatitude: Latitude as text; without it a canonical match must be unique.
            townlongitude: Longitude as text.
            radius_km: Farthest a canonical-name match may be from the coordinates.

        Returns:
            townprofileid, townname, cityname and townstatus, or None.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool not initialized")

        query = f"""
            SELECT townprofileid, townname, cityname, townstatus
            FROM etown_townprofile
            WHERE {_TOWN_MATCH_PREDICATE.format(
                name='$1', lat='$2', lon='$3', radius='$4')}
            ORDER BY
                CASE WHEN lower(btrim(townname)) = lower(btrim($1)) THEN 0 ELSE 1 END ASC,
                etown_town_distance_km(townlatitude, townlongitude, $2::text, $3::text)
                    ASC NULLS LAST,
                createdon ASC NULLS LAST
            LIMIT 1
        """
        row = await cls.fetchrow(
            query, townname.strip(), townlatitude, townlongitude, radius_km
        )
        return dict(row) if row else None

    @classmethod
    @with_retry()
    async def get_town_name_by_id(cls, townprofileid: str) -> Optional[str]:
        """Return the stored townname of a town profile, or None."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool not initialized")
        return await cls.fetchval(
            "SELECT townname FROM etown_townprofile WHERE townprofileid = $1", townprofileid
        )

    @classmethod
    @with_retry()
    async def insert_etownprovider_in_db(
        cls,
        etownproviderid: str,
        providerid: str,
        providerbusinessname: str,
        townprofileid: str,
        townname: str,
        areaname: Optional[str] = None,
        upsert: bool = False,
        is_primary: bool = True,
    ) -> None:
        """Insert a provider-town link, ignoring conflicts, or update it in place when upsert is True.

        Args:
            etownproviderid: Primary key; providerid for the home town, uuid5 for coverage.
            providerid: Provider id in etown_provider.
            providerbusinessname: Business name of the provider.
            townprofileid: Town profile id.
            townname: Denormalised town name.
            areaname: Optional area within the town.
            upsert: True updates town, area and is_primary on primary-key conflict.
            is_primary: True for the home town, False for a coverage town.
        """
        if upsert:
            query = """
                INSERT INTO etown_etownprovider
                    (etownproviderid, providerid, providerbusinessname, townprofileid, townname, areaname, is_primary, createdon)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT (etownproviderid) DO UPDATE SET
                    townprofileid = EXCLUDED.townprofileid,
                    townname      = EXCLUDED.townname,
                    areaname      = EXCLUDED.areaname,
                    is_primary    = EXCLUDED.is_primary
            """
        else:
            query = """
                INSERT INTO etown_etownprovider
                    (etownproviderid, providerid, providerbusinessname, townprofileid, townname, areaname, is_primary, createdon)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT DO NOTHING
            """
        await cls.execute(
            query,
            etownproviderid, providerid, providerbusinessname, townprofileid, townname, areaname,
            is_primary,
        )

    @classmethod
    @with_retry()
    async def check_is_member(cls, userprofileid: str) -> tuple[bool, str | None]:
        """Return (is_member, name) for a user profile, where member means role 2 or rolename member."""
        query = "SELECT role, rolename, name FROM public.etown_userprofile WHERE userprofileid = $1"
        result = await cls.fetchrow(query, userprofileid)
        if result:
            is_member = str(result.get("role", "")) == "2" or str(result.get("rolename", "")).lower() == "member"
            name = result.get("name")
            return is_member, name
        return False, None

    @classmethod
    @with_retry()
    async def insert_town_image(
        cls, townprofileid: str, townimageurl: str, userprofileid: str, contactid: str,
        caption: Optional[str] = None,
    ) -> str:
        """Insert a pending town image uploaded by a member, mirror it to Dataverse, and return its id.

        Raises:
            HTTPException: 403 when the uploader is not a member.
        """
        townimageid = str(uuid.uuid4())
        createdon = datetime.now(timezone.utc).replace(tzinfo=None)
        is_member, name = await cls.check_is_member(userprofileid)
        if not is_member:
            raise HTTPException(status_code=403, detail="User is not a member or does not exist")
        
        query = """
            INSERT INTO public.etown_townimages (
                townimageid, townprofileid, townimageurl, userprofileid, contactid, createdon, verification, name, caption
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """
        await cls.execute(
            query, townimageid, townprofileid, townimageurl, userprofileid, contactid,
            createdon, 'pending', name, caption,
        )
        from app.etown.mytown.mytown_syncing import sync_town_image_to_dataverse

        task = asyncio.create_task(sync_town_image_to_dataverse(
            townimageid=townimageid,
            townprofileid=townprofileid,
            townimageurl=townimageurl,
            userprofileid=userprofileid,
            contactid=contactid,
            verification='pending',
            name=name,
            caption=caption,
        ))
        _TOWN_IMAGE_SYNC_TASKS.add(task)
        task.add_done_callback(_TOWN_IMAGE_SYNC_TASKS.discard)
        return townimageid

async def handle_town_view_count(target_date: str, page: int, limit: int) -> dict:
    """Return a paged TownViewCountResponse dict of town view events for a YYYY-MM-DD date, raising 500 on failure."""
    try:
        offset = (page - 1) * limit
        parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
        result = await Database.get_town_view_count(parsed_date, limit, offset)
        
        from app.schemas.api import TownViewCountItem, TownViewCountResponse
        
        items = []
        for row in result.get("data", []):
            view_time_iso = None
            if isinstance(row.get("view_time"), datetime):
                view_time_iso = row.get("view_time").isoformat()
            elif row.get("view_time"):
                view_time_iso = str(row.get("view_time"))
                
            items.append(TownViewCountItem(
                town_name=row.get("town_name"),
                viewer_name=row.get("viewer_name"),
                view_time=view_time_iso
            ))
            
        return TownViewCountResponse(
            total_count=result.get("total_count", 0),
            page=page,
            limit=limit,
            data=items
        ).model_dump()
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in handle_town_view_count: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch view counts: {str(e)}")

async def handle_provider_town_assignment(
    provider_id: str,
    business_name: Optional[str],
    townname: Optional[str],
    cityname: Optional[str],
    statecode: Optional[str] = None,
    areaname: Optional[str] = None,
    townprofileid: Optional[str] = None,
    upsert_link: bool = False,
    is_primary: bool = True,
    sync_link: bool = True,
    allow_create: bool = True,
) -> Optional[str]:
    """Link a provider to a covering town (name, nearby town, district, then create), writing Postgres and Dataverse.

    Args:
        provider_id: Provider id; also the home link's etownproviderid.
        business_name: Name stored on the link; read from etown_provider when omitted.
        townname: Submitted town name; skipped without it unless townprofileid is given.
        cityname: Submitted city or district; skipped without it unless townprofileid is given.
        statecode: State code used as geocoding context for a new town.
        areaname: Optional area stored on the link.
        townprofileid: Explicit town to link, skipping resolution and geocoding.
        upsert_link: True updates the existing link row on conflict (address edit path).
        is_primary: False adds a coverage town instead of setting the home town.
        sync_link: False writes the link to Postgres only, leaving the Dataverse link to the caller.
        allow_create: False never creates a town and falls back to the district town only.

    Returns:
        The linked townprofileid, or None when nothing resolved.

    Raises:
        PlanLimitError: When a new town link would exceed the plan's max_towns.
        HTTPException: 404 when an explicit townprofileid does not exist.
    """
    if not townprofileid and (not townname or not cityname):
        logger.warning(
            "[town_assignment] Skipping provider %s — missing townname or cityname",
            provider_id,
        )
        return None

    if not business_name:
        business_name = await Database.fetchval(
            "SELECT businessname FROM etown_provider WHERE providerid = $1", provider_id
        ) or ""

    from app.etown.mytown.mytown_syncing import (
        sync_town_profile_to_dataverse,
        sync_etownprovider_to_dataverse,
        sync_etownprovider_delete_from_dataverse,
    )

    resolved_townprofileid: Optional[str] = None
    anchor: Optional[Dict[str, Any]] = None

    if townprofileid:
        resolved_townprofileid = await Database.fetchval(
            "SELECT townprofileid FROM etown_townprofile WHERE townprofileid = $1",
            townprofileid,
        )
        if not resolved_townprofileid:
            raise HTTPException(
                status_code=404, detail=f"Town profile {townprofileid} does not exist",
            )
    else:
        anchor = await Database.fetchrow(
            "SELECT latitude, longitude FROM etown_provider WHERE providerid = $1", provider_id
        )
        metro = is_metro_place(cityname, townname)
        absorb_radius = TOWN_ABSORB_RADIUS_METRO_KM if metro else TOWN_ABSORB_RADIUS_KM

        existing = await Database.check_town_presence_in_db(townname, cityname)
        if existing and existing.get("townprofileid"):
            resolved_townprofileid = existing["townprofileid"]
            logger.info(
                "[town_assignment] Rung 1 — town '%s' already exists as %s for provider %s",
                townname, resolved_townprofileid, provider_id,
            )
        if (
            not resolved_townprofileid
            and is_primary
            and anchor
            and anchor["latitude"] is not None
            and anchor["longitude"] is not None
        ):
            covering = await Database.find_nearest_town_by_distance(
                anchor["latitude"], anchor["longitude"], absorb_radius,
            )
            if covering:
                resolved_townprofileid = covering["townprofileid"]
                logger.info(
                    "[town_assignment] Rung 2 — '%s' is already covered by town '%s' (%s) at "
                    "%.1fkm; linking provider %s there with areaname '%s' instead of creating it",
                    townname, covering["townname"], resolved_townprofileid,
                    covering["distance_km"], provider_id, areaname or townname,
                )

        district = (cityname or "").strip()

        if not resolved_townprofileid and not allow_create:
            if district and district.lower() != (townname or "").strip().lower():
                existing = await Database.check_town_presence_in_db(district)
                if existing and existing.get("townprofileid"):
                    resolved_townprofileid = existing["townprofileid"]
                    logger.info(
                        "[town_assignment] Rung 3 — no town covers '%s'; linking provider %s to "
                        "its district town '%s' (%s)",
                        townname, provider_id, district, resolved_townprofileid,
                    )
            if not resolved_townprofileid:
                logger.info(
                    "[town_assignment] No town covers '%s' (%s) within %.0fkm of provider %s and "
                    "creation is not allowed on this path; leaving it unlinked",
                    townname, cityname, absorb_radius, provider_id,
                )

        if not resolved_townprofileid and allow_create:
            try:
                presence = await handle_check_town_presence(
                    townname, cityname, statecode,
                    latitude=anchor["latitude"] if anchor else None,
                    longitude=anchor["longitude"] if anchor else None,
                )
            except Exception as exc:
                logger.error(
                    "[town_assignment] check_town_presence failed for provider %s (%s, %s): %s",
                    provider_id, townname, cityname, exc, exc_info=True,
                )
                return

            district = (presence.administartivelevel3value or cityname or "").strip()

            async def _create_town(name: str, geo_context: str) -> Optional[str]:
                """Geocode `name` and insert it as a town, unless a town already covers it.

                Args:
                    name: Town name to create — the district, or a metro's own name.
                    geo_context: District/city passed to the geocoder as disambiguating context.

                The absorb test is repeated here against the *geocoded* coordinates rather than
                the provider's, so a coverage town named by hand resolves against the place it
                names. It is the same test rung 2 applies, and it catches the case where the
                provider sits far from the town they submitted.

                The row is inserted with no image: a grounded card costs roughly ten seconds of
                Google lookup plus thirteen of generation, and this runs inside a request.
                schedule_town_card_generation fills townimage in afterwards.

                Returns:
                    Optional[str]: townprofileid of the created or pre-existing town; None on failure.
                """
                try:
                    details = await generate_towndetails_logic(name, geo_context, statecode or "")
                except Exception as exc:
                    logger.error(
                        "[town_assignment] generate_towndetails failed for '%s' (context '%s'): %s",
                        name, geo_context, exc, exc_info=True,
                    )
                    return None

                effective_cityname = district or details.cityname
                if details.latitude and details.longitude:
                    covered_by = await Database.find_nearest_town_by_distance(
                        details.latitude, details.longitude,
                        TOWN_ABSORB_RADIUS_METRO_KM if is_metro_place(effective_cityname, name)
                        else TOWN_ABSORB_RADIUS_KM,
                    )
                    if covered_by:
                        logger.info(
                            "[town_assignment] Not creating '%s' for provider %s — town '%s' (%s) "
                            "already covers it at %.1fkm",
                            name, provider_id, covered_by["townname"],
                            covered_by["townprofileid"], covered_by["distance_km"],
                        )
                        return covered_by["townprofileid"]

                candidate_id = str(uuid.uuid4())
                try:
                    effective_id, created = await Database.insert_town_profile_in_db(
                        townprofileid=candidate_id,
                        townname=details.townname,
                        cityname=effective_cityname,
                        selfreferencingid=candidate_id,
                        towndescription=details.description,
                        townlatitude=str(details.latitude),
                        townlongitude=str(details.longitude),
                        townimage=None,
                        townstatus="2",
                    )
                except Exception as exc:
                    logger.error(
                        "[town_assignment] insert_town_profile failed for '%s': %s", name, exc, exc_info=True,
                    )
                    return None

                if created:
                    await sync_town_profile_to_dataverse(
                        townprofileid=effective_id,
                        townname=details.townname,
                        cityname=effective_cityname,
                        selfreferencingid=effective_id,
                        towndescription=details.description,
                        townlatitude=details.latitude,
                        townlongitude=details.longitude,
                        townimage=None,
                        townstatus="2",
                    )
                    schedule_town_card_generation(
                        townprofileid=effective_id,
                        townname=details.townname,
                        cityname=effective_cityname,
                        statecode=statecode or None,
                        towndescription=details.description,
                        latitude=details.latitude,
                        longitude=details.longitude,
                    )
                    from app.etown.dataverse_sync.dataverse_sync_utils import refresh_content_vectors

                    await refresh_content_vectors({"etown_townprofile": [effective_id]})
                return effective_id
            if presence.townprofileid and (
                presence.townnamepresent or presence.administartivelevel3present
            ):
                resolved_townprofileid = presence.townprofileid
                logger.info(
                    "[town_assignment] Rung 3 — no town covers '%s'; linking provider %s to the "
                    "town geocoding resolved, '%s' (%s), keeping areaname '%s'",
                    townname, provider_id,
                    presence.administartivelevel3value or presence.townname,
                    resolved_townprofileid, areaname or townname,
                )

            else:
                create_name = (townname or "").strip() if metro else (
                    district or (townname or "").strip()
                )
                resolved_townprofileid = await _create_town(
                    create_name, district or cityname or create_name,
                )
                if resolved_townprofileid:
                    logger.info(
                        "[town_assignment] Rung 4 — nothing covered '%s' (%s); created %s '%s' "
                        "(%s) for provider %s",
                        townname, cityname, "metro town" if metro else "district town",
                        create_name, resolved_townprofileid, provider_id,
                    )

    if not resolved_townprofileid and allow_create:
        if anchor is None:
            anchor = await Database.fetchrow(
                "SELECT latitude, longitude FROM etown_provider WHERE providerid = $1", provider_id
            )
        if anchor and anchor["latitude"] is not None and anchor["longitude"] is not None:
            nearest = await Database.find_nearest_town_by_distance(
                anchor["latitude"], anchor["longitude"], TOWN_FALLBACK_MAX_KM,
            )
            if nearest:
                resolved_townprofileid = nearest["townprofileid"]
                logger.info(
                    "[town_assignment] Town '%s' could not be created for provider %s; linking the "
                    "nearest town '%s' (%s) at %.1fkm instead",
                    townname, provider_id, nearest["townname"], resolved_townprofileid, nearest["distance_km"],
                )
            else:
                logger.warning(
                    "[town_assignment] Town '%s' could not be created for provider %s and no town "
                    "lies within %skm of (%s, %s)",
                    townname, provider_id, TOWN_FALLBACK_MAX_KM, anchor["latitude"], anchor["longitude"],
                )
        else:
            logger.warning(
                "[town_assignment] Town '%s' could not be created for provider %s, which has no "
                "coordinates to find a nearest town from",
                townname, provider_id,
            )

    if not resolved_townprofileid:
        logger.warning(
            "[town_assignment] No town profile resolved for provider %s; skipping link creation",
            provider_id,
        )
        return

    resolved_townname = await Database.get_town_name_by_id(resolved_townprofileid) or townname or ""
    if townname and resolved_townname.strip().lower() != townname.strip().lower():
        logger.info(
            "[town_assignment] Provider %s submitted town '%s'; linking as '%s'",
            provider_id, townname, resolved_townname,
        )

    link_id = provider_id if is_primary else str(
        uuid.uuid5(uuid.NAMESPACE_DNS, f"etownprovider:{provider_id}:{resolved_townprofileid}")
    )

    try:
        plan_userprofileid = await Database.fetchval(
            "SELECT userprofileid FROM etown_provider WHERE providerid = $1", provider_id
        )
        links = await Database.fetch(
            "SELECT etownproviderid, townprofileid FROM etown_etownprovider "
            "WHERE providerid = $1 AND COALESCE(deleted, 'false') <> 'true'",
            provider_id,
        )
    except Exception as exc:
        logger.warning(
            "[town_assignment] Town cap check failed for provider %s, allowing: %s",
            provider_id, exc,
        )
    else:
        plan = await resolve_plan(plan_userprofileid)
        town_cap = plan.get("max_towns") if plan else None
        writes_row = upsert_link or all(row["etownproviderid"] != link_id for row in links)
        current_towns = {row["townprofileid"] for row in links if row["townprofileid"]}
        towns_after = {
            row["townprofileid"] for row in links
            if row["townprofileid"] and not (writes_row and row["etownproviderid"] == link_id)
        }
        if writes_row:
            towns_after.add(resolved_townprofileid)
        if (
            town_cap is not None
            and len(towns_after) > len(current_towns)
            and len(towns_after) > town_cap
        ):
            err = PlanLimitError(
                f"Your {plan['plan_name']} plan covers {town_cap} town(s). "
                f"You already cover {len(current_towns)}, so {resolved_townname} cannot be added.",
                plan_code=plan["plan_code"], limit=town_cap, used=len(current_towns),
                userprofile_id=plan_userprofileid, limit_column="max_towns",
            )
            await record_refusal(err)
            raise err

    if is_primary and upsert_link:
        superseded = await Database.fetch(
            "DELETE FROM etown_etownprovider "
            "WHERE providerid = $1 AND townprofileid = $2 AND etownproviderid <> $3 "
            "RETURNING etownproviderid",
            provider_id, resolved_townprofileid, link_id,
        )
        for row in superseded:
            logger.info(
                "[town_assignment] Provider %s moved home into covered town %s; dropping link %s",
                provider_id, resolved_townprofileid, row["etownproviderid"],
            )
            await sync_etownprovider_delete_from_dataverse(row["etownproviderid"])

    if is_primary:
        await Database.execute(
            "UPDATE etown_etownprovider SET is_primary = FALSE "
            "WHERE providerid = $1 AND etownproviderid <> $2 AND is_primary",
            provider_id, link_id,
        )

    await Database.insert_etownprovider_in_db(
        etownproviderid=link_id,
        providerid=provider_id,
        providerbusinessname=business_name,
        townprofileid=resolved_townprofileid,
        townname=resolved_townname,
        areaname=areaname,
        upsert=upsert_link,
        is_primary=is_primary,
    )
    if sync_link:
        await sync_etownprovider_to_dataverse(
            etownproviderid=link_id,
            provider_id=provider_id,
            business_name=business_name,
            townprofileid=resolved_townprofileid,
        )
    logger.info(
        "[town_assignment] Provider %s (%s) linked to %s town %s",
        provider_id, business_name, "home" if is_primary else "coverage", resolved_townprofileid,
    )
    return resolved_townprofileid


async def handle_provider_coverage_towns(
    provider_id: str,
    actionmode: str,
    town_details: List[Dict[str, Any]],
    business_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Add or hard-delete several coverage towns for a provider, each independently, mirroring deletes to Dataverse.

    Args:
        provider_id: Provider id in etown_provider.
        actionmode: add | remove.
        town_details: One entry per town, each with a name, a townprofileid, or both.
        business_name: Stored on the link; read from etown_provider when omitted.

    Returns:
        success, actionmode, providerid, towns_held and per-town results in town_details.

    Raises:
        HTTPException: 400 on bad input; else the first per-town failure when none succeeded.
        PlanLimitError: The plan cap refusal, when no town succeeded.
    """
    mode = (actionmode or "").strip().lower()
    if mode not in ("add", "remove"):
        raise HTTPException(status_code=400, detail="actionmode must be 'add' or 'remove'")
    if not provider_id:
        raise HTTPException(status_code=400, detail="providerid is required")

    seen = set()
    entries: List[Dict[str, Optional[str]]] = []
    for town in town_details or []:
        name = (town.get("name") or "").strip() or None
        town_id = (town.get("townprofileid") or "").strip() or None
        key = town_id or (name or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        entries.append({"name": name, "townprofileid": town_id})
    if not entries:
        raise HTTPException(
            status_code=400,
            detail="town_details must hold at least one town with a name or a townprofileid",
        )

    if not business_name:
        business_name = await Database.fetchval(
            "SELECT businessname FROM etown_provider WHERE providerid = $1", provider_id
        ) or ""

    from app.etown.mytown.mytown_syncing import sync_etownprovider_delete_from_dataverse

    results: List[Dict[str, Any]] = []
    first_error: Optional[Exception] = None
    for entry in entries:
        try:
            if mode == "add":
                linked_townprofileid = await handle_provider_town_assignment(
                    provider_id=provider_id,
                    business_name=business_name,
                    townname=entry["name"],
                    cityname=entry["name"],
                    townprofileid=entry["townprofileid"],
                    is_primary=False,
                )
                if not linked_townprofileid:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Could not resolve a town profile for {entry['name']}",
                    )
                results.append({
                    "name": await Database.get_town_name_by_id(linked_townprofileid) or entry["name"],
                    "townprofileid": linked_townprofileid,
                    "success": True,
                })
            else:
                link = await Database.fetchrow(
                    "SELECT etownproviderid, townprofileid, townname, is_primary "
                    "FROM etown_etownprovider "
                    "WHERE providerid = $1 "
                    "  AND (($2::text IS NOT NULL AND townprofileid = $2) "
                    "       OR ($3::text IS NOT NULL AND lower(btrim(townname)) = lower(btrim($3)))) "
                    "ORDER BY ($2::text IS NOT NULL AND townprofileid = $2) DESC, is_primary DESC "
                    "LIMIT 1",
                    provider_id, entry["townprofileid"], entry["name"],
                )
                if not link:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Provider {provider_id} is not linked to that town",
                    )
                if link["is_primary"]:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"{link['townname']} is the provider's home town and cannot be "
                            "removed — change the business address to move it."
                        ),
                    )
                await Database.execute(
                    "DELETE FROM etown_etownprovider WHERE etownproviderid = $1",
                    link["etownproviderid"],
                )
                await sync_etownprovider_delete_from_dataverse(link["etownproviderid"])
                results.append({
                    "name": link["townname"],
                    "townprofileid": link["townprofileid"],
                    "success": True,
                })
        except (PlanLimitError, HTTPException) as exc:
            logger.warning("mytown: coverage town %s failed", entry["name"], exc_info=True)
            first_error = first_error or exc
            results.append({
                "name": entry["name"],
                "townprofileid": entry["townprofileid"],
                "success": False,
                "status_code": 402 if isinstance(exc, PlanLimitError) else exc.status_code,
                "message": str(exc) if isinstance(exc, PlanLimitError) else str(exc.detail),
            })
        except Exception as exc:
            logger.error(
                "[town_assignment] Coverage %s failed for provider %s town %s: %s",
                mode, provider_id, entry["townprofileid"] or entry["name"], exc, exc_info=True,
            )
            first_error = first_error or exc
            results.append({
                "name": entry["name"],
                "townprofileid": entry["townprofileid"],
                "success": False,
                "status_code": 500,
                "message": str(exc),
            })

    if not any(result["success"] for result in results):
        raise first_error or RuntimeError("All coverage-town operations failed")

    towns_held = await Database.fetchval(
        "SELECT count(DISTINCT townprofileid) FROM etown_etownprovider "
        "WHERE providerid = $1 AND COALESCE(deleted, 'false') <> 'true'",
        provider_id,
    )
    logger.info(
        "[town_assignment] Provider %s coverage %s: %s of %s towns done; %s held",
        provider_id, mode, sum(1 for r in results if r["success"]), len(results), towns_held,
    )
    return {
        "success": all(result["success"] for result in results),
        "actionmode": mode,
        "providerid": provider_id,
        "towns_held": towns_held,
        "town_details": results,
    }


async def prune_coverage_towns_to_plan(
    provider_id: str,
    userprofile_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Hard-delete coverage town links beyond the plan's max_towns, keeping the home town then the oldest links.

    Args:
        provider_id: Provider id in etown_provider.
        userprofile_id: Profile the plan is resolved against; read from etown_provider when omitted.

    Returns:
        providerid, plan_code, max_towns, towns_held after the prune, and the removed links.
    """
    if not provider_id:
        return {"providerid": provider_id, "plan_code": None, "max_towns": None,
                "towns_held": 0, "removed": []}

    if not userprofile_id:
        userprofile_id = await Database.fetchval(
            "SELECT userprofileid FROM etown_provider WHERE providerid = $1", provider_id
        )

    plan = await resolve_plan(userprofile_id) if userprofile_id else None
    town_cap = plan.get("max_towns") if plan else None
    if town_cap is None:
        return {"providerid": provider_id, "plan_code": plan.get("plan_code") if plan else None,
                "max_towns": None, "towns_held": 0, "removed": []}

    links = await Database.fetch(
        "SELECT etownproviderid, townprofileid, townname, is_primary "
        "FROM etown_etownprovider "
        "WHERE providerid = $1 AND COALESCE(deleted, 'false') <> 'true' "
        "ORDER BY is_primary DESC, createdon ASC NULLS FIRST, etownproviderid",
        provider_id,
    )

    kept_towns: set = set()
    surplus: List[Dict[str, Any]] = []
    for link in links:
        townprofileid = link["townprofileid"]
        if not townprofileid:
            continue
        if link["is_primary"] or townprofileid in kept_towns or len(kept_towns) < town_cap:
            kept_towns.add(townprofileid)
            continue
        surplus.append(link)

    removed: List[Dict[str, Any]] = []
    from app.etown.mytown.mytown_syncing import sync_etownprovider_delete_from_dataverse

    for link in surplus:
        try:
            await Database.execute(
                "DELETE FROM etown_etownprovider WHERE etownproviderid = $1",
                link["etownproviderid"],
            )
            await sync_etownprovider_delete_from_dataverse(link["etownproviderid"])
            removed.append({
                "name": link["townname"],
                "townprofileid": link["townprofileid"],
                "etownproviderid": link["etownproviderid"],
            })
        except Exception as exc:
            logger.error(
                "[town_assignment] Provider %s could not drop coverage town %s on downgrade: %s",
                provider_id, link["townprofileid"], exc, exc_info=True,
            )

    if removed:
        logger.info(
            "[town_assignment] Provider %s pruned to %s | plan=%s | max_towns=%s | dropped=%s",
            provider_id, len(kept_towns), plan.get("plan_code"), town_cap,
            [row["townprofileid"] for row in removed],
        )
    return {
        "providerid": provider_id,
        "plan_code": plan.get("plan_code"),
        "max_towns": town_cap,
        "towns_held": len(kept_towns),
        "removed": removed,
    }


async def handle_promote_area_to_town(
    townprofileid: str,
    areaname: str,
    statecode: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a town profile for an area inside a town and repoint that area's provider links to it.

    Args:
        townprofileid: Town the area currently sits in.
        areaname: Area to promote, matched against the link's or provider's areaname.
        statecode: Optional state passed to the geocoder as context.

    Returns:
        The new townprofileid and name, the parent it left, and how many links moved.

    Raises:
        HTTPException: 422 blank area, 404 missing town or providers, 409 already a town, 502 geocode/insert failure.
    """
    from app.etown.mytown.mytown_syncing import (
        sync_town_profile_to_dataverse,
        sync_etownprovider_to_dataverse,
    )

    area = (areaname or "").strip()
    if not area:
        raise HTTPException(status_code=422, detail="areaname must not be blank")

    parent = await Database.fetchrow(
        "SELECT townprofileid, townname, cityname, selfreferencingid "
        "FROM etown_townprofile WHERE townprofileid = $1",
        townprofileid,
    )
    if not parent:
        raise HTTPException(
            status_code=404, detail=f"Town profile {townprofileid} does not exist",
        )

    if area.lower() == str(parent["townname"] or "").strip().lower():
        raise HTTPException(
            status_code=409,
            detail=f"'{area}' is the name of town {townprofileid}, not an area inside it",
        )

    existing = await Database.check_town_presence_in_db(area, parent["cityname"])
    if existing and existing.get("townprofileid"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{existing.get('townname')}' already has its own town profile "
                f"({existing['townprofileid']})"
            ),
        )

    movers = await Database.fetch(
        """
        SELECT ep.etownproviderid, ep.providerid,
               COALESCE(ep.providerbusinessname, p.businessname, '') AS businessname
          FROM etown_etownprovider ep
          JOIN etown_provider p ON p.providerid = ep.providerid
         WHERE ep.townprofileid = $1
           AND lower(btrim(COALESCE(NULLIF(btrim(ep.areaname), ''), p.areaname, '')))
               = lower(btrim($2))
           AND p.deleted IS DISTINCT FROM 'true'
           AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
           AND p.disabled IS NOT TRUE
        """,
        townprofileid, area,
    )
    if not movers:
        raise HTTPException(
            status_code=404,
            detail=f"No live provider in town {townprofileid} has areaname '{area}'",
        )

    geo_context = str(parent["cityname"] or parent["townname"] or area)
    try:
        details = await generate_towndetails_logic(area, geo_context, statecode or "")
    except Exception as exc:
        logger.error(
            "[promote_area] generate_towndetails failed for '%s' (context '%s'): %s",
            area, geo_context, exc, exc_info=True,
        )
        raise HTTPException(status_code=502, detail=f"Could not resolve details for '{area}'")

    candidate_id = str(uuid.uuid4())
    effective_cityname = str(parent["cityname"] or parent["townname"] or details.cityname)
    try:
        new_townprofileid, created = await Database.insert_town_profile_in_db(
            townprofileid=candidate_id,
            townname=details.townname,
            cityname=effective_cityname,
            selfreferencingid=parent["selfreferencingid"] or parent["townprofileid"],
            towndescription=details.description,
            townlatitude=str(details.latitude),
            townlongitude=str(details.longitude),
            townimage=None,
            townstatus="2",
        )
    except Exception as exc:
        logger.error("[promote_area] insert_town_profile failed for '%s': %s", area, exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Could not create a town for '{area}'")

    if created:
        await sync_town_profile_to_dataverse(
            townprofileid=new_townprofileid,
            townname=details.townname,
            cityname=effective_cityname,
            selfreferencingid=parent["selfreferencingid"] or parent["townprofileid"],
            towndescription=details.description,
            townlatitude=details.latitude,
            townlongitude=details.longitude,
            townimage=None,
            townstatus="2",
        )
        schedule_town_card_generation(
            townprofileid=new_townprofileid,
            townname=details.townname,
            cityname=effective_cityname,
            statecode=statecode or None,
            towndescription=details.description,
            latitude=details.latitude,
            longitude=details.longitude,
        )
        from app.etown.dataverse_sync.dataverse_sync_utils import refresh_content_vectors

        await refresh_content_vectors({"etown_townprofile": [new_townprofileid]})

    await Database.execute(
        "UPDATE etown_etownprovider SET townprofileid = $1, townname = $2 "
        "WHERE etownproviderid = ANY($3::text[])",
        new_townprofileid, details.townname, [row["etownproviderid"] for row in movers],
    )

    for row in movers:
        await sync_etownprovider_to_dataverse(
            etownproviderid=row["etownproviderid"],
            provider_id=row["providerid"],
            business_name=row["businessname"],
            townprofileid=new_townprofileid,
        )

    logger.info(
        "[promote_area] Promoted area '%s' out of town %s into town '%s' (%s); %d provider "
        "link(s) moved",
        area, townprofileid, details.townname, new_townprofileid, len(movers),
    )
    return {
        "success": True,
        "townprofileid": new_townprofileid,
        "townname": details.townname,
        "cityname": effective_cityname,
        "promoted_from": {
            "townprofileid": parent["townprofileid"],
            "townname": parent["townname"],
        },
        "providers_moved": len(movers),
        "town_created": created,
    }


async def handle_upload_town_image(request: Any) -> Any:
    """Decode a member's base64 town images, upload them to blob storage and record each as a pending town image.

    Raises:
        HTTPException: 403 non-member, 400 bad base64, 500 when no image was stored.
    """
    from app.schemas.api import UploadTownImageResponse, UploadedTownImage

    is_member, _ = await Database.check_is_member(request.userprofileid)
    if not is_member:
        raise HTTPException(status_code=403, detail="User is not a member or does not exist")

    images = request.townimage if isinstance(request.townimage, list) else [request.townimage]
    total = len(images)
    decoded: List[tuple[bytes, Optional[str]]] = []
    for position, image_item in enumerate(images, start=1):
        try:
            image = image_item.image if hasattr(image_item, "image") else image_item
            caption = image_item.caption if hasattr(image_item, "caption") else None
            image_data = image.split(",")[1] if "," in image else image
            decoded.append((base64.b64decode(image_data), caption))
        except Exception as e:
            logger.error(f"Failed to decode town image {position} of {total}: {e}")
            raise HTTPException(
                status_code=400, detail=f"Invalid base64 image data at position {position}"
            )

    container_name = normalize_container_name(settings.AZURE_BLOB_CONTAINER or "etowndev")
    uploaded: List[Any] = []
    for position, (image_bytes, caption) in enumerate(decoded, start=1):
        try:
            blob_name = f"townimages/{current_timestamp_prefix()}_{uuid.uuid4().hex}.jpg"
            blob_url = await upload_blob_bytes(container_name, blob_name, image_bytes, "image/jpeg")
        except Exception as e:
            logger.error(
                f"Failed to upload town image {position} of {total} to blob storage: {e}",
                exc_info=True,
            )
            continue

        try:
            townimageid = await Database.insert_town_image(
                townprofileid=request.townprofileid,
                townimageurl=blob_url,
                userprofileid=request.userprofileid,
                contactid=request.contactid,
                caption=caption,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Failed to insert town image {position} of {total} record into database: {e}",
                exc_info=True,
            )
            continue

        uploaded.append(
            UploadedTownImage(townimageid=townimageid, townimageurl=blob_url, caption=caption)
        )

    if not uploaded:
        raise HTTPException(status_code=500, detail="Failed to upload image")

    if len(uploaded) < total:
        message = f"{len(uploaded)} of {total} town images uploaded"
    elif total == 1:
        message = "Town image successfully uploaded"
    else:
        message = f"{total} town images successfully uploaded"

    return UploadTownImageResponse(
        success=True,
        message=message,
        townimageurl=uploaded[0].townimageurl,
        townimages=uploaded
    )


async def handle_category_pin(request: Any) -> Any:
    """Pin or unpin a category in a town and area (etown_category_pin) or at a location (etown_popular_category_pin), reusing the existing row.

    Raises:
        HTTPException: 400 blank cityname for town, or missing latitude or longitude for popular_provider; 404 unknown user or category.
    """
    from app.schemas.api import CategoryPinResponse

    popular = request.action_mode == "popular_provider"
    cityname = (request.cityname or "").strip().lower()
    areaname = (request.areaname or "").strip().lower()
    if popular and (request.latitude is None or request.longitude is None):
        raise HTTPException(status_code=400, detail="latitude and longitude are required for action_mode popular_provider")
    if not popular and not cityname:
        raise HTTPException(status_code=400, detail="cityname is required")

    category_id = str(request.category_id)
    logger.info(
        "Handler: CategoryPin. mode=%s pin=%s category=%s city=%s area=%s lat=%s lon=%s user=%s",
        request.action_mode, request.pin, category_id, cityname, areaname,
        request.latitude, request.longitude, request.userprofile_id,
    )

    lookup = await Database.fetchrow(
        """
        SELECT
            EXISTS (
                SELECT 1 FROM public.etown_userprofile
                WHERE lower(contactid) = lower($1) AND lower(userprofileid) = lower($2)
            ) AS user_exists,
            c.categoryname,
            c.displayname
        FROM (SELECT 1) AS one
        LEFT JOIN public.etown_category c ON c.categoryid = $3::uuid
        """,
        request.contact_id, request.userprofile_id, category_id,
    )
    if not lookup or not lookup["user_exists"]:
        raise HTTPException(status_code=404, detail="No such contact and user profile")
    if lookup["categoryname"] is None:
        raise HTTPException(status_code=404, detail="No such category")

    if popular:
        row = await Database.fetchrow(
            """
            INSERT INTO public.etown_popular_category_pin (contactid, userprofileid, latitude, longitude, categoryid, ispinned)
            VALUES ($1, $2, round($3::float8::numeric, 4), round($4::float8::numeric, 4), $5::uuid, $6)
            ON CONFLICT (userprofileid, latitude, longitude, categoryid)
            DO UPDATE SET ispinned = EXCLUDED.ispinned, contactid = EXCLUDED.contactid, modifiedon = (now() AT TIME ZONE 'utc')
            RETURNING popularcategorypinid::text AS categorypinid, ispinned, latitude::float8 AS latitude, longitude::float8 AS longitude
            """,
            request.contact_id, request.userprofile_id, request.latitude, request.longitude, category_id, request.pin == "true",
        )
        return CategoryPinResponse(
            success=True,
            message="Category pinned" if row["ispinned"] else "Category unpinned",
            action_mode="popular_provider",
            categorypinid=row["categorypinid"],
            category_id=category_id,
            categoryname=lookup["categoryname"],
            displayname=lookup["displayname"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            ispinned=row["ispinned"],
        )

    row = await Database.fetchrow(
        """
        INSERT INTO public.etown_category_pin (contactid, userprofileid, cityname, areaname, categoryid, ispinned)
        VALUES ($1, $2, $3, $4, $5::uuid, $6)
        ON CONFLICT (userprofileid, cityname, areaname, categoryid)
        DO UPDATE SET ispinned = EXCLUDED.ispinned, contactid = EXCLUDED.contactid, modifiedon = (now() AT TIME ZONE 'utc')
        RETURNING categorypinid::text AS categorypinid, ispinned
        """,
        request.contact_id, request.userprofile_id, cityname, areaname, category_id, request.pin == "true",
    )

    return CategoryPinResponse(
        success=True,
        message="Category pinned" if row["ispinned"] else "Category unpinned",
        categorypinid=row["categorypinid"],
        category_id=category_id,
        categoryname=lookup["categoryname"],
        displayname=lookup["displayname"],
        cityname=cityname,
        areaname=areaname or None,
        ispinned=row["ispinned"],
    )


async def handle_request_town_image(request: Any) -> Any:
    """Upload a base64 town image to blob storage and record it in etown_townreview as a pending request.

    Raises:
        HTTPException: 404 unknown town or contact, 400 bad filename or base64, 409 same file pending or the town's image, 500 on upload failure.
    """
    from app.schemas.api import RequestTownImageResponse

    filename = (request.filename or "").strip()
    if not filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    names = await Database.fetchrow(
        """
        SELECT
            (SELECT townname FROM public.etown_townprofile WHERE townprofileid = $1) AS townname,
            (SELECT firstname FROM public.etown_contact WHERE contactid = $2) AS contactidname,
            EXISTS (SELECT 1 FROM public.etown_townprofile WHERE townprofileid = $1) AS town_exists,
            EXISTS (SELECT 1 FROM public.etown_contact WHERE contactid = $2) AS contact_exists,
            EXISTS (
                SELECT 1
                FROM public.etown_townprofile tp
                JOIN public.etown_townreview tr
                  ON tr.townprofileid = tp.townprofileid
                 AND tr.contactid = $2
                 AND tr.userprofileid = $3
                 AND tr.status = 'approved'
                 AND tr.filename = $4
                WHERE tp.townprofileid = $1
                  AND regexp_replace(tp.townimage, '^.*/', '') = $4
            ) AS already_town_image,
            EXISTS (
                SELECT 1 FROM public.etown_townreview
                WHERE townprofileid = $1
                  AND contactid = $2
                  AND userprofileid = $3
                  AND status = 'pending'
                  AND filename = $4
            ) AS already_pending
        """,
        request.townprofileid,
        request.contactid,
        request.userprofileid,
        filename,
    )
    if not names["town_exists"]:
        raise HTTPException(status_code=404, detail="Town profile not found")
    if not names["contact_exists"]:
        raise HTTPException(status_code=404, detail="Contact not found")
    if names["already_town_image"] or names["already_pending"]:
        raise HTTPException(
            status_code=409,
            detail="This image has already been uploaded. Please choose a different image and try again.",
        )

    try:
        image_bytes = to_bytes(request.image)
    except ValueError:
        image_bytes = b""
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")
    content_type = detect_mime_type(image_bytes)
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="The uploaded file is not an image")

    townreviewid = str(uuid.uuid4())
    container_name = normalize_container_name(settings.AZURE_BLOB_CONTAINER or "etowndev")
    blob_name = f"townreview/{townreviewid}/{filename}"
    try:
        imageurl = await upload_blob_bytes(container_name, blob_name, image_bytes, content_type)
    except Exception as e:
        logger.error(
            f"request_town_image - blob upload failed townprofileid={request.townprofileid} error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to upload image")
    if not imageurl:
        raise HTTPException(status_code=500, detail="Failed to upload image")

    description = (request.description or "").strip() or None
    await Database.execute(
        """
        INSERT INTO public.etown_townreview (
            townreviewid, townprofileid, townname, contactid, contactidname,
            userprofileid, imageurl, filename, description, status, createdon
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending', $10)
        """,
        uuid.UUID(townreviewid),
        request.townprofileid,
        names["townname"],
        request.contactid,
        names["contactidname"],
        request.userprofileid,
        imageurl,
        filename,
        description,
        datetime.now(timezone.utc).replace(tzinfo=None),
    )

    return RequestTownImageResponse(
        success=True,
        message="Town image request submitted for review",
        townreviewid=townreviewid,
        townprofileid=request.townprofileid,
        townname=names["townname"],
        contactid=request.contactid,
        contactidname=names["contactidname"],
        userprofileid=request.userprofileid,
        imageurl=imageurl,
        filename=filename,
        description=description,
        status="pending",
    )


_TOWN_REFERENCE_BUDGET_SECONDS = 30.0
_TOWN_REFERENCE_BACKGROUND_BUDGET_SECONDS = 90.0
_TOWN_REFERENCE_JUDGE_TIMEOUT_SECONDS = 25.0
_TOWN_REFERENCE_JUDGE_BATCH_SIZE = 4
_TOWN_REFERENCE_MAX_CANDIDATES = 12
_TOWN_REFERENCE_MAX_PLACES = 5
_TOWN_REFERENCE_PHOTOS_PER_PLACE = 4
_TOWN_REFERENCE_SEARCH_RADIUS_M = 15000
_TOWN_REFERENCE_MAX_PLACE_DISTANCE_KM = 20.0
_TOWN_REFERENCE_MIN_RELEVANCE = settings.TOWN_PHOTO_MIN_RELEVANCE
_TOWN_REFERENCE_PHOTO_MAX_WIDTH = 1280
_TOWN_REFERENCE_MAX_PHOTO_BYTES = 8 * 1024 * 1024
_TOWN_REFERENCE_LOCALITY_TYPES = {"locality", "sublocality", "administrative_area_level_3", "administrative_area_level_2"}


async def fetch_google_town_reference(
    town_name: str,
    city_name: Optional[str] = None,
    state: Optional[str] = None,
    country: str = "India",
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    budget_seconds: float = _TOWN_REFERENCE_BUDGET_SECONDS,
) -> Optional[Dict[str, Any]]:
    """Return the most relevant vision-approved Google Places photo of a town within the time budget, or None.

    Args:
        town_name: Town name used in the Places queries.
        city_name: Optional district or city qualifier.
        state: Optional state qualifier, reverse-geocoded from the coordinates when missing.
        country: Country qualifier, default India.
        latitude: Optional town latitude biasing the search and filtering distant places.
        longitude: Optional town longitude biasing the search and filtering distant places.
        budget_seconds: Overall time budget for searching and judging.

    Returns:
        image_bytes (None below the relevance bar), content_type, description, photo_url, verdict and relevance, or None.
    """
    try:
        return await asyncio.wait_for(
            _fetch_google_town_reference(town_name, city_name, state, country, latitude, longitude),
            timeout=budget_seconds,
        )
    except asyncio.TimeoutError:
        logger.info(
            "town_reference - grounding stage exceeded the %ss budget for town='%s', generating from the description alone",
            budget_seconds, town_name,
        )
        return None
    except Exception as e:
        logger.warning(
            "town_reference - grounding stage failed for town='%s' (%s), generating from the description alone",
            town_name, e, exc_info=True,
        )
        return None


async def _fetch_google_town_reference(
    town_name: str,
    city_name: Optional[str],
    state: Optional[str],
    country: str,
    latitude: Optional[float],
    longitude: Optional[float],
) -> Optional[Dict[str, Any]]:
    """Search Places several ways near the town, judge photos in batches and return the best one at the relevance bar, or None."""
    api_key = settings.google_maps_api_key
    if not api_key:
        logger.info("town_reference - google_maps_api_key is unset, generating from the description alone")
        return None

    has_coordinates = latitude is not None and longitude is not None
    if not (state or "").strip() and has_coordinates:
        geocode = await _get_cached_google_maps_data(latitude, longitude)
        for component in (geocode or {}).get("address_components") or []:
            if "administrative_area_level_1" in (component.get("types") or []):
                state = component.get("long_name")
                break

    place_label = ", ".join([p.strip() for p in (town_name, city_name, state) if p and str(p).strip()])
    queries = list(dict.fromkeys([
        ", ".join([p.strip() for p in (town_name, city_name, state, country) if p and str(p).strip()]),
        f"tourist attractions in {place_label}",
        f"landmarks in {place_label}",
    ]))
    search_params: Dict[str, Any] = {"key": api_key, "language": "en"}
    if has_coordinates:
        search_params.update({"location": f"{latitude},{longitude}", "radius": _TOWN_REFERENCE_SEARCH_RADIUS_M})

    async with get_http_client() as client:
        searches = await asyncio.gather(*(
            client.get(
                "https://maps.googleapis.com/maps/api/place/textsearch/json",
                params={**search_params, "query": query},
            )
            for query in queries
        ), return_exceptions=True)

        places: Dict[str, Dict[str, Any]] = {}
        for query, search in zip(queries, searches):
            if isinstance(search, Exception):
                logger.warning("town_reference - places text search failed for query='%s': %r", query, search)
                continue
            search_data = search.json()
            if search_data.get("status") != "OK":
                logger.info(
                    "town_reference - places text search returned %s for query='%s'",
                    search_data.get("status"), query,
                )
                continue
            for result in search_data.get("results") or []:
                place_id = result.get("place_id")
                if not place_id or place_id in places or not result.get("photos"):
                    continue
                location = (result.get("geometry") or {}).get("location") or {}
                if has_coordinates and location.get("lat") is not None and location.get("lng") is not None:
                    lat1, lon1, lat2, lon2 = map(math.radians, (latitude, longitude, location["lat"], location["lng"]))
                    distance_km = 2 * 6371.0 * math.asin(math.sqrt(
                        math.sin((lat2 - lat1) / 2) ** 2
                        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
                    ))
                    if distance_km > _TOWN_REFERENCE_MAX_PLACE_DISTANCE_KM:
                        continue
                places[place_id] = result

        if not places:
            logger.info("town_reference - no nearby place with photos for town='%s' (%s)", town_name, place_label)
            return None

        ranked = sorted(
            places.values(),
            key=lambda r: (
                not (set(r.get("types") or []) & _TOWN_REFERENCE_LOCALITY_TYPES),
                -(r.get("user_ratings_total") or 0),
            ),
        )[:_TOWN_REFERENCE_MAX_PLACES]

        details = await asyncio.gather(*(
            client.get(
                "https://maps.googleapis.com/maps/api/place/details/json",
                params={"place_id": r["place_id"], "fields": "name,photos", "key": api_key, "language": "en"},
            )
            for r in ranked
        ), return_exceptions=True)

        photos_by_place: List[List[Tuple[str, Dict[str, Any]]]] = []
        for result, detail in zip(ranked, details):
            detail_result = {} if isinstance(detail, Exception) else (detail.json().get("result") or {})
            photos = [
                p for p in (detail_result.get("photos") or result.get("photos") or [])
                if (p.get("photo_reference") or "").strip()
            ]
            photos.sort(key=lambda p: (p.get("width") or 0) <= (p.get("height") or 0))
            place_name = detail_result.get("name") or result.get("name") or town_name
            photos_by_place.append([(place_name, p) for p in photos[:_TOWN_REFERENCE_PHOTOS_PER_PLACE]])

        candidates: List[Tuple[str, Dict[str, Any]]] = []
        for rank in range(_TOWN_REFERENCE_PHOTOS_PER_PLACE):
            candidates.extend(photos[rank] for photos in photos_by_place if rank < len(photos))
        candidates = candidates[:_TOWN_REFERENCE_MAX_CANDIDATES]
        logger.info(
            "town_reference - judging up to %s photo(s) from %s place(s) for town='%s' (%s)",
            len(candidates), len(ranked), town_name, place_label,
        )

        best: Optional[Dict[str, Any]] = None
        fallback: Optional[Dict[str, Any]] = None
        for batch_start in range(0, len(candidates), _TOWN_REFERENCE_JUDGE_BATCH_SIZE):
            batch = candidates[batch_start:batch_start + _TOWN_REFERENCE_JUDGE_BATCH_SIZE]
            verdicts = await asyncio.gather(*(
                asyncio.wait_for(
                    _judge_town_reference_candidate(
                        client=client,
                        api_key=api_key,
                        photo=photo,
                        place_name=place_name,
                        town_name=town_name,
                        city_name=city_name,
                        state=state,
                    ),
                    timeout=_TOWN_REFERENCE_JUDGE_TIMEOUT_SECONDS,
                )
                for place_name, photo in batch
            ), return_exceptions=True)

            for offset, ((place_name, _photo), verdict) in enumerate(zip(batch, verdicts)):
                index = batch_start + offset
                if isinstance(verdict, Exception) or not verdict:
                    logger.warning(
                        "town_reference - photo %s from '%s' could not be judged for town='%s': %r",
                        index, place_name, town_name, verdict,
                    )
                    continue
                reason = str(verdict.get("reason") or "").strip()
                description = str(verdict.get("description") or "").strip()
                try:
                    relevance = int(float(verdict.get("relevance") or 0))
                except (TypeError, ValueError):
                    relevance = 0
                if not (verdict.get("usable") and description):
                    logger.info(
                        "town_reference - photo %s from '%s' rejected for town='%s' at relevance %s (%s)",
                        index, place_name, town_name, relevance, reason or "unusable",
                    )
                    continue
                scored = {
                    "description": description,
                    "photo_url": verdict["photo_url"],
                    "verdict": f"pass {relevance}%: {reason}" if reason else f"pass {relevance}%",
                    "image_bytes": verdict.get("image_bytes"),
                    "content_type": verdict.get("content_type") or "image/jpeg",
                    "relevance": relevance,
                    "place_name": place_name,
                }
                if relevance >= _TOWN_REFERENCE_MIN_RELEVANCE:
                    logger.info(
                        "town_reference - photo %s from '%s' accepted for town='%s' at relevance %s (%s)",
                        index, place_name, town_name, relevance, reason or "usable",
                    )
                    if best is None or relevance > best["relevance"]:
                        best = scored
                else:
                    logger.info(
                        "town_reference - photo %s from '%s' below the %s bar for town='%s' at relevance %s (%s)",
                        index, place_name, _TOWN_REFERENCE_MIN_RELEVANCE, town_name, relevance, reason or "usable",
                    )
                    if fallback is None or relevance > fallback["relevance"]:
                        fallback = scored
            if best:
                return best

    if fallback:
        logger.info(
            "town_reference - no photo reached relevance %s for town='%s'; best was %s, used only to ground generation",
            _TOWN_REFERENCE_MIN_RELEVANCE, town_name, fallback["relevance"],
        )
        return {**fallback, "image_bytes": None, "verdict": f"below bar {fallback['relevance']}%"}

    logger.info(
        "town_reference - no usable photo among %s candidate(s) for town='%s'",
        len(candidates), town_name,
    )
    return None


async def _judge_town_reference_candidate(
    client: Any,
    api_key: str,
    photo: Dict[str, Any],
    place_name: str,
    town_name: str,
    city_name: Optional[str],
    state: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Download one Places photo and return the Gemini judge's verdict and relevance with photo_url, bytes and content type, or None."""
    public_photo_url = (
        "https://maps.googleapis.com/maps/api/place/photo"
        f"?maxwidth={_TOWN_REFERENCE_PHOTO_MAX_WIDTH}&photoreference={photo['photo_reference'].strip()}"
    )
    try:
        photo_response = await client.get(f"{public_photo_url}&key={api_key}", follow_redirects=True)
        photo_response.raise_for_status()
        image_bytes = photo_response.content
    except Exception as e:
        logger.warning("town_reference - photo fetch failed for town='%s': %s", town_name, e, exc_info=True)
        return None

    if not image_bytes or len(image_bytes) > _TOWN_REFERENCE_MAX_PHOTO_BYTES:
        return None

    content_type = (photo_response.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
    location = ", ".join([p for p in (town_name, city_name, state) if p and str(p).strip()])
    encoded = base64.b64encode(image_bytes).decode("ascii")
    try:
        response = await gemini_2_5_flash_vision.ainvoke([
            {"role": "system", "content": TOWN_REFERENCE_VISION_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Town: {location}\nGoogle place the photo is listed under: {place_name}"},
                    {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{encoded}"}},
                ],
            },
        ])
    except Exception as e:
        logger.warning("town_reference - vision judge failed for town='%s': %s", town_name, e, exc_info=True)
        return None

    content = getattr(response, "content", None)
    if isinstance(content, list):
        raw = " ".join(
            str(part.get("text") or "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
    else:
        raw = str(content or response).strip()

    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        logger.warning("town_reference - vision judge returned no JSON object for town='%s'", town_name)
        return None
    try:
        verdict = json.loads(raw[start:end + 1])
    except Exception as e:
        logger.warning("town_reference - vision judge JSON was unreadable for town='%s': %s", town_name, e, exc_info=True)
        return None

    verdict["photo_url"] = public_photo_url
    verdict["image_bytes"] = image_bytes
    verdict["content_type"] = content_type
    return verdict


async def update_town_profile_image(townprofileid: str, image_url: str) -> bool:
    """Set etown_townprofile.townimage for a town, returning True only when a row was updated."""
    try:
        result = await Database.execute(
            "UPDATE public.etown_townprofile SET townimage = $2 WHERE townprofileid = $1",
            townprofileid, image_url,
        )
    except Exception as e:
        logger.error(
            "generate_town_image - failed to store townimage for townprofileid=%s: %s",
            townprofileid, e, exc_info=True,
        )
        return False

    updated = (str(result or "").split() or ["0"])[-1] not in ("0", "")
    if not updated:
        logger.warning("generate_town_image - no town profile row matched townprofileid=%s", townprofileid)
    return updated


_TOWN_CARD_TASKS: Dict[str, asyncio.Task] = {}
_TOWN_IMAGE_SYNC_TASKS: set = set()


async def generate_town_card(
    town_name: str,
    town_description: str,
    city_name: Optional[str] = None,
    state: Optional[str] = None,
    country: str = "India",
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    use_google_reference: bool = True,
    width: int = 1280,
    height: int = 848,
    steps: Optional[int] = None,
    seed: Optional[int] = None,
    store_format: str = "jpeg",
    store_quality: int = 85,
    store_max_side: Optional[int] = None,
    reference_budget_seconds: float = _TOWN_REFERENCE_BUDGET_SECONDS,
) -> Optional[Dict[str, Any]]:
    """Produce a town card image: store the approved Google photo, else generate one from the description.

    Args:
        town_name: Town the card is for.
        town_description: Stored or generated description of the town.
        city_name: Optional district or city qualifier.
        state: Optional state qualifier.
        country: Country qualifier, default India.
        latitude: Optional latitude added to the prompt.
        longitude: Optional longitude added to the prompt.
        use_google_reference: False skips the Google photo lookup.
        width: Output width; the server caps a side at 1280.
        height: Output height; sides must be multiples of 16.
        steps: Diffusion steps; None uses the server's tuned value.
        seed: Explicit seed, else one derived from the town and city name.
        store_format: Stored image encoding.
        store_quality: Encoder quality for jpeg or webp.
        store_max_side: Optional cap on the longest stored side.
        reference_budget_seconds: Time budget for the Google photo search and judging.

    Returns:
        image_url, image_source, blob_path, prompts, size, seed and reference, or None.

    Raises:
        Exception: Whatever handle_generate_image raises.
    """
    prompt_lines = [f"Town: {town_name}"]
    if city_name and city_name.strip():
        prompt_lines.append(f"City/District: {city_name.strip()}")
    if state and state.strip():
        prompt_lines.append(f"State/Region: {state.strip()}")
    if country and country.strip():
        prompt_lines.append(f"Country: {country.strip()}")
    if latitude is not None and longitude is not None:
        prompt_lines.append(f"Coordinates: {latitude}, {longitude}")
    prompt_lines.append(f"Description: {town_description}")

    reference = None
    if use_google_reference:
        reference = await fetch_google_town_reference(
            town_name=town_name, city_name=city_name, state=state, country=country,
            latitude=latitude, longitude=longitude, budget_seconds=reference_budget_seconds,
        )

    if reference and reference.get("image_bytes"):
        content_type = reference.get("content_type") or "image/jpeg"
        extension = "png" if "png" in content_type else ("webp" if "webp" in content_type else "jpg")
        container = normalize_container_name(settings.AZURE_BLOB_CONTAINER or "etowndev")
        blob_name = f"town_photos/{current_timestamp_prefix()}_{uuid.uuid4().hex[:8]}.{extension}"
        try:
            stored_path = await upload_blob_bytes(
                container, blob_name, reference["image_bytes"], content_type,
            )
        except Exception as e:
            stored_path = None
            logger.warning(
                "town_card - storing the Google photo failed for town='%s' (%s); generating instead",
                town_name, e, exc_info=True,
            )
        if stored_path:
            logger.info(
                "town_card - stored the real Google photo for town='%s' (%s KB, %s)",
                town_name, len(reference["image_bytes"]) // 1024, reference["verdict"],
            )
            return {
                "image_url": stored_path,
                "image_source": "google_photo",
                "blob_path": stored_path,
                "enhanced_prompt": None,
                "raw_prompt": None,
                "width": width,
                "height": height,
                "seed": seed,
                "reference": reference,
            }

    if reference:
        prompt_lines.append("Reference photograph of this real town shows: " + reference["description"])
    raw_prompt = "\n".join(prompt_lines)

    if seed is None:
        seed_key = f"{town_name}|{(city_name or '').strip()}".lower().encode("utf-8")
        seed = zlib.crc32(seed_key) & 0x7FFFFFFF

    from app.agent_infrastructure.tools.tools_utils.generate_image import handle_generate_image

    _result_text, implicit_output = await handle_generate_image(
        prompt=raw_prompt,
        image_type="town",
        width=width,
        height=height,
        steps=steps,
        seed=seed,
        store_format=store_format,
        store_quality=store_quality,
        store_max_side=store_max_side,
    )
    if not implicit_output or not implicit_output.get("image_url"):
        return None

    return {
        "image_url": implicit_output.get("blob_path") or implicit_output["image_url"],
        "image_source": "description_only",
        "blob_path": implicit_output.get("blob_path"),
        "enhanced_prompt": implicit_output.get("enhanced_prompt"),
        "raw_prompt": raw_prompt,
        "width": implicit_output.get("width", width),
        "height": implicit_output.get("height", height),
        "seed": implicit_output.get("seed", seed),
        "reference": reference,
    }


def schedule_town_card_generation(
    townprofileid: str,
    townname: str,
    cityname: Optional[str] = None,
    statecode: Optional[str] = None,
    towndescription: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> None:
    """Start a background town card task unless one is running for the town or name/description is missing."""
    if not townprofileid or not townname or not (towndescription or "").strip():
        logger.info(
            "town_card - skipped scheduling for townprofileid=%s: name or description missing",
            townprofileid,
        )
        return

    existing = _TOWN_CARD_TASKS.get(townprofileid)
    if existing and not existing.done():
        return

    task = asyncio.create_task(_generate_and_store_town_card(
        townprofileid=townprofileid,
        townname=townname,
        cityname=cityname,
        statecode=statecode,
        towndescription=towndescription,
        latitude=latitude,
        longitude=longitude,
    ))
    _TOWN_CARD_TASKS[townprofileid] = task

    def _cleanup(_task: asyncio.Task, key: str = townprofileid) -> None:
        _TOWN_CARD_TASKS.pop(key, None)

    task.add_done_callback(_cleanup)


async def _generate_and_store_town_card(
    townprofileid: str,
    townname: str,
    cityname: Optional[str],
    statecode: Optional[str],
    towndescription: Optional[str],
    latitude: Optional[float],
    longitude: Optional[float],
) -> None:
    """Generate a town card, store it on etown_townprofile.townimage and mirror it to Dataverse, never raising."""
    try:
        card = await generate_town_card(
            town_name=townname,
            town_description=towndescription or "",
            city_name=cityname,
            state=statecode,
            latitude=latitude,
            longitude=longitude,
            reference_budget_seconds=_TOWN_REFERENCE_BACKGROUND_BUDGET_SECONDS,
        )
    except Exception as e:
        logger.error(
            "town_card - generation failed for town='%s' (%s): %s",
            townname, townprofileid, e, exc_info=True,
        )
        return

    if not card:
        logger.warning("town_card - the model produced no image for town='%s' (%s)", townname, townprofileid)
        return

    image_url = card["image_url"]
    if image_url.startswith("data:"):
        logger.warning(
            "town_card - blob upload failed for town='%s' (%s); leaving townimage empty rather "
            "than storing an inline data URL", townname, townprofileid,
        )
        return

    if not await update_town_profile_image(townprofileid, image_url):
        return

    from app.etown.mytown.mytown_syncing import sync_town_profile_image_to_dataverse

    await sync_town_profile_image_to_dataverse(townprofileid, image_url)
    logger.info(
        "town_card - stored a %s image for town='%s' (%s)",
        card.get("image_source", "description_only"), townname, townprofileid,
    )


async def handle_verify_town_image(
    townimageid: str,
    actionmode: str,
    reviewer_userprofileid: Optional[str] = None,
) -> Dict[str, Any]:
    """Set a town image's verification to approved or rejected in Postgres and return the updated image fields.

    Args:
        townimageid: Town image being moderated.
        actionmode: approve or reject; approved and rejected are also accepted.
        reviewer_userprofileid: Optional moderator profile id, used only for logging.

    Returns:
        The image's ids, url, name, new verification and previous_verification.

    Raises:
        HTTPException: 400 for an unknown actionmode, 404 when the image does not exist.
    """
    action = str(actionmode or "").strip().lower()
    resolved = {"approve": "approved", "approved": "approved",
                "reject": "rejected", "rejected": "rejected"}.get(action)
    if not resolved:
        raise HTTPException(
            status_code=400,
            detail="actionmode must be 'approve' or 'reject'.",
        )

    row = await Database.fetchrow(
        "SELECT townimageid, townprofileid, townimageurl, userprofileid, contactid, name, verification "
        "FROM public.etown_townimages WHERE townimageid = $1",
        townimageid,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Town image {townimageid} not found")

    previous = normalize_verification(row.get("verification"))
    await Database.execute(
        "UPDATE public.etown_townimages SET verification = $2 WHERE townimageid = $1",
        townimageid, resolved,
    )
    logger.info(
        "town_image_verify - %s moved %s -> %s by %s",
        townimageid, previous, resolved, reviewer_userprofileid or "unattributed",
    )
    return {
        "townimageid": townimageid,
        "townprofileid": row.get("townprofileid"),
        "townimageurl": row.get("townimageurl"),
        "userprofileid": row.get("userprofileid"),
        "contactid": row.get("contactid"),
        "name": row.get("name"),
        "verification": resolved,
        "previous_verification": previous,
    }


MAX_ATTACHMENTS = 5
MAX_VIDEO_BYTES = 15 * 1024 * 1024


ALLOWED_ATTACHMENT_TYPES: Dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
    "video/3gpp": "3gp",
}

THUMBNAIL_TYPES = frozenset({"image/jpeg", "image/jpg", "image/png", "image/webp"})

NEED_STATUSES = ("draft", "posted", "closed")


def _canonical(content_type: str) -> str:
    """Return image/jpeg for image/jpg, video/mp4 for the other ftyp containers, and any other type unchanged."""
    if content_type == "image/jpg":
        return "image/jpeg"
    if content_type in ("video/quicktime", "video/3gpp"):
        return "video/mp4"
    return content_type


_POST_TABLES: Dict[str, Tuple[str, str]] = {
    "need": ("etown_need", "needid"),
    "update": ("etown_update", "updateid"),
    "event": ("etown_event", "eventid"),
}

POST_TYPES = tuple(_POST_TABLES)

_ID_COLUMNS = tuple(id_column for _, id_column in _POST_TABLES.values())

_COMMON_COLUMNS = (
    "townname", "townprofileid", "userprofileid", "contactid", "title",
    "description", "category", "latitude", "longitude", "needed_at",
    "attachments", "status", "location_name",
)

_TYPE_ONLY_COLUMNS: Dict[str, Dict[str, str]] = {
    "need": {"budget": "text", "need_status": "text"},
    "update": {},
    "event": {
        "start_date": "date",
        "start_time": "time",
        "end_date": "date",
        "end_time": "time",
        "venue": "text",
        "organizer_name": "text",
        "organizer_number": "text",
        "entry_info": "text",
        "event_status": "text",
    },
}


_TOWN_FEED_WHERE = """
WHERE  n.townprofileid = $1
  AND  n.status = 'posted'
  AND  COALESCE(n.deleted, 'false') <> 'true'
  AND  ($2::text IS NULL OR lower(btrim(n.townname)) = lower(btrim($2)))
  AND  NOT EXISTS (SELECT 1 FROM public.etown_contact dc
                   WHERE dc.contactid = n.contactid AND lower(btrim(COALESCE(dc.deleted, ''))) = 'true')
"""

_MY_DRAFTS_WHERE = """
WHERE  n.userprofileid = $1
  AND  n.status = 'draft'
  AND  COALESCE(n.deleted, 'false') <> 'true'
  AND  ($2::text IS NULL OR n.townprofileid = $2)
  AND  NOT EXISTS (SELECT 1 FROM public.etown_contact dc
                   WHERE dc.contactid = n.contactid AND lower(btrim(COALESCE(dc.deleted, ''))) = 'true')
"""

_MY_POSTED_WHERE = """
WHERE  n.userprofileid = $1
  AND  n.status = 'posted'
  AND  COALESCE(n.deleted, 'false') <> 'true'
  AND  ($2::text IS NULL OR n.townprofileid = $2)
  AND  NOT EXISTS (SELECT 1 FROM public.etown_contact dc
                   WHERE dc.contactid = n.contactid AND lower(btrim(COALESCE(dc.deleted, ''))) = 'true')
"""

_LIKE_ACTIVE = "l.likemode = 1 AND NOT etown_is_deleted(l.userprofileid)"

_COMMENT_ACTIVE = "m.deleted = 'false' AND NOT etown_is_deleted(m.userprofileid)"

REVIEWED_POST_TYPES = ("update", "event")

_POST_BY_ID_SQL = " UNION ALL ".join(
    f"SELECT '{post_type}'::text AS post_type, userprofileid, status, "
    f"COALESCE(deleted, 'false') AS deleted FROM public.{table} WHERE {id_column} = $1"
    for post_type, (table, id_column) in _POST_TABLES.items()
)


class NeedValidationError(ValueError):
    """A posted attachment breaks the type, size or count rules."""


async def upload_need_attachments(
    attachments: List[Dict[str, Any]],
    userprofile_id: str,
) -> List[Dict[str, Any]]:
    """Validate every base64 image or video attachment and its optional thumbnail, then upload each to blob storage.

    Args:
        attachments: Items shaped {"data": base64 or data URL, "thumbnail": optional base64 image}.
        userprofile_id: Poster profile id, used as the blob name prefix.

    Returns:
        One {path, content_type, size_bytes, thumbnail_path when sent} per file, paths relative.

    Raises:
        NeedValidationError: Too many, undecodable, empty, oversized or wrongly typed files or thumbnails.
        RuntimeError: AZURE_BLOB_CONTAINER is not configured.
    """
    incoming = [a for a in (attachments or []) if a and str(a.get("data") or "").strip()]
    if not incoming:
        return []

    if len(incoming) > MAX_ATTACHMENTS:
        logger.warning(
            "NEEDS | ATTACHMENT_REJECTED | user=%s | reason=too_many | count=%d | max=%d",
            userprofile_id, len(incoming), MAX_ATTACHMENTS,
        )
        raise NeedValidationError(
            f"Too many files: {len(incoming)}. You can upload up to {MAX_ATTACHMENTS}."
        )

    staged: List[Dict[str, Any]] = []
    for position, item in enumerate(incoming, start=1):
        raw = str(item.get("data") or "")
        label = f"Attachment {position}"
        try:
            content = to_bytes(raw)
        except ValueError:
            logger.warning(
                "NEEDS | ATTACHMENT_REJECTED | user=%s | reason=undecodable | position=%d",
                userprofile_id, position,
            )
            raise NeedValidationError(f"{label} is not valid base64.")
        if not content:
            logger.warning(
                "NEEDS | ATTACHMENT_REJECTED | user=%s | reason=empty | position=%d",
                userprofile_id, position,
            )
            raise NeedValidationError(f"{label} is empty.")

        declared = ""
        header = raw[:128].strip()
        if header.lower().startswith("data:") and ";base64," in header.lower():
            declared = header[5:header.lower().index(";base64,")].strip().lower()
        sniffed = detect_mime_type(content)
        content_type = declared if declared in ALLOWED_ATTACHMENT_TYPES else sniffed
        if content_type not in ALLOWED_ATTACHMENT_TYPES:
            logger.warning(
                "NEEDS | ATTACHMENT_REJECTED | user=%s | reason=bad_type | position=%d | "
                "declared=%s | sniffed=%s",
                userprofile_id, position, declared or "-", sniffed,
            )
            raise NeedValidationError(
                f"{label} is not a supported image or video "
                f"(declared '{declared or 'none'}', detected '{sniffed}'). "
                f"Allowed: {', '.join(sorted(ALLOWED_ATTACHMENT_TYPES))}"
            )
        if _canonical(sniffed) != _canonical(content_type):
            logger.warning(
                "NEEDS | ATTACHMENT_REJECTED | user=%s | reason=content_mismatch | position=%d | "
                "claimed=%s | sniffed=%s",
                userprofile_id, position, content_type, sniffed,
            )
            raise NeedValidationError(
                f"{label} does not contain {content_type} data (its contents look like '{sniffed}')."
            )

        if content_type.startswith("video/") and len(content) > MAX_VIDEO_BYTES:
            logger.warning(
                "NEEDS | ATTACHMENT_REJECTED | user=%s | reason=too_large | position=%d | "
                "type=%s | bytes=%d | max_bytes=%d",
                userprofile_id, position, content_type, len(content), MAX_VIDEO_BYTES,
            )
            raise NeedValidationError(
                f"{label} is {len(content) / (1024 * 1024):.1f}MB. Maximum is "
                f"{MAX_VIDEO_BYTES // (1024 * 1024)}MB for a video."
            )

        thumbnail = b""
        thumbnail_type = ""
        if str(item.get("thumbnail") or "").strip():
            try:
                thumbnail = to_bytes(str(item.get("thumbnail")))
            except ValueError:
                thumbnail = b""
            thumbnail_type = _canonical(detect_mime_type(thumbnail)) if thumbnail else ""
            if thumbnail_type not in THUMBNAIL_TYPES:
                logger.warning(
                    "NEEDS | ATTACHMENT_REJECTED | user=%s | reason=bad_thumbnail | position=%d | "
                    "sniffed=%s", userprofile_id, position, thumbnail_type or "-",
                )
                raise NeedValidationError(
                    f"The thumbnail for {label.lower()} must be a base64 JPEG, PNG or WebP image."
                )

        staged.append({
            "content": content,
            "content_type": content_type,
            "extension": ALLOWED_ATTACHMENT_TYPES[content_type],
            "thumbnail": thumbnail,
            "thumbnail_type": thumbnail_type,
        })

    container_name = normalize_container_name(settings.AZURE_BLOB_CONTAINER)
    if not container_name:
        logger.error(
            "NEEDS | BLOB_CONTAINER_MISSING | user=%s | AZURE_BLOB_CONTAINER is not set, "
            "so no attachment can be stored", userprofile_id,
        )
        raise RuntimeError("AZURE_BLOB_CONTAINER is not configured")

    timestamp = current_timestamp_prefix()
    attachments: List[Dict[str, Any]] = []
    for index, item in enumerate(staged, start=1):
        blob_name = f"{userprofile_id}/needs/{timestamp}_{index}.{item['extension']}"
        logger.debug(
            "NEEDS | ATTACHMENT_UPLOADING | user=%s | blob=%s | type=%s | bytes=%d",
            userprofile_id, blob_name, item["content_type"], len(item["content"]),
        )
        path = await upload_blob_bytes(
            container_name, blob_name, item["content"], item["content_type"]
        )
        stored = {
            "path": path,
            "content_type": item["content_type"],
            "size_bytes": len(item["content"]),
        }
        if item["thumbnail"]:
            stored["thumbnail_path"] = await upload_blob_bytes(
                container_name,
                f"{userprofile_id}/needs/{timestamp}_{index}_thumb.{ALLOWED_ATTACHMENT_TYPES[item['thumbnail_type']]}",
                item["thumbnail"],
                item["thumbnail_type"],
            )
        attachments.append(stored)

    logger.info(
        "NEEDS | ATTACHMENTS_UPLOADED | user=%s count=%d bytes=%d",
        userprofile_id, len(attachments), sum(a["size_bytes"] for a in attachments),
    )
    return attachments


def event_status(
    start_date: Optional[date],
    start_time: Optional[clock_time],
    end_date: Optional[date],
    end_time: Optional[clock_time] = None,
) -> Optional[str]:
    """Return upcoming, live or completed for an event's IST schedule, or None when it has neither date.

    Args:
        start_date: Event start date, or None.
        start_time: Event start time, or None for midnight.
        end_date: Event end date, or None to end on start_date at end_time, else EVENT_OPEN_ENDED_HOURS after the start.
        end_time: Event end time, or None to end when end_date is over.

    Returns:
        upcoming before start_date at start_time, live until the end, then completed; None without dates.
    """
    if not start_date and not end_date:
        return None
    now = datetime.now(IST)
    start = datetime.combine(start_date, start_time or clock_time.min, tzinfo=IST) if start_date else None
    if start and now < start:
        return "upcoming"
    if end_time:
        end = datetime.combine(end_date or start_date, end_time, tzinfo=IST)
    elif end_date:
        end = datetime.combine(end_date + timedelta(days=1), clock_time.min, tzinfo=IST)
    else:
        end = start + timedelta(hours=settings.EVENT_OPEN_ENDED_HOURS)
    return "live" if now < end else "completed"


async def refresh_event_statuses() -> int:
    """Move every undeleted, not yet completed event whose stored event_status is stale to its current one, returning the count."""
    now = "(now() AT TIME ZONE 'Asia/Kolkata')"
    start = "(e.start_date + COALESCE(e.start_time, time '00:00'))"
    computed = (
        "(CASE WHEN e.start_date IS NULL AND e.end_date IS NULL THEN NULL "
        f"WHEN e.start_date IS NOT NULL AND {now} < {start} THEN 'upcoming' "
        f"WHEN {now} < CASE WHEN e.end_time IS NOT NULL THEN COALESCE(e.end_date, e.start_date) + e.end_time "
        "WHEN e.end_date IS NOT NULL THEN (e.end_date + 1)::timestamp "
        f"ELSE {start} + make_interval(hours => {int(settings.EVENT_OPEN_ENDED_HOURS)}) END THEN 'live' "
        "ELSE 'completed' END)"
    )
    result = await Database.execute(
        f"UPDATE public.etown_event e SET event_status = {computed} "
        "WHERE COALESCE(e.deleted, 'false') <> 'true' "
        "AND e.event_status IS DISTINCT FROM 'completed' "
        f"AND e.event_status IS DISTINCT FROM {computed}"
    )
    try:
        return int(str(result).split()[-1])
    except (ValueError, IndexError):
        logger.warning("NEEDS | EVENT_STATUS_COUNT_UNREAD | status=%r", result)
        return 0


def correct_event_statuses(items: List[Dict[str, Any]]) -> Dict[str, str]:
    """Fix in place each listed upcoming or live event whose event_status is wrong at the current IST time.

    Args:
        items: Feed items in API shape; only events whose event_status is upcoming or live are checked.

    Returns:
        {eventid: corrected event_status} for each event that was wrong; empty when all were right.
    """
    moved: Dict[str, str] = {}
    for item in items:
        if item.get("post_type") != "event" or item.get("event_status") not in ("upcoming", "live"):
            continue
        current = event_status(item.get("start_date"), item.get("start_time"), item.get("end_date"), item.get("end_time"))
        if current != item["event_status"]:
            item["event_status"] = current
            moved[item["eventid"]] = current
    return moved


async def store_event_statuses(moved: Dict[str, str]) -> None:
    """Store corrected event statuses on etown_event rows still upcoming or live; failures are logged and swallowed.

    Args:
        moved: {eventid: event_status} as correct_event_statuses returns it.
    """
    try:
        result = await Database.execute(
            "UPDATE public.etown_event e SET event_status = m.event_status "
            "FROM unnest($1::text[], $2::text[]) AS m(eventid, event_status) "
            "WHERE e.eventid = m.eventid AND e.event_status IN ('upcoming', 'live')",
            list(moved), list(moved.values()),
        )
        logger.info("NEEDS | EVENT_STATUS_CORRECTED | moved=%s | %s", moved, result)
    except Exception as exc:
        logger.error("NEEDS | EVENT_STATUS_CORRECT_FAILED | moved=%s | %s", moved, exc, exc_info=True)


def _post_columns(post_type: str) -> Tuple[str, ...]:
    """Return the columns a post type's table stores besides its id: the shared set plus that type's own columns."""
    return _COMMON_COLUMNS + tuple(_TYPE_ONLY_COLUMNS[post_type])


def _person_columns(prefix: str) -> List[str]:
    """Return role, name and image columns for the person resolved by _person_joins, each name starting with prefix."""
    return [
        f"up.role AS {prefix}role",
        f"CASE WHEN up.role = 1 THEN p.businessname ELSE NULLIF(TRIM(c.firstname), '') END AS {prefix}name",
        f"CASE WHEN up.role = 1 THEN p.serviceimageurl ELSE c.profileimage END AS {prefix}image",
    ]


def _person_joins(alias: str) -> str:
    """Return the userprofile, contact and provider joins resolving the person whose ids sit on the row aliased alias."""
    return (
        f"LEFT JOIN public.etown_userprofile up ON up.userprofileid = {alias}.userprofileid "
        f"LEFT JOIN public.etown_contact c ON c.contactid = {alias}.contactid "
        "LEFT JOIN LATERAL (SELECT ep.businessname, ep.serviceimageurl FROM public.etown_provider ep "
        f"WHERE up.role = 1 AND ep.userprofileid = {alias}.userprofileid "
        "AND COALESCE(ep.deleted, 'false') <> 'true' "
        "ORDER BY ep.creationdate DESC NULLS LAST LIMIT 1) p ON true "
    )


def _review_status_sql(column: str) -> str:
    """Return SQL mapping a review column to pending (blank, pending or 0), approved (approved or 1) or rejected."""
    return (
        f"CASE WHEN lower(btrim(COALESCE({column}, ''))) IN ('', 'pending', '0') THEN 'pending' "
        f"WHEN lower(btrim({column})) IN ('approved', '1') THEN 'approved' ELSE 'rejected' END"
    )


def _approved_gate(post_type: str, column: str) -> str:
    """Return an AND clause keeping an update or event only once its review column is approved, or '' for a need."""
    if post_type not in REVIEWED_POST_TYPES:
        return ""
    return f" AND {_review_status_sql(column)} = 'approved' "


def _sibling_sql(alias: str) -> str:
    """Return SQL matching undeleted rows aliased alias that are town copies of row n: same poster, created_at and status."""
    return (
        f"{alias}.userprofileid = n.userprofileid AND {alias}.created_at = n.created_at "
        f"AND {alias}.status = n.status AND COALESCE({alias}.deleted, 'false') <> 'true'"
    )


def _group_lead_gate(post_type: str, town_param: str) -> str:
    """Return an AND clause keeping one row per group of town copies, the lowest id among copies in town_param when set."""
    table, own_id = _POST_TABLES[post_type]
    return (
        f" AND NOT EXISTS (SELECT 1 FROM public.{table} g WHERE {_sibling_sql('g')} "
        f"AND g.{own_id} < n.{own_id} AND ({town_param}::text IS NULL OR g.townprofileid = {town_param})) "
    )


def _post_select(
    post_type: str, where: str, viewer: str = "NULL::text", response_counts: bool = False, towns: str = "self"
) -> str:
    """Return one post table's SELECT in the shared feed shape, with engagement counts, viewer flags and its towns.

    Args:
        post_type: need, update or event; picks the table.
        where: WHERE clause over the table aliased n.
        viewer: SQL expression of the viewer's userprofileid.
        response_counts: True adds each need's unique responder count.
        towns: all lists every town copy of the post, owner does so only on the viewer's own posts, self only the row's own town.
    """
    table, own_id = _POST_TABLES[post_type]
    towns_sql = (
        "(SELECT jsonb_agg(jsonb_build_object('townname', g.townname, 'townprofileid', g.townprofileid, "
        f"'postid', g.{own_id}) ORDER BY g.townname, g.{own_id}) "
        f"FROM public.{table} g WHERE {_sibling_sql('g')})"
    )
    own_town_sql = (
        "jsonb_build_array(jsonb_build_object('townname', n.townname, 'townprofileid', n.townprofileid, "
        f"'postid', n.{own_id}))"
    )
    towns_column = {
        "all": f"{towns_sql} AS towns",
        "owner": f"CASE WHEN n.userprofileid = {viewer} THEN {towns_sql} ELSE {own_town_sql} END AS towns",
        "self": f"{own_town_sql} AS towns",
    }[towns]
    in_circle = (
        "(EXISTS (SELECT 1 FROM public.etown_memberrelation mr "
        f"WHERE (mr.from_userprofile = {viewer} AND mr.to_userprofile = n.userprofileid) "
        f"OR (mr.to_userprofile = {viewer} AND mr.from_userprofile = n.userprofileid)) "
        "OR EXISTS (SELECT 1 FROM public.etown_providerrelation pr "
        f"WHERE (pr.fromuserprofileid = {viewer} AND pr.touserprofileid = n.userprofileid) "
        f"OR (pr.touserprofileid = {viewer} AND pr.fromuserprofileid = n.userprofileid)))"
    )
    columns = [f"'{post_type}'::text AS post_type"]
    columns += [f"n.{c}" if c == own_id else f"NULL::text AS {c}" for c in _ID_COLUMNS]
    columns += [f"n.{c}" for c in _COMMON_COLUMNS]
    review_sql = _review_status_sql("n.review")
    columns += [
        f"CASE WHEN {review_sql} = 'approved' THEN n.{c} ELSE {review_sql} END AS {c}"
        if owner == post_type and c == "event_status"
        else f"n.{c}" if owner == post_type else f"NULL::{sql_type} AS {c}"
        for owner, owned in _TYPE_ONLY_COLUMNS.items()
        for c, sql_type in owned.items()
    ]
    columns += ["n.created_at"] + _person_columns("poster_")
    columns += [
        f"(SELECT count(*) FROM public.etown_communitylike l "
        f"WHERE l.postid = n.{own_id} AND {_LIKE_ACTIVE})::int AS like_count",
        f"(SELECT count(*) FROM public.etown_communitycomment m "
        f"WHERE m.postid = n.{own_id} AND {_COMMENT_ACTIVE})::int AS comment_count",
        "COALESCE(n.view_count, 0)::int AS view_count",
        f"EXISTS (SELECT 1 FROM public.etown_communitylike l WHERE l.postid = n.{own_id} "
        f"AND l.likemode = 1 AND l.userprofileid = {viewer}) AS is_liked",
        f"{in_circle} AS mycircle",
        (
            f"CASE WHEN {in_circle} THEN 'Connected' "
            "WHEN EXISTS (SELECT 1 FROM public.etown_invitation iv WHERE iv.invitationcode = 'Requested' "
            f"AND ((iv.inviteruserprofile = {viewer} AND iv.inviteuserprofile = n.userprofileid) "
            f"OR (iv.inviteuserprofile = {viewer} AND iv.inviteruserprofile = n.userprofileid))) "
            "THEN 'Requested' END AS invitationstatus"
        ),
        f"{review_sql} AS update_status" if post_type == "update" else "NULL::text AS update_status",
        (
            "(SELECT count(DISTINCT r.userprofileid) FROM public.etown_needresponse r "
            "WHERE r.needid = n.needid AND COALESCE(r.deleted, 'false') <> 'true')::int AS response_count"
            if response_counts and post_type == "need"
            else "NULL::int AS response_count"
        ),
        towns_column,
    ]
    return (
        f"SELECT {', '.join(columns)} "
        f"FROM public.{table} n "
        f"{_person_joins('n')}"
        f"{where}"
    )


async def create_posts(
    post_type: str,
    fields: Dict[str, Any],
    towns: List[Dict[str, str]],
    created_at: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Insert one need, update or event per town in one statement under new uuid4 ids, updates and events with review pending.

    Args:
        post_type: need, update or event; picks etown_need, etown_update or etown_event.
        fields: Column values shared by every town's row; keys the table does not store are ignored.
        towns: One {townname, townprofileid} per row to insert, in the order to return them.
        created_at: An existing group's created_at, so the new rows join its town copies; None stamps now().

    Returns:
        The inserted rows in towns order, each with post_type, all three id keys, attachment urls and IST timestamps.
    """
    table, id_column = _POST_TABLES[post_type]
    if post_type == "event":
        fields = {
            **fields,
            "event_status": event_status(
                fields.get("start_date"), fields.get("start_time"), fields.get("end_date"), fields.get("end_time")
            ),
        }
    columns = (
        (id_column,) + _post_columns(post_type)
        + (("review",) if post_type in REVIEWED_POST_TYPES else ())
        + (("created_at",) if created_at else ())
    )
    postids = [str(uuid.uuid4()) for _ in towns]
    values: List[Any] = []
    tuples = []
    for postid, town in zip(postids, towns):
        row_fields = {**fields, **town, "created_at": created_at, "review": "pending"}
        base = len(values)
        values += [postid] + [
            json.dumps(row_fields.get(c) or []) if c == "attachments" else row_fields.get(c)
            for c in columns[1:]
        ]
        tuples.append("(" + ", ".join(
            f"${base + i}::jsonb" if c == "attachments" else f"${base + i}"
            for i, c in enumerate(columns, 1)
        ) + ")")
    rows = await Database.fetch(
        f"INSERT INTO public.{table} ({', '.join(columns)}) VALUES {', '.join(tuples)} RETURNING *",
        *values,
    )
    by_id = {r[id_column]: r for r in rows}
    stored = [
        _row_to_need({
            **{c: None for c in _ID_COLUMNS},
            **{c: None for owned in _TYPE_ONLY_COLUMNS.values() for c in owned},
            **dict(by_id[postid]),
            "post_type": post_type,
            "update_status": "pending" if post_type == "update" else None,
            **({"event_status": "pending"} if post_type == "event" else {}),
        })
        for postid in postids
    ]
    for post in stored:
        logger.info(
            "NEEDS | CREATED | post_type=%s %s=%s town=%s townprofileid=%s user=%s status=%s attachments=%d",
            post_type, id_column, post[id_column], post["townname"], post["townprofileid"],
            fields.get("userprofileid"), fields.get("status"), len(post["attachments"]),
        )
    return stored


COMMUNITY_SEARCH_MAX_WORDS = 8

COMMUNITY_SEARCH_WORD_THRESHOLD = 0.5


def _fuzzy_search_sql(feed: str, phrase: str, words: str, threshold: str) -> str:
    """Wrap a feed SELECT so each post gets matched (half its words fuzzy-found) and a match_score from its text and poster name.

    Args:
        feed: The feed SELECT whose rows are searched.
        phrase: Placeholder of the normalised search phrase.
        words: Placeholder of the text[] of search words.
        threshold: Placeholder of the per-word trigram similarity cut-off.
    """
    return f"""
SELECT d.*,
       (SELECT 2 * count(*) FILTER (WHERE d.search_doc ~ ('\\m' || w)
                                    OR (char_length(w) >= 3 AND word_similarity(w, d.search_doc) >= {threshold}))
               >= count(*)
        FROM unnest({words}::text[]) w) AS matched,
       round((
           (SELECT avg(GREATEST(word_similarity(w, d.search_doc),
                                CASE WHEN d.search_doc ~ ('\\m' || w) THEN 0.9 ELSE 0 END))
            FROM unnest({words}::text[]) w)
           + 0.5 * word_similarity({phrase}, lower(d.title))
           + 0.4 * word_similarity({phrase}, lower(COALESCE(d.poster_name, '')))
           + CASE WHEN strpos(lower(d.title), {phrase}) > 0 THEN 0.3
                  WHEN strpos(d.search_doc, {phrase}) > 0 THEN 0.15 ELSE 0 END
       )::numeric, 3)::float8 AS match_score
FROM (
    SELECT f.*,
           lower(concat_ws(' ', f.post_type, f.title, f.poster_name, f.category, f.location_name, f.townname,
                           f.venue, f.organizer_name, f.entry_info, f.description)) AS search_doc
    FROM ({feed}) f
) d
"""


async def list_needs(
    *,
    status: str = "posted",
    townprofileid: Optional[str] = None,
    townname: Optional[str] = None,
    userprofileid: Optional[str] = None,
    post_type: Optional[str] = None,
    same_user: bool = False,
    needs_only: bool = False,
    exclude_contactid: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
) -> Dict[str, Any]:
    """Return a page of a town's posted posts, or one member's own posted posts or drafts, newest first, with totals.

    Args:
        status: posted for the town feed, draft for the caller's own drafts.
        townprofileid: Town to read; required for the town feed, optional narrowing for own posts.
        townname: Optional case-insensitive name filter for the town feed.
        userprofileid: Owner for own posts and drafts; always the viewer whose like sets is_liked.
        post_type: need, update or event to read one table; None reads all three.
        same_user: True reads only userprofileid's own posts, with each need's response_count.
        needs_only: True reads and counts only etown_need, leaving update and event at 0.
        exclude_contactid: Optional contact whose own posts are left out of the rows and counts.
        search: Optional free text; half its words must fuzzy-match the type, title, description, poster name or details.
        page: 1-based page number.
        limit: Rows per page.

    Returns:
        {"needs": [...], "counts": {"all", "need", "update", "event", "open", "closed"}, "total": int, "total_pages": int};
        counts ignore post_type so every tab's number is known, while total follows it; with
        needs_only, update and event count 0; with same_user, open and closed split
        the needs, otherwise they are None.

    Raises:
        ValueError: search is not blank but has no word of two or more letters or digits.
    """
    phrase = " ".join(
        "".join(" " if unicodedata.category(ch)[0] in "PSCZ" else ch for ch in (search or "").lower()).split()
    )
    words = list(dict.fromkeys(w for w in phrase.split() if len(w) >= 2))[:COMMUNITY_SEARCH_MAX_WORDS]
    if phrase and not words:
        raise ValueError("search must contain a word of at least 2 letters or digits")

    offset = (max(int(page), 1) - 1) * limit
    counted_types = ("need",) if needs_only else POST_TYPES
    types = (post_type,) if post_type else counted_types
    if status == "draft":
        where = _MY_DRAFTS_WHERE
        first, second = userprofileid, (townprofileid or "").strip() or None
    elif same_user:
        where = _MY_POSTED_WHERE
        first, second = userprofileid, (townprofileid or "").strip() or None
    else:
        where = _TOWN_FEED_WHERE
        first, second = townprofileid, (townname or "").strip() or None
    excluded = (exclude_contactid or "").strip() or None

    def _where_at(index: int) -> str:
        return where + f" AND (${index}::text IS NULL OR n.contactid IS DISTINCT FROM ${index}::text) "

    gated = status != "draft" and not same_user

    def _gates(t: str) -> str:
        if gated:
            return _approved_gate(t, "n.review")
        return _group_lead_gate(t, "$2") if same_user else ""

    closed_sql = "lower(btrim(COALESCE({}.need_status, ''))) = 'closed'"
    feed = " UNION ALL ".join(
        _post_select(
            t, _where_at(9 if words else 6) + _gates(t),
            viewer="$5::text", response_counts=same_user, towns="all" if same_user else "owner",
        )
        for t in types
    )
    viewer = (userprofileid or "").strip() or None
    if words:
        searched = _fuzzy_search_sql(feed, "$6", "$7", "$8")
        counted_feed = " UNION ALL ".join(
            _post_select(t, _where_at(6) + _gates(t))
            for t in counted_types
        )
        per_type = ", ".join(f"count(*) FILTER (WHERE s.post_type = '{t}')::int AS {t}" for t in counted_types)
        if same_user:
            per_type += f", count(*) FILTER (WHERE s.post_type = 'need' AND {closed_sql.format('s')})::int AS closed"
        page_rows, count_rows = await asyncio.gather(
            Database.fetch_once(
                f"SELECT * FROM ({searched}) s WHERE s.matched "
                "ORDER BY s.match_score DESC, s.created_at DESC LIMIT $3 OFFSET $4",
                first, second, limit, offset, viewer, phrase, words, COMMUNITY_SEARCH_WORD_THRESHOLD, excluded,
            ),
            Database.fetch_once(
                f"SELECT {per_type} FROM ({_fuzzy_search_sql(counted_feed, '$3', '$4', '$5')}) s WHERE s.matched",
                first, second, phrase, words, COMMUNITY_SEARCH_WORD_THRESHOLD, excluded,
            ),
        )
        rows = [{k: v for k, v in r.items() if k not in ("search_doc", "matched")} for r in page_rows]
        counted = count_rows[0]
    else:
        rows = await Database.fetch(
            f"SELECT * FROM ({feed}) p ORDER BY p.created_at DESC LIMIT $3 OFFSET $4",
            first, second, limit, offset, viewer, excluded,
        )
        per_type = ", ".join(
            f"(SELECT count(*) FROM public.{_POST_TABLES[t][0]} n {_where_at(3)}"
            + _gates(t)
            + f")::int AS {t}"
            for t in counted_types
        )
        if same_user:
            per_type += (
                f", (SELECT count(*) FROM public.etown_need n {_where_at(3)}{_gates('need')} "
                f"AND {closed_sql.format('n')})::int AS closed"
            )
        counted = await Database.fetchrow(f"SELECT {per_type}", first, second, excluded)
    counts = {t: int(counted[t] or 0) if t in counted_types else 0 for t in POST_TYPES}
    counts["all"] = sum(counts[t] for t in POST_TYPES)
    counts["closed"] = int(counted["closed"] or 0) if same_user else None
    counts["open"] = counts["need"] - counts["closed"] if same_user else None
    total = sum(counts[t] for t in types)
    if words:
        logger.info(
            "NEEDS | SEARCH | status=%s townprofileid=%s user=%s post_type=%s phrase=%r words=%s matched=%d",
            status, townprofileid, userprofileid, post_type, phrase, words, total,
        )
    return {
        "needs": [_row_to_need(r) for r in rows],
        "counts": counts,
        "total": total,
        "total_pages": (total + limit - 1) // limit if limit else 0,
    }


async def get_community_post(
    *, post_type: str, postid: str, userprofileid: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Return one need, update or event in the feed shape, or None when it is missing or hidden from the viewer.

    Args:
        post_type: need, update or event; picks the table.
        postid: needid, updateid or eventid of the post.
        userprofileid: The viewer; sets is_liked, and only the owner sees their drafts and unapproved posts.

    Returns:
        The post as GET /community returns it, with response_count on needs, or None.
    """
    own_id = _POST_TABLES[post_type][1]
    where = f"""
WHERE  n.{own_id} = $1
  AND  COALESCE(n.deleted, 'false') <> 'true'
  AND  NOT EXISTS (SELECT 1 FROM public.etown_contact dc
                   WHERE dc.contactid = n.contactid AND lower(btrim(COALESCE(dc.deleted, ''))) = 'true')
  AND  (n.userprofileid = $2::text OR (n.status = 'posted' {_approved_gate(post_type, 'n.review')}))
"""
    row = await Database.fetchrow(
        _post_select(post_type, where, viewer="$2::text", response_counts=True, towns="owner"),
        postid, userprofileid,
    )
    return _row_to_need(row) if row else None


async def record_community_visit(
    userprofileid: str, visited_at: datetime, post_types: Sequence[str] = POST_TYPES,
) -> None:
    """Upsert an IST time as a user's last community visit for the given post types, never raising.

    Args:
        userprofileid: The user whose single etown_community_lastvisit row is written.
        visited_at: Aware IST time taken before the feed was read, so no post slips between read and write.
        post_types: The post types read, each stamped in its own <type>_visited_at column.
    """
    columns = [f"{t}_visited_at" for t in POST_TYPES if t in post_types]
    if not columns:
        logger.warning(
            "NEEDS | VISIT_SKIPPED | user=%s | post_types=%s | no known post type",
            userprofileid, list(post_types),
        )
        return
    try:
        result = await Database.execute(
            f"""
INSERT INTO public.etown_community_lastvisit (userprofileid, last_visited_at, {", ".join(columns)})
VALUES ($1, $2, {", ".join("$2" for _ in columns)})
ON CONFLICT (userprofileid) DO UPDATE
SET    last_visited_at = EXCLUDED.last_visited_at,
       {", ".join(f"{c} = EXCLUDED.{c}" for c in columns)},
       updated_at = now()
""",
            userprofileid, visited_at,
        )
    except Exception as exc:
        logger.error(
            "NEEDS | VISIT_RECORD_FAILED | user=%s | at=%s | columns=%s | %s",
            userprofileid, visited_at.isoformat(), columns, exc, exc_info=True,
        )
        return
    logger.info(
        "NEEDS | VISIT_RECORDED | user=%s | at=%s | columns=%s | result=%s",
        userprofileid, visited_at.isoformat(), columns, result,
    )


async def get_community_town_header(
    townprofileid: str,
    exclude_contactid: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a town's image, provider count and view count the way /mytown_suggestions builds them, never raising.

    Args:
        townprofileid: The etown_townprofile whose header is read.
        exclude_contactid: Optional contact whose own providers are left out of provider_count.

    Returns:
        townimage, provider_count and view_count; empty values when the town is missing or the read fails.
    """
    header: Dict[str, Any] = {"townimage": "", "provider_count": 0, "view_count": 0}
    params: List[Any] = [townprofileid]
    exclude_own_and = ""
    if exclude_contactid:
        params.append(exclude_contactid)
        exclude_own_and = "AND p.contactid != $2"

    query = f"""
WITH Town AS (
    SELECT townprofileid, townname, townimage
    FROM   etown_townprofile
    WHERE  townprofileid = $1
    LIMIT  1
),
MappedProviders AS (
    SELECT DISTINCT p.providerid
    FROM   Town t
    JOIN   etown_etownprovider ep ON ep.townprofileid = t.townprofileid
    JOIN   etown_provider p ON p.providerid = ep.providerid
    WHERE  p.deleted IS DISTINCT FROM 'true'
      AND  (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
      AND  p.disabled IS NOT TRUE
      {exclude_own_and}

    UNION

    SELECT DISTINCT p.providerid
    FROM   Town t
    JOIN   etown_provider p ON LOWER(TRIM(p.city)) = LOWER(TRIM(t.townname))
    WHERE  NOT EXISTS (
               SELECT 1 FROM public.etown_etownprovider et
               WHERE  et.providerid = p.providerid
                 AND  COALESCE(et.townprofileid, '') <> ''
           )
      AND  p.deleted IS DISTINCT FROM 'true'
      AND  (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
      AND  p.disabled IS NOT TRUE
      {exclude_own_and}
)
SELECT t.townimage,
       (SELECT COUNT(*)::int FROM MappedProviders) AS provider_count,
       COALESCE(get_town_view_count(t.townprofileid), 0) AS view_count
FROM   Town t
"""
    try:
        row = await Database.fetchrow(query, *params)
        if not row:
            logger.info("NEEDS | TOWN_HEADER_MISSING | townprofileid=%s", townprofileid)
            return header
        header["townimage"] = row.get("townimage") or ""
        header["provider_count"] = int(row.get("provider_count") or 0)
        header["view_count"] = int(row.get("view_count") or 0)
    except Exception as exc:
        logger.error(
            "NEEDS | TOWN_HEADER_FAILED | townprofileid=%s | %s", townprofileid, exc, exc_info=True,
        )
    return header


async def publish_post(
    *,
    postid: str,
    userprofileid: str,
    needed_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Publish the caller's own draft need, update or event and its draft copies in other towns, and report the outcome.

    Args:
        postid: needid, updateid or eventid of the draft to publish.
        userprofileid: Caller, who must own the draft.
        needed_at: Optional replacement needed-by date; None keeps the stored value.

    Returns:
        {"outcome", "need", "posts"}; outcome is published, already_posted, not_found, not_owner or closed;
        need is postid's post with its towns, posts every town copy this call published.
    """
    found = await Database.fetchrow(_POST_BY_ID_SQL, postid)
    if not found or found["deleted"] == "true":
        logger.info("NEEDS | PUBLISH_NOOP | postid=%s user=%s outcome=not_found", postid, userprofileid)
        return {"outcome": "not_found", "need": None, "posts": []}

    post_type = found["post_type"]
    table, id_column = _POST_TABLES[post_type]
    updated = await Database.fetch(
        f"UPDATE public.{table} t SET status = 'posted', needed_at = COALESCE($3, t.needed_at), "
        f"updated_at = now() FROM public.{table} s "
        f"WHERE s.{id_column} = $1 AND s.userprofileid = $2 AND s.status = 'draft' "
        "AND COALESCE(s.deleted, 'false') <> 'true' "
        "AND t.userprofileid = s.userprofileid AND t.created_at = s.created_at AND t.status = 'draft' "
        f"AND COALESCE(t.deleted, 'false') <> 'true' RETURNING t.{id_column}",
        postid, userprofileid, needed_at,
    )
    if updated:
        outcome = "published"
    elif found["userprofileid"] != userprofileid:
        outcome = "not_owner"
    elif found["status"] == "closed":
        outcome = "closed"
    else:
        outcome = "already_posted"
    logger.info(
        "NEEDS | PUBLISH_%s | post_type=%s %s=%s user=%s towns=%d needed_at_overridden=%s",
        "DONE" if updated else "NOOP", post_type, id_column, postid, userprofileid, len(updated),
        needed_at is not None,
    )
    if outcome in ("not_owner", "closed"):
        return {"outcome": outcome, "need": None, "posts": []}

    ids = [r[id_column] for r in updated] or [postid]
    rows = await Database.fetch(
        _post_select(post_type, f"WHERE n.{id_column} = ANY($1::text[])", towns="all"), ids
    )
    posts = [_row_to_need(r) for r in rows]
    need = next(p for p in posts if p[id_column] == postid)
    return {"outcome": outcome, "need": need, "posts": posts if updated else []}


async def edit_or_delete_post(
    *,
    action: str,
    post_type: str,
    postid: str,
    userprofileid: str,
    changes: Dict[str, Any],
    new_attachments: List[Dict[str, Any]],
    remove_attachments: List[str],
    towns: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Edit the sent fields and towns of, or soft-delete, the caller's own post across all its town copies.

    Args:
        action: edit or delete.
        post_type: need, update or event; picks the table.
        postid: needid, updateid or eventid of any of the post's town copies.
        userprofileid: The caller; must be the post's owner.
        changes: Column values to overwrite on every town copy on edit, keyed by column name.
        new_attachments: Base64 files to add on edit, shaped like upload_need_attachments' input.
        remove_attachments: Stored attachment paths to drop on edit.
        towns: On edit, the full {townname, townprofileid} list; missing towns' copies move to new ones, then are deleted or added.

    Returns:
        {"outcome", "need", "reason", "posts", "moved", "added"}; outcome is edited, deleted, not_found, not_owner or invalid;
        need is a surviving copy with its towns, posts every copy touched, moved the copies moved to a new town, added the fresh copies.

    Raises:
        NeedValidationError: A new attachment breaks the type, size or count rules.
    """
    table, id_column = _POST_TABLES[post_type]
    found = await Database.fetchrow(f"SELECT * FROM public.{table} WHERE {id_column} = $1", postid)
    if not found or (found["deleted"] or "false") == "true":
        outcome = "not_found"
    elif found["userprofileid"] != userprofileid:
        outcome = "not_owner"
    else:
        outcome = "edited" if action == "edit" else "deleted"
    if outcome in ("not_found", "not_owner"):
        logger.info(
            "NEEDS | %s_REFUSED | post_type=%s %s=%s user=%s reason=%s",
            action.upper(), post_type, id_column, postid, userprofileid, outcome,
        )
        return {"outcome": outcome, "need": None, "reason": None, "posts": [], "moved": [], "added": []}

    group = await Database.fetch(
        f"SELECT {id_column}, townprofileid FROM public.{table} "
        "WHERE userprofileid = $1 AND created_at = $2 AND status = $3 AND COALESCE(deleted, 'false') <> 'true'",
        userprofileid, found["created_at"], found["status"],
    )
    group_ids = [r[id_column] for r in group]
    removed_ids: List[str] = []
    moved_ids: List[str] = []
    added: List[Dict[str, Any]] = []
    if action == "delete":
        removed_ids = group_ids
        await Database.execute(
            f"UPDATE public.{table} SET deleted = 'true', updated_at = now() "
            f"WHERE {id_column} = ANY($1::text[]) AND userprofileid = $2",
            group_ids, userprofileid,
        )
    else:
        merged = {**dict(found), **changes}
        start_date, end_date = merged.get("start_date"), merged.get("end_date")
        start_time, end_time = merged.get("start_time"), merged.get("end_time")
        reason = None
        if towns is not None and not towns:
            reason = "at least one town is required"
        elif start_date and end_date and end_date < start_date:
            reason = "end_date must not be before start_date"
        elif (end_date or start_date) == start_date and start_time and end_time and end_time < start_time:
            reason = "end_time must not be before start_time on the same day"
        if reason:
            logger.info(
                "NEEDS | EDIT_REFUSED | post_type=%s %s=%s user=%s reason=%s",
                post_type, id_column, postid, userprofileid, reason,
            )
            return {"outcome": "invalid", "need": None, "reason": reason, "posts": [], "moved": [], "added": []}
        if post_type == "event" and {"start_date", "start_time", "end_date", "end_time"} & set(changes):
            changes = {**changes, "event_status": event_status(start_date, start_time, end_date, end_time)}

        if new_attachments or remove_attachments:
            stored = found["attachments"]
            if isinstance(stored, str):
                try:
                    stored = json.loads(stored)
                except ValueError:
                    stored = []
            dropped = set(remove_attachments)
            kept = [a for a in (stored or []) if a.get("path") not in dropped]
            if len(kept) + len(new_attachments) > MAX_ATTACHMENTS:
                raise NeedValidationError(
                    f"Too many files: {len(kept) + len(new_attachments)}. "
                    f"You can keep up to {MAX_ATTACHMENTS}."
                )
            changes = {
                **changes,
                "attachments": kept + await upload_need_attachments(new_attachments, userprofileid),
            }

        allowed = set(_post_columns(post_type))
        columns = [c for c in changes if c in allowed]
        if columns:
            assignments = ", ".join(
                f"{c} = ${i}::jsonb" if c == "attachments" else f"{c} = ${i}"
                for i, c in enumerate(columns, 3)
            )
            await Database.execute(
                f"UPDATE public.{table} SET {assignments}, updated_at = now() "
                f"WHERE {id_column} = ANY($1::text[]) AND userprofileid = $2",
                group_ids, userprofileid,
                *[json.dumps(changes[c]) if c == "attachments" else changes[c] for c in columns],
            )

        if towns is not None:
            wanted = {t["townprofileid"]: t for t in towns}
            current = {r["townprofileid"]: r[id_column] for r in group}
            stale_ids = [pid for town_id, pid in current.items() if town_id not in wanted]
            new_towns = [t for town_id, t in wanted.items() if town_id not in current]
            moves = list(zip(stale_ids, new_towns))
            moved_ids = [pid for pid, _ in moves]
            removed_ids = stale_ids[len(moves):]
            new_towns = new_towns[len(moves):]
            for pid, town in moves:
                await Database.execute(
                    f"UPDATE public.{table} SET townname = $3, townprofileid = $4, updated_at = now() "
                    f"WHERE {id_column} = $1 AND userprofileid = $2",
                    pid, userprofileid, town["townname"], town["townprofileid"],
                )
            if new_towns:
                source = dict(await Database.fetchrow(
                    f"SELECT * FROM public.{table} WHERE {id_column} = $1", postid
                ))
                if isinstance(source.get("attachments"), str):
                    try:
                        source["attachments"] = json.loads(source["attachments"])
                    except ValueError:
                        source["attachments"] = []
                added = await create_posts(post_type, source, new_towns, created_at=found["created_at"])
            if removed_ids:
                await Database.execute(
                    f"UPDATE public.{table} SET deleted = 'true', updated_at = now() "
                    f"WHERE {id_column} = ANY($1::text[]) AND userprofileid = $2",
                    removed_ids, userprofileid,
                )

    added_ids = [p[id_column] for p in added]
    rows = await Database.fetch(
        _post_select(post_type, f"WHERE n.{id_column} = ANY($1::text[])", towns="all"),
        group_ids + added_ids,
    )
    posts = [_row_to_need(r) for r in rows]
    for p in posts:
        p["deleted"] = "true" if p[id_column] in removed_ids else "false"
    surviving = [p for p in posts if p["deleted"] == "false"]
    need = next((p for p in surviving if p[id_column] == postid), surviving[0] if surviving else None)
    moved = [p for p in posts if p[id_column] in moved_ids]
    logger.info(
        "NEEDS | %s | post_type=%s %s=%s user=%s fields=%s towns_kept=%d towns_moved=%d towns_added=%d towns_removed=%d",
        outcome.upper(), post_type, id_column, postid, userprofileid,
        ",".join(sorted(changes)) if action == "edit" else "-",
        len(group_ids) - len(moved_ids) - len(removed_ids), len(moved_ids), len(added_ids), len(removed_ids),
    )
    return {"outcome": outcome, "need": need, "reason": None, "posts": posts, "moved": moved, "added": added}


async def respond_to_need(
    *,
    needid: str,
    userprofileid: str,
    contactid: Optional[str],
    message: str,
    oauth_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Store a response to a posted, open need and send the poster the need's title then the message in Messages.

    Args:
        needid: The need being responded to.
        userprofileid: The responder, who must not be the need's poster.
        contactid: The responder's contactid, stored on the response.
        message: Response text.
        oauth_token: Optional Google OAuth token, passed on so the poster gets a push for the message.

    Returns:
        {"outcome", "response"}; outcome is responded, not_found, need_closed or own_need.

    Raises:
        HTTPException: 404 when either userprofile is unknown to Messages.
    """
    need = await Database.fetchrow(
        "SELECT needid, userprofileid, title, status, need_status, COALESCE(deleted, 'false') AS deleted "
        "FROM public.etown_need WHERE needid = $1",
        needid,
    )
    if not need or need["deleted"] == "true" or need["status"] != "posted":
        outcome = "not_found"
    elif need["need_status"] == "closed":
        outcome = "need_closed"
    elif str(need["userprofileid"]).strip().lower() == userprofileid.strip().lower():
        outcome = "own_need"
    else:
        outcome = "responded"
    if outcome != "responded":
        logger.info("NEEDS | RESPOND_REFUSED | needid=%s user=%s reason=%s", needid, userprofileid, outcome)
        return {"outcome": outcome, "response": None}

    owner = str(need["userprofileid"])
    owner_type = await MessageDatabase.get_user_participant_type(owner)
    if owner_type is None:
        raise HTTPException(status_code=404, detail="The need's poster could not be found.")
    opened = await MessageService.create_conversation(
        CreateConversationRequest(userprofile_id=userprofileid, opponent_id=owner, opponent_type=owner_type)
    )
    conversation_id = opened["conversation"].id
    title_sent = await MessageService.send_message(
        SendMessageRequest(conversation_id=conversation_id, userprofile_id=userprofileid, message_text=need["title"])
    )
    message_sent = await MessageService.send_message(
        SendMessageRequest(
            conversation_id=conversation_id,
            userprofile_id=userprofileid,
            message_text=message,
            oauth_token=oauth_token,
        )
    )

    row = await Database.fetchrow(
        "INSERT INTO public.etown_needresponse (needresponseid, needid, userprofileid, contactid, message, "
        "conversation_id, title_message_id, message_id) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *",
        str(uuid.uuid4()), needid, userprofileid, contactid, message, conversation_id,
        title_sent["message"]["id"], message_sent["message"]["id"],
    )
    logger.info(
        "NEEDS | RESPONDED | needid=%s user=%s owner=%s conv=%s new_conversation=%s",
        needid, userprofileid, owner, conversation_id, opened["created"],
    )
    return {"outcome": outcome, "response": _with_ist(row)}


_RESPONSE_ACTIVE = "COALESCE(r.deleted, 'false') <> 'true' AND NOT etown_is_deleted(r.userprofileid)"

_LATEST_RESPONSES_PAGE_SQL = (
    "SELECT l.*, " + ", ".join(_person_columns("")) + " "
    "FROM (SELECT DISTINCT ON (r.userprofileid) r.needresponseid, r.needid, r.userprofileid, "
    "COALESCE(NULLIF(r.contactid, ''), u.contactid) AS contactid, r.message, r.conversation_id, "
    "r.title_message_id, r.message_id, r.created_at "
    "FROM public.etown_needresponse r "
    "LEFT JOIN public.etown_userprofile u ON u.userprofileid = r.userprofileid "
    f"WHERE r.needid = $1 AND {_RESPONSE_ACTIVE} "
    "ORDER BY r.userprofileid, r.created_at DESC) l "
    + _person_joins("l")
    + "ORDER BY l.created_at DESC LIMIT $2 OFFSET $3"
)

_RESPONDER_COUNT_SQL = (
    "SELECT count(DISTINCT r.userprofileid) FROM public.etown_needresponse r "
    f"WHERE r.needid = $1 AND {_RESPONSE_ACTIVE}"
)


async def list_need_responses(*, needid: str, page: int = 1, limit: int = 20) -> Dict[str, Any]:
    """Return a page of each responder's latest response to a need, newest first, with the responder's role, name and image.

    Args:
        needid: The need whose responses are listed.
        page: 1-based page number.
        limit: Rows per page.

    Returns:
        {"responses": [...], "total": int, "total_pages": int}; total counts unique responders.
    """
    offset = (max(int(page), 1) - 1) * limit
    rows = await Database.fetch(_LATEST_RESPONSES_PAGE_SQL, needid, limit, offset)
    total = int(await Database.fetchval(_RESPONDER_COUNT_SQL, needid) or 0)
    return {
        "responses": [_with_ist(r) for r in rows],
        "total": total,
        "total_pages": (total + limit - 1) // limit if limit else 0,
    }


_LIKE_UPSERT_SQL = """
INSERT INTO public.etown_communitylike (likeid, post_type, postid, userprofileid, contactid, likemode, likemodename)
VALUES ($1, $2, $3, $4, $5, $6, CASE $6::int WHEN 1 THEN 'Like' ELSE 'Dislike' END)
ON CONFLICT (postid, userprofileid) DO UPDATE
   SET likemode     = EXCLUDED.likemode,
       likemodename = EXCLUDED.likemodename,
       contactid    = EXCLUDED.contactid,
       updated_at   = now()
RETURNING likeid, post_type, postid, userprofileid, contactid, likemode, likemodename, created_at, updated_at
"""

_LIKE_COUNT_SQL = f"SELECT count(*) FROM public.etown_communitylike l WHERE l.postid = $1 AND {_LIKE_ACTIVE}"

_LIKES_FROM = (
    "FROM public.etown_communitylike l "
    + _person_joins("l")
    + f"WHERE l.postid = $1 AND {_LIKE_ACTIVE} "
)

_LIKES_PAGE_SQL = (
    "SELECT l.likeid, l.post_type, l.postid, l.userprofileid, l.contactid, l.updated_at AS liked_at, "
    + ", ".join(_person_columns(""))
    + " "
    + _LIKES_FROM
    + "ORDER BY l.updated_at DESC LIMIT $2 OFFSET $3"
)

_COMMENT_COUNT_SQL = (
    f"SELECT count(*) FROM public.etown_communitycomment m WHERE m.postid = $1 AND {_COMMENT_ACTIVE}"
)

_COMMENTS_SELECT = (
    "SELECT m.commentid, m.post_type, m.postid, m.userprofileid, m.contactid, m.comment, "
    "m.created_at, m.updated_at, "
    + ", ".join(_person_columns(""))
    + " FROM public.etown_communitycomment m "
    + _person_joins("m")
)

_COMMENTS_PAGE_SQL = (
    _COMMENTS_SELECT
    + f"WHERE m.postid = $1 AND {_COMMENT_ACTIVE} ORDER BY m.created_at DESC LIMIT $2 OFFSET $3"
)

_INSERT_COMMENT_SQL = """
INSERT INTO public.etown_communitycomment (commentid, post_type, postid, userprofileid, contactid, comment)
VALUES ($1, $2, $3, $4, $5, $6)
"""

_COMMENT_BY_ID_SQL = """
SELECT commentid, postid, userprofileid, deleted
FROM   public.etown_communitycomment
WHERE  commentid = $1
"""


def _with_ist(row: Any) -> Dict[str, Any]:
    """Return a row as a dict with every datetime value converted to IST."""
    out = dict(row) if row else {}
    for key, value in out.items():
        if isinstance(value, datetime):
            out[key] = value.astimezone(IST) if value.tzinfo else value.replace(tzinfo=IST)
    return out


async def find_reviewed_post(*, postid: str, post_type: str) -> Optional[Dict[str, Any]]:
    """Return an update or event's status, deleted flag and stored review state, without changing it.

    Args:
        postid: updateid or eventid of the post.
        post_type: update or event; picks the table.

    Returns:
        {"post_type", "postid", "status", "deleted", "review_status"}, or None when the
        post_type's table has no row with this id.
    """
    table, id_column = _POST_TABLES[post_type]
    found = await Database.fetchrow(
        f"SELECT status, COALESCE(deleted, 'false') AS deleted, "
        f"{_review_status_sql('review')} AS review_status "
        f"FROM public.{table} WHERE {id_column} = $1",
        postid,
    )
    if not found:
        return None

    return {
        "post_type": post_type,
        "postid": postid,
        "status": found["status"],
        "deleted": found["deleted"] == "true",
        "review_status": found["review_status"],
    }


async def _is_live_post(post_type: str, postid: str) -> bool:
    """Return True when the post_type's table holds a posted, undeleted post with this id, approved if reviewed."""
    table, id_column = _POST_TABLES[post_type]
    return bool(await Database.fetchval(
        f"SELECT EXISTS (SELECT 1 FROM public.{table} WHERE {id_column} = $1 "
        "AND status = 'posted' AND COALESCE(deleted, 'false') <> 'true'"
        f"{_approved_gate(post_type, 'review')})",
        postid,
    ))


async def set_community_like(
    *,
    post_type: str,
    postid: str,
    userprofileid: str,
    contactid: Optional[str],
    like_mode: int,
) -> Dict[str, Any]:
    """Like or dislike a posted need, update or event, one row per userprofile, and return the outcome and new count.

    Args:
        post_type: need, update or event; the table the post is looked up in and the type stored.
        postid: needid, updateid or eventid of the post.
        userprofileid: Who is liking.
        contactid: The liker's contactid, used to resolve a member's name and image.
        like_mode: 1 to like (stored as Like), 2 to dislike (stored as Dislike).

    Returns:
        {"outcome", "post_type", "like", "like_count"}; outcome is liked, disliked or post_not_found.
    """
    if not await _is_live_post(post_type, postid):
        logger.info(
            "COMMUNITY | LIKE_REFUSED | post_type=%s postid=%s user=%s reason=post_not_found",
            post_type, postid, userprofileid,
        )
        return {"outcome": "post_not_found", "post_type": None, "like": None, "like_count": 0}

    row = await Database.fetchrow(
        _LIKE_UPSERT_SQL, str(uuid.uuid4()), post_type, postid, userprofileid, contactid, like_mode
    )
    like_count = int(await Database.fetchval(_LIKE_COUNT_SQL, postid) or 0)
    outcome = "liked" if like_mode == 1 else "disliked"
    logger.info(
        "COMMUNITY | %s | post_type=%s postid=%s user=%s like_count=%d",
        outcome.upper(), post_type, postid, userprofileid, like_count,
    )
    return {"outcome": outcome, "post_type": post_type, "like": _with_ist(row), "like_count": like_count}


async def list_community_likes(*, postid: str, page: int = 1, limit: int = 20) -> Dict[str, Any]:
    """Return a page of a post's current likers, most recent first, with each liker's role, name and image.

    Args:
        postid: needid, updateid or eventid of the post.
        page: 1-based page number.
        limit: Rows per page.

    Returns:
        {"likes": [...], "total": int, "total_pages": int}.
    """
    offset = (max(int(page), 1) - 1) * limit
    rows = await Database.fetch(_LIKES_PAGE_SQL, postid, limit, offset)
    total = int(await Database.fetchval(_LIKE_COUNT_SQL, postid) or 0)
    return {
        "likes": [_with_ist(r) for r in rows],
        "total": total,
        "total_pages": (total + limit - 1) // limit if limit else 0,
    }


async def save_community_comment(
    *,
    action: str,
    post_type: Optional[str],
    postid: Optional[str],
    commentid: Optional[str],
    userprofileid: str,
    contactid: Optional[str],
    comment: Optional[str],
) -> Dict[str, Any]:
    """Add a comment to a posted need, update or event, or edit or soft-delete the caller's own comment.

    Args:
        action: add, update or delete.
        post_type: need, update or event of the post; used by add, which looks it up and stores it.
        postid: Post to comment on; used by add only.
        commentid: Comment to change; used by update and delete.
        userprofileid: The caller; must be the commenter for update and delete.
        contactid: The commenter's contactid, stored on add.
        comment: Comment text for add and update.

    Returns:
        {"outcome", "comment", "postid", "comment_count"}; outcome is added, updated, deleted,
        post_not_found, comment_not_found or not_owner.
    """
    if action == "add":
        if not await _is_live_post(post_type, postid):
            logger.info(
                "COMMUNITY | COMMENT_REFUSED | post_type=%s postid=%s user=%s reason=post_not_found",
                post_type, postid, userprofileid,
            )
            return {"outcome": "post_not_found", "comment": None, "postid": postid, "comment_count": 0}
        commentid = str(uuid.uuid4())
        await Database.execute(
            _INSERT_COMMENT_SQL, commentid, post_type, postid, userprofileid, contactid, comment
        )
        outcome = "added"
    else:
        existing = await Database.fetchrow(_COMMENT_BY_ID_SQL, commentid)
        if not existing or existing["deleted"] == "true":
            outcome = "comment_not_found"
        elif existing["userprofileid"] != userprofileid:
            outcome = "not_owner"
        else:
            outcome = "updated" if action == "update" else "deleted"
        if outcome in ("comment_not_found", "not_owner"):
            logger.info(
                "COMMUNITY | COMMENT_REFUSED | commentid=%s user=%s action=%s reason=%s",
                commentid, userprofileid, action, outcome,
            )
            return {"outcome": outcome, "comment": None, "postid": None, "comment_count": 0}
        postid = existing["postid"]
        if action == "update":
            await Database.execute(
                "UPDATE public.etown_communitycomment SET comment = $2, updated_at = now() WHERE commentid = $1",
                commentid, comment,
            )
        else:
            await Database.execute(
                "UPDATE public.etown_communitycomment SET deleted = 'true', updated_at = now() WHERE commentid = $1",
                commentid,
            )

    row = None
    if outcome != "deleted":
        row = await Database.fetchrow(_COMMENTS_SELECT + "WHERE m.commentid = $1", commentid)
    comment_count = int(await Database.fetchval(_COMMENT_COUNT_SQL, postid) or 0)
    logger.info(
        "COMMUNITY | COMMENT_%s | commentid=%s postid=%s user=%s comment_count=%d",
        outcome.upper(), commentid, postid, userprofileid, comment_count,
    )
    return {
        "outcome": outcome,
        "comment": _with_ist(row) if row else None,
        "postid": postid,
        "comment_count": comment_count,
    }


_VIEW_INSERT_SQL = (
    "WITH ins AS (INSERT INTO public.etown_communityview (post_type, postid, userprofileid, view_type, view_date) "
    "SELECT v.post_type, v.postid, $3::text, $4::text, $5::date "
    "FROM unnest($1::text[], $2::text[]) AS v(post_type, postid) WHERE "
    + " OR ".join(
        f"(v.post_type = '{post_type}' AND EXISTS (SELECT 1 FROM public.{table} p "
        f"WHERE p.{id_column} = v.postid AND COALESCE(p.deleted, 'false') <> 'true'))"
        for post_type, (table, id_column) in _POST_TABLES.items()
    )
    + " ON CONFLICT (postid, userprofileid, view_date) WHERE view_type = 'public' DO NOTHING "
    + "RETURNING post_type, postid)"
    + "".join(
        f", bump_{post_type} AS (UPDATE public.{table} t SET view_count = t.view_count + 1 "
        f"FROM ins WHERE ins.post_type = '{post_type}' AND t.{id_column} = ins.postid RETURNING 1)"
        for post_type, (table, id_column) in _POST_TABLES.items()
    )
    + " SELECT postid FROM ins"
)

_VIEW_COUNTS_SQL = " UNION ALL ".join(
    f"SELECT '{post_type}'::text AS post_type, {id_column} AS postid, view_count FROM public.{table} "
    f"WHERE {id_column} = ANY($1::text[]) AND COALESCE(deleted, 'false') <> 'true'"
    for post_type, (table, id_column) in _POST_TABLES.items()
)


async def record_community_views(
    *,
    userprofileid: str,
    posts: List[Dict[str, str]],
    view_type: str,
) -> Dict[str, Any]:
    """Count a user's view of each need, update or event, public views once per IST day, and return every item's view count.

    Args:
        userprofileid: The viewer.
        posts: Items as {"type", "id"}; types other than need, update or event are skipped.
        view_type: onclick, counted on every call, or public, counted once per post per IST day.

    Returns:
        {"view_date", "counted", "posts"}; each post is {"type", "id", "counted", "view_count"},
        view_count null for a skipped type or an id with no undeleted post.
    """
    view_date = datetime.now(IST).date()
    countable = list(dict.fromkeys((p["type"], p["id"]) for p in posts if p["type"] in POST_TYPES))
    inserted: set = set()
    counts: Dict[Tuple[str, str], int] = {}
    if countable:
        types = [t for t, _ in countable]
        ids = [i for _, i in countable]
        rows = await Database.fetch(_VIEW_INSERT_SQL, types, ids, userprofileid, view_type, view_date)
        inserted = {r["postid"] for r in rows}
        counts = {
            (r["post_type"], r["postid"]): int(r["view_count"] or 0)
            for r in await Database.fetch(_VIEW_COUNTS_SQL, ids)
        }

    out = []
    for p in posts:
        known = (p["type"], p["id"]) in counts
        out.append({
            "type": p["type"],
            "id": p["id"],
            "counted": known and p["id"] in inserted,
            "view_count": counts[(p["type"], p["id"])] if known else None,
        })
        if known:
            inserted.discard(p["id"])
    counted = sum(1 for o in out if o["counted"])
    logger.info(
        "COMMUNITY | VIEWS | user=%s view_type=%s date=%s sent=%d counted=%d",
        userprofileid, view_type, view_date, len(posts), counted,
    )
    return {"view_date": view_date, "counted": counted, "posts": out}


async def list_community_comments(*, postid: str, page: int = 1, limit: int = 20) -> Dict[str, Any]:
    """Return a page of a post's live comments, newest first, with each commenter's role, name and image.

    Args:
        postid: needid, updateid or eventid of the post.
        page: 1-based page number.
        limit: Rows per page.

    Returns:
        {"comments": [...], "total": int, "total_pages": int}.
    """
    offset = (max(int(page), 1) - 1) * limit
    rows = await Database.fetch(_COMMENTS_PAGE_SQL, postid, limit, offset)
    total = int(await Database.fetchval(_COMMENT_COUNT_SQL, postid) or 0)
    return {
        "comments": [_with_ist(r) for r in rows],
        "total": total,
        "total_pages": (total + limit - 1) // limit if limit else 0,
    }


def _row_to_need(row: Any) -> Dict[str, Any]:
    """Map an etown_need row to the API shape, decoding attachments with public urls and converting timestamps to IST."""
    need = dict(row) if row else {}
    if isinstance(need.get("towns"), str):
        need["towns"] = json.loads(need["towns"])
    raw = need.get("attachments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            logger.warning("NEEDS | BAD_ATTACHMENTS_JSON | needid=%s", need.get("needid"))
            raw = []
    need["attachments"] = [
        {
            **a,
            "url": to_public_blob_url(a.get("path")),
            "thumbnail_url": to_public_blob_url(a["thumbnail_path"]) if a.get("thumbnail_path") else None,
        }
        for a in (raw or [])
    ]
    for key in ("needed_at", "created_at"):
        value = need.get(key)
        if isinstance(value, datetime):
            need[key] = (
                value.astimezone(IST) if value.tzinfo else value.replace(tzinfo=IST)
            )
    return need
