"""AI Search Utilities - v1 semantic, keyword and fuzzy search over members and providers.

Domain layer behind GET /api/search/aisearch: combines pgvector similarity with text, fuzzy and phone-number matching, applies proximity and visibility filters, enriches results with ratings, favourites and connections, and ranks and deduplicates them, with an optional Google Places leg.

Features:
    - Vector search: Gemini embeddings-based semantic similarity
    - Fallback search: Keyword-based search when vector results insufficient
    - Flexible name search: PostgreSQL full-text search for word order independent name matching
    - Profile enrichment: Rating, favorite, and connection data enhancement
    - Geographic filtering: Location-based result filtering
    - Mutual connections: Social network overlap analysis
    - Result ranking: Relevance-based result reordering
    - Search caching: Embedded query and keyword caching
    - Performance metrics: Search execution time monitoring
    - Multilingual support: Tamil, Hindi, and English search terms
    - Deduplication: Unique result presentation and business name deduplication in provider lists
    - Fuzzy matching: Typo-tolerant search with auto-correction for business names and services
    - Smart search: Auto-correct misspellings and find similar matches
    - Proximity search: Distance-based filtering and sorting for "near me" queries
    - Phone number lookup: Fast exact phone number matching for members and providers
    - Web search kill switch: ENABLE_AISEARCH_WEB_SEARCH disables Google Places calls from aisearch()
    - Provider exclusion: Disabled and review-pending providers are held out of every result leg
    - Premium badges: Etown_providertype rewritten to the paid tier's badge label after ranking


Classes:
    FuzzySearchConfig(): Configuration for fuzzy matching thresholds and performance

Functions:
    is_phone_number_query(): Detect phone number search queries
    normalize_phone_number(): Normalize phone numbers for database matching (drops +91)
    normalize_text_for_matching(): Normalize text for search matching (whitespace/case)
    strip_proximity_phrases(): Remove 'near me'/proximity phrases (English, Tamil, Hindi) before embedding
    haversine_km(): Great-circle distance in kilometers between two coordinates
    _apply_distance_to_provider_results(): Populate distance fields on provider rows from user coordinates
    _resolve_user_coordinates(): Resolve caller coordinates from the database when not supplied
    detect_proximity_search(): Identify "near me" and proximity search terms
    _should_run_mytown_lookup(): Heuristic gate to skip MyTown lookups for non-location queries
    _query_tokens_for_text_search(): Expand a query into plural/verb-form token variants for text matching
    _should_attempt_auto_correct(): Gate auto-correction to short queries
    _quick_autocorrect(): Cheap local autocorrect for a single service token, no DB dictionary
    _should_prefer_text_first(): Decide when explicit keyword intent should run text before vector search
    _has_strong_provider_text_hit(): Detect a clear multi-token text match already present in text results
    _has_exact_member_name_match(): Detect an exact member first-name match against the query
    build_service_dictionary_cached(): Cached service-terminology dictionary from etown_metadata_term
    build_city_dictionary_cached(): Cached city-name dictionary from etown_metadata_city
    fuzzy_match_text(): Find best matches for misspelled business names and services
    _is_reasonable_auto_correction(): Guard against auto-correcting to an unrelated long business name
    auto_correct_query(): Auto-correct search queries using separate service/city dictionaries
    preprocess_search_query(): Enhanced query preprocessing with spell-check
    cached_embed_query(): Cached embedding generation for search queries
    cached_keyword_suggestions(): Cached keyword extraction from queries
    get_fallback_search_keywords(): Generate fallback search terms
    _build_response_keywords(): Lightweight keywords derived from already-fetched response data
    _process_location_metadata(): Filter displayable cities/areas and dedupe keywords against them
    extract_city_state(): Parse geographic information from addresses
    enrich_profiles_with_rating_data(): Add rating information to profiles
    enrich_profiles_with_favourite_count(): Add favorite counts to profiles
    mutual_connection_status(): Inject connection/invitation status into nested result profiles
    rerank_search_results(): Improve result relevance ordering (Distance > Semantic > Text)
    format_provider_results(): Structure provider data for API responses
    format_member_results(): Structure member data for API responses
    fetch_provider_profiles_by_contact_ids_bulk(): Bulk provider profile retrieval
    fetch_member_address_details_bulk(): Bulk member address retrieval, falling back to the provider table
    fetch_mutual_connections(): Find shared connections between two users
    bulk_fetch_mutual_connections(): Find shared connections for many target contacts in one query
    enrich_profiles_with_mycircle_count(): Add social circle size data
    phone_lookup_search(): Indexed lookup across contact, userprofile, member and provider numbers
    _map_places_to_provider_rows(): Map Google Places results into DB-row-shaped provider dicts
    _augment_providers_with_web(): Top up sparse local results from Google Places, dedupe and persist
    _apply_premium_badges(): Rewrite Etown_providertype in a response to the paid tier's badge label
    aisearch(): Main AI search endpoint logic with vector, text, and phone search
    enrich_by_ids(): Fast-path enrichment of pre-known member/provider ids without running a search

Dependencies:
    - asyncio: Asynchronous search operations and per-stage timeouts
    - math: Haversine great-circle distance for proximity ranking
    - async_lru: Search result and dictionary caching
    - rapidfuzz: High-performance fuzzy string matching
    - app.db.client: Async PostgreSQL access
    - app.utils.embeddings: Gemini embedding generation
    - app.etown.mytown.mytown_utils: MyTown DB alias and city-token extraction
    - app.schemas.api: Search API response models
    - app.agent_infrastructure.infrastructure.llm_clients: gpt41 client
    - app.etown.plans.plans_utils: premium badge resolution
    - app.etown.provider.provider_review_utils: review-pending exclusion SQL fragments
    - app.core.config: feature flags such as ENABLE_AISEARCH_WEB_SEARCH

Updates:
    23-Feb-2026: Comprehensive documentation with complete feature coverage
    26-Feb-2026: Full-text member search; hybrid vector+text scoring; similarity threshold fix.
    27-Feb-2026: Distance>Semantic>Text ranking; city deduplication, filtering and area cleanup.
    03-Mar-2026: Added business name deduplication in providercurrent lists using DISTINCT ON clause
    04-Mar-2026: Fixed whitespace normalization in vector search SQL; added fuzzy matching and auto-correction.
    05-Mar-2026: Added proximity search detection and distance-based city filtering for "near me" queries
    09-Mar-2026: Added phone number lookup functionality with fast indexed search for members and providers
    30-Jul-2026: Added ENABLE_AISEARCH_WEB_SEARCH, a kill switch for the Google Places fallback.
    12-Aug-2026: build_city_dictionary_cached() reads row['city'].
    14-Aug-2026: The web fallback drops non-India places.
    20-Aug-2026: Verified markers report the paid tier's badge label, applied after ranking.
    26-Aug-2026: Silent exception handlers now logged with tracebacks; vague log messages rewritten.
    31-Aug-2026: Phone lookup matches userprofile/member/provider numbers; web fallback skips phone queries.
    02-Sep-2026: Providers with etown_provider.disabled are excluded from every result path.
    17-Sep-2026: Providers awaiting review are held out of every result leg.
    21-Sep-2026: Review exclusion extended to provider_table_targeted_query.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    07-Oct-2026: Soft-deleted providers and provider services excluded from every result path.
    08-Oct-2026: Removed LangSmith tracing.

Notes:
    Uses minimum similarity threshold of 0.15 for vector search. Implements
    intelligent fallback to keyword search when vector results are insufficient.
    Supports geographic bounding for location-aware search optimization.
    
    **Performance Optimization:**
    Enhanced member search uses PostgreSQL full-text search with GIN index for flexible name word order matching. 
        
    **Flexible Name Search:**
    The member search now supports flexible word order in names - searching : to_tsvector() and plainto_tsquery() for token-based matching while maintaining ILIKE fallback for backwards compatibility.
"""

import asyncio
import json
import logging
import time
import re
import uuid
from typing import Dict, List, Any, Optional, Set, Tuple
from dataclasses import dataclass
from async_lru import alru_cache
from rapidfuzz import fuzz, process
import math

from app.db.client import Database
from app.agent_infrastructure.infrastructure.llm_clients import gpt41
from app.utils.embeddings import generate_embedding, vector_to_postgres_string
from app.core.config import settings
from langchain_core.messages import SystemMessage, HumanMessage
from app.etown.mytown.mytown_utils import Database as MyTownDatabase, extract_city_tokens
from app.etown.plans.plans_utils import premium_display, resolve_provider_badges
from app.etown.provider.provider_review_utils import (
    review_pending_exclusion,
    review_pending_provider_ids_cte,
    review_pending_userprofile_exclusion,
)

_EXCLUDE_REVIEW_PENDING_PS = "AND " + review_pending_exclusion("ps.providerid")
_EXCLUDE_REVIEW_PENDING_P = "AND " + review_pending_exclusion("p.providerid")
_EXCLUDE_REVIEW_PENDING_U = "AND " + review_pending_userprofile_exclusion("u.userprofileid")
_EXCLUDE_REVIEW_PENDING_BARE = "AND " + review_pending_exclusion("etown_providerservices_copy.providerid")
_REVIEW_PENDING_CTE = review_pending_provider_ids_cte()
_EXCLUDE_DELETED_PS = (
    "AND ps.deleted IS DISTINCT FROM 'true'\n"
    "              AND NOT EXISTS (SELECT 1 FROM etown_provider dl"
    " WHERE dl.providerid = ps.providerid AND dl.deleted = 'true')"
)
_EXCLUDE_DELETED_BARE = (
    "AND etown_providerservices_copy.deleted IS DISTINCT FROM 'true'\n"
    "        AND NOT EXISTS (SELECT 1 FROM etown_provider dl"
    " WHERE dl.providerid = etown_providerservices_copy.providerid AND dl.deleted = 'true')"
)
from app.schemas.api import (
    AISearchResponse, 
    SearchResults,
    AreaSuggestion,
    CityItem,
)

ENABLE_FALLBACK_SEARCH = False
FALLBACK_SEARCH_THRESHOLD = 2
ENABLE_AISEARCH_WEB_SEARCH = True
ENABLE_AISEARCH_WEB_FALLBACK = True
AISEARCH_WEB_FALLBACK_THRESHOLD = 10
AISEARCH_WEB_FALLBACK_RADIUS_METERS = 25000
FALLBACK_MAX_KEYWORDS = 5
FALLBACK_MAX_PARALLEL_SEARCHES = 6
FALLBACK_KEYWORD_MIN_LENGTH = 3
ENABLE_FALLBACK_DEDUPLICATION = True
MIN_SIMILARITY_THRESHOLD = 0.15
EMBEDDING_TIMEOUT_SECONDS = 5.0
FALLBACK_EXECUTION_TIMEOUT_SECONDS = 3.0
MYTOWN_AREAS_TIMEOUT_SECONDS = 1.0
MYTOWN_CITIES_TIMEOUT_SECONDS = 2.0
AISEARCH_PROVIDER_VECTOR_TIMEOUT_SECONDS = 6.0
AISEARCH_PROVIDER_TEXT_TIMEOUT_SECONDS = 6.0
AISEARCH_MEMBER_QUERY_TIMEOUT_SECONDS = 5.0
AISEARCH_ENRICH_TIMEOUT_SECONDS = 1.0
AISEARCH_CONNECTION_TIMEOUT_SECONDS = 1.0
AISEARCH_COORD_LOOKUP_TIMEOUT_SECONDS = 1.0
AISEARCH_EMERGENCY_QUERY_TIMEOUT_SECONDS = 1.0
AUTO_CORRECT_MAX_TOKEN_COUNT = 2
TEXT_FIRST_MIN_TOKEN_COUNT = 2
TEXT_SEARCH_MAX_TOKEN_PATTERNS = 4
TEXT_SEARCH_STOPWORDS = {"and", "the", "for", "with", "near", "from", "into", "your"}
BUSINESS_DICTIONARY_TIMEOUT_SECONDS = 1.5
KEYWORD_SUGGESTIONS_TIMEOUT_SECONDS = 1.0
PHONE_LOOKUP_TIMEOUT_SECONDS = 5.0
PREPROCESS_TIMEOUT_SECONDS = 1.5
MIN_PHONE_DIGITS = 10
MAX_PHONE_DIGITS = 13
QUICK_AUTOCORRECT_MAP = {
    "plumber": "plumbing",
    "electrician": "electrical",
}
PLURAL_SUFFIX_MAP = {
    "shoes": "shoe",
    "mobiles": "mobile",
    "computers": "computer",
    "hotels": "hotel",
    "restaurants": "restaurant",
    "services": "service",
    "works": "work",
    "stores": "store",
    "shops": "shop",
    "materials": "material",
    "repairs": "repair",
    "cleaners": "cleaner",
    "painters": "painter",
    "tailors": "tailor",
    "caterers": "caterer",
}
MORPH_SERVICE_ROOT_HINTS = (
    "plumb",
    "electr",
    "carpent",
    "paint",
    "weld",
    "repair",
    "mechan",
    "clean",
    "cater",
)
ROLE_ID_TO_NAME = {
    0: "provider",
    1: "family",
    2: "relatives",
    3: "friend",
    4: "customer"
}

logger = logging.getLogger("app")

COMBINED_MEMBER_QUERY = """
    SELECT
        c.contactid,
        c.firstname,
        c.email,
        c.profileimage,
        c.address,
        c.city,
        c.statecode,
        c.latitude,
        c.longitude,
        c.mobilephone,
        c.rolecodename,
        c.publicsearchstatus,
        c.createdon,
        u.userprofileid,
        u.role
    FROM etown_contact c
    JOIN etown_userprofile u ON c.contactid = u.contactid
    WHERE
        u.role = 2
        AND c.verified IS NOT FALSE
        AND (

            (
                $6::text[] IS NOT NULL
                AND array_length($6::text[], 1) > 0
                AND u.userprofileid = ANY($6::text[])
            )

            OR

            (
                c.contactid != $1::text
                AND (
                    LOWER(COALESCE(c.firstname, '')) LIKE $2
                    OR LOWER(COALESCE(c.email, '')) LIKE $2
                    OR (
                        array_length($5::text[], 1) IS NOT NULL
                        AND (
                            LOWER(COALESCE(c.firstname, '')) LIKE ALL($5::text[])
                            OR LOWER(COALESCE(c.email, '')) LIKE ALL($5::text[])
                        )
                    )
                )
            )
        )
    LIMIT $3 OFFSET $4
"""

def is_phone_number_query(query: str) -> bool:
    """Return True if the query has a phone-length digit count and is at least 80 percent digits."""
    if not query:
        return False
    
    digits = re.sub(r'\D', '', query.strip())
    
    if len(digits) < MIN_PHONE_DIGITS or len(digits) > MAX_PHONE_DIGITS:
        return False
    
    original_clean = query.strip().replace(' ', '').replace('-', '').replace('+', '').replace('(', '').replace(')', '')
    digit_ratio = len(digits) / len(original_clean) if original_clean else 0
    
    return digit_ratio >= 0.8 and len(digits) <= MAX_PHONE_DIGITS


def normalize_phone_number(phone: str) -> str:
    """Return the last ten digits of a phone number, or all its digits when there are fewer than ten."""
    if not phone:
        return ""

    digits = re.sub(r'\D', '', phone.strip())

    return digits[-10:] if len(digits) >= 10 else digits


@dataclass
class FuzzySearchConfig:
    """Configuration for fuzzy matching thresholds and performance settings.
    
    Args:
        auto_correct_threshold: Minimum similarity score for auto-correction (0-100).
        fuzzy_match_threshold: Minimum score to include fuzzy matches (0-100).
        max_corrections: Maximum number of corrections to return.
        enable_phonetic: Enable sound-based matching for better accuracy.
    
    Returns:
        FuzzySearchConfig: Configuration object for fuzzy search operations.
    """
    auto_correct_threshold: float = 85.0
    fuzzy_match_threshold: float = 60.0
    max_corrections: int = 5
    enable_phonetic: bool = False

FUZZY_CONFIG = FuzzySearchConfig()

def normalize_text_for_matching(text: str) -> str:
    """Return lowercased text with & as "and", punctuation spaced out and whitespace collapsed."""
    if not text:
        return ""
    normalized = text.lower().strip()
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return re.sub(r'\s+', ' ', normalized).strip()


_PROXIMITY_PATTERNS = re.compile(
    r'(?:'
    r'\bnear\s+my\s+location\b'
    r'|\bclosest\s+to\s+me\b'
    r'|\bclose\s+to\s+me\b'
    r'|\baround\s+me\b'
    r'|\bnear\s+me\b'
    r'|\bnear\s+by\b'
    r'|\bnearby\b'
    r'|\bnearest\b'
    r'|\bclosest\b'
    r'|என்\s*அருகில்'
    r'|அருகிலுள்ள'
    r'|பக்கத்தில்'
    r'|என்னருகில்'
    r'|मेरे\s+पास'
    r'|मेरे\s+नज़दीक'
    r'|मेरे\s+नजदीक'
    r'|आस\s*पास'
    r'|यहाँ\s+के\s+पास'
    r'|नज़दीक'
    r'|नजदीक'
    r')',
    re.IGNORECASE | re.UNICODE
)


def strip_proximity_phrases(query: str) -> str:
    """Remove English, Tamil and Hindi proximity phrases from a query, returning the original if nothing would remain."""
    if not query:
        return query
    cleaned = _PROXIMITY_PATTERNS.sub(' ', query)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned if cleaned else query


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Fast great-circle distance in kilometers."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def _apply_distance_to_provider_results(
    providers: List[Dict[str, Any]],
    user_latitude: Optional[float],
    user_longitude: Optional[float],
) -> None:
    """Populate distance fields for provider rows when user coordinates are available."""
    if user_latitude is None or user_longitude is None:
        return

    for provider in providers:
        try:
            raw_lat = provider.get("Etown_latitude")
            raw_lon = provider.get("Etown_longitude")
            provider_lat = float(raw_lat) if raw_lat not in (None, "") else None
            provider_lon = float(raw_lon) if raw_lon not in (None, "") else None

            if provider_lat is None or provider_lon is None:
                provider["Etown_distance_km"] = None
                provider["Etown_distance_meters"] = None
                continue

            distance_km = round(
                haversine_km(user_latitude, user_longitude, provider_lat, provider_lon),
                2,
            )
            provider["Etown_distance_km"] = distance_km
            provider["Etown_distance_meters"] = int(distance_km * 1000)
        except Exception:
            logger.debug("aisearch: distance could not be computed for a provider row; leaving it unset", exc_info=True)
            provider["Etown_distance_km"] = None
            provider["Etown_distance_meters"] = None


async def _resolve_user_coordinates(
    contact_id: Optional[str],
    user_profile_id: Optional[str],
) -> Optional[Tuple[float, float]]:
    """Resolve user coordinates from DB when request doesn't send latitude/longitude."""
    normalized_contact = str(contact_id or "").strip().lower()
    normalized_profile = str(user_profile_id or "").strip().lower()
    if not normalized_contact and not normalized_profile:
        return None

    combined_query = """
        SELECT latitude, longitude
        FROM (

            SELECT c.latitude, c.longitude, 1 AS priority
            FROM etown_contact c
            WHERE ($1 <> '' AND TRIM(LOWER(c.contactid)) = $1)
              AND c.latitude IS NOT NULL AND c.longitude IS NOT NULL

            UNION ALL

            SELECT c.latitude, c.longitude, 2
            FROM etown_contact c
            JOIN etown_userprofile u ON c.contactid = u.contactid
            WHERE ($2 <> '' AND TRIM(LOWER(u.userprofileid)) = $2)
              AND c.latitude IS NOT NULL AND c.longitude IS NOT NULL

            UNION ALL

            SELECT pvd.latitude, pvd.longitude, 3
            FROM etown_provider pvd
            WHERE (($1 <> '' AND TRIM(LOWER(pvd.contactid)) = $1)
                OR ($2 <> '' AND TRIM(LOWER(pvd.userprofileid)) = $2))
              AND pvd.latitude IS NOT NULL AND pvd.longitude IS NOT NULL

            UNION ALL

            SELECT ps.latitude, ps.longitude, 4
            FROM etown_providerservices_copy ps
            WHERE (($1 <> '' AND TRIM(LOWER(ps.contactid)) = $1)
                OR ($2 <> '' AND TRIM(LOWER(ps.userprofileid)) = $2))
              AND ps.latitude IS NOT NULL AND ps.longitude IS NOT NULL

        ) t
        ORDER BY priority
        LIMIT 1
"""

    try:
        row = await asyncio.wait_for(
            Database.fetchrow(combined_query, normalized_contact, normalized_profile),
            timeout=AISEARCH_COORD_LOOKUP_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.warning("aisearch: user coordinate lookup failed or timed out", exc_info=True)
        return None

    if not row:
        return None

    try:
        lat_raw = row.get("latitude")
        lon_raw = row.get("longitude")
        if lat_raw is None or lon_raw is None:
            return None
        return float(lat_raw), float(lon_raw)
    except Exception:
        logger.warning("aisearch: stored user coordinates are not numeric", exc_info=True)
        return None


def detect_proximity_search(query: str) -> bool:
    """Return True if the query contains an English proximity term such as near me, nearby or closest."""
    if not query:
        return False
    
    proximity_terms = [
        'near me', 'nearby', 'close to me', 'in my area', 'around me',
        'near by', 'close by', 'in vicinity', 'local', 'closest'
    ]
    
    query_lower = query.lower()
    return any(term in query_lower for term in proximity_terms)


_SERVICE_SKIP_KEYWORDS = {
    "plumber", "plumbing", "electrician", "electrical", "carpenter", "carpentry",
    "painter", "painting", "welder", "welding", "mechanic", "mechanical",
    "repair", "cleaning", "catering", "doctor", "dentist", "lawyer",
    "teacher", "tutor", "salon", "barber", "tailor", "driver",
    "cook", "chef", "mason", "gardener", "pest", "courier",
    "photographer", "designer", "trainer", "consultant", "accountant",
    "architect", "interior", "movers", "packers", "laundry",
    "food", "restaurant", "bakery", "grocery", "pharmacy",
    "fitness", "yoga", "gym", "spa", "massage",
    "sweets", "cakes", "flowers", "gifts",
}


def _should_run_mytown_lookup(query: str) -> bool:
    """Heuristic gate to avoid expensive MyTown lookups for non-location queries."""
    if not query:
        return False

    if detect_proximity_search(query):
        return True

    tokens = _query_tokens_for_text_search(query)
    if not tokens:
        return False

    location_hints = {
        "nagar", "colony", "street", "road", "layout", "area",
        "town", "city", "village", "district", "state",
    }
    if any(token in location_hints for token in tokens):
        return True

    if len(tokens) > 1:
        return False

    token = tokens[0]
    if any(token.startswith(hint) for hint in MORPH_SERVICE_ROOT_HINTS):
        return False

    if token in _SERVICE_SKIP_KEYWORDS:
        return False

    return True


def _query_tokens_for_text_search(query: str) -> List[str]:
    """Build a stable list of meaningful query tokens for text matching."""
    normalized_query = normalize_text_for_matching(query)
    base_tokens = [
        token
        for token in normalized_query.split()
        if len(token) >= 3 and token not in TEXT_SEARCH_STOPWORDS
    ]

    def _expand_token_variants(token: str) -> List[str]:
        variants = [token]

        if token in PLURAL_SUFFIX_MAP:
            variants.append(PLURAL_SUFFIX_MAP[token])
        for plural, singular in PLURAL_SUFFIX_MAP.items():
            if token == singular and plural not in variants:
                variants.append(plural)

        if len(token) < 5:
            return variants

        if token.endswith("s") and not token.endswith("ss") and token[:-1] not in variants:
            variants.append(token[:-1])

        if token.endswith("er"):
            root = token[:-2]
            if len(root) >= 3:
                variants.extend([root, f"{root}ing"])
        elif token.endswith("ing"):
            root = token[:-3]
            if len(root) >= 3:
                variants.extend([root, f"{root}er"])

        return variants

    expanded_tokens: List[str] = []
    for token in base_tokens:
        for variant in _expand_token_variants(token):
            if len(variant) >= 3 and variant not in TEXT_SEARCH_STOPWORDS:
                expanded_tokens.append(variant)

    result = list(dict.fromkeys(expanded_tokens))

    if 2 <= len(base_tokens) <= 3:
        from itertools import permutations
        for perm in permutations(base_tokens):
            joined = " ".join(perm)
            if joined not in result:
                result.append(joined)

    return result


def _should_attempt_auto_correct(normalized_query: str) -> bool:
    """Keep fuzzy auto-correction off the critical path for long phrase queries."""
    if not normalized_query or len(normalized_query) < 3:
        return False
    return len(normalized_query.split()) <= AUTO_CORRECT_MAX_TOKEN_COUNT


def _quick_autocorrect(normalized_query: str) -> Optional[Tuple[str, float, str]]:
    """Cheap local autocorrect for short service tokens without DB dictionary lookups."""
    if not normalized_query:
        return None

    tokens = normalized_query.split()
    if len(tokens) != 1:
        return None

    token = tokens[0]
    mapped = QUICK_AUTOCORRECT_MAP.get(token)
    if mapped and mapped != token:
        return mapped, 84.0, "service"

    if len(token) < 5:
        return None

    if token.endswith("er"):
        root = token[:-2]
        if root and any(root.startswith(hint) for hint in MORPH_SERVICE_ROOT_HINTS):
            candidate = f"{root}ing"
            if candidate != token:
                return candidate, 72.0, "service"
    elif token.endswith("ing"):
        root = token[:-3]
        if root and any(root.startswith(hint) for hint in MORPH_SERVICE_ROOT_HINTS):
            candidate = f"{root}er"
            if candidate != token:
                return candidate, 70.0, "service"

    return None


def _should_prefer_text_first(query: str) -> bool:
    """Use text-first search when keyword intent is explicit."""
    tokens = _query_tokens_for_text_search(query)
    if len(tokens) >= TEXT_FIRST_MIN_TOKEN_COUNT:
        return True
    if len(tokens) == 1 and len(tokens[0]) >= 4:
        return True
    return False


def _has_strong_provider_text_hit(rows: List[Dict[str, Any]], query: str) -> bool:
    """Detect when text search already produced a clear multi-token match."""
    query_tokens = _query_tokens_for_text_search(query)
    if not rows:
        return False

    normalized_query = normalize_text_for_matching(query)
    searchable_fields = (
        "businessname",
        "business_name",
        "services",
        "servicedescription",
        "description",
        "businesscategory",
        "servicecategory",
        "address",
        "city",
    )

    if len(query_tokens) < TEXT_FIRST_MIN_TOKEN_COUNT:
        for row in rows:
            searchable_text = normalize_text_for_matching(
                " ".join(str(row.get(field)) for field in searchable_fields if row.get(field))
            )
            if normalized_query and normalized_query in searchable_text:
                return True
        return False

    for row in rows:
        searchable_text = normalize_text_for_matching(
            " ".join(str(row.get(field)) for field in searchable_fields if row.get(field))
        )
        if not searchable_text:
            continue
        if normalized_query and normalized_query in searchable_text:
            return True
        matched_tokens = sum(1 for token in query_tokens if token in searchable_text)
        if matched_tokens >= len(query_tokens):
            return True

    return False


def _has_exact_member_name_match(members: List[Dict[str, Any]], query: str) -> bool:
    """Return True when any member first-name exactly matches the normalized query."""
    normalized_query = normalize_text_for_matching(query)
    if not normalized_query:
        return False

    for member in members:
        member_name = normalize_text_for_matching(member.get("Etown_firstname") or "")
        if member_name and member_name == normalized_query:
            return True
    return False

@alru_cache(maxsize=1000, ttl=3600)
async def build_service_dictionary_cached() -> List[str]:
    """Return normalized service terms from etown_metadata_term for autocorrect, or [] on timeout or error."""
    try:
        query = """
            select term from etown_metadata_term
        """
        result = await asyncio.wait_for(
            Database.fetch(query),
            timeout=BUSINESS_DICTIONARY_TIMEOUT_SECONDS,
        )
        terms = [normalize_text_for_matching(row['term']) for row in result]
        logger.info(f"Service dictionary built: {len(terms)} terms")
        return terms
    except asyncio.TimeoutError:
        logger.warning(
            "Service dictionary build timed out after %.2fs",
            BUSINESS_DICTIONARY_TIMEOUT_SECONDS,
        )
        return []
    except Exception as e:
        logger.warning(f"Failed to build service dictionary: {e}")
        return []


@alru_cache(maxsize=1000, ttl=3600)
async def build_city_dictionary_cached() -> List[str]:
    """Return normalized city names from etown_metadata_city for autocorrect, or [] on timeout or error."""
    try:
        query = """
           select city from etown_metadata_city
        """
        result = await asyncio.wait_for(
            Database.fetch(query),
            timeout=BUSINESS_DICTIONARY_TIMEOUT_SECONDS,
        )
        terms = [normalize_text_for_matching(row['city']) for row in result]
        logger.info(f"City dictionary built: {len(terms)} terms")
        return terms
    except asyncio.TimeoutError:
        logger.warning(
            "City dictionary build timed out after %.2fs",
            BUSINESS_DICTIONARY_TIMEOUT_SECONDS,
        )
        return []
    except Exception as e:
        logger.warning(f"Failed to build city dictionary: {e}")
        return []

async def fuzzy_match_text(query: str, candidates: List[str], limit: int = 5) -> List[Tuple[str, float]]:
    """Return up to limit (candidate, score) fuzzy matches above the threshold, best first, or [] on error."""
    if not query or not candidates:
        return []
    
    try:
        normalized_query = normalize_text_for_matching(query)
        
        all_matches: List[Tuple[str, float]] = []
        extract_limit = max(1, limit)
        score_cutoff = FUZZY_CONFIG.fuzzy_match_threshold

        simple_matches = process.extract(
            normalized_query,
            candidates,
            scorer=fuzz.ratio,
            limit=extract_limit,
            score_cutoff=score_cutoff,
        )
        all_matches.extend([(match, score) for match, score, _ in simple_matches])

        token_matches = process.extract(
            normalized_query,
            candidates,
            scorer=fuzz.token_sort_ratio,
            limit=extract_limit,
            score_cutoff=score_cutoff,
        )
        all_matches.extend([(match, score) for match, score, _ in token_matches])

        if ' ' not in normalized_query:
            qlen = len(normalized_query)
            similar_length_candidates = [
                c for c in candidates
                if ' ' not in c and min(len(c), qlen) / max(len(c), qlen) >= 0.75
            ]
            if similar_length_candidates:
                partial_matches = process.extract(
                    normalized_query,
                    similar_length_candidates,
                    scorer=fuzz.partial_ratio,
                    limit=extract_limit,
                    score_cutoff=score_cutoff,
                )
                all_matches.extend([(match, score) for match, score, _ in partial_matches])
        
        match_dict = {}
        for match, score in all_matches:
            if match in match_dict:
                match_dict[match] = max(match_dict[match], score)
            else:
                match_dict[match] = score
        
        sorted_matches = sorted(match_dict.items(), key=lambda x: x[1], reverse=True)
        filtered_matches = [(match, score) for match, score in sorted_matches 
                          if score >= FUZZY_CONFIG.fuzzy_match_threshold]
        
        return filtered_matches[:limit]
    
    except Exception as e:
        logger.error(f"Fuzzy matching failed for query '{query}': {e}", exc_info=True)
        return []


def _is_reasonable_auto_correction(original_query: str, candidate: str, confidence: float) -> bool:
    """Avoid over-correcting to unrelated long business names."""
    original = normalize_text_for_matching(original_query)
    corrected = normalize_text_for_matching(candidate)

    if not original or not corrected:
        return False

    o_words = original.split()
    c_words = corrected.split()

    if len(o_words) == 1 and len(c_words) > 2:
        return False

    length_gap = abs(len(corrected) - len(original))
    if length_gap > 12:
        return False

    if len(corrected) > max(2 * len(original), len(original) + 10):
        return False

    original_tokens = {w for w in o_words if len(w) >= 3}
    corrected_tokens = {w for w in c_words if len(w) >= 3}
    if original_tokens and corrected_tokens and len(original_tokens) >= 2:
        overlap_ratio = len(original_tokens.intersection(corrected_tokens)) / len(original_tokens)
        if overlap_ratio < 0.6:
            return False

    short_original_tokens = {w for w in o_words if len(w) >= 2}
    short_corrected_tokens = {w for w in c_words if len(w) >= 2}
    if short_original_tokens and short_corrected_tokens and len(short_original_tokens) >= 2:
        overlap_ratio = len(short_original_tokens.intersection(short_corrected_tokens)) / len(short_original_tokens)
        if overlap_ratio < 0.6:
            return False

    return True

async def auto_correct_query(query: str) -> Dict[str, Any]:
    """Return the best service or city dictionary correction for a query above the threshold, or a no-correction dict."""
    no_correction = {
        "corrected_query": query,
        "confidence": 0.0,
        "correction_applied": False,
        "original_query": query,
        "correction_type": None
    }
    try:
        logger.debug(f"AUTO-CORRECT START | Query: '{query}'")
        normalized_query = normalize_text_for_matching(query)
        
        service_dict, city_dict = await asyncio.gather(
            build_service_dictionary_cached(),
            build_city_dictionary_cached()
        )
        logger.debug(f"Dictionaries loaded: {len(service_dict)} service terms, {len(city_dict)} city terms")
        
        if not service_dict and not city_dict:
            logger.debug("Both dictionaries empty - returning original query")
            return no_correction
        
        if normalized_query in set(service_dict):
            logger.debug(f"Exact match found in service dictionary for '{normalized_query}' - skipping correction")
            return no_correction
        if normalized_query in set(city_dict):
            logger.debug(f"Exact match found in city dictionary for '{normalized_query}' - skipping correction")
            return no_correction

        service_matches = await fuzzy_match_text(query, service_dict, limit=3) if service_dict else []
        city_matches = await fuzzy_match_text(query, city_dict, limit=3) if city_dict else []
        
        if service_matches:
            logger.debug(f"Service top matches: {service_matches[:3]}")
        if city_matches:
            logger.debug(f"City top matches: {city_matches[:3]}")
        
        best_match = None
        best_score = 0.0
        best_type = None
        
        if service_matches and service_matches[0][1] >= FUZZY_CONFIG.auto_correct_threshold:
            best_match = service_matches[0][0]
            best_score = service_matches[0][1]
            best_type = "service"
        
        if city_matches and city_matches[0][1] >= FUZZY_CONFIG.auto_correct_threshold:
            if city_matches[0][1] > best_score:
                best_match = city_matches[0][0]
                best_score = city_matches[0][1]
                best_type = "city"
        
        if best_match:
            logger.info(f"Auto-correction: '{query}' → '{best_match}' (confidence: {best_score:.1f}%, type: {best_type})")
            return {
                "corrected_query": best_match,
                "confidence": best_score,
                "correction_applied": True,
                "original_query": query,
                "correction_type": best_type
            }
        
        svc_best = service_matches[0][1] if service_matches else 0
        city_best = city_matches[0][1] if city_matches else 0
        logger.debug(f"No correction applied: service best={svc_best:.1f}%, city best={city_best:.1f}% < threshold {FUZZY_CONFIG.auto_correct_threshold}%")
        return no_correction
        
    except Exception as e:
        logger.error(f"Auto-correction failed for query '{query}': {e}", exc_info=True)
        return {**no_correction, "error": str(e)}


async def preprocess_search_query(query: str, enable_auto_correct: bool = True) -> Dict[str, Any]:
    """Normalize and optionally auto-correct a search query, returning processed and original forms plus correction metadata."""
    try:
        logger.debug(f"PREPROCESSING START | Query: '{query}' | Auto-correct: {enable_auto_correct}")
        stripped = strip_proximity_phrases(query)
        if stripped != query:
            logger.info(f"PROXIMITY STRIPPED: '{query}' → '{stripped}'")
        
        normalized = normalize_text_for_matching(stripped)
        logger.debug("NORMALIZED: '%s' -> '%s'", query, normalized)
        
        if enable_auto_correct and _should_attempt_auto_correct(normalized):
            quick_correction = _quick_autocorrect(normalized)
            if quick_correction:
                corrected_query, confidence, correction_type = quick_correction
                logger.info(
                    "QUICK AUTO-CORRECTED: '%s' -> '%s' (confidence: %.1f%%, type: %s)",
                    normalized,
                    corrected_query,
                    confidence,
                    correction_type,
                )
                return {
                    "processed_query": corrected_query,
                    "original_query": query,
                    "normalized_query": normalized,
                    "correction_applied": True,
                    "correction_confidence": confidence,
                    "correction_type": correction_type,
                    "preprocessing_time": 0.0
                }

            if len(normalized.split()) == 1:
                logger.debug("Running lightweight city-only auto-correct for single-token query '%s'", normalized)
                try:
                    city_dict = await build_city_dictionary_cached()
                    if city_dict:
                        if normalized in set(city_dict):
                            return {
                                "processed_query": normalized,
                                "original_query": query,
                                "normalized_query": normalized,
                                "correction_applied": False,
                                "correction_confidence": 0.0,
                                "correction_type": None,
                                "preprocessing_time": 0.0
                            }
                        city_matches = await fuzzy_match_text(normalized, city_dict, limit=3)
                        if city_matches and city_matches[0][1] >= FUZZY_CONFIG.auto_correct_threshold:
                            corrected = city_matches[0][0]
                            conf = city_matches[0][1]
                            if _is_reasonable_auto_correction(normalized, corrected, conf):
                                logger.info(
                                    "CITY AUTO-CORRECTED (single-token): '%s' -> '%s' (confidence: %.1f%%)",
                                    normalized, corrected, conf,
                                )
                                return {
                                    "processed_query": corrected,
                                    "original_query": query,
                                    "normalized_query": normalized,
                                    "correction_applied": True,
                                    "correction_confidence": conf,
                                    "correction_type": "city",
                                    "preprocessing_time": 0.0
                                }
                except Exception as e:
                    logger.debug("City-only auto-correct failed for '%s': %s", normalized, e)
                return {
                    "processed_query": normalized,
                    "original_query": query,
                    "normalized_query": normalized,
                    "correction_applied": False,
                    "correction_confidence": 0.0,
                    "correction_type": None,
                    "preprocessing_time": 0.0
                }

            logger.debug(f"Starting auto-correction for '{normalized}' (length: {len(normalized)})")
            correction_result = await auto_correct_query(normalized)
            logger.debug(f"Auto-correction result: {correction_result}")
            return {
                "processed_query": correction_result.get("corrected_query", normalized),
                "original_query": query,
                "normalized_query": normalized,
                "correction_applied": correction_result.get("correction_applied", False),
                "correction_confidence": correction_result.get("confidence", 0.0),
                "correction_type": correction_result.get("correction_type"),
                "preprocessing_time": 0.0
            }
        elif enable_auto_correct:
            logger.debug(
                "Auto-correction bypassed for '%s' because it exceeds the fast-path guardrails",
                normalized,
            )
        else:
            logger.debug(
                "Auto-correction skipped: enabled=%s, token_count=%s, length=%s",
                enable_auto_correct,
                len(normalized.split()) if normalized else 0,
                len(normalized),
            )
        
        logger.debug("Query preprocessing: no correction applied | query=%r", normalized)
        return {
            "processed_query": normalized,
            "original_query": query,
            "normalized_query": normalized,
            "correction_applied": False,
            "correction_confidence": 0.0,
            "correction_type": None,
            "preprocessing_time": 0.0
        }
        
    except asyncio.TimeoutError:
        logger.warning(
            "Auto-correction timed out after %.2fs for query '%s'",
            BUSINESS_DICTIONARY_TIMEOUT_SECONDS,
            query,
        )
        return {
            "processed_query": normalize_text_for_matching(query),
            "original_query": query,
            "normalized_query": normalize_text_for_matching(query),
            "correction_applied": False,
            "correction_confidence": 0.0,
            "preprocessing_time": 0.0,
        }
    except Exception as e:
        logger.error(f"Query preprocessing failed for '{query}': {e}", exc_info=True)
        return {
            "processed_query": query,
            "original_query": query,
            "normalized_query": query,
            "correction_applied": False,
            "correction_confidence": 0.0,
            "correction_type": None,
            "preprocessing_time": 0.0,
            "error": str(e)
        }

@alru_cache(maxsize=1000)
async def cached_embed_query(query: str) -> List[float]:
    """Caches embedding generation (0ms for repeat queries)."""
    return await generate_embedding(query)

@alru_cache(maxsize=500)
async def cached_keyword_suggestions(query: str) -> List[str]:
    """Caches regex-heavy keyword suggestions."""
    if not query or len(query.strip()) < 1:
        return []
        
    query_text = query.strip().lower()
    starts_with_pattern = f"{query_text}%"
    sql = """
    WITH individual_words AS (
        SELECT unnest(regexp_matches(lower(businessname), '[a-z0-9]+', 'g')) as word
        FROM etown_providerservices_copy 
        WHERE businessname ILIKE $1
        """ + _EXCLUDE_REVIEW_PENDING_BARE + """
        """ + _EXCLUDE_DELETED_BARE + """

        UNION ALL
        SELECT unnest(regexp_matches(lower(businesscategory), '[a-z0-9]+', 'g')) as word
        FROM etown_providerservices_copy 
        WHERE businesscategory ILIKE $1
        """ + _EXCLUDE_REVIEW_PENDING_BARE + """
        """ + _EXCLUDE_DELETED_BARE + """

        UNION ALL
        SELECT unnest(regexp_matches(lower(servicecategory), '[a-z0-9]+', 'g')) as word
        FROM etown_providerservices_copy 
        WHERE servicecategory ILIKE $1
        """ + _EXCLUDE_REVIEW_PENDING_BARE + """
        """ + _EXCLUDE_DELETED_BARE + """

        UNION ALL
        SELECT unnest(regexp_matches(lower(services), '[a-z0-9]+', 'g')) as word
        FROM etown_providerservices_copy 
        WHERE services ILIKE $1
        """ + _EXCLUDE_REVIEW_PENDING_BARE + """
        """ + _EXCLUDE_DELETED_BARE + """

        UNION ALL
        SELECT unnest(regexp_matches(lower(servicedescription), '[a-z0-9]+', 'g')) as word
        FROM etown_providerservices_copy 
        WHERE servicedescription ILIKE $1
        """ + _EXCLUDE_REVIEW_PENDING_BARE + """
        """ + _EXCLUDE_DELETED_BARE + """

        UNION ALL
        SELECT unnest(regexp_matches(lower(description), '[a-z0-9]+', 'g')) as word
        FROM etown_providerservices_copy 
        WHERE description ILIKE $1
        """ + _EXCLUDE_REVIEW_PENDING_BARE + """
        """ + _EXCLUDE_DELETED_BARE + """
    )
    SELECT word as keyword
    FROM individual_words
    WHERE word LIKE $2
    AND length(word) > 2
    AND word NOT IN ('and', 'the', 'with', 'for', 'from')
    GROUP BY word
    ORDER BY count(*) DESC, length(word) ASC
    LIMIT 10
    """
    try:
        contain_pattern = f"%{query_text}%"
        rows = await asyncio.wait_for(
            Database.fetch(sql, contain_pattern, starts_with_pattern),
            timeout=KEYWORD_SUGGESTIONS_TIMEOUT_SECONDS,
        )
        return [row['keyword'] for row in rows]
    except asyncio.TimeoutError:
        logger.warning(
            "Keyword suggestion query timed out after %.2fs for '%s'",
            KEYWORD_SUGGESTIONS_TIMEOUT_SECONDS,
            query_text,
        )
        return []
    except Exception as e:
        logger.error(f"Error fetching keyword suggestions: {e}", exc_info=True)
        return []

async def get_fallback_search_keywords(original_query: str) -> List[str]:
    """Use fast database keyword generation instead of slow LLM."""
    logger.info(f"Generating fallback search keywords for query: '{original_query}'")
    try:
        normalized_query = normalize_text_for_matching(original_query)
        query_tokens = [
            token
            for token in normalized_query.split()
            if len(token) >= FALLBACK_KEYWORD_MIN_LENGTH
        ]

        suggestion_inputs = [normalized_query] + query_tokens if normalized_query else query_tokens
        suggestion_inputs = list(dict.fromkeys(inp for inp in suggestion_inputs if inp))
        suggestion_tasks = [cached_keyword_suggestions(inp) for inp in suggestion_inputs]
        suggestion_lists = await asyncio.wait_for(
            asyncio.gather(*suggestion_tasks, return_exceptions=True),
            timeout=KEYWORD_SUGGESTIONS_TIMEOUT_SECONDS,
        )

        merged_keywords: List[str] = []
        for suggestions in suggestion_lists:
            if isinstance(suggestions, Exception):
                continue
            for keyword in suggestions:
                normalized_keyword = normalize_text_for_matching(keyword)
                if (
                    normalized_keyword
                    and len(normalized_keyword) >= FALLBACK_KEYWORD_MIN_LENGTH
                    and normalized_keyword not in merged_keywords
                ):
                    merged_keywords.append(normalized_keyword)

        for token in query_tokens:
            if token not in merged_keywords:
                merged_keywords.append(token)

        if len(merged_keywords) > FALLBACK_MAX_KEYWORDS:
            merged_keywords = merged_keywords[:FALLBACK_MAX_KEYWORDS]

        return merged_keywords
             
    except asyncio.TimeoutError:
        logger.warning(
            "Fallback keyword generation timed out after %.2fs for query '%s'",
            KEYWORD_SUGGESTIONS_TIMEOUT_SECONDS,
            original_query,
        )
        return query_tokens[:FALLBACK_MAX_KEYWORDS]
    except Exception as e:
        logger.error(f"Error generating fallback search keywords: {e}", exc_info=True)
        return []


def _build_response_keywords(
    query: str,
    providers: List[Dict[str, Any]],
    areas: List[AreaSuggestion],
    cities: List[CityItem],
) -> List[str]:
    """Generate lightweight keywords from already-fetched response data."""
    normalized_query = normalize_text_for_matching(query)
    query_tokens = [
        token
        for token in _query_tokens_for_text_search(normalized_query)
        if len(token) >= FALLBACK_KEYWORD_MIN_LENGTH
    ]
    blocked_keywords = set(query_tokens)
    if normalized_query:
        blocked_keywords.add(normalized_query)

    keyword_candidates: List[str] = []

    for city in cities[:3]:
        city_name = normalize_text_for_matching(city.name or "")
        if city_name:
            keyword_candidates.append(city_name)

    for area in areas[:3]:
        area_name = normalize_text_for_matching(area.area_name or "")
        if area_name:
            keyword_candidates.append(area_name)

    provider_fields = (
        "Etown_businessName",
        "Etown_businessCategory",
        "Etown_services",
        "Etown_servicedescription",
        "Etown_description",
    )
    for provider in providers[:8]:
        for field_name in provider_fields:
            field_value = normalize_text_for_matching(provider.get(field_name) or "")
            if not field_value:
                continue

            for token in re.findall(r"[a-z0-9]+", field_value):
                if len(token) < FALLBACK_KEYWORD_MIN_LENGTH:
                    continue
                if token in TEXT_SEARCH_STOPWORDS or token in blocked_keywords:
                    continue
                if query_tokens and not any(token.startswith(query_token) for query_token in query_tokens):
                    continue
                keyword_candidates.append(token)

    cleaned_keywords: List[str] = []
    seen_keywords = set()
    for candidate in keyword_candidates:
        if not candidate or candidate in blocked_keywords or candidate in seen_keywords:
            continue
        seen_keywords.add(candidate)
        cleaned_keywords.append(candidate)
        if len(cleaned_keywords) >= FALLBACK_MAX_KEYWORDS:
            break

    return cleaned_keywords

def _process_location_metadata(
    cities: List[CityItem],
    areas: List[AreaSuggestion],
    keywords: List[str],
) -> Tuple[List[CityItem], List[AreaSuggestion], List[str]]:
    """Filter cities/areas and remove location duplicates from keywords."""

    def _normalize(value: Optional[str]) -> str:
        return (value or "").strip().lower()

    def _is_displayable_city(city: CityItem) -> bool:
        if not city or not _normalize(city.name):
            return False
        has_description = bool(_normalize(city.description))
        has_image = bool(_normalize(city.image))
        return has_description or has_image

    filtered_cities = [city for city in cities if _is_displayable_city(city)]

    city_names_lower = {
        _normalize(city.name)
        for city in filtered_cities
        if _normalize(city.name)
    }
    filtered_areas = [
        area
        for area in areas
        if area and _normalize(area.area_name) not in city_names_lower
    ]

    location_names = set(city_names_lower)
    location_names.update(
        _normalize(area.area_name)
        for area in filtered_areas
        if area and _normalize(area.area_name)
    )
    filtered_keywords = [
        keyword
        for keyword in (keywords or [])
        if _normalize(keyword) not in location_names
    ]

    return filtered_cities, filtered_areas, filtered_keywords

def extract_city_state(address):
    """Extract city and state code from address string."""
    if not address or not isinstance(address, str):
        return None, None

    state_abbreviations = {
        "Andhra Pradesh": "AP", "Arunachal Pradesh": "AR", "Assam": "AS", "Bihar": "BR",
        "Chhattisgarh": "CG", "Goa": "GA", "Gujarat": "GJ", "Haryana": "HR", "Himachal Pradesh": "HP",
        "Jharkhand": "JH", "Karnataka": "KA", "Kerala": "KL", "Madhya Pradesh": "MP", "Maharashtra": "MH",
        "Manipur": "MN", "Meghalaya": "ML", "Mizoram": "MZ", "Nagaland": "NL", "Odisha": "OR",
        "Punjab": "PB", "Rajasthan": "RJ", "Sikkim": "SK", "Tamil Nadu": "TN", "Telangana": "TS",
        "Tripura": "TR", "Uttar Pradesh": "UP", "Uttarakhand": "UK", "West Bengal": "WB",
        "Andaman and Nicobar Islands": "AN", "Chandigarh": "CH", "Dadra and Nagar Haveli and Daman and Diu": "DNHDD",
        "Delhi": "DL", "Jammu and Kashmir": "JK", "Ladakh": "LA", "Lakshadweep": "LD", "Puducherry": "PY"
    }

    parts = [part.strip() for part in address.split(',') if part.strip()]
    state = None
    city = None

    for i in range(len(parts)):
        part_cleaned = re.sub(r'\b\d{6}\b', '', parts[i]).strip()
        if part_cleaned in state_abbreviations:
            state = state_abbreviations[part_cleaned]
            if i > 0:
                city = parts[i - 1]
            break

    return city, state

async def enrich_profiles_with_rating_data(profiles: List[Dict[str, Any]], current_user_profile_id: str) -> List[Dict[str, Any]]:
    """Enriches provider profiles with booking/rating counts (Bulk)."""
    if not profiles or not current_user_profile_id:
        return profiles

    provider_profile_ids = set()
    for profile in profiles:
        is_provider = profile.get("Etown_role") == 1
        if is_provider:
            upid = profile.get('Etown_userProfileId') or profile.get('userprofileid')
            if upid:
                provider_profile_ids.add(upid.strip().lower())

    if not provider_profile_ids:
        return profiles

    provider_ids_list = list(provider_profile_ids)
    placeholders = ','.join([f"${i+1}" for i in range(len(provider_ids_list))])
    
    ratings_query = f"""
        SELECT
            LOWER(TRIM(provideruserprofileid)) as provideruserprofileid,
            SUM(memberbookingcount) as total_bookings,
            SUM(ratingcount) as total_ratings,
            AVG(overallrating) as avg_overall_rating
        FROM etown_ratings
        WHERE LOWER(TRIM(provideruserprofileid)) IN ({placeholders})
        GROUP BY LOWER(TRIM(provideruserprofileid));
    """
    
    try:
        rating_records = await Database.fetch(ratings_query, *provider_ids_list)
        ratings_map = {}
        for record in rating_records:
            ratings_map[record['provideruserprofileid']] = {
                "bookings": record.get('total_bookings', 0),
                "ratings": record.get('total_ratings', 0),
                "overall": record.get('avg_overall_rating', 0.0)
            }
            
        for profile in profiles:
            upid = profile.get('Etown_userProfileId') or profile.get('userprofileid')
            is_provider = profile.get("Etown_role") == 1
            
            if is_provider and upid:
                rating_data = ratings_map.get(upid.strip().lower())
                if rating_data:
                    profile["Etown_memberbookingcount"] = int(rating_data.get("bookings", 0))
                    profile["Etown_ratingcount"] = int(rating_data.get("ratings", 0))
                    profile["Etown_overallrating"] = float(rating_data.get("overall", 0.0))
                else:
                    profile["Etown_memberbookingcount"] = 0
                    profile["Etown_ratingcount"] = 0
                    profile["Etown_overallrating"] = 0.0
            else:
                profile["Etown_memberbookingcount"] = 0
                profile["Etown_ratingcount"] = 0
                profile["Etown_overallrating"] = 0.0
                
    except Exception as e:
        logger.error(f"Error fetching rating data: {e}", exc_info=True)
        
    return profiles

async def enrich_profiles_with_favourite_count(profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enriches provider profiles with favourite count (Bulk)."""
    if not profiles:
        return profiles

    provider_ids = set()
    for p in profiles:
        pid = p.get('Etown_providerid')
        if pid:
            provider_ids.add(str(pid))

    if not provider_ids:
        for p in profiles:
            p["Etown_favourite_count"] = 0
        return profiles

    provider_ids_list = list(provider_ids)
    query = """
        SELECT providerid, COUNT(*) as fav_count 
        FROM etown_collectionitems 
        WHERE providerid = ANY($1::text[]) 
          AND (isfavourite IS NULL OR LOWER(isfavourite) != 'unfavourite')
        GROUP BY providerid
    """
    
    try:
        rows = await Database.fetch(query, provider_ids_list)
        fav_map = {row['providerid']: int(row['fav_count']) for row in rows}
        
        for p in profiles:
            pid = p.get('Etown_providerid')
            if pid and str(pid) in fav_map:
                p["Etown_favourite_count"] = fav_map[str(pid)]
            else:
                p["Etown_favourite_count"] = 0
                
    except Exception as e:
        logger.error(f"Error fetching favourite counts: {e}", exc_info=True)
        for p in profiles:
            p["Etown_favourite_count"] = 0
            
    return profiles

async def mutual_connection_status(results_dict: Dict[str, List[Dict[str, Any]]], current_contact_id: str, current_user_profile_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Determines relationship status for profiles."""
    if not current_user_profile_id or not results_dict:
        return results_dict

    all_user_profile_ids = set()
    def _collect_ids(data):
        if isinstance(data, dict):
            upid = data.get('Etown_userProfileId') or data.get('Etown_userprofileid')
            if upid: all_user_profile_ids.add(upid)
            for v in data.values(): _collect_ids(v)
        elif isinstance(data, list):
            for item in data: _collect_ids(item)
    _collect_ids(results_dict)
    
    if not all_user_profile_ids:
        return results_dict
        
    target_ids = list(all_user_profile_ids)
    placeholders = ','.join([f"${i+2}" for i in range(len(target_ids))])
    
    rel_query = f"""
        SELECT 
            CASE WHEN from_userprofile = $1 THEN to_userprofile ELSE from_userprofile END as other_id,
            'member' as type,
            CAST(relationshiptype AS TEXT) as rel_val
        FROM etown_memberrelation 
        WHERE (from_userprofile = $1 AND to_userprofile IN ({placeholders}))
           OR (to_userprofile = $1 AND from_userprofile IN ({placeholders}))
        UNION
        SELECT 
            CASE WHEN fromuserprofileid = $1 THEN touserprofileid ELSE fromuserprofileid END as other_id,
            'provider' as type,
            relationshiptypename as rel_val
        FROM etown_providerrelation
        WHERE (fromuserprofileid = $1 AND touserprofileid IN ({placeholders}))
           OR (touserprofileid = $1 AND fromuserprofileid IN ({placeholders}))
    """
    
    inv_query = f"""
        SELECT 
            CASE WHEN inviteruserprofile = $1 THEN inviteuserprofile ELSE inviteruserprofile END as other_id,
            invitationcode
        FROM etown_invitation
        WHERE invitationcode = 'Requested'
          AND ((inviteruserprofile = $1 AND inviteuserprofile IN ({placeholders}))
            OR (inviteuserprofile = $1 AND inviteruserprofile IN ({placeholders})))
    """
    
    try:
        params = [current_user_profile_id] + target_ids
        rels_task = Database.fetch(rel_query, *params)
        invs_task = Database.fetch(inv_query, *params)
        rels, invs = await asyncio.gather(rels_task, invs_task)
        connection_map = {}
        
        for r in rels:
            oid = r['other_id']
            rtype = r['type']
            connection_map[oid] = {"status": "Connected", "role": rtype}
        for i in invs:
            oid = i['other_id']
            if oid not in connection_map:
                connection_map[oid] = {"status": "Requested", "role": None}
        
        def _inject(data):
            if isinstance(data, dict):
                upid = data.get('Etown_userProfileId') or data.get('Etown_userprofileid')
                info = connection_map.get(upid) if upid else None
                if 'Etown_contactId' in data or 'Etown_contactid' in data:
                    if info:
                        data['Etown_isconnected'] = (info['status'] == "Connected")
                        data['Etown_invitation'] = info['status']
                        data['Etown_status'] = info['role']
                    else:
                        data['Etown_isconnected'] = False
                        data['Etown_invitation'] = None
                        data['Etown_status'] = None
                for v in data.values(): _inject(v)
            elif isinstance(data, list):
                for item in data: _inject(item)
        _inject(results_dict)
    except Exception as e:
        logger.error(f"Error in mutual connection status: {e}", exc_info=True)
    return results_dict

def rerank_search_results(results: List[Dict[str, Any]], search_query: str) -> List[Dict[str, Any]]:
    """Rerank results based on generic relevance scoring - Distance > Semantic > Text."""
    if not results or not search_query:
        return results

    query_lower = search_query.lower().strip()
    query_normalized = normalize_text_for_matching(search_query)
    query_words = query_lower.split()
    query_token_set = set(_query_tokens_for_text_search(query_normalized))
    filtered_results = []

    for item in results:
        text_score = 0
        name = (item.get('Etown_businessName') or item.get('Etown_firstname') or '').lower()
        name_normalized = normalize_text_for_matching(name)
        email = (item.get('Etown_email') or '').lower()
        desc = (item.get('Etown_description') or '').lower()
        category = (item.get('Etown_businessCategory') or '').lower()
        services = (item.get('Etown_services') or '').lower()
        
        if name_normalized == query_normalized: 
            text_score += 100
        elif name == query_lower: 
            text_score += 100
        elif name_normalized.startswith(query_normalized): 
            text_score += 80
        elif name.startswith(query_lower): 
            text_score += 80
        elif query_normalized in name_normalized:
            text_score += 60
        elif query_lower in name: 
            text_score += 60
        
        for qw in query_words:
            if len(qw) < 2: continue
            if qw in name: text_score += 15
            if qw in services: text_score += 12
            if qw in desc: text_score += 8
            if qw in category: text_score += 10
            if email and qw in email: text_score += 5

        non_location_score = text_score

        city = (item.get('Etown_city') or '').lower()
        address = (item.get('Etown_address') or '').lower()
        for qw in query_words:
            if len(qw) < 2: continue
            if qw in city: text_score += 15
            if qw in address: text_score += 8

        name_tokens = set(name_normalized.split())
        service_tokens = set(normalize_text_for_matching(services).split())
        category_tokens = set(normalize_text_for_matching(category).split())

        if query_token_set:
            matching_name_tokens = query_token_set.intersection(name_tokens)
            if len(matching_name_tokens) == len(query_token_set):
                text_score += 95
            elif len(matching_name_tokens) >= max(2, len(query_token_set) - 1):
                text_score += 55

        matching_services = query_token_set.intersection(service_tokens) if query_token_set else set()
        matching_categories = query_token_set.intersection(category_tokens) if query_token_set else set()
        
        if matching_services:
            text_score += len(matching_services) * 20
        if matching_categories:
            text_score += len(matching_categories) * 15
                
        verification_score = 0
        if item.get('Etown_publicSearchProvider') == 'etown_verified':
            verification_score = 25
            
        is_multi_token = len(query_words) > 1
        if is_multi_token and non_location_score == 0:
            text_score = min(text_score, 20)

        total_text_score = text_score + verification_score
        item['_text_relevance'] = total_text_score

        similarity = item.get('Etown_similarity', -1.0)
        has_high_similarity = similarity >= MIN_SIMILARITY_THRESHOLD

        include_threshold = 20 if not is_multi_token else 40
        has_meaningful_text_match = total_text_score >= include_threshold
        has_strong_similarity = similarity >= 0.25

        if has_meaningful_text_match or has_strong_similarity:
             
            filtered_results.append(item)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "INCLUDED: %s - text_score: %s, similarity: %.3f",
                    name,
                    total_text_score,
                    similarity,
                )
        else:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "FILTERED OUT: %s - text_score: %s, similarity: %.3f",
                    name,
                    total_text_score,
                    similarity,
                )

    filtered_results.sort(key=lambda x: (x.get('_text_relevance', 0), x.get('Etown_similarity', -1)), reverse=True)
    
    if len(filtered_results) < 2:
        logger.info(f"Only {len(filtered_results)} results after strict filtering, checking fallback candidates...")
        fallback_results = []
        for item in results:
            if item not in filtered_results:
                total_score = item.get('_text_relevance', 0)
                similarity = item.get('Etown_similarity', -1.0)
                name = (item.get('Etown_businessName') or item.get('Etown_firstname') or '').lower()

                if total_score >= 60 or similarity >= 0.25:
                    fallback_results.append(item)
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(
                            "FALLBACK ADDED: %s - text_score: %s, similarity: %.3f",
                            name,
                            total_score,
                            similarity,
                        )
        
        fallback_results.sort(key=lambda x: (x.get('_text_relevance', 0), x.get('Etown_similarity', -1)), reverse=True)
        filtered_results.extend(fallback_results[:max(0, 3 - len(filtered_results))])
    else:
        logger.info(f"Strict filtering successful: {len(filtered_results)} relevant results found")
        
    return filtered_results

def format_provider_results(db_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Format provider DB rows."""
    formatted = []
    for row in db_rows:
        try:
            loc = row.get('location')
            if isinstance(loc, str): 
                try: loc = json.loads(loc)
                except Exception:
                    logger.debug("format_provider_results: loc column is not valid JSON; treating it as empty", exc_info=True)
                    loc = {}
            elif not loc: loc = {}
            
            contact = row.get('contact')
            if isinstance(contact, str): 
                try: contact = json.loads(contact)
                except Exception:
                    logger.debug("format_provider_results: contact column is not valid JSON; treating it as empty", exc_info=True)
                    contact = {}
            elif not contact: contact = {}
            
            items = row.get('item')
            if isinstance(items, str): 
                try: items = json.loads(items)
                except Exception:
                    logger.debug("format_provider_results: items column is not valid JSON; treating it as empty", exc_info=True)
                    items = []
            elif not items: items = []
            
            addr = row.get('address') or contact.get('address', '')
            city = row.get('city')
            state = row.get('statecode')
            if (not city or not state) and addr:
                ext_city, ext_state = extract_city_state(addr)
                if not city: city = ext_city
                if not state: state = ext_state
            desc = row.get('description', '') or row.get('servicedescription', '')
            cat = row.get('businesscategory') or ''
            
            if not desc and items:
                first = items[0] if isinstance(items, list) else {}
                if isinstance(first, dict):
                    desc = first.get('description', '') or first.get('desc', '')
            if not cat and items:
                 first = items[0] if isinstance(items, list) else {}
                 if isinstance(first, dict):
                      cat = first.get('category', '')
            
            provider_image = row.get('serviceimageurl') or row.get('bussiness_image')
            latitude_value = loc.get('lat') if isinstance(loc, dict) and loc.get('lat') is not None else row.get('latitude')
            longitude_value = loc.get('lon') if isinstance(loc, dict) and loc.get('lon') is not None else row.get('longitude')
            obj = {
                'Etown_providerid': row.get('providerid'), 
                'Etown_userProfileId': row.get('user_profile_id') or row.get('userprofileid'),
                'Etown_role': row.get('role', 1),
                'Etown_rolename': row.get('rolename', 'Provider'),
                'Etown_businessName': row.get('business_name') or row.get('businessname'),
                'Etown_businessCategory': cat,
                'Etown_services': row.get('services', ''),
                'Etown_contactid': row.get('contact_id') or row.get('contactid'),
                'Etown_firstname': row.get('firstname'),
                'Etown_ownername': row.get('contactidname'),
                'Etown_email': row.get('email'),
                'Etown_mobilenumber': row.get('mobilenumber') or row.get('mobilephone'),
                'Etown_profileimage': row.get('profileimage'),
                'Etown_servicedescription': row.get('servicedescription', ''),
                'Etown_serviceimageurl': provider_image,
                'Etown_address': addr,
                'Etown_city': city,
                'Etown_statecode': state,
                'Etown_areaname': row.get('areaname'),
                'Etown_latitude': str(latitude_value) if latitude_value is not None else None,
                'Etown_longitude': str(longitude_value) if longitude_value is not None else None,
                'Etown_publicSearchProvider': row.get('publicsearchprovider') or row.get('provider_type') or row.get('providertype'),
                'Etown_providertype': row.get('providertype'),
                'Etown_availability': row.get('availability'),
                'Etown_createdon': row.get('creationdate') or row.get('createdon'),
                'Etown_description': desc,
                'Etown_services': row.get('services', ''),
                'Etown_similarity': row.get('similarity_score', -1.0),
                'Etown_distance_km': None,
                'Etown_distance_meters': None,
                'Etown_memberbookingcount': int(row.get('memberbookingcount', 0) or 0),
                'Etown_ratingcount': int(row.get('ratingcount', 0) or 0),
                'Etown_overallrating': float(row.get('overallrating', 0.0) or 0.0),
                'Etown_status': row.get('status'),
                'Etown_invitation': row.get('invitation'),
                'Etown_isconnected': False,
                'Etown_mycircle_count': int(row.get('Etown_mycircle_count', 0) or 0),
                'Etown_favourite_count': int(row.get('favourite_count', 0) or 0),
                'Etown_resultType': 1,
                'Etown_source': row.get('source', 'database')
            }
            formatted.append(obj)
        except Exception as e:
            logger.error(f"Error formatting provider row: {e}", exc_info=True)
            continue
    return formatted

def format_member_results(db_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Format member DB rows."""
    formatted = []
    for row in db_rows:
        try:
            addr = row.get('address', '')
            db_city = row.get('city')
            db_state = row.get('statecode')
            
            if db_city and db_state:
                city = db_city
                state = db_state
            else:
                city, state = extract_city_state(addr)
            obj = {
                'Etown_userprofileid': row.get('userprofileid'),
                'Etown_role': 2,
                'Etown_rolename': 'Member',
                'Etown_firstname': row.get('firstname') or row.get('name'),
                'Etown_contactid': row.get('contactid'),
                'Etown_mobilenumber': row.get('mobilephone') or row.get('mobilenumber'),
                'Etown_email': row.get('email'),
                'Etown_profileimage': row.get('profileimage'),
                'Etown_createdon': row.get('createdon'),
                'Etown_address': addr,
                'Etown_statecode': state,
                'Etown_city': city,
                'Etown_resultType': 2,
                'providercurrent': [],     
                'mutualconnections': []    
            }
            formatted.append(obj)
        except Exception as e:
            logger.error(f"Error formatting member row: {e}", exc_info=True)
            continue
    return formatted

async def fetch_provider_profiles_by_contact_ids_bulk(contact_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch provider profiles by contact IDs (Bulk) with unique business names per contact."""
    if not contact_ids: return {}
    unique_ids = list(set(str(cid) for cid in contact_ids if cid))
    if not unique_ids: return {}
    
    query = """
        SELECT DISTINCT ON (ps.businessname, ps.city)
            ps.contactid,
            ps.providerid,
            ps.providertype,
            ps.publicsearchprovider,
            ps.serviceimageurl,
            ps.userprofileid,
            ps.latitude,
            ps.longitude,
            ps.businessname,
            ps.businesscategory,
            ps.services,
            ps.contactidname,
            ps.servicedescription,
            ps.mobilenumber,
            ps.address,
            ps.city,
            ps.statecode,
            ps.areaname,
            ps.availability,
            ps.createdon
        FROM etown_providerservices_copy ps
        WHERE ps.contactid = ANY($1::text[])
          AND NOT EXISTS (
                SELECT 1 FROM etown_provider dp
                 WHERE dp.disabled
                   AND dp.userprofileid = ps.userprofileid
              )
          """ + _EXCLUDE_REVIEW_PENDING_PS + """
          """ + _EXCLUDE_DELETED_PS + """
        ORDER BY ps.businessname, ps.city, ps.providerid DESC
    """
    try:
        rows = await Database.fetch(query, unique_ids)
        grouped = {}
        for r in rows:
            cid = str(r.get('contactid')).strip().lower()
            if cid not in grouped: grouped[cid] = []
            grouped[cid].append(dict(r))
        result_map = {}
        for cid, raw_rows in grouped.items():
            result_map[cid] = format_provider_results(raw_rows)
        return result_map
    except Exception as e:
        logger.error(f"Error in bulk provider fetch: {e}", exc_info=True)
        return {}

async def fetch_member_address_details_bulk(contact_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch member address details from additional tables when contact address is null."""
    if not contact_ids: return {}
    unique_ids = list(set(str(cid) for cid in contact_ids if cid))
    if not unique_ids: return {}

    try:
        provider_query = """
            SELECT contactid, address, city, statecode 
            FROM etown_provider 
            WHERE contactid = ANY($1::text[])
        """
        provider_rows = await Database.fetch(provider_query, unique_ids)
        
        result_map = {}
        for row in provider_rows:
            cid = str(row.get('contactid')).strip().lower()
            if cid:
                result_map[cid] = {
                    'address': row.get('address'),
                    'city': row.get('city'),
                    'statecode': row.get('statecode')
                }
        
        logger.debug(f"Fetched member address details for {len(result_map)} contacts from provider table")
        return result_map
        
    except Exception as e:
        logger.error(f"Error fetching member address details: {e}", exc_info=True)
        return {}

async def fetch_mutual_connections(current_contact_id: str, target_contact_id: str) -> List[Dict[str, Any]]:
    """Return up to five contacts connected to both users through member or provider relations, or [] on error."""
    try:
        query = """
            WITH CurrentUserConnections AS (
                SELECT to_contactid AS contact_id FROM etown_memberrelation WHERE from_contactid = $1
                UNION SELECT from_contactid FROM etown_memberrelation WHERE to_contactid = $1
                UNION SELECT tocontactid FROM etown_providerrelation WHERE fromcontactid = $1
                UNION SELECT fromcontactid FROM etown_providerrelation WHERE tocontactid = $1
            ),
            TargetUserConnections AS (
                SELECT to_contactid AS contact_id FROM etown_memberrelation WHERE from_contactid = $2
                UNION SELECT from_contactid FROM etown_memberrelation WHERE to_contactid = $2
                UNION SELECT tocontactid FROM etown_providerrelation WHERE fromcontactid = $2
                UNION SELECT fromcontactid FROM etown_providerrelation WHERE tocontactid = $2
            ),
            MutualContactIDs AS (
                SELECT contact_id FROM CurrentUserConnections
                INTERSECT SELECT contact_id FROM TargetUserConnections
            )
            SELECT DISTINCT ON (c.contactid)
                c.firstname AS "Etown_firstname",
                c.profileimage AS "Etown_profileimage",
                c.contactid AS "Etown_contactid",
                u.userprofileid AS "Etown_userprofileid"
            FROM etown_contact c
            JOIN etown_userprofile u ON c.contactid = u.contactid
            WHERE c.contactid IN (SELECT contact_id FROM MutualContactIDs)
            ORDER BY c.contactid, (CASE WHEN u.role = 2 THEN 0 ELSE 1 END)
            LIMIT 5;
        """
        rows = await Database.fetch(query, current_contact_id, target_contact_id)
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching mutual connections: {e}", exc_info=True)
        return []


async def bulk_fetch_mutual_connections(
    current_contact_id: str,
    target_contact_ids: List[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """Return up to five mutual connections per target contact in one query, keyed by target contact id."""
    if not target_contact_ids:
        return {}
    unique_targets = list({str(c) for c in target_contact_ids if c})
    if not unique_targets:
        return {}
    try:
        query = """
        WITH CurrentUserConns AS (
            SELECT to_contactid AS cid FROM etown_memberrelation WHERE from_contactid = $1
            UNION SELECT from_contactid FROM etown_memberrelation WHERE to_contactid = $1
            UNION SELECT tocontactid FROM etown_providerrelation WHERE fromcontactid = $1
            UNION SELECT fromcontactid FROM etown_providerrelation WHERE tocontactid = $1
        ),
        TargetConns AS (
            SELECT from_contactid AS tc, to_contactid AS cid
            FROM etown_memberrelation WHERE from_contactid = ANY($2::text[])
            UNION ALL
            SELECT to_contactid AS tc, from_contactid AS cid
            FROM etown_memberrelation WHERE to_contactid = ANY($2::text[])
            UNION ALL
            SELECT fromcontactid AS tc, tocontactid AS cid
            FROM etown_providerrelation WHERE fromcontactid = ANY($2::text[])
            UNION ALL
            SELECT tocontactid AS tc, fromcontactid AS cid
            FROM etown_providerrelation WHERE tocontactid = ANY($2::text[])
        ),
        MutualPairs AS (
            SELECT DISTINCT tc.tc AS target_contact_id, tc.cid AS mutual_contact_id
            FROM TargetConns tc
            JOIN CurrentUserConns cur ON tc.cid = cur.cid
        )
        SELECT DISTINCT ON (mp.target_contact_id, c.contactid)
            mp.target_contact_id,
            c.firstname AS "Etown_firstname",
            c.profileimage AS "Etown_profileimage",
            c.contactid AS "Etown_contactid",
            u.userprofileid AS "Etown_userprofileid"
        FROM MutualPairs mp
        JOIN etown_contact c ON c.contactid = mp.mutual_contact_id
        JOIN etown_userprofile u ON u.contactid = c.contactid
        ORDER BY mp.target_contact_id, c.contactid, (CASE WHEN u.role = 2 THEN 0 ELSE 1 END)
        """
        rows = await Database.fetch(query, current_contact_id, unique_targets)
        result_map: Dict[str, List[Dict[str, Any]]] = {tid: [] for tid in unique_targets}
        counts: Dict[str, int] = {tid: 0 for tid in unique_targets}
        for row in rows:
            tc = row['target_contact_id']
            if tc in result_map and counts.get(tc, 0) < 5:
                result_map[tc].append({
                    'Etown_firstname': row['Etown_firstname'],
                    'Etown_profileimage': row['Etown_profileimage'],
                    'Etown_contactid': row['Etown_contactid'],
                    'Etown_userprofileid': row['Etown_userprofileid'],
                })
                counts[tc] = counts.get(tc, 0) + 1
        return result_map
    except Exception as e:
        logger.error(f"Error in bulk_fetch_mutual_connections: {e}", exc_info=True)
        return {tid: [] for tid in unique_targets}

async def enrich_profiles_with_mycircle_count(profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Set Etown_mycircle_count on each profile from its member-relation count, defaulting to zero."""
    if not profiles: return profiles
    profile_ids = set()
    for p in profiles:
        upid = p.get('Etown_userProfileId') or p.get('Etown_userprofileid')
        if upid: profile_ids.add(upid.strip())
    if not profile_ids:
        for p in profiles: p['Etown_mycircle_count'] = 0
        return profiles

    ids_list = list(profile_ids)
    query = """
        SELECT target_id, count(*) as count
        FROM (
            SELECT from_userprofile AS target_id FROM etown_memberrelation WHERE from_userprofile = ANY($1::text[])
            UNION ALL
            SELECT to_userprofile AS target_id FROM etown_memberrelation WHERE to_userprofile = ANY($1::text[])
        ) sub
        GROUP BY target_id
    """
    try:
        rows = await Database.fetch(query, ids_list)
        count_map = {row['target_id']: int(row['count']) for row in rows}
        for p in profiles:
            upid = p.get('Etown_userProfileId') or p.get('Etown_userprofileid')
            if upid and upid.strip() in count_map: p['Etown_mycircle_count'] = count_map[upid.strip()]
            else: p['Etown_mycircle_count'] = 0
    except Exception as e:
        logger.error(f"Error enriching mycircle_count: {e}", exc_info=True)
        for p in profiles: p['Etown_mycircle_count'] = 0
    return profiles

async def phone_lookup_search(
    normalized_phone: str,
    contact_id: str,
    user_profile_id: str,
    limit: int = 5,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Look up a 10-digit number across contact, userprofile, member and provider tables, returning members and providers.

    Args:
        normalized_phone: 10-digit normalized phone number.
        contact_id: Current user's contact ID, for filtering and connections.
        user_profile_id: Current user's profile ID, for filtering and connections.
        limit: Maximum results to return per category.
        latitude: Optional caller latitude for provider distances.
        longitude: Optional caller longitude for provider distances.

    Returns:
        Dict with members and providers lists, plus error on timeout or failure.
    """
    try:
        lookup_query = """
            WITH phone_contacts AS MATERIALIZED (
                SELECT c.contactid
                  FROM etown_contact c
                 WHERE c.mobilephone IS NOT NULL
                   AND right(regexp_replace(COALESCE(c.mobilephone, ''), '\\D', '', 'g'), 10) = $1
                UNION
                SELECT up.contactid
                  FROM etown_userprofile up
                 WHERE up.mobilenumber IS NOT NULL
                   AND right(regexp_replace(COALESCE(up.mobilenumber, ''), '\\D', '', 'g'), 10) = $1
                UNION
                SELECT mb.contactid
                  FROM etown_member mb
                 WHERE mb.mobilenumber IS NOT NULL
                   AND right(regexp_replace(COALESCE(mb.mobilenumber, ''), '\\D', '', 'g'), 10) = $1
                UNION
                SELECT pv.contactid
                  FROM etown_provider pv
                 WHERE right(regexp_replace(pv.mobilenumber, '[^0-9]', '', 'g'), 10) = $1
                UNION
                SELECT c2.contactid
                  FROM etown_contact c2
                 WHERE c2.alternatemobile IS NOT NULL
                   AND right(regexp_replace(COALESCE(c2.alternatemobile, ''), '\\D', '', 'g'), 10) = $1
                UNION
                SELECT pv2.contactid
                  FROM etown_provider pv2
                 WHERE pv2.alternatemobile IS NOT NULL
                   AND right(regexp_replace(pv2.alternatemobile, '[^0-9]', '', 'g'), 10) = $1
            )
            SELECT
                c.contactid,
                c.mobilephone,
                c.firstname,
                c.email,
                c.profileimage,
                c.address,
                c.city,
                c.statecode,
                c.latitude,
                c.longitude,
                c.createdon,
                u.userprofileid,
                u.role,
                CASE
                    WHEN u.role = 1 THEN 'Provider'
                    WHEN u.role = 2 THEN 'Member'
                    ELSE 'Other'
                END as rolename,
                p.publicsearchprovider,
                p.providertype,
                p.businesscategory,
                p.contactidname,
                p.description,
                p.availability,
                p.areaname,
                p.serviceimageurl
            FROM etown_contact c
            JOIN etown_userprofile u ON c.contactid = u.contactid
            LEFT JOIN LATERAL (
                SELECT
                    p.publicsearchprovider,
                    p.providertype,
                    p.businesscategory,
                    p.contactidname,
                    p.description,
                    p.availability,
                    p.areaname,
                    p.serviceimageurl
                FROM etown_provider p
                WHERE u.role = 1
                  AND p.contactid = c.contactid
                  AND p.disabled IS NOT TRUE
                  AND p.deleted IS DISTINCT FROM 'true'
                ORDER BY (p.userprofileid = u.userprofileid) DESC NULLS LAST,
                         p.creationdate DESC NULLS LAST
                LIMIT 1
            ) p ON TRUE
            WHERE c.contactid IN (SELECT contactid FROM phone_contacts)
              AND c.contactid != $2
              AND u.role IN (1, 2)

              AND NOT etown_is_retired(c.contactid)

              AND NOT (u.role = 1 AND u.deleted = 'true')

              AND NOT EXISTS (
                    SELECT 1 FROM etown_provider dp
                     WHERE dp.disabled
                       AND dp.userprofileid = u.userprofileid
                  )

              """ + _EXCLUDE_REVIEW_PENDING_U + """
            ORDER BY u.role DESC, c.createdon DESC
            LIMIT $3;
"""
        
        phone_results = await asyncio.wait_for(
            Database.fetch(lookup_query, normalized_phone, contact_id, limit * 2),
            timeout=PHONE_LOOKUP_TIMEOUT_SECONDS
        )
        member_rows = []
        provider_rows = []
        
        for row in phone_results:
            if row['role'] == 2:
                member_rows.append(dict(row))
            elif row['role'] == 1:
                provider_rows.append(dict(row))
        provider_contact_ids = [row['contactid'] for row in provider_rows] if provider_rows else []
        member_contact_ids = [row['contactid'] for row in member_rows if not row.get('address') and not row.get('city')] if member_rows else []
        
        fetch_tasks = []
        if provider_contact_ids:
            fetch_tasks.append(fetch_provider_profiles_by_contact_ids_bulk(provider_contact_ids))
        if member_contact_ids:
            fetch_tasks.append(fetch_member_address_details_bulk(member_contact_ids))
            
        if fetch_tasks:
            results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            provider_details_map = results[0] if provider_contact_ids and not isinstance(results[0], Exception) else {}
            member_details_map = results[-1] if member_contact_ids and len(results) > 0 and not isinstance(results[-1], Exception) else {}
        else:
            provider_details_map = {}
            member_details_map = {}
        enriched_member_rows = []
        for row in member_rows:
            if member_details_map:
                contact_id_key = str(row['contactid']).strip().lower()
                if contact_id_key in member_details_map and not row.get('address'):
                    additional_data = member_details_map[contact_id_key]
                    row = dict(row)
                    row['address'] = additional_data.get('address') or row.get('address')
                    row['city'] = additional_data.get('city') or row.get('city')
                    row['statecode'] = additional_data.get('statecode') or row.get('statecode')
            enriched_member_rows.append(row)
        
        formatted_members = format_member_results(enriched_member_rows[:limit])
        formatted_providers = []
        seen_detail_contacts = set()
        seen_provider_keys = set()
        for row in provider_rows[:limit]:
            contact_id_key = str(row['contactid']).strip().lower()
            provider_details = provider_details_map.get(contact_id_key, [])
            
            if provider_details:
                if contact_id_key in seen_detail_contacts:
                    continue
                seen_detail_contacts.add(contact_id_key)
                profileimage = row.get('profileimage')
                mobilephone = row.get('mobilephone')
                for p in provider_details:
                    if not p.get('Etown_profileimage'):
                        p['Etown_profileimage'] = profileimage
                    if not p.get('Etown_mobilenumber'):
                        p['Etown_mobilenumber'] = mobilephone
                candidates = provider_details
            else:
                candidates = [{
                    'Etown_providerid': None,
                    'Etown_userProfileId': row['userprofileid'],
                    'Etown_role': 1,
                    'Etown_rolename': 'Provider',
                    'Etown_businessName': row['firstname'] or 'Unknown Business',
                    'Etown_businessCategory': row.get('businesscategory') or '',
                    'Etown_services': '',
                    'Etown_contactid': row['contactid'],
                    'Etown_firstname': row['firstname'],
                    'Etown_ownername': row.get('contactidname'),
                    'Etown_email': row['email'],
                    'Etown_mobilenumber': row['mobilephone'],
                    'Etown_profileimage': row['profileimage'],
                    'Etown_servicedescription': '',
                    'Etown_serviceimageurl': row.get('serviceimageurl'),
                    'Etown_address': row['address'],
                    'Etown_city': row['city'],
                    'Etown_statecode': row['statecode'],
                    'Etown_areaname': row.get('areaname'),
                    'Etown_providertype': row.get('providertype'),
                    'Etown_availability': row.get('availability'),
                    'Etown_latitude': str(row['latitude']) if row['latitude'] else None,
                    'Etown_longitude': str(row['longitude']) if row['longitude'] else None,
                    'Etown_createdon': row['createdon'],
                    'Etown_description': row.get('description'),
                    'Etown_similarity': 1.0,
                    'Etown_distance_km': None,
                    'Etown_distance_meters': None,
                    'Etown_resultType': 1,
                    'Etown_publicSearchProvider': row.get('publicsearchprovider'),
                    'Etown_memberbookingcount': 0,
                    'Etown_ratingcount': 0,
                    'Etown_overallrating': 0.0,
                    'Etown_mycircle_count': 0,
                    'Etown_favourite_count': 0,
                }]

            for provider in candidates:
                dedupe_key = provider.get('Etown_providerid') or (
                    f"{provider.get('Etown_userProfileId')}|{provider.get('Etown_businessName')}"
                )
                if dedupe_key in seen_provider_keys:
                    continue
                seen_provider_keys.add(dedupe_key)
                formatted_providers.append(provider)
        if latitude is not None and longitude is not None:
            _apply_distance_to_provider_results(formatted_providers, latitude, longitude)
        return {
            'members': formatted_members,
            'providers': formatted_providers
        }
        
    except asyncio.TimeoutError:
        logger.warning(
            "Phone lookup timed out after %.2fs for number '%s'",
            PHONE_LOOKUP_TIMEOUT_SECONDS,
            normalized_phone
        )
        return {'members': [], 'providers': [], 'error': 'timeout'}
    except Exception as e:
        logger.error(f"Phone lookup failed for number '{normalized_phone}': {e}", exc_info=True)
        return {'members': [], 'providers': [], 'error': 'lookup_failed'}


def _map_places_to_provider_rows(places: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map Google Places results into DB-row-shaped provider dicts, skipping market places and places outside India."""
    from app.agent_infrastructure.tools.tools_utils.web_search_utils import is_india_place

    rows: List[Dict[str, Any]] = []
    for p in places or []:
        primary_type = p.get('primaryType')
        if primary_type == 'market':
            logger.info("Skipping market provider: %s", (p.get('displayName') or {}).get('text'))
            continue

        if not p.get('nationalPhoneNumber'):
            logger.info("Skipping provider without phone number: %s", (p.get('displayName') or {}).get('text'))
            continue

        if not is_india_place(p):
            logger.info("Skipping non-India provider: %s", (p.get('displayName') or {}).get('text'))
            continue

        place_id = p.get('id')
        location = p.get('location') or {}

        editorial_summary = (p.get('editorialSummary') or {}).get('text')
        primary_type_display = (p.get('primaryTypeDisplayName') or {}).get('text')
        description = editorial_summary or primary_type_display

        rows.append({
            'providerid': str(uuid.uuid5(uuid.NAMESPACE_DNS, f"provider:{place_id}")) if place_id else None,
            'businessname': (p.get('displayName') or {}).get('text'),
            'businesscategory': primary_type,
            'description': description,
            'servicedescription': description,
            'address': p.get('formattedAddress'),
            'latitude': location.get('latitude'),
            'longitude': location.get('longitude'),
            'mobilenumber': p.get('nationalPhoneNumber'),
            'providertype': 'etown_nonverified',
            'publicsearchprovider': 'non-verified',
            'overallrating': p.get('rating'),
            'ratingcount': p.get('userRatingCount'),
            'source': 'public_web',
        })
    return rows


async def _augment_providers_with_web(
    final_providers: List[Dict[str, Any]],
    original_query: str,
    limit: int,
    contact_id: str,
    user_profile_id: str,
    user_name: str,
    latitude: Optional[float],
    longitude: Optional[float],
) -> List[Dict[str, Any]]:
    """Append deduplicated, persisted Google Places providers after local results, capping the combined list at limit."""
    from app.agent_infrastructure.tools.tools_utils.web_search_utils import (
        google_map_service,
        process_and_insert_web_provider,
    )

    payload: Dict[str, Any] = {
        "textQuery": original_query,
        "pageSize": limit,
        "locationBias": {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": AISEARCH_WEB_FALLBACK_RADIUS_METERS,
            }
        } if latitude is not None and longitude is not None else None,
    }

    map_response = await google_map_service.place_search_with_fallback(payload)
    if map_response.get("fallback_used"):
        logger.info(
            "AISearch web fallback active for query='%s' via %s (RapidAPI failure=%s)",
            original_query,
            map_response.get("effective_provider"),
            map_response.get("rapidapi_failure"),
        )
    places = map_response.get('places', []) or []
    if not places:
        return final_providers

    web_providers = format_provider_results(_map_places_to_provider_rows(places))
    _apply_distance_to_provider_results(web_providers, latitude, longitude)

    existing_names = {
        normalize_text_for_matching(p.get('Etown_businessName') or '')
        for p in final_providers
    }
    existing_names.discard('')

    new_web_providers: List[Dict[str, Any]] = []
    new_name_keys = set()
    seen_names = set(existing_names)
    for provider in web_providers:
        name_key = normalize_text_for_matching(provider.get('Etown_businessName') or '')
        if not name_key or name_key in seen_names:
            continue
        seen_names.add(name_key)
        new_name_keys.add(name_key)
        new_web_providers.append(provider)

    if not new_web_providers:
        return final_providers

    if contact_id and user_profile_id:
        insert_names: List[str] = []
        insert_coros = []
        for place in places:
            place_name_key = normalize_text_for_matching((place.get('displayName') or {}).get('text') or '')
            if place_name_key in new_name_keys:
                insert_names.append(place_name_key)
                insert_coros.append(
                    process_and_insert_web_provider(contact_id, user_profile_id, user_name or "User", place)
                )
        if insert_coros:
            outcomes = await asyncio.gather(*insert_coros, return_exceptions=True)
            unpersisted_names = {
                name for name, outcome in zip(insert_names, outcomes)
                if isinstance(outcome, Exception)
                or (isinstance(outcome, dict) and outcome.get("status") in ("no_city", "no_phone", "not_india"))
            }
            if unpersisted_names:
                before = len(new_web_providers)
                new_web_providers = [
                    p for p in new_web_providers
                    if normalize_text_for_matching(p.get('Etown_businessName') or '') not in unpersisted_names
                ]
                logger.info(
                    "AISearch web fallback dropped %d result(s) that could not be persisted "
                    "(no city, no phone, outside India, or insert error)", before - len(new_web_providers),
                )
            if not new_web_providers:
                return final_providers
            
            candidate_ids = [
                str(p['Etown_providerid']) for p in new_web_providers if p.get('Etown_providerid')
            ]
            if candidate_ids:
                try:
                    stored = {
                        str(r['providerid']) for r in await Database.fetch(
                            "SELECT providerid FROM etown_provider WHERE providerid = ANY($1::text[])",
                            candidate_ids,
                        )
                    }
                    before_missing = len(new_web_providers)
                    new_web_providers = [
                        p for p in new_web_providers
                        if not p.get('Etown_providerid') or str(p['Etown_providerid']) in stored
                    ]
                    if len(new_web_providers) != before_missing:
                        logger.info(
                            "AISearch web fallback dropped %d result(s) whose providerid resolves "
                            "to no row (already stored under another id)",
                            before_missing - len(new_web_providers),
                        )
                except Exception as _vex:
                    logger.warning("AISearch web fallback id verification failed: %s", _vex)
            if not new_web_providers:
                return final_providers

    logger.info(
        "AISearch web fallback added %d web provider(s) for query='%s' (had %d local)",
        len(new_web_providers), original_query, len(final_providers),
    )
    return (final_providers + new_web_providers)[:limit]


async def _apply_premium_badges(response: Dict[str, Any]) -> None:
    """Rewrite Etown_providertype on provider cards and members' providercurrent cards to the premium badge label, in place."""
    cards = list(response.get('provider') or [])
    for member in response.get('member') or []:
        cards.extend(member.get('providercurrent') or [])
    if not cards:
        return
    badge_map = await resolve_provider_badges(c.get('Etown_userProfileId') for c in cards)
    for card in cards:
        premium = (badge_map.get(card.get('Etown_userProfileId')) or {}).get('premium_label')
        card['Etown_providertype'] = premium_display(card.get('Etown_providertype'), premium)


async def aisearch(
    search_query: str,
    limit: int,
    offset: int,
    contact_id: str,
    user_profile_id: str,
    mycircle: bool,
    latitude: Optional[float],
    longitude: Optional[float],
    enable_auto_correct: bool = True,
    include_core: bool = True,
    include_meta: bool = True,
    include_enrichment: bool = True,
    resolve_missing_coordinates: bool = True,
) -> Dict[str, Any]:
    """Perform AI-powered search using vector similarity with fuzzy matching, auto-correction, and phone lookup."""
    t0 = time.time()
    
    performance_metrics = {
        "embedding_time": 0.0,
        "preprocessing_time": 0.0,
        "main_search_time": 0.0,
        "phone_lookup_time": 0.0, 
        "mytown_areas_time": 0.0,
        "mytown_cities_time": 0.0,
        "enrichment_time": 0.0,
        "total_time": 0.0,
        "mytown_timeout_occurred": False
    }
    
    logger.info(f"SEARCH START | Query: '{search_query}' | Limit: {limit} | Offset: {offset}")
    
    if include_core and is_phone_number_query(search_query):
        logger.info(f"PHONE LOOKUP DETECTED | Query: '{search_query}'")
        
        t_phone_start = time.time()
        normalized_phone = normalize_phone_number(search_query)
        
        if len(normalized_phone) >= MIN_PHONE_DIGITS:
            logger.info(f"PHONE LOOKUP | Normalized: '{normalized_phone}'")
            phone_results = await phone_lookup_search(
                normalized_phone, contact_id, user_profile_id, max(5, limit),
                latitude=latitude, longitude=longitude,
            )
            
            phone_error = phone_results.get('error')
            if phone_error:
                logger.warning(f"PHONE LOOKUP ERROR | Type: {phone_error} | Number: '{normalized_phone}'")
            members = phone_results.get('members', [])
            providers = phone_results.get('providers', [])
            t_enrich_start = time.time()
            try:
                for member in members:
                    member['Etown_isconnected'] = False
                    member['Etown_invitation'] = None
                    member['Etown_status'] = None
                    
                for provider in providers:
                    provider['Etown_isconnected'] = False
                    provider['Etown_invitation'] = None 
                    provider['Etown_status'] = None
                    
            except Exception as e:
                logger.error(f"Phone lookup status setting failed: {e}", exc_info=True)
            
            if providers:
                try:
                    await asyncio.gather(
                        enrich_profiles_with_rating_data(providers, user_profile_id),
                        enrich_profiles_with_favourite_count(providers),
                        enrich_profiles_with_mycircle_count(providers),
                        return_exceptions=True
                    )
                except Exception as e:
                    logger.error(f"Phone lookup enrichment failed: {e}", exc_info=True)

            performance_metrics["phone_lookup_time"] = time.time() - t_phone_start
            performance_metrics["enrichment_time"] = time.time() - t_enrich_start
            performance_metrics["total_time"] = time.time() - t0
            if phone_error:
                performance_metrics["phone_lookup_error"] = phone_error
            
            logger.info(
                f"PHONE LOOKUP COMPLETE | Members: {len(members)}, Providers: {len(providers)} | "
                f"Total: {performance_metrics['total_time']:.2f}s"
            )

            if members or providers:
                await _apply_premium_badges({"member": members, "provider": providers})

                return {
                    "member": members[:limit] if offset == 0 else members[offset:offset+limit],
                    "provider": providers[:limit] if offset == 0 else providers[offset:offset+limit],
                    "areas": [],
                    "cities": [],
                    "keywords": [],
                    "performance_metrics": performance_metrics,
                    "search_type": "phone_lookup",
                    "corrected_query": normalized_phone,
                    "original_query": search_query
                }

            logger.info(
                f"PHONE LOOKUP EMPTY | Number: '{normalized_phone}' | falling through to normal search"
            )
        else:
            logger.warning(f"PHONE LOOKUP SKIPPED | Invalid length: {len(normalized_phone)} digits")
    
    speculative_embed_task = None
    if include_core:
        speculative_embed_task = asyncio.create_task(
            asyncio.wait_for(cached_embed_query(search_query), timeout=EMBEDDING_TIMEOUT_SECONDS)
        )

    t_preprocess = time.time()
    try:
        preprocessing_result = await asyncio.wait_for(
            preprocess_search_query(search_query, enable_auto_correct),
            timeout=PREPROCESS_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning(f"Preprocessing timed out for query '{search_query}'; using raw query")
        preprocessing_result = {
            "processed_query": search_query,
            "original_query": search_query,
            "correction_applied": False,
            "correction_confidence": 0.0,
            "correction_type": None
        }
    
    processed_query = preprocessing_result["processed_query"]
    original_query = preprocessing_result["original_query"]
    correction_applied = preprocessing_result["correction_applied"]
    correction_confidence = preprocessing_result["correction_confidence"]
    correction_type = preprocessing_result.get("correction_type")
    performance_metrics["preprocessing_time"] = time.time() - t_preprocess
    
    if correction_applied:
        logger.info(
            "AUTO-CORRECTED: '%s' -> '%s' (confidence: %.1f%%, type: %s)",
            original_query,
            processed_query,
            correction_confidence,
            correction_type,
        )
    
    final_search_query = processed_query
    prefer_text_first = _should_prefer_text_first(final_search_query)

    if resolve_missing_coordinates and (latitude is None or longitude is None):
        inferred_coords = await _resolve_user_coordinates(contact_id, user_profile_id)
        if inferred_coords is not None:
            inferred_lat, inferred_lon = inferred_coords
            if latitude is None:
                latitude = inferred_lat
            if longitude is None:
                longitude = inferred_lon
            logger.info(
                "AI search inferred coordinates for distance ranking (lat=%.6f, lon=%.6f)",
                latitude,
                longitude,
            )
        else:
            logger.debug(
                "AI search distance not computed: request missing coordinates and no user location found"
            )

    t1 = time.time()
    embedding_string = None
    embedding_available = False
    if include_core:
        try:
            if correction_applied and speculative_embed_task:
                speculative_embed_task.cancel()
                embedding_vector = await asyncio.wait_for(
                    cached_embed_query(final_search_query),
                    timeout=EMBEDDING_TIMEOUT_SECONDS,
                )
            elif speculative_embed_task:
                embedding_vector = await asyncio.wait_for(
                    asyncio.shield(speculative_embed_task), timeout=EMBEDDING_TIMEOUT_SECONDS
                )
            else:
                embedding_vector = await asyncio.wait_for(
                    cached_embed_query(final_search_query),
                    timeout=EMBEDDING_TIMEOUT_SECONDS,
                )
            embedding_string = vector_to_postgres_string(embedding_vector)
            embedding_available = True
            performance_metrics["embedding_time"] = time.time() - t1
            logger.debug(f"Embedding generated in {performance_metrics['embedding_time']:.3f}s")
        except asyncio.TimeoutError:
            performance_metrics["embedding_time"] = time.time() - t1
            logger.warning(
                "Embedding timed out for query '%s' after %.2fs; using text-only search",
                final_search_query,
                EMBEDDING_TIMEOUT_SECONDS,
            )
        except Exception as e:
            performance_metrics["embedding_time"] = time.time() - t1
            logger.error(f"Embedding failed: {e}", exc_info=True)
    else:
        logger.debug("Skipping embedding generation for meta-only search '%s'", final_search_query)

    async def execute_search(
        query_text,
        embed_str,
        use_vector: bool = True,
        is_fallback: bool = False,
        include_members: bool = True,
    ):
        prov_query_vector = """
            WITH retired_ids AS MATERIALIZED (
                SELECT identity_id FROM etown_retired_identity
                 WHERE deleted = 'true'
                    OR (deactivated = 'true' AND reactivation IS DISTINCT FROM 'true')
            ),
            disabled_provider_ids AS MATERIALIZED (
                SELECT userprofileid::text AS identity_id
                  FROM etown_provider
                 WHERE disabled
            ),
            """ + _REVIEW_PENDING_CTE + """
            SELECT providerid, userprofileid,
                   businessname, businesscategory, services,
                   contactid, contactidname, email,
                   mobilenumber, servicedescription,
                   serviceimageurl, address, city, statecode,
                   areaname, latitude, longitude, publicsearchprovider,
                   providertype, availability, createdon,
                   NULL as description,
                   0.0 as similarity_score,
                   CASE
                       WHEN LOWER(TRIM(businessname)) = $6 THEN 100
                       WHEN LOWER(TRIM(businessname)) LIKE $5 THEN 50
                       ELSE 1
                   END as exact_match_boost,
                   CASE
                       WHEN LOWER(TRIM(COALESCE(publicsearchprovider, ''))) IN ('etown_verified', 'verified') THEN 100
                       ELSE 1
                   END as verification_priority
            FROM etown_providerservices_copy ps
            WHERE ps.contactid != $1::text

              AND NOT EXISTS (
                    SELECT 1 FROM retired_ids ri
                     WHERE ri.identity_id = ps.contactid
                        OR ri.identity_id = ps.userprofileid
                  )

              AND NOT EXISTS (
                    SELECT 1 FROM disabled_provider_ids dp
                     WHERE dp.identity_id = ps.userprofileid
                  )

              AND NOT EXISTS (
                    SELECT 1 FROM review_pending_ids rp
                     WHERE rp.providerid = ps.providerid
                  )
              AND ps.deleted IS DISTINCT FROM 'true'
              AND NOT EXISTS (SELECT 1 FROM etown_provider dl WHERE dl.providerid = ps.providerid AND dl.deleted = 'true')
            ORDER BY ps.content_vector <#> $2::vector ASC
            LIMIT $3 OFFSET $4
"""
        prov_query_text_only = """
                WITH retired_ids AS MATERIALIZED (
                    SELECT identity_id FROM etown_retired_identity
                     WHERE deleted = 'true'
                        OR (deactivated = 'true' AND reactivation IS DISTINCT FROM 'true')
                ),
                disabled_provider_ids AS MATERIALIZED (
                    SELECT userprofileid::text AS identity_id
                      FROM etown_provider
                     WHERE disabled
                ),
                """ + _REVIEW_PENDING_CTE + """
                SELECT
                p.providerid, p.userprofileid,
                p.businessname, p.businesscategory, p.services,
                p.contactid, p.contactidname, p.email,
                p.mobilenumber, p.servicedescription,
                p.serviceimageurl, p.address, p.city, p.statecode,
                p.areaname, p.latitude, p.longitude, p.publicsearchprovider,
                p.providertype, p.availability, p.createdon,
                NULL as description,
                0.0 as similarity_score,

                CASE
                    WHEN LOWER(TRIM(p.businessname)) = $6 THEN 100
                    WHEN LOWER(TRIM(p.businessname)) LIKE $4 THEN 50
                    ELSE 1
                END AS exact_match_boost,

                CASE
                    WHEN LOWER(TRIM(COALESCE(p.publicsearchprovider, '')))
                         IN ('etown_verified', 'verified') THEN 100
                    ELSE 1
                END AS verification_priority

            FROM etown_providerservices_copy p

            WHERE p.contactid != $1::text

              AND NOT EXISTS (
                    SELECT 1 FROM retired_ids ri
                     WHERE ri.identity_id = p.contactid
                        OR ri.identity_id = p.userprofileid
                  )

              AND NOT EXISTS (
                    SELECT 1 FROM disabled_provider_ids dp
                     WHERE dp.identity_id = p.userprofileid
                  )

              AND NOT EXISTS (
                    SELECT 1 FROM review_pending_ids rp
                     WHERE rp.providerid = p.providerid
                  )
              AND p.deleted IS DISTINCT FROM 'true'
              AND NOT EXISTS (SELECT 1 FROM etown_provider dl WHERE dl.providerid = p.providerid AND dl.deleted = 'true')
              AND (
                  LOWER(COALESCE(p.businessname, '')) LIKE $4 OR
                  LOWER(COALESCE(p.servicedescription, '')) LIKE $4 OR
                  LOWER(COALESCE(p.businesscategory, '')) LIKE $4 OR
                  LOWER(COALESCE(p.services, '')) LIKE $4 OR
                  LOWER(COALESCE(p.address, '')) LIKE $4 OR
                  LOWER(COALESCE(p.city, '')) LIKE $4 OR
                  LOWER(COALESCE(p.businessname, '')) LIKE ANY($5::text[]) OR
                  LOWER(COALESCE(p.servicedescription, '')) LIKE ANY($5::text[]) OR
                  LOWER(COALESCE(p.businesscategory, '')) LIKE ANY($5::text[]) OR
                  LOWER(COALESCE(p.services, '')) LIKE ANY($5::text[]) OR
                  LOWER(COALESCE(p.address, '')) LIKE ANY($5::text[]) OR
                  LOWER(COALESCE(p.city, '')) LIKE ANY($5::text[])
              )

            ORDER BY
                exact_match_boost DESC,
                verification_priority DESC,
                p.businessname

            LIMIT $2 OFFSET $3
"""

        async def _provider_search() -> List[Dict[str, Any]]:
            expanded_limit = min(limit * (2 if not is_fallback else 1), 24) if limit < 50 else min(limit, 50)
            normalized_query = normalize_text_for_matching(query_text)
            phrase_pattern = f"%{normalized_query}%"
            token_patterns = [
                f"%{token}%"
                for token in _query_tokens_for_text_search(normalized_query)[:TEXT_SEARCH_MAX_TOKEN_PATTERNS]
            ]
            if not token_patterns:
                token_patterns = [phrase_pattern]

            if use_vector and embed_str:
                vector_task = asyncio.wait_for(
                    Database.fetch(prov_query_vector, contact_id, embed_str, expanded_limit, offset, phrase_pattern, normalized_query),
                    timeout=AISEARCH_PROVIDER_VECTOR_TIMEOUT_SECONDS,
                )
                text_task = asyncio.wait_for(
                    Database.fetch(
                        prov_query_text_only, contact_id, expanded_limit, offset,
                        phrase_pattern, token_patterns, normalized_query,
                    ),
                    timeout=AISEARCH_PROVIDER_TEXT_TIMEOUT_SECONDS,
                )
                results = await asyncio.gather(vector_task, text_task, return_exceptions=True)

                vector_rows = results[0] if not isinstance(results[0], Exception) else []
                text_rows = results[1] if not isinstance(results[1], Exception) else []

                if isinstance(results[0], Exception):
                    logger.warning("Provider vector query failed for '%s': %s", query_text, results[0])
                if isinstance(results[1], Exception):
                    logger.warning("Provider text query failed for '%s': %s", query_text, results[1])

                merged_rows = list(text_rows) + list(vector_rows)
                merged_rows.sort(
                    key=lambda item: (
                        int(item.get("exact_match_boost", 0) or 0),
                        int(item.get("verification_priority", 0) or 0),
                    ),
                    reverse=True,
                )

                seen_ids = set()
                merged = []
                for r in merged_rows:
                    pid = r.get('providerid') or r.get('contactid')
                    if pid and pid not in seen_ids:
                        seen_ids.add(pid)
                        merged.append(r)
                logger.debug(
                    "Parallel provider search: %d vector + %d text → %d merged for '%s'",
                    len(vector_rows), len(text_rows), len(merged), query_text,
                )
                return merged[:expanded_limit]

            try:
                return await asyncio.wait_for(
                    Database.fetch(
                        prov_query_text_only,
                        contact_id,
                        expanded_limit,
                        offset,
                        phrase_pattern,
                        token_patterns,
                        normalized_query,
                    ),
                    timeout=AISEARCH_PROVIDER_TEXT_TIMEOUT_SECONDS,
                )
            except Exception as e:
                logger.warning(f"Provider text query failed for '{query_text}': {e}")
                return []

        async def _member_search() -> List[Dict[str, Any]]:
            if not include_members:
                return []
            normalized_query = normalize_text_for_matching(query_text)
            text_pattern = f"%{normalized_query}%"
            all_name_tokens = [
                t for t in normalized_query.split()
                if t and t not in TEXT_SEARCH_STOPWORDS
            ]
            member_tokens = all_name_tokens[:TEXT_SEARCH_MAX_TOKEN_PATTERNS] if all_name_tokens else []
            member_all_patterns = [f"%{token}%" for token in member_tokens] if member_tokens else [text_pattern]
            profile_search_ids = []
            try:
                return await asyncio.wait_for(
                    Database.fetch(
                        COMBINED_MEMBER_QUERY,
                        contact_id,
                        text_pattern,
                        limit,
                        offset,
                        member_all_patterns,
                        profile_search_ids,
                    ),
                    timeout=AISEARCH_MEMBER_QUERY_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Member query timed out after %.2fs for '%s'",
                    AISEARCH_MEMBER_QUERY_TIMEOUT_SECONDS,
                    query_text,
                )
                return []
            except Exception as e:
                logger.warning(f"Member query failed for '{query_text}': {e}")
                return []

        try:
            t_search_start = time.time()
            prov_task = _provider_search()
            mem_task = _member_search()
            results = await asyncio.gather(prov_task, mem_task, return_exceptions=True)
            p_rows = results[0] if not isinstance(results[0], Exception) else []
            m_rows = results[1] if not isinstance(results[1], Exception) else []
            logger.debug(
                "DB Search (%s) finished in %.3fs | Found: %s Prov, %s Mem",
                "Fallback" if is_fallback else "Main",
                time.time() - t_search_start,
                len(p_rows),
                len(m_rows),
            )
            return p_rows, m_rows
        except Exception as e:
            logger.error(f"Search execution failed: {e}", exc_info=True)
            return [], []

    clean_query = search_query.strip()
    corrected_clean_query = processed_query.strip()
    search_pattern_text = f"%{clean_query}%"
    corrected_search_pattern_text = f"%{corrected_clean_query}%"
    
    t_gather_start = time.time()
    
    async def timed_main_search():
        main_search_start = time.time()
        try:
            return await execute_search(
                final_search_query,
                embedding_string,
                use_vector=embedding_available,
            )
        finally:
            performance_metrics["main_search_time"] = time.time() - main_search_start

    task_main = timed_main_search() if include_core else asyncio.sleep(0, result=([], []))
    
    async def timeout_protected_mytown_areas():
        area_start = time.time()
        try:
            patterns = [search_pattern_text]
            if corrected_search_pattern_text != search_pattern_text:
                patterns.append(corrected_search_pattern_text)
            
            area_tasks = [
                asyncio.wait_for(
                    MyTownDatabase.search_mytown_areas(p, limit, offset),
                    timeout=MYTOWN_AREAS_TIMEOUT_SECONDS
                ) for p in patterns
            ]
            results = await asyncio.gather(*area_tasks, return_exceptions=True)
            
            seen = set()
            merged = []
            for res in results:
                if isinstance(res, Exception):
                    continue
                for row in res:
                    area_key = (row.get('areaname', ''), row.get('city', ''))
                    if area_key not in seen:
                        seen.add(area_key)
                        merged.append(row)
            
            performance_metrics["mytown_areas_time"] = time.time() - area_start
            return merged
        except asyncio.TimeoutError:
            performance_metrics["mytown_areas_time"] = time.time() - area_start
            performance_metrics["mytown_timeout_occurred"] = True
            logger.warning(f"MyTown area search timed out after {performance_metrics['mytown_areas_time']:.3f}s for query: {clean_query}")
            return []
        except Exception as e:
            performance_metrics["mytown_areas_time"] = time.time() - area_start
            logger.error(f"MyTown area search failed after {performance_metrics['mytown_areas_time']:.3f}s: {e}", exc_info=True)
            return []
    
    async def timeout_protected_mytown_cities():
        city_start = time.time()
        try:
            original_tokens = extract_city_tokens(clean_query)
            corrected_tokens = extract_city_tokens(corrected_clean_query) if corrected_clean_query != clean_query else []
            seen_tokens = set()
            city_tokens = []
            for t in original_tokens + corrected_tokens:
                if t not in seen_tokens:
                    seen_tokens.add(t)
                    city_tokens.append(t)
            
            if not city_tokens:
                performance_metrics["mytown_cities_time"] = time.time() - city_start
                return []
            
            result = await asyncio.wait_for(
                MyTownDatabase.search_mytown_cities(city_tokens, limit, offset),
                timeout=MYTOWN_CITIES_TIMEOUT_SECONDS
            )
            performance_metrics["mytown_cities_time"] = time.time() - city_start
            return result
        except asyncio.TimeoutError:
            performance_metrics["mytown_cities_time"] = time.time() - city_start
            performance_metrics["mytown_timeout_occurred"] = True
            logger.warning(f"MyTown city search timed out after {performance_metrics['mytown_cities_time']:.3f}s for corrected query: {corrected_clean_query}")
            return []
        except Exception as e:
            performance_metrics["mytown_cities_time"] = time.time() - city_start
            logger.error(f"MyTown city search failed after {performance_metrics['mytown_cities_time']:.3f}s: {e}", exc_info=True)
            return []
    
    run_mytown_lookup = include_meta and _should_run_mytown_lookup(corrected_clean_query)
    if run_mytown_lookup:
        task_areas = timeout_protected_mytown_areas()
        task_cities = timeout_protected_mytown_cities()
    else:
        if include_meta:
            logger.debug("Skipping MyTown lookups for non-location query '%s'", corrected_clean_query)
        task_areas = asyncio.sleep(0, result=[])
        task_cities = asyncio.sleep(0, result=[])
    
    keywords = []
    task_keywords = (
        get_fallback_search_keywords(corrected_clean_query)
        if offset == 0 and not include_core
        else asyncio.sleep(0, result=[])
    )
    results_all = await asyncio.gather(task_main, task_areas, task_cities, task_keywords, return_exceptions=True)
    if offset == 0:
        raw_keywords = results_all[3] if not isinstance(results_all[3], Exception) else []
        keywords = [kw for kw in raw_keywords if kw.lower() not in [clean_query.lower(), corrected_clean_query.lower()]]
        
    gather_duration = time.time() - t_gather_start
    logger.debug(f"Primary Gather took {gather_duration:.3f}s | Areas: {performance_metrics['mytown_areas_time']:.3f}s | Cities: {performance_metrics['mytown_cities_time']:.3f}s")

    main_res = results_all[0]
    area_rows = results_all[1] if not isinstance(results_all[1], Exception) else []
    city_rows = results_all[2] if not isinstance(results_all[2], Exception) else []    
    prov_results, mem_results = [], []
    if not isinstance(main_res, Exception):
        prov_results, mem_results = main_res

    if include_core and len(prov_results) == 0 and logger.isEnabledFor(logging.DEBUG):
        try:
            check_q = "SELECT count(*) as cnt FROM etown_providerservices_copy WHERE content_vector IS NULL"
            check_res = await Database.fetchrow(check_q)
            if check_res and check_res['cnt'] > 0:
                logger.warning(f"SEARCH ALERT: Found {check_res['cnt']} rows with NULL vectors in etown_providerservices_copy. This is why search returns 0 results.")
                logger.warning("ACTION REQUIRED: Run 'python scripts/fix_provider_embeddings.py' to populate embeddings.")
        except Exception as e:
            logger.error(f"Diagnostic check failed: {e}", exc_info=True)

    t_format_start = time.time()
    provider_data = format_provider_results([dict(r) for r in prov_results])
    member_data = format_member_results([dict(r) for r in mem_results])
    performance_metrics["format_time"] = time.time() - t_format_start

    if ENABLE_FALLBACK_SEARCH and (len(provider_data) + len(member_data) < FALLBACK_SEARCH_THRESHOLD) and offset == 0:
        elapsed_before_fallback = time.time() - t0
        fallback_time_budget = max(0.5, 2.0 - elapsed_before_fallback)
        fallback_timeout = min(FALLBACK_EXECUTION_TIMEOUT_SECONDS, fallback_time_budget)
        logger.info(
            "Low results, triggering fallback search (elapsed=%.3fs, timeout=%.2fs)",
            elapsed_before_fallback,
            fallback_timeout,
        )
        fallback_keywords = await get_fallback_search_keywords(final_search_query)
        if not fallback_keywords:
            normalized_query = normalize_text_for_matching(final_search_query)
            token_fallback = [
                token
                for token in normalized_query.split()
                if len(token) >= FALLBACK_KEYWORD_MIN_LENGTH
            ]
            if token_fallback:
                fallback_keywords = token_fallback[:1]
            elif len(normalized_query) >= FALLBACK_KEYWORD_MIN_LENGTH:
                fallback_keywords = [normalized_query]
        
        if fallback_keywords:
            fallback_keywords = fallback_keywords[:2]
            embed_tasks = []
            valid_kw = []
            for kw in fallback_keywords:
                embed_tasks.append(asyncio.wait_for(cached_embed_query(kw), timeout=EMBEDDING_TIMEOUT_SECONDS))
                valid_kw.append(kw)
            
            if embed_tasks:
                try:
                    embeddings_results = await asyncio.gather(*embed_tasks, return_exceptions=True)
                    search_tasks = []
                    for i, res in enumerate(embeddings_results):
                        if isinstance(res, Exception):
                            search_tasks.append(
                                execute_search(
                                    valid_kw[i],
                                    None,
                                    use_vector=False,
                                    is_fallback=True,
                                    include_members=False,
                                )
                            )
                            continue
                        kw_embed_str = vector_to_postgres_string(res)
                        search_tasks.append(
                            execute_search(
                                valid_kw[i],
                                kw_embed_str,
                                use_vector=True,
                                is_fallback=True,
                                include_members=False,
                            )
                        )
                    
                    if search_tasks:
                        fallback_results = await asyncio.wait_for(
                            asyncio.gather(*search_tasks, return_exceptions=True),
                            timeout=fallback_timeout,
                        )
                        for res in fallback_results:
                            if isinstance(res, tuple):
                                p_rows, m_rows = res
                                provider_data.extend(format_provider_results([dict(r) for r in p_rows]))
                                member_data.extend(format_member_results([dict(r) for r in m_rows]))
                except asyncio.TimeoutError:
                    logger.warning(
                        "Fallback search timed out after %.2fs for query '%s'",
                        FALLBACK_EXECUTION_TIMEOUT_SECONDS,
                        search_query,
                    )
                except Exception as e:
                    logger.error(f"Error in fallback parallel execution: {e}", exc_info=True)

    t_dedupe_start = time.time()
    unique_providers = {}
    for p in provider_data:
        upid = p.get('Etown_userProfileId')
        if upid and upid not in unique_providers: unique_providers[upid] = p
    provider_data = list(unique_providers.values())

    unique_members = {}
    for m in member_data:
        upid = m.get('Etown_userprofileid')
        if upid and upid not in unique_members: unique_members[upid] = m
    member_data = list(unique_members.values())
    performance_metrics["dedupe_time"] = time.time() - t_dedupe_start

    t_enrich_start = time.time() if include_enrichment else None
    
    _apply_distance_to_provider_results(provider_data, latitude, longitude)

    t_rerank_start = time.time()
    raw_provider_candidates = list(provider_data)
    provider_data = rerank_search_results(provider_data, search_query)
    if not provider_data and raw_provider_candidates:
        recovery_count = max(2, min(limit, 5))
        conservative_recovery = [
            row
            for row in raw_provider_candidates
            if row.get("_text_relevance", 0) >= 50 or row.get("Etown_similarity", -1.0) >= 0.25
        ]
        if conservative_recovery:
            logger.warning(
                "Rerank removed all %s provider candidates for '%s'; restoring %s conservative candidates",
                len(raw_provider_candidates),
                search_query,
                min(recovery_count, len(conservative_recovery)),
            )
            conservative_recovery.sort(
                key=lambda x: (x.get('_text_relevance', 0), x.get('Etown_similarity', -1.0)),
                reverse=True,
            )
            provider_data = conservative_recovery[:recovery_count]
        else:
            logger.info(
                "Rerank removed all %s provider candidates for '%s'; not restoring low-relevance rows",
                len(raw_provider_candidates),
                search_query,
            )
    
    performance_metrics["rerank_time"] = time.time() - t_rerank_start
    logger.info(f"After relevance filtering: {len(provider_data)} providers for query '{search_query}' (rerank took {performance_metrics['rerank_time']:.3f}s)")

    gated_provider_data = [
        row
        for row in provider_data
        if row.get("_text_relevance", 0) >= 50 or row.get("Etown_similarity", -1.0) >= 0.25
    ]
    if len(gated_provider_data) != len(provider_data):
        logger.warning(
            "Provider hard-gate removed %s weak rows for query '%s'",
            len(provider_data) - len(gated_provider_data),
            search_query,
        )
        provider_data = gated_provider_data

    if provider_data and _has_exact_member_name_match(member_data, final_search_query):
        filtered_for_member_intent = [
            row
            for row in provider_data
            if (
                normalize_text_for_matching(row.get("Etown_businessName") or "") == normalize_text_for_matching(final_search_query)
                or row.get("_text_relevance", 0) >= 100
            )
        ]
        if len(filtered_for_member_intent) != len(provider_data):
            logger.info(
                "Exact member-name match detected for '%s'; removed %s weak provider rows",
                search_query,
                len(provider_data) - len(filtered_for_member_intent),
            )
            provider_data = filtered_for_member_intent
    
    def provider_sort_key(p):
        """Sort by Distance > Verified > Semantic > Text priority (for RELEVANT results only)."""
        dist = p.get('Etown_distance_km')
        similarity = p.get('Etown_similarity', 0)
        text_relevance = p.get('_text_relevance', 0)
        
        if dist is not None:
            distance_priority = 0 if dist <= 10 else (1 if dist <= 50 else (2 if dist <= 100 else 3))
            distance_score = dist
        else:
            distance_priority = 4
            distance_score = 9999
        
        is_not_verified = 0 if p.get('Etown_publicSearchProvider') == 'etown_verified' else 1
        
        semantic_score = -similarity
        
        text_score = -text_relevance
        
        return (distance_priority, is_not_verified, distance_score, semantic_score, text_score)
    
    if latitude is not None and longitude is not None:
        provider_data.sort(key=provider_sort_key)
    else:
        def no_location_sort_key(p):
            similarity = p.get('Etown_similarity', 0)
            text_relevance = p.get('_text_relevance', 0)
            is_not_verified = 0 if p.get('Etown_publicSearchProvider') == 'etown_verified' else 1
            
            if text_relevance >= 100:
                priority = 0
                primary_score = -text_relevance
                secondary_score = -similarity
            else:
                priority = 1
                primary_score = -similarity
                secondary_score = -text_relevance
            
            return (priority, is_not_verified, primary_score, secondary_score)
        
        provider_data.sort(key=no_location_sort_key)

    async def emergency_provider_fallback() -> List[Dict[str, Any]]:
        """Last-resort provider fallback to avoid empty API responses."""
        normalized_query = normalize_text_for_matching(final_search_query or search_query)
        raw_tokens = [token for token in _query_tokens_for_text_search(normalized_query) if len(token) >= 3]
        unique_tokens = list(dict.fromkeys(raw_tokens))
        token_patterns = [f"%{token}%" for token in unique_tokens[:5]]
        phrase_pattern = f"%{normalized_query}%" if normalized_query else None
        if phrase_pattern and phrase_pattern not in token_patterns:
            token_patterns.insert(0, phrase_pattern)

        targeted_query = """
            SELECT ps.*, 0.0 as similarity_score
            FROM etown_providerservices_copy ps
            WHERE ps.contactid != $1::text
              AND NOT EXISTS (
                    SELECT 1 FROM etown_provider dp
                     WHERE dp.disabled
                       AND dp.userprofileid = ps.userprofileid
                  )
              """ + _EXCLUDE_REVIEW_PENDING_PS + """
              """ + _EXCLUDE_DELETED_PS + """
              AND (
                  LOWER(COALESCE(ps.businessname, '')) LIKE ANY($2::text[]) OR
                  LOWER(COALESCE(ps.servicedescription, '')) LIKE ANY($2::text[]) OR
                  LOWER(COALESCE(ps.businesscategory, '')) LIKE ANY($2::text[]) OR
                  LOWER(COALESCE(ps.services, '')) LIKE ANY($2::text[]) OR
                  LOWER(COALESCE(ps.address, '')) LIKE ANY($2::text[]) OR
                  LOWER(COALESCE(ps.city, '')) LIKE ANY($2::text[])
              )
            LIMIT $3
        """

        if token_patterns:
            try:
                targeted_rows = await asyncio.wait_for(
                    Database.fetch(targeted_query, contact_id, token_patterns, max(5, min(limit, 12))),
                    timeout=AISEARCH_EMERGENCY_QUERY_TIMEOUT_SECONDS,
                )
                if targeted_rows:
                    return format_provider_results([dict(r) for r in targeted_rows])
            except Exception as e:
                logger.warning(f"Emergency targeted fallback failed for '{search_query}': {e}")

        provider_table_targeted_query = """
            SELECT
                p.providerid,
                p.userprofileid AS user_profile_id,
                p.userprofileid,
                1 AS role,
                'Provider' AS rolename,
                p.businessname AS business_name,
                p.businessname,
                NULL::text AS businesscategory,
                NULL::text AS services,
                p.contactid AS contact_id,
                p.contactid,
                NULL::text AS firstname,
                p.userprofileidname AS contactidname,
                p.email,
                p.mobilenumber,
                p.mobilenumber AS mobilephone,
                NULL::text AS profileimage,
                p.description AS servicedescription,
                p.serviceimageurl,
                NULL::text AS bussiness_image,
                p.address,
                p.city,
                p.statecode,
                p.areaname,
                NULL::text AS location,
                p.latitude,
                p.longitude,
                p.publicsearchprovider,
                p.providertype AS provider_type,
                p.providertype,
                NULL::text AS availability,
                p.creationdate,
                p.creationdate AS createdon,
                p.description,
                NULL::text AS contact,
                NULL::jsonb AS item,
                0.0 AS similarity_score
            FROM public.etown_provider p
            WHERE TRIM(LOWER(COALESCE(p.contactid, ''))) != $1::text
              AND p.disabled IS NOT TRUE
              AND p.deleted IS DISTINCT FROM 'true'
              """ + _EXCLUDE_REVIEW_PENDING_P + """
              AND (
                  LOWER(COALESCE(p.businessname, '')) LIKE ANY($2::text[]) OR
                  LOWER(COALESCE(p.description, '')) LIKE ANY($2::text[]) OR
                  LOWER(COALESCE(p.city, '')) LIKE ANY($2::text[]) OR
                  LOWER(COALESCE(p.areaname, '')) LIKE ANY($2::text[])
              )
            ORDER BY p.creationdate DESC
            LIMIT $3
        """

        if token_patterns:
            try:
                provider_targeted_rows = await asyncio.wait_for(
                    Database.fetch(
                        provider_table_targeted_query,
                        str(contact_id or "").strip().lower(),
                        token_patterns,
                        max(5, min(limit, 12)),
                    ),
                    timeout=AISEARCH_EMERGENCY_QUERY_TIMEOUT_SECONDS,
                )
                if provider_targeted_rows:
                    return format_provider_results([dict(r) for r in provider_targeted_rows])
            except Exception as e:
                logger.warning(f"Emergency provider-table targeted fallback failed for '{search_query}': {e}")

        return []

    if include_core and not provider_data and not member_data and offset == 0:
        if area_rows or city_rows:
            logger.info(
                "Skipping emergency provider fallback for '%s' because location suggestions are available",
                search_query,
            )
        else:
            emergency_providers = await emergency_provider_fallback()
            if emergency_providers:
                ranked_emergency = rerank_search_results(emergency_providers, search_query)
                if ranked_emergency:
                    provider_data = ranked_emergency
                else:
                    query_tokens = search_query.strip().lower().split()
                    if len(query_tokens) == 1:
                        provider_data = emergency_providers
                    else:
                        provider_data = []
                if provider_data:
                    logger.warning(
                        "Emergency fallback activated for '%s'; returning %s providers (after reranking)",
                        search_query,
                        len(provider_data),
                    )
                    _apply_distance_to_provider_results(provider_data, latitude, longitude)
                    if latitude is not None and longitude is not None:
                        provider_data.sort(key=provider_sort_key)
        
    final_providers = provider_data[:limit]
    final_members = member_data[:limit] 

    all_member_contact_ids = [m.get('Etown_contactid') for m in final_members if m.get('Etown_contactid')]
    
    if include_enrichment and all_member_contact_ids:
        mutual_conn_map_task = bulk_fetch_mutual_connections(contact_id, all_member_contact_ids) if mycircle else asyncio.sleep(0, result={})
        bulk_providers_map, mutual_conn_map = await asyncio.gather(
            fetch_provider_profiles_by_contact_ids_bulk(all_member_contact_ids),
            mutual_conn_map_task
        )
    else:
        bulk_providers_map = {}
        mutual_conn_map = {}

    all_nested_providers = []
    for member in final_members:
        cid = str(member.get('Etown_contactid') or '').strip().lower()
        providers_fetched = bulk_providers_map.get(cid, [])
        member['providercurrent'] = providers_fetched
        if providers_fetched:
            all_nested_providers.extend(providers_fetched)

        member['mutualconnections'] = mutual_conn_map.get(member.get('Etown_contactid', ''), [])

    if include_enrichment and final_providers:
        all_profiles_to_enrich = final_providers
        
        enrich_tasks = [
            enrich_profiles_with_rating_data(all_profiles_to_enrich, user_profile_id),
        ]
        
        if mycircle:
            enrich_tasks.append(enrich_profiles_with_mycircle_count(all_profiles_to_enrich))
        
        if enrich_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*enrich_tasks),
                    timeout=AISEARCH_ENRICH_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Enrichment timed out after %.2fs for query '%s'; returning results without enrichment",
                    AISEARCH_ENRICH_TIMEOUT_SECONDS,
                    search_query,
                )
            except Exception as e:
                logger.warning(f"Enrichment failed for query '{search_query}': {e}")

    if t_enrich_start is not None:
        performance_metrics["enrichment_time"] = time.time() - t_enrich_start
        logger.debug(f"Enrichment took {performance_metrics['enrichment_time']:.3f}s")

    if (
        ENABLE_AISEARCH_WEB_SEARCH
        and ENABLE_AISEARCH_WEB_FALLBACK
        and include_core
        and offset == 0
        and not mycircle
        and not is_phone_number_query(search_query)
        and len(final_providers) < AISEARCH_WEB_FALLBACK_THRESHOLD
    ):
        logger.info(
            "Insufficient local providers (%d < %d) for '%s' — triggering web search",
            len(final_providers), AISEARCH_WEB_FALLBACK_THRESHOLD, search_query,
        )
        try:
            final_providers = await _augment_providers_with_web(
                final_providers=final_providers,
                original_query=original_query,
                limit=limit,
                contact_id=contact_id,
                user_profile_id=user_profile_id,
                user_name="User",
                latitude=latitude,
                longitude=longitude,
            )
        except Exception as e:
            logger.warning("AISearch web fallback failed for '%s': %s", search_query, e)

    formatted_areas = []
    formatted_cities = []
    if include_meta:
        for row in area_rows:
            formatted_areas.append(AreaSuggestion(
                area_name=row.get('areaname'),
                latitude=str(row.get('latitude')) if row.get('latitude') else None,
                longitude=str(row.get('longitude')) if row.get('longitude') else None,
                provider_count=row.get('provider_count', 0),
                verified_count=row.get('verified_count', 0),
                unverified_count=row.get('unverified_count', 0),
                city_name=row.get('city'),
                state_code=row.get('statecode')
            ))
        
        temp_cities = []
        for row in city_rows:
            city_image = row.get('townimage')
            city_description = row.get('towndescription')
            
            dist = None
            if latitude is not None and longitude is not None:
                c_lat = row.get('latitude')
                c_lon = row.get('longitude')
                if c_lat and c_lon:
                    try: 
                        dist = round(haversine_km(latitude, longitude, float(c_lat), float(c_lon)), 2)
                    except Exception: 
                        logger.debug("aisearch: city distance could not be computed; leaving it unset", exc_info=True)
                        dist = None

            temp_cities.append(CityItem(
                name=row.get('city') or "",
                distance_km=dist,
                image=city_image or "",
                provider_count=int(row.get('provider_count', 0) or 0),
                verified_count=int(row.get('verified_count', 0) or 0),
                unverified_count=int(row.get('unverified_count', 0) or 0),
                latitude=str(row.get('latitude')).strip() if row.get('latitude') else None,
                longitude=str(row.get('longitude')).strip() if row.get('longitude') else None,
                description=city_description or "",
                favourite_count=int(row.get('favourite_count', 0) or 0)
            ))
        
        city_dict = {}
        for city in temp_cities:
            city_name_lower = city.name.lower() if city.name else ""
            if city_name_lower not in city_dict or city.provider_count > city_dict[city_name_lower].provider_count:
                city_dict[city_name_lower] = city
        
        formatted_cities = list(city_dict.values())
        
        is_proximity_search = detect_proximity_search(search_query)
        if latitude is not None and longitude is not None:
            if is_proximity_search:
                filtered_cities = []
                for city in formatted_cities:
                    if city.distance_km is not None and city.distance_km <= 150.0:
                        filtered_cities.append(city)
                formatted_cities = filtered_cities
                formatted_cities.sort(key=lambda c: c.distance_km if c.distance_km is not None else float('inf'))
            else:
                def city_sort_key(city):
                    dist = city.distance_km if city.distance_km is not None else 1000.0
                    provider_count = city.provider_count or 0
                    if dist <= 50:
                        return (0, dist)
                    if dist <= 150:
                        return (1, -provider_count, dist)
                    return (2, -provider_count, dist)
                
                formatted_cities.sort(key=city_sort_key)
        else:
            formatted_cities.sort(key=lambda c: -(c.provider_count or 0))
        formatted_cities, formatted_areas, _ = _process_location_metadata(
            formatted_cities,
            formatted_areas,
            [],
        )

    if not keywords and offset == 0:
        keywords = _build_response_keywords(
            corrected_clean_query,
            final_providers,
            formatted_areas,
            formatted_cities,
        )

    has_meaningful_results = len(final_members) > 0 or len(final_providers) > 0

    if not has_meaningful_results:
        logger.info(f"No meaningful member/provider results found for query: '{search_query}'")

    _, _, filtered_keywords = _process_location_metadata(
        formatted_cities,
        formatted_areas,
        keywords,
    )

    final_response = {
        "member": final_members,
        "provider": final_providers,
        "areas": formatted_areas,
        "cities": formatted_cities,
        "keywords": filtered_keywords
    }
    
    if include_enrichment and mycircle and has_meaningful_results:
        final_response = await mutual_connection_status(final_response, contact_id, user_profile_id)
        final_response['member'] = [m for m in final_response['member'] if m.get('Etown_isconnected')]
        final_response['provider'] = [p for p in final_response['provider'] if p.get('Etown_isconnected')]
    elif include_enrichment and not mycircle and has_meaningful_results and (final_members or final_providers):
        try:
            final_response = await asyncio.wait_for(
                mutual_connection_status(final_response, contact_id, user_profile_id),
                timeout=AISEARCH_CONNECTION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.debug("Skipping connection-status enrichment - too slow for non-mycircle search")

    await _apply_premium_badges(final_response)

    performance_metrics["total_time"] = time.time() - t0
    
    final_response["original_query"] = original_query
    final_response["correction_applied"] = correction_applied
    final_response["correction_confidence"] = correction_confidence
    final_response["performance_metrics"] = performance_metrics
    
    if has_meaningful_results:
        correction_info = f" | Corrected: {correction_applied}" if correction_applied else ""
        timing_detail = (
            f" | Format: {performance_metrics.get('format_time', 0):.3f}s"
            f" | Dedupe: {performance_metrics.get('dedupe_time', 0):.3f}s"
            f" | Rerank: {performance_metrics.get('rerank_time', 0):.3f}s"
            f" | Enrich: {performance_metrics.get('enrichment_time', 0):.3f}s"
        )
        logger.info(f"SEARCH COMPLETE | Total Time: {performance_metrics['total_time']:.3f}s | Providers: {len(final_providers)} | Members: {len(final_members)} | Areas: {len(formatted_areas)}{correction_info}{timing_detail}")
    else:
        correction_info = f" | Corrected: {correction_applied}" if correction_applied else ""
        logger.info(f"SEARCH COMPLETE - NO RESULTS | Total Time: {performance_metrics['total_time']:.3f}s | Query: '{final_search_query}'{correction_info}")
    return final_response


async def enrich_by_ids(
    member_ids: List[str],
    provider_ids: List[str],
    contact_id: str,
    user_profile_id: str,
    mycircle: bool = False,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> Dict[str, Any]:
    """Fetch member and provider profiles by id and apply the search enrichment steps without running a search.

    Args:
        member_ids: List of member userprofileid values to enrich.
        provider_ids: List of provider userprofileid values to enrich.
        contact_id: Current user's contact ID.
        user_profile_id: Current user's profile ID.
        mycircle: Whether to filter by mutual connections.
        latitude: Optional user latitude for distance calculation.
        longitude: Optional user longitude for distance calculation.

    Returns:
        Search-shaped response dict; areas, cities and keywords are always empty.
    """
    t0 = time.time()
    performance_metrics: Dict[str, Any] = {
        "enrichment_time": 0.0,
        "fetch_time": 0.0,
        "total_time": 0.0,
    }

    logger.info(
        "ENRICH_BY_IDS START | Members: %s | Providers: %s",
        len(member_ids),
        len(provider_ids),
    )

    t_fetch = time.time()

    async def _fetch_members() -> List[Dict[str, Any]]:
        if not member_ids:
            return []
        
        normalized_ids = [mid.strip().lower() for mid in member_ids if mid and mid.strip()]
        if not normalized_ids:
            return []
            
        try:
            rows = await asyncio.wait_for(
                Database.fetch(
                    COMBINED_MEMBER_QUERY,
                    "",
                    "",
                    50,
                    0,
                    [],
                    normalized_ids,
                ), 
                timeout=AISEARCH_MEMBER_QUERY_TIMEOUT_SECONDS,
            )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("enrich_by_ids batch member fetch failed: %s", e)
            return []

    async def _fetch_providers() -> List[Dict[str, Any]]:
        if not provider_ids:
            return []
        normalized_ids = [pid.strip().lower() for pid in provider_ids if pid and pid.strip()]
        if not normalized_ids:
            return []
        query = """
            SELECT providerid, userprofileid,
                   businessname, businesscategory, services,
                   contactid, contactidname, email,
                   mobilenumber, servicedescription,
                   serviceimageurl, address, city, statecode,
                   areaname, latitude, longitude, publicsearchprovider,
                   providertype, availability, createdon,
                   description,
                   0.0 as similarity_score
            FROM etown_providerservices_copy ps
            WHERE TRIM(LOWER(ps.userprofileid)) = ANY($1::text[])
              AND NOT EXISTS (
                    SELECT 1 FROM etown_provider dp
                     WHERE dp.disabled
                       AND dp.userprofileid = ps.userprofileid
                  )
              """ + _EXCLUDE_REVIEW_PENDING_PS + """
              """ + _EXCLUDE_DELETED_PS + """
        """
        try:
            rows = await asyncio.wait_for(
                Database.fetch(query, normalized_ids),
                timeout=AISEARCH_PROVIDER_TEXT_TIMEOUT_SECONDS,
            )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("enrich_by_ids provider fetch failed: %s", e)
            return []

    raw_members, raw_providers = await asyncio.gather(
        _fetch_members(), _fetch_providers()
    )
    performance_metrics["fetch_time"] = time.time() - t_fetch

    provider_data = format_provider_results(raw_providers)
    member_data = format_member_results(raw_members)

    unique_providers: Dict[str, Dict] = {}
    for p in provider_data:
        upid = p.get("Etown_userProfileId")
        if upid and upid not in unique_providers:
            unique_providers[upid] = p
    provider_data = list(unique_providers.values())

    unique_members: Dict[str, Dict] = {}
    for m in member_data:
        upid = m.get("Etown_userprofileid")
        if upid and upid not in unique_members:
            unique_members[upid] = m
    member_data = list(unique_members.values())

    _apply_distance_to_provider_results(provider_data, latitude, longitude)

    t_enrich = time.time()

    all_member_contact_ids = [
        m.get("Etown_contactid") for m in member_data if m.get("Etown_contactid")
    ]

    if all_member_contact_ids:
        mutual_conn_map_task = (
            bulk_fetch_mutual_connections(contact_id, all_member_contact_ids)
            if mycircle
            else asyncio.sleep(0, result={})
        )
        bulk_providers_map, mutual_conn_map = await asyncio.gather(
            fetch_provider_profiles_by_contact_ids_bulk(all_member_contact_ids),
            mutual_conn_map_task,
        )
    else:
        bulk_providers_map = {}
        mutual_conn_map = {}

    for member in member_data:
        cid = str(member.get("Etown_contactid") or "").strip().lower()
        member["providercurrent"] = bulk_providers_map.get(cid, [])
        member["mutualconnections"] = mutual_conn_map.get(
            member.get("Etown_contactid", ""), []
        )

    if provider_data:
        enrich_tasks = [
            enrich_profiles_with_rating_data(provider_data, user_profile_id),
        ]
        if mycircle:
            enrich_tasks.append(enrich_profiles_with_mycircle_count(provider_data))
        try:
            await asyncio.wait_for(
                asyncio.gather(*enrich_tasks),
                timeout=AISEARCH_ENRICH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("enrich_by_ids enrichment timed out")
        except Exception as e:
            logger.warning("enrich_by_ids enrichment failed: %s", e)

    performance_metrics["enrichment_time"] = time.time() - t_enrich

    final_response: Dict[str, Any] = {
        "member": member_data,
        "provider": provider_data,
        "areas": [],
        "cities": [],
        "keywords": [],
    }

    if mycircle and (member_data or provider_data):
        final_response = await mutual_connection_status(
            final_response, contact_id, user_profile_id
        )
        final_response["member"] = [
            m for m in final_response["member"] if m.get("Etown_isconnected")
        ]
        final_response["provider"] = [
            p for p in final_response["provider"] if p.get("Etown_isconnected")
        ]
    elif member_data or provider_data:
        try:
            final_response = await asyncio.wait_for(
                mutual_connection_status(
                    final_response, contact_id, user_profile_id
                ),
                timeout=AISEARCH_CONNECTION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.debug("enrich_by_ids connection-status timed out; skipping")

    await _apply_premium_badges(final_response)

    performance_metrics["total_time"] = time.time() - t0

    final_response["original_query"] = ""
    final_response["correction_applied"] = False
    final_response["correction_confidence"] = 0.0
    final_response["performance_metrics"] = performance_metrics

    logger.info(
        "ENRICH_BY_IDS COMPLETE | Total: %.3fs | Fetch: %.3fs | Enrich: %.3fs | Members: %s | Providers: %s",
        performance_metrics["total_time"],
        performance_metrics["fetch_time"],
        performance_metrics["enrichment_time"],
        len(final_response["member"]),
        len(final_response["provider"]),
    )
    return final_response

