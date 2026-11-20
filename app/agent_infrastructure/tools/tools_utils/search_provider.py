"""Search Provider Tool - Chaatie provider search with hybrid lexical and vector ranking and web fallback.

Member-agent tool that parses the query, expands it into cross-script and service-intent variants, searches each concurrently through a lexical/category arm and a vector arm, then merges, filters and ranks visible providers by relevance and distance. It falls back to Google Places when local results are thin.

Features:
    - Lexical/Category Retrieval: normalized_category plus full-text match is the primary signal
    - Vector Similarity Search: Embedding-based semantic search with PGVector integration
    - Multilingual Support: Search term translation for Tamil, Hindi, and English
    - Geographic Filtering: Location-based distance calculations with coordinate validation
    - Intelligent Fallbacks: Multi-stage search strategy with term generation and web search
    - Rating Integration: Provider rating and review data enrichment
    - Result Filtering: Relevance filtering and quality validation
    - Performance Optimization: Bounding box optimization for geographic queries
    - Context Management: User context integration for personalized search
    - Web Search Integration: Google Maps fallback for comprehensive coverage
    - Error Recovery: Robust error handling with graceful degradation
    - Cross-script Query Variants: Romanized Tamil and Tamil-script variant expansion without external dependencies
    - Service Intent Resolution: problems are resolved to the trades that serve them and searched
    - Query Parsing: explicit location, distance limit, landmark, nearest and timeframe requests
    - Typo Correction: conservative one-character fixes against known categories; known trade words are kept
    - Visibility Gating: disabled and review-pending providers are excluded from every arm
    - Plan Badges: results carry plan-derived badge labels and Etown_providertype
    - Concurrent Variant Search: All query variants are searched in parallel via asyncio.gather
    - Embedding Reuse: each variant text is embedded once per search; failed embeddings are not cached
    - Result-Derived Messaging: The reply describes what was actually found, not what was asked

Classes:
    No classes defined (module uses function-based utilities)

Functions:
    is_provider_verified(): Standardized verification status checking for provider types
    handle_search_provider(): Main search handler - lexical + vector retrieval, radius fallbacks, web search
    _bounding_box(): Lat/lon box that fully contains a radius, used to pre-filter candidates
    _escape_like(): Escapes LIKE metacharacters in user-supplied patterns
    _extract_explicit_location(): Extracts a city requested with in/near/at/around
    _extract_explicit_distance_km(): Extracts an explicit distance limit such as "within 2 km"
    _requests_closest_provider(): True when the user asks for the nearest result
    _requests_date_and_time(): True when the request includes an appointment timeframe
    _requests_weekend_choice(): True when the user asks for a this/next weekend appointment
    _extract_nearby_landmark(): Extracts a landmark reference to verify near the user
    _correct_search_typos(): Corrects conservative one-character typos against known categories, sparing trade words
    _lexical_terms(): Builds the lexical arm's term list from the query and resolved intent
    _fetch_provider_subservices(): Fetches a provider's subservices from etown_providerservices
    _enrich_results_with_subservices(): Attaches each database result's subservices, concurrently
    _parse_jsonb(): Decodes a jsonb column value that asyncpg hands back as a JSON string
    _enrich_results_with_provider_details(): Attaches Google Places details to each result by providerid
    _humanize_place_types(): Drops Google's umbrella place types and title-cases the rest
    _link_web_results_to_db_providers(): Re-attaches Etown identity to Places hits we already store
    _build_search_query_variants(): Builds cross-script and resolved-trade query variants
    _score_web_results_for_ranking(): Gives Places rows the trade/item ranking signals of DB rows
    _dominant_result_label(): Finds the most common word across result labels for the reply
    _select_best_candidate(): Picks the better-ranked of two duplicate provider candidates
    _rating_sort_key(): Sort key ranking higher-rated providers first, unrated ones last
    distance_sort_key(): Nearest-first sort key on distance_km, missing or invalid distances last

Dependencies:
    - asyncio: Concurrent retrieval arms, subservice/details enrichment, and web-insert execution
    - math / uuid / collections.Counter: bounding-box geometry, provider ids, label voting
    - difflib.SequenceMatcher: typo correction against known categories
    - web_search_utils.PLACES_TYPE_TO_CATEGORY / search_helpers._stem: trade words exempt from typo correction
    - geopy: Geographic distance calculations and coordinate validation
    - app.etown.plans.plans_utils: premium_display and resolve_provider_badges for badges
    - app.etown.provider.provider_review_utils: review_pending_exclusion SQL fragment
    - app.db.client: Database operations with retry mechanisms
    - app.utils.embeddings: Vector embedding generation and similarity search
    - app.utils.translation_utils: Multilingual search term processing
    - app.agent_infrastructure.tools.context_manager: User context management
    - search_helpers: Result enrichment, relevance filtering and text-normalisation utilities
    - service_intent: resolve_service_intent() trade/category resolution
    - web_search_utils: Google Places search, India gating, and provider insertion
    - search_notifications: Background WhatsApp nudge for unverified providers in results
    - app.logs.enhanced_logging: db_query_monitor decorator on handle_search_provider

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description
    01-Apr-2026: Standardized is_provider_verified() checks; hybrid ranking with business-name and verification boosts.
    07-Apr-2026: Added query variants and cross-script filtering for romanized Tamil input.
    25-Jun-2026: Integrated search_notifications to WhatsApp-nudge unverified providers in results.
    10-Aug-2026: Fixed irrelevant results - SQL bbox pre-filter, lexical arm, working radius cap.
    14-Aug-2026: Google Places results outside India are skipped before surfacing or inserting.
    20-Aug-2026: Badge label on verified markers; per-term suppression; Etown_providertype in results.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    02-Sep-2026: Providers with etown_provider.disabled are excluded from every arm and the web linker.
    07-Sep-2026: Trade and distance outrank provenance; dropped 100 km allowance; intent-keyed expansion ladder.
    08-Sep-2026: Exact matches first; stricter distance limits; relevance-based web-search fallback and quotas.
    09-Sep-2026: Bounded proximity for relevance; stricter cross-trade matching; density-scaled proximity bonus.
    17-Sep-2026: Providers awaiting review are held out of every search arm.
    06-Oct-2026: Retrieval arms run without retry; a failed radius-expansion query stops expansion, not the search.
    14-Oct-2026: Typo correction no longer rewrites known trade words such as painting; query embeddings are reused within one search.
    27-Oct-2026: Radius extension skips already-shown providers before the cut; Tamil-script terms are translated whatever the app language.
"""

import json
import logging
import asyncio
import math
import re
import uuid
from difflib import SequenceMatcher
from collections import Counter
from typing import Dict, Any, List, Optional, Tuple
from geopy.distance import geodesic
from app.core.logging import logger
from app.db.client import Database
from app.etown.plans.plans_utils import premium_display, resolve_provider_badges
from app.etown.provider.provider_review_utils import review_pending_exclusion
from app.utils.embeddings import generate_embedding, vector_to_postgres_string
from app.agent_infrastructure.tools.tools_utils.web_search_utils import (
    google_map_service,
    places_category,
    PLACES_TYPE_TO_CATEGORY,
    STATE_CODE_MAP,
)
from app.agent_infrastructure.tools.tools_utils.search_helpers import (
    enrich_profiles_with_rating_data,
    filter_irrelevant_results,
    filter_positive_reviews,
    llm_batch_relevance_filter,
    normalize_search_text,
    map_romanized_tamil_terms,
    contains_tamil_script,
    validate_search_context,
    generate_fallback_terms,
    shown_ids_for_search,
    SEARCH_STOPWORDS,
    _item_affinity_band,
    _satisfied_trade,
    _stem,
)
from app.agent_infrastructure.tools.tools_utils.service_intent import ServiceIntent, resolve_service_intent
from app.agent_infrastructure.tools.tools_utils.service_intent import CANONICAL_CATEGORIES
from app.utils.translation_utils import SearchTermTranslator
from app.agent_infrastructure.tools.context_manager import get_execution_context
from app.agent_infrastructure.tools.tools_utils.web_search_utils import (
    google_map_service,
    is_india_place,
    process_and_insert_web_provider)
from app.logs.enhanced_logging import db_query_monitor
from app.agent_infrastructure.tools.tools_utils.search_notifications import notify_unverified_providers_background

_background_tasks = set()
MAX_SEARCH_VARIANTS = 8

_KM_PER_DEGREE = 111.0
SERVICE_MAX_DISTANCE_KM = 100.0
VERIFIED_DISTANCE_MULTIPLIER = 1.5
LOCAL_REACH_CEILING_KM = 25.0 * VERIFIED_DISTANCE_MULTIPLIER
AFFINITY_PROXIMITY_BONUS_KM = 8.0
MAX_AFFINITY_PROXIMITY_BONUS_KM = 25.0
MIN_LOCAL_RESULTS_BEFORE_WEB_SEARCH = 5
EXTENDED_SEARCH_RADIUS_KM = 500.0
MAX_LEXICAL_TERMS = 12

_GENERIC_PLACE_TYPES = {
    "point_of_interest", "establishment", "store", "service",
    "premise", "geocode", "political",
}


def _bounding_box(
    latitude: float, longitude: float, radius_km: float
) -> Tuple[float, float, float, float]:
    """Return clamped (min_lat, max_lat, min_lon, max_lon) bounds of a box covering a radius around a point."""
    lat_delta = radius_km / _KM_PER_DEGREE
    cos_lat = max(math.cos(math.radians(latitude)), 0.01)
    lon_delta = radius_km / (_KM_PER_DEGREE * cos_lat)
    return (
        max(latitude - lat_delta, -90.0),
        min(latitude + lat_delta, 90.0),
        max(longitude - lon_delta, -180.0),
        min(longitude + lon_delta, 180.0),
    )


def _escape_like(text: str) -> str:
    """Escape LIKE metacharacters so a query's own '%'/'_' cannot act as wildcards."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _extract_explicit_location(search_term: str) -> Optional[str]:
    """Extract a city explicitly requested with ``in``, ``near``, ``at`` or ``around``."""
    match = re.search(
        r"\b(?:in|near|at|around)\s+(?!me\b|here\b|my\s+(?:location|place|area)\b)"
        r"([A-Za-z][A-Za-z .'-]{1,60}?)(?=\s+(?:for|who|that|with|and|under)\b|[,?.!]|$)",
        search_term or "",
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def _extract_explicit_distance_km(search_term: str) -> Optional[float]:
    """Extract an explicit distance limit such as ``within 2 km`` or ``under 1 mile``."""
    match = re.search(
        r"\b(?:within|under|below|less\s+than|up\s+to|max(?:imum)?\s+of)\s+"
        r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>km|kilometers?|m|meters?|mi|miles?)\b",
        search_term or "",
        re.IGNORECASE,
    )
    if not match:
        return None
    value = float(match.group("value"))
    unit = match.group("unit").lower()
    return value / 1000 if unit in {"m", "meter", "meters"} else value * 1.60934 if unit in {"mi", "mile", "miles"} else value


def _requests_closest_provider(search_term: str) -> bool:
    """Return whether the user asks for the nearest result, rather than general options."""
    return bool(re.search(r"\b(?:closest|nearest)\b", search_term or "", re.IGNORECASE))


def _requests_date_and_time(search_term: str) -> bool:
    """Return whether the request includes a relative or explicit appointment timeframe."""
    return bool(re.search(
        r"\b(?:today|tonight|tomorrow|this\s+weekend|next\s+weekend|this\s+week|next\s+week|"
        r"(?:mon|tues?|wed|thurs?|fri|sat|sun)(?:day)?|\d{1,2}[/-]\d{1,2})\b",
        search_term or "",
        re.IGNORECASE,
    ))


def _requests_weekend_choice(search_term: str) -> bool:
    """Return whether the user asks for an appointment this or next weekend."""
    return bool(re.search(r"\b(?:this|next)\s+weekend\b", search_term or "", re.IGNORECASE))


def _named_day(search_term: str) -> Optional[str]:
    """Extract a relative, weekday, or explicit date from the search term."""
    text = search_term or ""
    relative = re.search(r"\b(today|tonight|tomorrow)\b", text, re.IGNORECASE)
    if relative:
        return relative.group(1).lower()
    weekday = re.search(
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text, re.IGNORECASE
    )
    if weekday:
        return f"on {weekday.group(1).capitalize()}"
    explicit = re.search(r"\b(\d{1,2}[/-]\d{1,2})\b", text)
    if explicit:
        return f"on {explicit.group(1)}"
    return None


def _extract_nearby_landmark(search_term: str) -> Optional[str]:
    """Extract a supported landmark reference that must be verified near the user."""
    match = re.search(
        r"\bnear\s+(?:the\s+)?(?P<landmark>railway\s+station|train\s+station|metro\s+station|bus\s+station)\b",
        search_term or "",
        re.IGNORECASE,
    )
    return match.group("landmark").lower() if match else None


def _correct_search_typos(search_term: str) -> str:
    """Correct conservative one-character typos against known provider categories, leaving known trade words untouched."""
    vocabulary = {
        word
        for category in CANONICAL_CATEGORIES
        for word in category.replace("_", " ").split()
        if len(word) >= 3
    }
    trade_stems = {
        _stem(word)
        for place_type in PLACES_TYPE_TO_CATEGORY
        for word in place_type.split("_")
        if len(word) >= 3 and word not in vocabulary
    }
    corrected_words = []
    for word in (search_term or "").split():
        bare_word = word.strip(".,!?;:'\"").lower()
        if len(bare_word) < 3 or bare_word in vocabulary or _stem(bare_word) in trade_stems:
            corrected_words.append(word)
            continue
        match = max(
            vocabulary,
            key=lambda candidate: SequenceMatcher(None, bare_word, candidate).ratio(),
            default=None,
        )
        match_score = SequenceMatcher(None, bare_word, match).ratio() if match else 0
        if match and abs(len(bare_word) - len(match)) <= 1 and match_score >= 0.80:
            corrected_words.append(word.replace(bare_word, match))
        else:
            corrected_words.append(word)
    corrected = " ".join(corrected_words)
    if corrected != search_term:
        logger.info("search_provider: corrected likely typo | original=%r corrected=%r", search_term, corrected)
    return corrected


def _lexical_terms(original_query: str, intent_match_terms: List[str]) -> List[str]:
    """Return deduplicated lower-case ASCII terms of 3+ chars from the query and intent terms, capped at MAX_LEXICAL_TERMS."""
    terms: List[str] = []
    seen: set = set()
    for source in (normalize_search_text(original_query).split(), intent_match_terms):
        for word in source:
            word = word.strip().lower()
            if (
                len(word) < 3
                or not word.isascii()
                or not word.isalnum()
                or word in SEARCH_STOPWORDS
                or word in seen
            ):
                continue
            seen.add(word)
            terms.append(word)
            if len(terms) >= MAX_LEXICAL_TERMS:
                return terms
    return terms


async def _fetch_provider_subservices(user_profile_id: Optional[str]) -> List[Dict[str, Any]]:
    """Return a provider's etown_providerservices rows as subservice dicts, newest first, or an empty list."""
    if not user_profile_id:
        return []
    
    try:
        query = """
            SELECT
                providerservicesid,
                services,
                serviceimageurl,
                serviceimageurl1,
                serviceimageurl2,
                serviceimageurl3,
                pricerange,
                createdon,
                servicedescription,
                businessname,
                providertype,
                businesscategory,
                serviceitems
            FROM public.etown_providerservices
            WHERE userprofileid = $1
            ORDER BY createdon DESC NULLS LAST
        """
        
        rows = await Database.fetch(query, user_profile_id)
        if not rows:
            return []
        premium_label = (
            (await resolve_provider_badges([user_profile_id])).get(user_profile_id) or {}
        ).get("premium_label")
        
        subservices = []
        for row in rows:
            service_image = None
            for img_field in ['serviceimageurl1', 'serviceimageurl2', 'serviceimageurl3', 'serviceimageurl']:
                img = row.get(img_field)
                if img:
                    service_image = img
                    break
            
            bc = str(row.get('businesscategory') or '').lower()
            subservice = {
                "id": row.get("providerservicesid"),
                "name": row.get("services"),
                "description": row.get("servicedescription"),
                "business_name": row.get("businessname"),
                "Etown_providertype": premium_display(row.get("providertype"), premium_label),
                "price": row.get("pricerange"),
                "service_image": service_image,
                "category": bc if bc in ('service', 'product') else None,
                "serviceitems": _parse_jsonb(row.get("serviceitems")),
            }
            subservices.append(subservice)
        
        return subservices
    except Exception as e:
        logger.warning(f"Error fetching subservices for user_profile_id {user_profile_id}: {e}")
        return []


async def _enrich_results_with_subservices(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach a subservices list to each result, fetched in parallel for database rows and empty for web rows."""
    if not results:
        return results
    
    db_results = [r for r in results if r.get("source") == "database"]
    web_results = [r for r in results if r.get("source") != "database"]
    
    if not db_results:
        return results
    
    try:
        fetch_tasks = [
            _fetch_provider_subservices(r.get("userprofileid"))
            for r in db_results
        ]
        subservices_list = await asyncio.gather(*fetch_tasks, return_exceptions=False)
        
        for result, subservices in zip(db_results, subservices_list):
            result["subservices"] = subservices if isinstance(subservices, list) else []
        
        for web_result in web_results:
            web_result["subservices"] = []
        
        enriched_results = []
        db_idx = 0
        web_idx = 0
        for r in results:
            if r.get("source") == "database":
                enriched_results.append(db_results[db_idx])
                db_idx += 1
            else:
                enriched_results.append(web_results[web_idx])
                web_idx += 1
        
        return enriched_results
    
    except Exception as e:
        logger.warning(f"Error enriching results with subservices: {e}")
        for r in results:
            r["subservices"] = []
        return results


def _parse_jsonb(value: Any) -> Any:
    """Decode a jsonb column returned by asyncpg (which hands them back as JSON strings)."""
    if value is None:
        return None
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            logger.debug("search_provider: a jsonb column did not decode; treating it as null")
            return None
    return value


async def _enrich_results_with_provider_details(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach etown_provider_details Places data as a details object to each result, deriving web providerids from place_id."""
    if not results:
        return results

    derived_ids: Dict[int, str] = {}
    for r in results:
        if not r.get("providerid") and r.get("placeid"):
            derived_ids[id(r)] = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"provider:{r['placeid']}"))

    provider_ids = list(
        {str(r["providerid"]) for r in results if r.get("providerid")} | set(derived_ids.values())
    )
    if not provider_ids:
        for r in results:
            r.setdefault("details", None)
        return results

    try:
        rows = await Database.fetch(
            """
            SELECT providerid, place_id, google_rating, google_rating_count,
                   business_status, website_uri, google_maps_uri,
                   opening_hours, reviews, images, comments
            FROM etown_provider_details
            WHERE providerid = ANY($1::text[])
            """,
            provider_ids,
        )
        details_map: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            details = {
                "google_rating": row["google_rating"],
                "google_rating_count": row["google_rating_count"],
                "business_status": row["business_status"],
                "website_uri": row["website_uri"],
                "google_maps_uri": row["google_maps_uri"],
                "opening_hours": _parse_jsonb(row["opening_hours"]),
                "reviews": filter_positive_reviews(_parse_jsonb(row["reviews"])),
                "images": _parse_jsonb(row["images"]),
            }
            comments_val = _parse_jsonb(row["comments"])
            if comments_val:
                details["comments"] = comments_val
            details_map[str(row["providerid"])] = details

        stored_ids = {
            str(row["providerid"]) for row in await Database.fetch(
                "SELECT providerid FROM etown_provider WHERE providerid = ANY($1::text[])",
                provider_ids,
            )
        }
        unpersisted = 0
        for r in results:
            derived = derived_ids.get(id(r))
            if derived:
                if derived in stored_ids or derived in details_map:
                    r["providerid"] = derived
                else:
                    unpersisted += 1
            pid = str(r["providerid"]) if r.get("providerid") else derived
            r["details"] = details_map.get(pid) if pid else None
        if unpersisted:
            logger.info(
                "[SEARCH_PROVIDER] %s Places result(s) left without a providerid — never persisted, "
                "so any action against a fabricated id would fail", unpersisted,
            )
        return results
    except Exception as e:
        logger.warning(f"Error enriching results with provider details: {e}")
        for r in results:
            r.setdefault("details", None)
        return results


def _humanize_place_types(types: Any) -> List[str]:
    """Return Google place types without the generic umbrella types, with underscores spaced and capitalised."""
    if not isinstance(types, list):
        return []
    return [
        str(t).replace("_", " ").strip().capitalize()
        for t in types
        if str(t).strip().lower() not in _GENERIC_PLACE_TYPES
    ]


async def _link_web_results_to_db_providers(
    web_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Copy Etown identity onto Places results matching a DB provider by place_id UUID5, then unambiguous phone or name."""
    if not web_results:
        return web_results

    pscopy_ids = [
        str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pscopy:{r['placeid']}"))
        for r in web_results if r.get("placeid")
    ]
    phones = [
        "".join(ch for ch in str(r.get("mobilenumber") or "") if ch.isdigit())[-10:]
        for r in web_results
    ]
    phones = [p for p in phones if len(p) == 10]
    names = [
        str(r["businessname"]).strip().lower()
        for r in web_results if r.get("businessname")
    ]

    if not (pscopy_ids or phones or names):
        return web_results

    try:
        rows = await Database.fetch_once(
            """
            SELECT epc.providerservicesid, epc.providerid, epc.contactid, epc.userprofileid,
                   epc.businessname, epc.services, epc.servicedescription, epc.businesscategory,
                   epc.servicecategory, epc.providertype, epc.pricerange, epc.yearsinbusiness,
                   epc.address, epc.city, epc.latitude, epc.longitude, epc.mobilenumber,
                   epc.email, epc.websiteurl, epc.serviceitems, epc.normalized_category,
                   COALESCE(ep.trusted, false) AS trusted,
                   COALESCE(ep.boosted, false) AS boosted,
                   RIGHT(regexp_replace(COALESCE(epc.mobilenumber, ''), '\\D', '', 'g'), 10) AS phone_key
            FROM etown_providerservices_copy epc
            LEFT JOIN etown_provider ep ON ep.providerid = epc.providerid
            WHERE (epc.providerservicesid = ANY($1::text[])
               OR RIGHT(regexp_replace(COALESCE(epc.mobilenumber, ''), '\\D', '', 'g'), 10) = ANY($2::text[])
               OR LOWER(TRIM(epc.businessname)) = ANY($3::text[]))

              AND NOT etown_is_retired(epc.contactid)
              AND NOT etown_is_retired(epc.userprofileid)
              AND ep.disabled IS NOT TRUE
              AND """ + review_pending_exclusion("epc.providerid") + """
""",
            pscopy_ids, phones, names,
        )
    except Exception as e:
        logger.warning(f"Could not link web results to DB providers: {e}")
        return web_results
    by_id: Dict[str, Dict[str, Any]] = {}
    by_phone: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        record = dict(row)
        by_id.setdefault(str(record["providerservicesid"]), record)
        if record.get("phone_key"):
            by_phone.setdefault(record["phone_key"], []).append(record)
        if record.get("businessname"):
            by_name.setdefault(str(record["businessname"]).strip().lower(), []).append(record)

    def _unambiguous(bucket: Optional[List[Dict[str, Any]]], city: Optional[str]) -> Optional[Dict[str, Any]]:
        """Return the single provider in this bucket, narrowing by city before giving up."""
        if not bucket:
            return None
        if len({str(rec.get("providerid")) for rec in bucket}) == 1:
            return bucket[0]
        if city:
            same_city = [
                rec for rec in bucket
                if str(rec.get("city") or "").strip().lower() == str(city).strip().lower()
            ]
            if same_city and len({str(rec.get("providerid")) for rec in same_city}) == 1:
                return same_city[0]
        return None

    linked = 0
    ambiguous = 0
    for r in web_results:
        place_id = r.get("placeid")
        phone_key = "".join(ch for ch in str(r.get("mobilenumber") or "") if ch.isdigit())[-10:]
        name_key = str(r.get("businessname") or "").strip().lower()
        city = r.get("city")
        match = None
        if place_id:
            match = by_id.get(str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pscopy:{place_id}")))
        if not match and len(phone_key) == 10:
            match = _unambiguous(by_phone.get(phone_key), city)
            if not match and by_phone.get(phone_key):
                ambiguous += 1
        if not match and name_key:
            match = _unambiguous(by_name.get(name_key), city)
            if not match and by_name.get(name_key):
                ambiguous += 1
        if not match:
            r["types"] = _humanize_place_types(r.get("types"))
            continue

        for field in (
            "providerservicesid", "providerid", "contactid", "userprofileid", "businessname",
            "services", "servicedescription", "businesscategory", "servicecategory",
            "providertype", "pricerange", "yearsinbusiness", "email", "websiteurl",
            "normalized_category", "trusted", "boosted",
        ):
            r[field] = match.get(field)
        r["serviceitems"] = _parse_jsonb(match.get("serviceitems"))
        r["address"] = match.get("address") or r.get("address")
        r["city"] = match.get("city") or r.get("city")
        r["source"] = "database"
        r["types"] = _humanize_place_types(r.get("types"))
        linked += 1

    if linked:
        badge_map = await resolve_provider_badges(
            r.get("userprofileid") for r in web_results if isinstance(r, dict)
        )
        for r in web_results:
            if isinstance(r, dict) and r.get("userprofileid"):
                r["providertype"] = premium_display(
                    r.get("providertype"),
                    (badge_map.get(r["userprofileid"]) or {}).get("premium_label"),
                )

    if linked or ambiguous:
        logger.info(
            "[SEARCH_PROVIDER] Linked %s/%s Places result(s) back to existing Etown providers "
            "(%s left unlinked as ambiguous)",
            linked, len(web_results), ambiguous,
        )
    return web_results


def _build_search_query_variants(
    original_query: str,
    processed_query: str,
    trade_terms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Build deduplicated, priority-ordered query variants (original, translated, normalised, romanised Tamil, trades).

    Args:
        original_query: The user's query as typed.
        processed_query: The translated or original English query.
        trade_terms: Trades resolved by service_intent, each searched as its own variant.

    Returns:
        Variant dicts ordered by priority, capped at MAX_SEARCH_VARIANTS.
    """
    variant_candidates: List[Tuple[str, str, int]] = [
        (original_query, "original", 0),
        (processed_query, "translated_or_original", 1),
    ]

    for candidate, label, priority in list(variant_candidates):
        normalized = normalize_search_text(candidate)
        if normalized:
            variant_candidates.append((normalized, f"{label}_normalized", priority + 1))

        mapped = map_romanized_tamil_terms(candidate)
        if mapped and mapped != normalize_search_text(candidate):
            variant_candidates.append((mapped, f"{label}_mapped", priority + 5))

    for index, trade in enumerate(trade_terms or []):
        if trade and trade.strip():
            variant_candidates.append((trade.strip(), f"trade_{index}", 3 + index))

    deduped_variants: List[Dict[str, Any]] = []
    seen = set()
    for query_text, label, priority in sorted(variant_candidates, key=lambda item: item[2]):
        normalized_key = normalize_search_text(query_text)
        if not normalized_key or normalized_key in seen:
            continue
        seen.add(normalized_key)
        deduped_variants.append({
            "query": query_text,
            "label": label,
            "priority": priority,
            "is_tamil": contains_tamil_script(query_text),
        })

    return deduped_variants[:MAX_SEARCH_VARIANTS]


def _score_web_results_for_ranking(
    web_results: List[Dict[str, Any]], intent: ServiceIntent
) -> None:
    """Set trade, item-affinity and category match/conflict ranking signals on Places rows in place."""
    trades = [t for t in intent.trade_terms if t and t.strip()]
    items = [t for t in intent.item_terms if t and t.strip()]
    primary_category = next((c for c in intent.categories if c and c.strip()), None)
    for result in web_results:
        name = normalize_search_text(result.get("businessname", "") or "")
        description = normalize_search_text(result.get("servicedescription", "") or "")
        result["_matched_trade"] = _satisfied_trade(trades, [name, description]) or ""
        result["_item_affinity"] = _item_affinity_band(items, [name], [description])

        row_category = places_category({
            "primaryType": next(iter(result.get("primarytype") or []), None),
            "types": result.get("types") or [],
        })
        result["_matched_category"] = bool(
            primary_category and row_category == primary_category
        )
        result["_category_conflict"] = bool(
            primary_category
            and row_category
            and row_category != 'other'
            and row_category != primary_category
            and not result["_item_affinity"]
            and not result["_matched_trade"]
        )


def _describable_terms(intent: Any) -> set:
    """Return searchable terms that can describe the resolved intent."""
    words = set()
    for trade in getattr(intent, "trade_terms", ()) or ():
        words.update(normalize_search_text(trade).split())
    for item in getattr(intent, "item_terms", ()) or ():
        head = normalize_search_text(item).split()
        if head:
            words.add(head[-1])
    return words


def _dominant_result_label(
    results: List[Dict[str, Any]],
    allowed_terms: Optional[set] = None,
) -> Optional[str]:
    """Return the service word shared by at least 60 percent of results, optionally limited to allowed terms, else None."""
    if not results:
        return None

    labels = [
        normalize_search_text(r.get("services") or r.get("businesscategory") or "")
        for r in results
    ]
    labels = [label for label in labels if label]
    if not labels:
        return None

    counts = Counter()
    for label in labels:
        for word in set(label.split()):
            if len(word) < 4 or word in SEARCH_STOPWORDS:
                continue
            if allowed_terms is not None and word not in allowed_terms:
                continue
            counts[word] += 1
    if not counts:
        return None

    word, frequency = counts.most_common(1)[0]
    if frequency < max(2, int(len(results) * 0.6)):
        return None
    return word


def _select_best_candidate(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Select the better ranked candidate when merging duplicate providers."""
    existing_key = (
        int(existing.get("_variant_priority", 999)),
        int(existing.get("_vector_rank", 9999)),
        existing.get("distance_km", 9999),
    )
    incoming_key = (
        int(incoming.get("_variant_priority", 999)),
        int(incoming.get("_vector_rank", 9999)),
        incoming.get("distance_km", 9999),
    )
    return incoming if incoming_key < existing_key else existing

def is_provider_verified(providertype: str) -> bool:
    """Return True when providertype is 'verified' or ends in '_verified', and is not nonverified or unverified."""
    if not providertype:
        return False
    
    normalized_type = str(providertype).lower().strip()
    if 'nonverified' in normalized_type or 'unverified' in normalized_type:
        return False

    verified_patterns = ['verified', 'etown_verified']
    return any(pattern == normalized_type or normalized_type.endswith('_' + pattern) for pattern in verified_patterns)


def _rating_sort_key(item: Dict[str, Any]) -> float:
    """Return the negated rating as an ascending sort key, placing unrated providers after rated ones."""
    details = item.get('details') or {}
    rating = details.get('google_rating') or item.get('rating') or item.get('overallrating')
    return -float(rating) if rating else 1.0


def distance_sort_key(item: Any) -> Tuple[int, float]:
    """Return a nearest-first sort key on numeric distance_km, placing missing, invalid or sentinel distances last."""
    distance = item.get('distance_km') if isinstance(item, dict) else None
    if isinstance(distance, bool) or not isinstance(distance, (int, float)):
        return (1, 0.0)
    if not math.isfinite(distance) or distance < 0 or distance >= 9999:
        return (1, 0.0)
    return (0, float(distance))


@db_query_monitor('provider_search')
async def handle_search_provider(
    search_term: str,
    search_category: str = "business",
    years_in_business: int = 0,
    limit: int = 10,
    offset: int = 0,
    max_distance_meters: int = 25000,
    user_latitude: Optional[float] = None,
    user_longitude: Optional[float] = None,
    contact_id: Optional[str] = None,
    user_profile_id: Optional[str] = None,
    user_name: Optional[str] = None,
    shown_provider_ids: Optional[List[str]] = None,
    shown_provider_ids_by_term: Optional[Dict[str, List[str]]] = None,
    prioritize_closest: bool = False,
    request_date_and_time: bool = False,
    request_weekend_choice: bool = False,
    request_named_day: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """Search providers across DB vector, lexical and Places legs with translation, ranking and web fallback.

    Args:
        search_term: User's search query, typo-corrected and translated when needed.
        search_category: Category filter: business, product or service.
        years_in_business: Minimum years in business; 0 disables the filter.
        limit: Maximum number of results to return.
        offset: Pagination offset.
        max_distance_meters: Distance cap for results.
        user_latitude: User latitude for distance calculation.
        user_longitude: User longitude for distance calculation.
        contact_id: Searcher's contact id, excluded from results and used for web inserts.
        user_profile_id: Searcher's userprofile id for web provider inserts.
        user_name: Searcher's name for web provider inserts.
        shown_provider_ids: Provider ids already shown in this conversation, excluded.
        shown_provider_ids_by_term: Shown ids keyed by search term; only this term's entry is excluded.
        prioritize_closest: Force closest-first ranking.
        request_date_and_time: Force asking for the service date and time.
        request_weekend_choice: Make the date/time ask offer this Saturday or Sunday.
        request_named_day: Explicit named day requested for the search.

    Returns:
        Tuple of provider result dicts and the response message.
    """
    search_term = _correct_search_typos(search_term)
    _shown_ids: set = shown_ids_for_search(
        shown_provider_ids_by_term, search_term, shown_provider_ids
    )
    searcher_contact_id = contact_id

    original_query = search_term
    translated_query = search_term
    
    context = get_execution_context()
    user_language = context.get("language", "en") if context else "en"
    
    source_language = "ta" if contains_tamil_script(search_term) else user_language
    try:
        if source_language != "en":
            logger.info(f"Translating search term from {source_language} to English")
            _, translated_query = await SearchTermTranslator.translate_search_term_for_booking(
                search_term, source_language
            )
            if translated_query and translated_query.strip():
                logger.info(f"Using translated query: '{translated_query}' (from '{original_query}')")
                search_term = translated_query
            else:
                logger.warning(f"Translation failed or empty, using original: '{original_query}'")
                search_term = original_query
        else:
            logger.info(f"User language is English, no translation needed")
            translated_query = original_query
            search_term = original_query
    except Exception as e:
        logger.error(f"Translation failed: {e}, using original query: '{original_query}'", exc_info=True)
        search_term = original_query
        
    if not search_term or not search_term.strip():
        logger.warning(f"Search term is empty after processing, falling back to original: '{original_query}'")
        search_term = original_query
        
    if not search_term or not search_term.strip():
        logger.error("Search term is still empty after all fallbacks, using generic search")
        search_term = "service provider"
    
    logger.info(f"Searching providers for term: '{search_term}' in category: '{search_category}'")

    nearby_landmark = _extract_nearby_landmark(original_query)
    explicit_location = None if nearby_landmark else _extract_explicit_location(original_query)
    explicit_state_code = next(
        (
            code for state_name, code in STATE_CODE_MAP.items()
            if explicit_location and state_name.lower() == explicit_location.lower()
        ),
        None,
    )
    explicit_distance_km = _extract_explicit_distance_km(original_query)
    requests_closest = prioritize_closest or _requests_closest_provider(original_query)
    requests_date_and_time = request_date_and_time or _requests_date_and_time(original_query)
    requests_weekend_choice = request_weekend_choice or _requests_weekend_choice(original_query)
    named_day = request_named_day or _named_day(original_query)
    if explicit_distance_km is None and max_distance_meters != 25000:
        explicit_distance_km = max_distance_meters / 1000
    if explicit_distance_km is not None:
        max_distance_meters = explicit_distance_km * 1000
        logger.info(
            "search_provider: explicit distance requested; using exact radius | km=%s",
            explicit_distance_km,
        )
    if explicit_location:
        logger.info(
            "search_provider: explicit location requested; ignoring caller coordinates | location=%s",
            explicit_location,
        )
        user_latitude = None
        user_longitude = None

    if nearby_landmark:
        if user_latitude is None or user_longitude is None:
            return [], (
                f"I need your location to check whether there is a {nearby_landmark} nearby "
                "before searching for providers."
            )
        landmark_response = await google_map_service.place_search_with_fallback({
            "textQuery": nearby_landmark,
            "pageSize": 5,
            "locationBias": {
                "circle": {
                    "center": {"latitude": user_latitude, "longitude": user_longitude},
                    "radius": max_distance_meters,
                }
            },
        })
        landmark_places = [
            place for place in landmark_response.get("places", [])
            if place.get("location", {}).get("latitude") is not None
            and place.get("location", {}).get("longitude") is not None
        ]
        landmark_places = [
            place for place in landmark_places
            if geodesic(
                (user_latitude, user_longitude),
                (
                    place["location"]["latitude"],
                    place["location"]["longitude"],
                ),
            ).km <= max_distance_meters / 1000
        ]
        if not landmark_places:
            return [], (
                f"I could not verify a {nearby_landmark} near your current location, "
                "so I did not present providers as being near one."
            )
        nearest_landmark = min(
            landmark_places,
            key=lambda place: geodesic(
                (user_latitude, user_longitude),
                (
                    place["location"]["latitude"],
                    place["location"]["longitude"],
                ),
            ).km,
        )
        landmark_location = nearest_landmark["location"]
        landmark_name = (nearest_landmark.get("displayName") or {}).get("text", nearby_landmark)
        user_latitude = landmark_location["latitude"]
        user_longitude = landmark_location["longitude"]
        search_term = re.sub(
            r"\s+near\s+(?:the\s+)?(?:railway\s+station|train\s+station|metro\s+station|bus\s+station)\b",
            "",
            search_term,
            flags=re.IGNORECASE,
        ).strip()
        original_query = search_term
        logger.info(
            "search_provider: verified landmark %r and searching from its coordinates",
            landmark_name,
        )

    intent = await resolve_service_intent(search_term, search_category)
    if intent.needs_clarification:
        question = (intent.clarification_question or "").strip() or (
            f"could you tell me a little more about the {original_query} problem"
        )
        return [], question[:1].upper() + question[1:].rstrip("?") + "?"
    query_variants = _build_search_query_variants(
        original_query, search_term, trade_terms=list(intent.trade_terms)
    )
    logger.info(
        "Search query variants: %s",
        [f"{variant['label']}={variant['query']}" for variant in query_variants]
    )
    lexical_terms = _lexical_terms(original_query, list(intent.match_terms))
    logger.info(
        "Lexical arm terms=%s categories=%s", lexical_terms, list(intent.categories)
    )
    embedding_strings: Dict[str, str] = {}

    def _distance_allowance_km(row: Dict[str, Any]) -> float:
        """Maximum distance this provider may be returned from on the first pass.

        Only the caller's ``max_distance_meters`` (plus the verified multiplier) applies.
        The former ``if is_service: max(allowed, SERVICE_MAX_DISTANCE_KM)`` floor was still
        universal in practice — ``businesscategory`` is 'service' on the majority of rows
        (prod: 56,069 of 85,187) — so it kept granting the 100 km allowance and 45-60 km
        listings were announced as "nearby" ahead of a 10 km match. Widening past the
        caller's radius is now only the progressive-expansion ladder's and the
        radius-extension fallback's job, and both are entered deliberately and set
        ``radius_extended`` so the reply stays honest about the distance.
        """
        base_km = max_distance_meters / 1000
        return (
            base_km
            if explicit_distance_km is not None
            else base_km * VERIFIED_DISTANCE_MULTIPLIER
            if is_provider_verified(row.get("providertype"))
            else base_km
        )

    def _build_base_conditions(
        category_filter: str, ignore_distance: bool
    ) -> Tuple[str, List[Any]]:
        """Build the shared WHERE clause and its parameters for every retrieval arm.

        Args:
            category_filter: 'business' (no filter), 'service' or 'product'.
            ignore_distance: True for the beyond-range fallback, which widens the geo box
                to ``EXTENDED_SEARCH_RADIUS_KM`` instead of the standard allowance.

        Returns:
            Tuple[str, List[Any]]: The WHERE fragment and its positional parameters.
        """
        params: List[Any] = []
        conditions: List[str] = []

        if explicit_location:
            params.append(explicit_location)
            if explicit_state_code:
                conditions.append(f"UPPER(BTRIM(COALESCE(statecode, ''))) = ${len(params)}")
                params[-1] = explicit_state_code
            else:
                conditions.append(f"LOWER(BTRIM(COALESCE(city, ''))) = LOWER(BTRIM(${len(params)}))")

        conditions.append(
            "NOT etown_is_retired(contactid) AND NOT etown_is_retired(userprofileid)"
        )

        conditions.append(
            "NOT EXISTS (SELECT 1 FROM etown_provider dp"
            " WHERE dp.disabled"
            " AND dp.userprofileid = etown_providerservices_copy.userprofileid)"
        )

        conditions.append(
            review_pending_exclusion("etown_providerservices_copy.providerid")
        )

        if category_filter and category_filter.lower() != "business":
            params.append(category_filter.lower())
            conditions.append(
                f"(LOWER(businesscategory) LIKE '%' || ${len(params)} || '%'"
                f" OR LOWER(servicecategory) = ${len(params)})"
            )

        if years_in_business > 0:
            params.append(str(years_in_business))
            conditions.append(f"yearsinbusiness >= ${len(params)}")

        if contact_id:
            params.append(contact_id)
            conditions.append(f"contactid != ${len(params)}")
        if user_latitude is not None and user_longitude is not None:
            radius_km = (
                EXTENDED_SEARCH_RADIUS_KM
                if ignore_distance
                else max_distance_meters / 1000
                if explicit_distance_km is not None
                else max(
                    (max_distance_meters / 1000) * VERIFIED_DISTANCE_MULTIPLIER,
                    SERVICE_MAX_DISTANCE_KM,
                )
            )
            params.extend(_bounding_box(user_latitude, user_longitude, radius_km))
            conditions.append(
                f"latitude BETWEEN ${len(params) - 3} AND ${len(params) - 2}"
                f" AND longitude BETWEEN ${len(params) - 1} AND ${len(params)}"
            )

        return (" AND ".join(conditions) if conditions else "TRUE"), params

    def _finalise_rows(
        rows: List[Any],
        variant_label: str,
        variant_priority: int,
        ignore_distance: bool,
    ) -> List[Dict[str, Any]]:
        """Dedupe by contact, apply the exact distance cap, and tag rows with their origin."""
        results: List[Dict[str, Any]] = []
        seen_contacts = set()
        for index, row in enumerate(rows, start=1):
            r = dict(row)
            if r.get('contactid') in seen_contacts:
                continue
            seen_contacts.add(r.get('contactid'))
            r.pop('content_vector', None)
            r.pop('hybrid_score', None)
            r.pop('term_hits', None)
            r.pop('category_match', None)
            if user_latitude and user_longitude and r.get('latitude') and r.get('longitude'):
                try:
                    dist = geodesic(
                        (user_latitude, user_longitude),
                        (float(r['latitude']), float(r['longitude'])),
                    ).km
                    is_boosted = bool(r.get('boosted', False))
                    if (
                        not ignore_distance
                        and dist > _distance_allowance_km(r)
                        and not is_boosted
                    ):
                        continue
                    r['distance_km'], r['distance_meters'] = round(dist, 2), int(dist * 1000)
                except Exception:
                    logger.debug("search_provider: dropping a row with unusable coordinates", exc_info=True)
                    continue

            r['_query_variant'] = variant_label
            r['_variant_priority'] = variant_priority
            r['_vector_rank'] = index
            results.append(r)
        return results

    async def execute_db_search(
        query_text: str,
        category_filter: str,
        use_vector: bool = True,
        variant_priority: int = 999,
        variant_label: str = "default",
        ignore_distance: bool = False,
    ) -> List[Dict[str, Any]]:
        where_clause, sql_params = _build_base_conditions(category_filter, ignore_distance)

        if use_vector:
            embedding_string = embedding_strings.get(query_text)
            if embedding_string is None:
                embedding_string = vector_to_postgres_string(await generate_embedding(query_text))
                embedding_strings[query_text] = embedding_string
            sql_params.append(embedding_string)
            vector_idx = len(sql_params)
            sql_params.append(f"%{_escape_like(query_text)}%")
            name_pattern_idx = len(sql_params)
            sql_params.extend([limit * 5, offset])
            limit_idx, offset_idx = len(sql_params) - 1, len(sql_params)

            sql = f"""
                SELECT subq.*, COALESCE(ep.trusted, false) AS trusted, COALESCE(ep.boosted, false) AS boosted
                FROM (
                    SELECT *,
                           (content_vector <#> ${vector_idx}::vector)
                           + CASE WHEN LOWER(businessname) LIKE LOWER(${name_pattern_idx}) THEN -0.7 ELSE 0.0 END
                           + CASE WHEN providertype IN ('etown_verified', 'verified') THEN -0.2 ELSE 0.0 END
                           AS hybrid_score
                    FROM etown_providerservices_copy
                    WHERE {where_clause} AND content_vector IS NOT NULL
                    ORDER BY hybrid_score ASC
                    LIMIT ${limit_idx} OFFSET ${offset_idx}
                ) subq
                LEFT JOIN etown_provider ep ON ep.providerid = subq.providerid
            """
        else:
            keywords = [w for w in query_text.split() if len(w) >= 2] or [query_text]
            text_conditions = []
            for word in keywords:
                sql_params.append(f"%{_escape_like(word)}%")
                text_conditions.append(
                    f"(businessname ILIKE ${len(sql_params)} OR services ILIKE ${len(sql_params)}"
                    f" OR servicedescription ILIKE ${len(sql_params)})"
                )
            sql_params.extend([limit * 5, offset])
            limit_idx, offset_idx = len(sql_params) - 1, len(sql_params)
            sql = f"""
                SELECT subq.*, COALESCE(ep.trusted, false) AS trusted, COALESCE(ep.boosted, false) AS boosted
                FROM (
                    SELECT * FROM etown_providerservices_copy
                    WHERE {where_clause} AND ({' OR '.join(text_conditions)})
                    ORDER BY businessname LIMIT ${limit_idx} OFFSET ${offset_idx}
                ) subq
                LEFT JOIN etown_provider ep ON ep.providerid = subq.providerid
            """

        rows = await Database.fetch_once(sql, *sql_params)
        return _finalise_rows(rows, variant_label, variant_priority, ignore_distance)

    async def execute_lexical_search(
        category_filter: str,
        terms: List[str],
        categories: List[str],
        variant_priority: int = 2,
        ignore_distance: bool = False,
    ) -> List[Dict[str, Any]]:
        """Retrieve candidates by canonical category and full-text match, not by embedding.

        The vector arm alone cannot find the right providers in this corpus: measured on
        prod for "office chair repair", the genuinely relevant local listings ranked
        3,000-10,600 of 85,197, because `services` is per-category boilerplate (one label
        repeats across 1,596 rows) and the embedding therefore carries almost no
        per-business signal. Matching `normalized_category` — the trigger-maintained
        canonical classification — plus a full-text hit on the listing text retrieves them
        directly. Uses the existing (previously unused) idx_pscopy_fts index.

        Args:
            category_filter: 'business', 'service' or 'product'.
            terms: Lower-cased ASCII words from the query and the resolved trade vocabulary.
            categories: Canonical normalized_category values resolved from the request.
            variant_priority: Ranking priority relative to the vector variants.
            ignore_distance: Passed through to widen the geo box and skip the distance cap.

        Returns:
            List[Dict[str, Any]]: Candidate rows, most term hits first. Empty when there is
                nothing to match on or the query fails.
        """
        if not terms and not categories:
            return []

        where_clause, sql_params = _build_base_conditions(category_filter, ignore_distance)

        listing_text = (
            "lower(COALESCE(businessname,'') || ' ' || COALESCE(services,'') || ' '"
            " || COALESCE(servicedescription,'') || ' '"
            " || replace(COALESCE(normalized_category,''), '_', ' '))"
        )
        sql_params.append(terms or [])
        terms_idx = len(sql_params)
        sql_params.append(categories or [])
        categories_idx = len(sql_params)

        match_conditions = [f"normalized_category = ANY(${categories_idx}::text[])"]
        if terms:
            sql_params.append(" | ".join(terms))
            match_conditions.append(
                "to_tsvector('english', COALESCE(businessname,'') || ' '"
                " || COALESCE(services,'') || ' ' || COALESCE(areaname,'') || ' '"
                f" || COALESCE(city,'')) @@ to_tsquery('english', ${len(sql_params)})"
            )

        sql_params.append(limit * 5)
        limit_idx = len(sql_params)

        sql = f"""
            SELECT subq.*, COALESCE(ep.trusted, false) AS trusted, COALESCE(ep.boosted, false) AS boosted
            FROM (
                SELECT *,
                       (SELECT count(*) FROM unnest(${terms_idx}::text[]) term
                        WHERE {listing_text} LIKE '%' || term || '%') AS term_hits,
                       (normalized_category = ANY(${categories_idx}::text[])) AS category_match
                FROM etown_providerservices_copy
                WHERE {where_clause} AND ({' OR '.join(match_conditions)})
                ORDER BY category_match DESC, term_hits DESC
                LIMIT ${limit_idx}
            ) subq
            LEFT JOIN etown_provider ep ON ep.providerid = subq.providerid
        """

        try:
            rows = await Database.fetch_once(sql, *sql_params)
        except Exception as e:
            logger.warning("Lexical/category search failed: %r", e)
            return []
        finalised = _finalise_rows(rows, "lexical_category", variant_priority, ignore_distance)
        logger.info(
            "Lexical/category arm retrieved %s row(s) (%s by exact category) for categories=%s terms=%s",
            len(finalised),
            sum(1 for row in rows if row.get("category_match")),
            categories, terms,
        )
        return finalised

    db_results_by_key: Dict[str, Dict[str, Any]] = {}

    async def search_all_variants(category_filter: str) -> None:
        """Run every retrieval arm concurrently and merge the rows into db_results_by_key.

        One vector arm per query variant, plus a single lexical/category arm — the latter
        depends on the resolved intent rather than on the individual variant wording, so
        running it once per search is sufficient.
        """
        arms: List[Tuple[str, Any]] = [
            (
                variant["label"],
                execute_db_search(
                    variant["query"],
                    category_filter,
                    use_vector=True,
                    variant_priority=variant["priority"],
                    variant_label=variant["label"],
                ),
            )
            for variant in query_variants
        ]
        arms.append((
            "lexical_category",
            execute_lexical_search(category_filter, lexical_terms, list(intent.categories)),
        ))

        arm_results = await asyncio.gather(
            *(coro for _, coro in arms), return_exceptions=True
        )
        for (label, _), rows in zip(arms, arm_results):
            if isinstance(rows, BaseException):
                logger.warning("Retrieval arm '%s' failed: %r", label, rows)
                continue
            for result in rows:
                dedupe_key = result.get("contactid") or result.get("providerservicesid") or normalize_search_text(result.get("businessname", ""))
                if not dedupe_key:
                    continue
                if dedupe_key in db_results_by_key:
                    db_results_by_key[dedupe_key] = _select_best_candidate(db_results_by_key[dedupe_key], result)
                else:
                    db_results_by_key[dedupe_key] = result

    await search_all_variants(search_category)
    db_results = list(db_results_by_key.values())

    if not db_results and search_category.lower() != "business":
        await search_all_variants("business")
        db_results = list(db_results_by_key.values())

    db_results.sort(
        key=lambda item: (
            int(item.get("_variant_priority", 999)),
            int(item.get("_vector_rank", 9999)),
            item.get("distance_km", 9999),
        )
    )
    _intent_keyword_sets: List[set] = [set(intent.match_terms)] if intent.match_terms else []
    _intent_keyword_sets.extend(
        {word for word in trade.split()} for trade in intent.trade_terms if trade.strip()
    )

    if db_results:
        db_results = await enrich_profiles_with_rating_data(db_results)
        db_results = await filter_irrelevant_results(
            search_term,
            db_results,
            query_variants=[variant["query"] for variant in query_variants],
            extra_keyword_sets=_intent_keyword_sets,
            is_item_specific=intent.is_item_specific,
            trade_terms=list(intent.trade_terms),
            accepted_categories=list(intent.categories),
            item_terms=list(intent.item_terms),
        )

    _filter_tokens = [
        w for w in normalize_search_text(original_query).split()
        if len(w) >= 2 and w not in SEARCH_STOPWORDS
    ]
    radius_extended = False

    if explicit_distance_km is None and len(db_results) < 5 and user_latitude and user_longitude:
        logger.info(f"Trying progressive distance expansion for insufficient results ({len(db_results)})")
        is_service_query = bool(
            (search_category or "").lower() == "service"
            or intent.is_item_specific
            or any(category.endswith("_services") for category in intent.categories)
        )

        if is_service_query:
            expanded_distances = [50000, 100000]
            expanded_variant_results = await asyncio.gather(
                *[
                    execute_db_search(
                        variant["query"],
                        search_category,
                        use_vector=True,
                        variant_priority=variant["priority"],
                        variant_label=variant["label"],
                        ignore_distance=True,
                    )
                    for variant in query_variants
                ],
                return_exceptions=True,
            )
            expanded_variant_rows: List[Dict[str, Any]] = []
            for variant, variant_rows in zip(query_variants, expanded_variant_results):
                if isinstance(variant_rows, BaseException):
                    logger.warning("Radius expansion arm '%s' failed: %r",variant["label"],variant_rows,)
                    continue
                expanded_variant_rows.extend(variant_rows)
            for expanded_distance in expanded_distances:
                if len(db_results) >= 5:
                    break
                logger.info("Filtering cached expanded candidates to %skm radius",expanded_distance / 1000,)
                expanded_results_by_key: Dict[str, Dict[str, Any]] = {}
                for result in expanded_variant_rows:
                    if result.get("latitude") and result.get("longitude"):
                        try:
                            dist_km = geodesic(
                                (user_latitude, user_longitude),
                                (
                                    float(result["latitude"]),
                                    float(result["longitude"]),
                                ),
                            ).km
                            if dist_km <= (expanded_distance / 1000):
                                result["distance_km"] = round(dist_km, 2)
                                result["distance_meters"] = int(dist_km * 1000)
                                dedupe_key = (
                                    result.get("contactid")
                                    or result.get("providerservicesid")
                                    or normalize_search_text(
                                        result.get("businessname", "")
                                    )
                                )
                                if (
                                    dedupe_key
                                    and dedupe_key not in db_results_by_key
                                    and dedupe_key not in expanded_results_by_key
                                ):
                                    expanded_results_by_key[dedupe_key] = result
                        except Exception:
                            logger.debug("search_provider: dropping an expanded-radius row ""with unusable coordinates",exc_info=True,)
                            continue
                if not expanded_results_by_key:
                    logger.debug("No new providers at %skm radius, skipping re-filter",expanded_distance / 1000,)
                    continue
                for key, result in expanded_results_by_key.items():
                    if key not in db_results_by_key:
                        db_results_by_key[key] = result
                expanded_db_results = list(db_results_by_key.values())
                expanded_db_results.sort(
                    key=lambda item: (
                        int(item.get("_variant_priority", 999)),
                        int(item.get("_vector_rank", 9999)),
                        item.get("distance_km", 9999),
                    )
                )
                if expanded_db_results:
                    expanded_db_results = await enrich_profiles_with_rating_data(expanded_db_results)
                    filtered_expanded = await filter_irrelevant_results(
                        search_term,
                        expanded_db_results,
                        query_variants=[variant["query"] for variant in query_variants],
                        extra_keyword_sets=_intent_keyword_sets,
                        is_item_specific=intent.is_item_specific,
                        trade_terms=list(intent.trade_terms),
                        accepted_categories=list(intent.categories),
                        item_terms=list(intent.item_terms),
                    )
                    if len(filtered_expanded) > len(db_results):
                        db_results = filtered_expanded
                        logger.info("Progressive expansion found %s results at %skm radius",len(db_results),expanded_distance / 1000,)
                        if len(db_results) >= 5:
                            break

    if explicit_distance_km is None and not db_results and user_latitude and user_longitude:
        logger.info("Zero providers within standard range — extending radius to find closest matches")
        extended_by_key: Dict[str, Dict[str, Any]] = {}
        extended_arms = [
            execute_db_search(
                variant["query"],
                search_category,
                use_vector=True,
                variant_priority=variant["priority"],
                variant_label=variant["label"],
                ignore_distance=True,
            )
            for variant in query_variants
        ]
        extended_arms.append(
            execute_lexical_search(
                search_category,
                lexical_terms,
                list(intent.categories),
                ignore_distance=True,
            )
        )
        for rows in await asyncio.gather(*extended_arms, return_exceptions=True):
            if isinstance(rows, BaseException):
                logger.warning("Radius-extension arm failed: %r", rows)
                continue
            for result in rows:
                dedupe_key = result.get("contactid") or result.get("providerservicesid") or normalize_search_text(result.get("businessname", ""))
                if not dedupe_key:
                    continue
                existing = extended_by_key.get(dedupe_key)
                extended_by_key[dedupe_key] = _select_best_candidate(existing, result) if existing else result

        extended_results = list(extended_by_key.values())
        if extended_results:
            extended_results = await enrich_profiles_with_rating_data(extended_results)
            extended_results = await filter_irrelevant_results(
                search_term,
                extended_results,
                query_variants=[variant["query"] for variant in query_variants],
                extra_keyword_sets=_intent_keyword_sets,
                is_item_specific=intent.is_item_specific,
                trade_terms=list(intent.trade_terms),
                accepted_categories=list(intent.categories),
                item_terms=list(intent.item_terms),
            )
        if extended_results:
            extended_results.sort(key=lambda item: item.get("distance_km", 9999))
            unseen_results = [
                r for r in extended_results
                if str(r.get("providerservicesid", "")) not in _shown_ids
                and str(r.get("contactid", "")) not in _shown_ids
            ]
            if unseen_results and len(unseen_results) < len(extended_results):
                logger.info(
                    f"Radius extension skipped {len(extended_results) - len(unseen_results)} "
                    f"already-shown provider(s) before taking the closest {limit}"
                )
                extended_results = unseen_results
            db_results = extended_results[:limit]
            for r in db_results:
                key = r.get("contactid") or r.get("providerservicesid") or normalize_search_text(r.get("businessname", ""))
                if key:
                    db_results_by_key[key] = r
            nearest_km = db_results[0].get("distance_km")
            standard_range_km = max_distance_meters / 1000
            if nearest_km is not None and nearest_km > standard_range_km:
                radius_extended = True
                logger.info(
                    f"Radius extension surfaced {len(db_results)} closest provider(s) "
                    f"beyond standard range; nearest {nearest_km} km"
                )
            else:
                in_range = [
                    r for r in db_results
                    if not isinstance(r.get("distance_km"), (int, float))
                    or r["distance_km"] <= standard_range_km
                ]
                logger.info(
                    f"Radius-extension fallback re-surfaced {len(in_range)} in-range "
                    f"provider(s) dropped by the first-pass filter"
                    + (f"; nearest {nearest_km} km" if nearest_km is not None else "")
                    + f". Dropped {len(db_results) - len(in_range)} out-of-range row(s); "
                    f"presenting the rest as normal nearby results."
                )
                db_results = in_range

    if _filter_tokens and len(db_results) > 1:
        protected = [
            r for r in db_results if r.get("_matched_trade") or r.get("_matched_category")
        ]
        adjudicable = [
            r for r in db_results
            if not (r.get("_matched_trade") or r.get("_matched_category"))
        ]
        if len(adjudicable) > 1:
            adjudicable = await llm_batch_relevance_filter(original_query, adjudicable)
        if protected:
            kept_ids = {id(r) for r in protected} | {id(r) for r in adjudicable}
            db_results = [r for r in db_results if id(r) in kept_ids]
        else:
            db_results = adjudicable
    web_results = []
    web_insert_coros = []
    web_insert_place_ids = []
    _local_ceiling_km = min(
        (max_distance_meters / 1000) * VERIFIED_DISTANCE_MULTIPLIER,
        LOCAL_REACH_CEILING_KM,
    )
    def _fills_local_quota(r: Dict[str, Any]) -> bool:
        """Near AND dealing in the thing asked for. A missing signal counts as relevant."""
        distance = r.get("distance_km")
        if not isinstance(distance, (int, float)) or distance > _local_ceiling_km:
            return False
        if not intent.item_terms or "_item_affinity" not in r:
            return True
        return int(r.get("_item_affinity") or 0) > 0

    if user_latitude is not None and user_longitude is not None:
        _local_count = sum(1 for r in db_results if _fills_local_quota(r))
    else:
        _local_count = len(db_results)

    if _local_count < MIN_LOCAL_RESULTS_BEFORE_WEB_SEARCH:
        logger.info(
            f"Only {_local_count} of {len(db_results)} local result(s) within "
            f"{_local_ceiling_km} km deal in {list(intent.item_terms) or 'the request'}, "
            f"triggering Web Search..."
        )
        if intent.item_terms:
            web_query = intent.item_terms[0]
        else:
            web_query = intent.trade_terms[0] if intent.trade_terms else original_query
        if explicit_location and explicit_location.lower() not in web_query.lower():
            web_query = f"{web_query} in {explicit_location}"
        if web_query != original_query:
            logger.info(f"Web search using resolved trade '{web_query}' (from '{original_query}')")
        payload = {
            "textQuery": web_query,
            "pageSize": limit,
            "locationBias": {"circle": {"center": {"latitude": user_latitude, "longitude": user_longitude}, "radius": max_distance_meters}} if user_latitude else None
        }
        map_response = await google_map_service.place_search_with_fallback(payload)
        if map_response.get("fallback_used"):
            logger.info(
                "Web search fallback active for query='%s' via %s (RapidAPI failure=%s)",
                original_query,
                map_response.get("effective_provider"),
                map_response.get("rapidapi_failure"),
            )
        places = map_response.get('places', [])
        
        for p in places:
            p_type = p.get('primaryType')
            
            if p_type == 'market':
                logger.info(f"Skipping market provider: {p.get('displayName', {}).get('text')}")
                continue
            
            if not p.get('nationalPhoneNumber'):
                logger.info(f"Skipping provider without phone number: {p.get('displayName', {}).get('text')}")
                continue

            if not is_india_place(p):
                logger.info(f"Skipping non-India provider: {p.get('displayName', {}).get('text')}")
                continue

            lat, lon = p.get('location', {}).get('latitude'), p.get('location', {}).get('longitude')
            dist_km = round(geodesic((user_latitude, user_longitude), (lat, lon)).km, 2) if user_latitude and lat else 9999
            dist_meters = int(dist_km * 1000) if dist_km != 9999 else 9999999
            editorial_summary = (p.get('editorialSummary') or {}).get('text')
            primary_type_display = (p.get('primaryTypeDisplayName') or {}).get('text')
            description = editorial_summary or primary_type_display
            
            _place_id = p.get('id')
            web_results.append({
                "businessname": p.get('displayName', {}).get('text'),
                "address": p.get('formattedAddress'),
                "servicedescription": description,
                "mobilenumber": p.get('nationalPhoneNumber'),
                "internationalphonenumber": p.get('internationalPhoneNumber'),
                "rating": p.get('rating'),
                "user_ratings_total": p.get('userRatingCount'),
                "websiteurl": p.get('websiteUri'),
                "place_uri": p.get('googleMapsUri'),
                "providertype": "public",
                "latitude": lat,
                "longitude": lon,
                "distance_km": dist_km,
                "distance_meters": dist_meters,
                "primarytype": [p_type] if p_type else [],
                "types": p.get('types', []),
                "placeid": _place_id,
                "providerservicesid": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pscopy:{_place_id}")) if _place_id else None,
                "source": "public_web",
                "trusted": False
            })
            if contact_id and user_profile_id:
                web_insert_place_ids.append(_place_id)
                web_insert_coros.append(process_and_insert_web_provider(contact_id, user_profile_id, user_name or "User", p))
    if web_insert_coros:
        insert_outcomes = await asyncio.gather(*web_insert_coros, return_exceptions=True)
        unpersisted_place_ids = {
            pid for pid, outcome in zip(web_insert_place_ids, insert_outcomes)
            if pid and (
                isinstance(outcome, Exception)
                or (isinstance(outcome, dict) and outcome.get("status") in ("no_city", "no_phone", "not_india"))
            )
        }
        if unpersisted_place_ids:
            before_city = len(web_results)
            web_results = [w for w in web_results if w.get("placeid") not in unpersisted_place_ids]
            logger.info(
                "[SEARCH_PROVIDER] Dropped %s Places result(s) that could not be persisted "
                "(no city, no phone, outside India, or insert error)",
                before_city - len(web_results),
            )

    if web_results:
        before_ceiling = len(web_results)
        web_distance_ceiling_km = (
            explicit_distance_km if explicit_distance_km is not None else SERVICE_MAX_DISTANCE_KM
        )
        web_results = [
            w for w in web_results
            if not isinstance(w.get("distance_km"), (int, float))
            or w["distance_km"] <= web_distance_ceiling_km
        ]
        if len(web_results) != before_ceiling:
            logger.info(
                "[SEARCH_PROVIDER] Dropped %s Places result(s) beyond the %s km distance ceiling",
                before_ceiling - len(web_results), web_distance_ceiling_km,
            )

    if web_results:
        web_results = await _link_web_results_to_db_providers(web_results)
        _score_web_results_for_ranking(web_results, intent)

    for r in db_results: r["source"] = "database"
    db_names = [d['businessname'] for d in db_results]
    final_results = db_results + [w for w in web_results if w['businessname'] not in db_names]

    if final_results:
        seen_contacts = set()
        seen_place_ids = set()
        seen_business_names = set()
        deduplicated_results = []
        
        for result in final_results:
            contact_id = result.get('contactid')
            place_id = result.get('placeid')
            business_name = result.get('businessname', '').strip().lower()
            
            if contact_id:
                if contact_id not in seen_contacts:
                    seen_contacts.add(contact_id)
                    deduplicated_results.append(result)
            elif place_id:
                if place_id not in seen_place_ids:
                    seen_place_ids.add(place_id)
                    deduplicated_results.append(result)
            elif business_name and business_name not in seen_business_names:
                seen_business_names.add(business_name)
                deduplicated_results.append(result)
                
        final_results = deduplicated_results
    
    suppressed_as_already_shown = 0
    suppressed_rows: List[Dict[str, Any]] = []
    if _shown_ids and final_results:
        before_count = len(final_results)
        kept_rows = []
        for r in final_results:
            if (
                str(r.get('providerservicesid', '')) in _shown_ids
                or str(r.get('contactid', '')) in _shown_ids
            ):
                suppressed_rows.append(r)
            else:
                kept_rows.append(r)
        final_results = kept_rows
        suppressed_as_already_shown = before_count - len(final_results)
        if suppressed_as_already_shown:
            logger.info(
                f"[SEARCH_PROVIDER] Filtered out {suppressed_as_already_shown} previously shown provider(s)"
            )

    if not final_results:
        if suppressed_as_already_shown:
            return [], (
                f"[EVERY MATCH WAS ALREADY SHOWN — do NOT tell the user nothing was found and do "
                f"NOT offer a web search; point them back to the {suppressed_as_already_shown} "
                f"provider(s) already listed above.]\n\n"
                f"Those are all the '{original_query}' providers I have near you — the "
                f"{suppressed_as_already_shown} option(s) I already showed you above. Would you "
                f"like details on any of them, or should I widen the search area?"
            )
        return [], f"I couldn't find any '{original_query}' near you on Etown. Would you like me to perform a web search for you?"

    if not radius_extended:
        standard_km = max_distance_meters / 1000
        in_standard_range = [
            r for r in final_results
            if not isinstance(r.get("distance_km"), (int, float))
            or r["distance_km"] <= standard_km
        ]
        if in_standard_range and len(in_standard_range) < len(final_results):
            logger.info(
                "Dropping %s verified-allowance row(s) beyond %s km: %s in-range match(es) exist.",
                len(final_results) - len(in_standard_range), standard_km, len(in_standard_range),
            )
            final_results = in_standard_range

    final_results = await _enrich_results_with_provider_details(final_results)

    if radius_extended:
        final_distances = [
            r.get("distance_km") for r in final_results
            if isinstance(r.get("distance_km"), (int, float))
        ]
        standard_km = max_distance_meters / 1000
        if final_distances and min(final_distances) <= standard_km:
            in_range = [
                r for r in final_results
                if not isinstance(r.get("distance_km"), (int, float))
                or r["distance_km"] <= standard_km
            ]
            logger.info(
                "Clearing radius_extended: the web-search fallback surfaced an in-range "
                f"provider ({min(final_distances)} km); dropped "
                f"{len(final_results) - len(in_range)} out-of-range row(s)."
            )
            final_results = in_range
            radius_extended = False

    def _is_relevant(result: Dict[str, Any]) -> bool:
        """Whether a row answers the request at all: its trade, its category, or the item.

        A row already filed under a different definite category, showing no item affinity,
        is not answering this request whatever its words say - see _category_conflict.
        """
        if result.get('_category_conflict'):
            return False
        return bool(
            result.get('_matched_trade')
            or result.get('_matched_category')
            or int(result.get('_item_affinity') or 0) > 0
        )

    def _proximity_bonus_km(rows: List[Dict[str, Any]]) -> float:
        """How much proximity a relevance match may buy, given how spread out these are.

        The 75th percentile of the candidate distances: in a dense town that is small and
        the floor applies, in a sparse district it is large, which is where relevance should
        count for more rather than less.
        """
        distances = sorted(
            float(r["distance_km"]) for r in rows
            if isinstance(r.get("distance_km"), (int, float))
        )
        if not distances:
            return AFFINITY_PROXIMITY_BONUS_KM
        p75 = distances[min(int(len(distances) * 0.75), len(distances) - 1)]
        return min(
            max(p75, AFFINITY_PROXIMITY_BONUS_KM), MAX_AFFINITY_PROXIMITY_BONUS_KM
        )

    _bonus_km = _proximity_bonus_km(final_results)

    def _effective_distance_km(result: Dict[str, Any]) -> float:
        """Distance a row is ranked at: its own, less the bonus its item match earns.

        Band 2 (the trade's own words name the item) earns the full bonus, band 1
        (description only) half of it. Floored at zero, so matches inside the bonus 
        all tie there and the real distance below orders them.
        """
        distance = result.get('distance_km')
        if not isinstance(distance, (int, float)):
            return 9999.0
        affinity = int(result.get('_item_affinity') or 0)
        return max(float(distance) - _bonus_km * min(affinity, 2) / 2, 0.0)

    if requests_closest:
        final_results.sort(
            key=lambda result: (
                result.get("distance_km", 9999)
                if isinstance(result.get("distance_km"), (int, float))
                else 9999,
                0 if result.get("source") == "database" else 1,
            )
        )
    else:
        if radius_extended:
            final_results.sort(
                key=lambda x: (
                    0 if x.get('boosted') else 1,
                    0 if _is_relevant(x) else 1,
                    _effective_distance_km(x),
                    x.get('distance_km', 9999),
                )
            )
        else:
            final_results.sort(
                key=lambda x: (
                    0 if x.get('boosted') else 1,
                    0 if _is_relevant(x) else 1,
                    _effective_distance_km(x),
                    x.get('distance_km', 9999),
                    0 if x.get('source') == 'database' else 1,
                    0 if is_provider_verified(x.get('providertype')) else 1,
                    _rating_sort_key(x),
                    int(x.get('_variant_priority', 999)),
                    int(x.get('_vector_rank', 9999)),
                )
            )

    final_results[:limit] = sorted(final_results[:limit], key=distance_sort_key)

    any_result_deals_in_the_item = any(
        int(result.get('_item_affinity') or 0) > 0 for result in final_results
    )

    for result in final_results:
        result.pop('_query_variant', None)
        result.pop('_variant_priority', None)
        result.pop('_vector_rank', None)
        result.pop('_kept_by_cross_script_fallback', None)
        result.pop('_text_relevance', None)
        result.pop('_matched_category', None)
        result.pop('_matched_trade', None)
        result.pop('_item_affinity', None)
        result.pop('_category_conflict', None)
        if 'id' not in result and result.get('providerservicesid'):
            result['id'] = result['providerservicesid']
        bc = str(result.get('businesscategory', '')).lower()
        result['category'] = bc if bc in ('service', 'product') else None
        result['serviceitems'] = _parse_jsonb(result.get('serviceitems'))
        result['Etown_providertype'] = result.pop('providertype', None)
    
    final_results = await _enrich_results_with_subservices(final_results)

    requested_label = intent.trade_terms[0] if intent.trade_terms else original_query.lower()
    describable_terms = _describable_terms(intent)
    found_label = _dominant_result_label(final_results, allowed_terms=describable_terms)
    found_phrase = f"{found_label} " if found_label else ""

    if radius_extended:
        distances = [r.get('distance_km') for r in final_results if isinstance(r.get('distance_km'), (int, float))]
        nearest_km = min(distances) if distances else None
        nearest_note = f" The nearest provider is **{nearest_km:.0f} km away**." if nearest_km is not None else ""
        response_message = (
            f"There are no {requested_label} providers within our standard 25 km range. "
            f"However, I have extended the search to find the closest matches for you.{nearest_note} "
            f"Here are the nearest options below — if you choose to book one, I'll confirm the distance with you first. "
            f"Tap avatar for more details or Call to connect directly with the provider."
        )
    else:
        _distances = [
            r.get('distance_km') for r in final_results
            if isinstance(r.get('distance_km'), (int, float))
        ]
        nearest_km = min(_distances) if _distances else None
        standard_range_km = max_distance_meters / 1000
        beyond_standard_range = nearest_km is not None and nearest_km > standard_range_km
        if beyond_standard_range:
            proximity = f"the closest is **{nearest_km:.0f} km away**"
        else:
            proximity = "nearby"

        if len(final_results) > 1:
            lead = (
                f"I have found several {found_phrase}options for you — {proximity}."
                if beyond_standard_range
                else f"I have found several {proximity} {found_phrase}options for you."
            )
            response_message = (
                f"{lead} You can browse them below and book the one that best fits your "
                f"requirements. Tap avatar for more details or Call to connect directly "
                f"with the service provider."
            )
        else:
            lead = (
                f"I have found a {found_phrase}provider for you — {proximity}."
                if beyond_standard_range
                else f"I have found a {found_phrase}provider near you."
            )
            response_message = (
                f"{lead} You can view the details below and proceed with booking if it "
                f"meets your requirements. Tap avatar for more details or Call to connect "
                f"directly with the service provider."
            )
    _shown_distances = [
        r.get('distance_km') for r in final_results
        if isinstance(r.get('distance_km'), (int, float))
    ]
    if _shown_distances and explicit_distance_km is None:
        response_message = (
            f"[NEVER state a search radius to the user — the radius is an internal retrieval "
            f"parameter, not a guarantee. Quote only the actual distances of these results: "
            f"nearest {min(_shown_distances):.0f} km, furthest {max(_shown_distances):.0f} km.]\n\n"
            f"{response_message}"
        )

    if explicit_distance_km is not None:
        provider_details = "; ".join(
            f"{result.get('businessname', 'Provider')}"
            + (
                f" ({result['distance_km']:.2f} km away)"
                if isinstance(result.get('distance_km'), (int, float))
                else ""
            )
            for result in final_results
        )
        response_message = (
            f"I found these providers within {explicit_distance_km:g} km: {provider_details}. "
            f"You can view each provider's details below and choose one to contact or book."
        )

    if requests_closest:
        closest = next(
            (
                result for result in final_results
                if isinstance(result.get("distance_km"), (int, float))
            ),
            None,
        )
        if closest:
            response_message = (
                f"The closest provider is {closest.get('businessname', 'the first provider')}, "
                f"approximately {closest['distance_km']:.2f} km away. "
                f"I have listed the other matching providers below, ordered by distance."
            )
        else:
            response_message = (
                "I found matching providers, but their distances are unavailable, "
                "so I cannot determine which one is closest."
            )

    if intent.item_terms and not any_result_deals_in_the_item:
        response_message = (
            f"[NONE of these listings mentions '{original_query}' itself — they are the right "
            f"KIND of business nearby, which is the closest match Etown has. Say that plainly: "
            f"present them as the nearest {requested_label} businesses that MIGHT have it and "
            f"suggest the user check, and do NOT state or imply that any of them sells or does "
            f"'{original_query}'.]\n\n{response_message} None of these listings mention "
            f"{intent.item_terms[0]} by name, but they are the closest {requested_label} "
            f"matches nearby, so it is worth checking with them first."
        )

    if requests_date_and_time:
        if requests_weekend_choice:
            response_message += (
                " For your requested weekend, which day works better for you, Saturday or Sunday, and what time?"
            )
        elif named_day:
            response_message += (
                f" For {named_day}, what time would work best for you?"
            )
        else:
            response_message += (
                " What date and time would work best for you?"
            )

    has_ratings = any(result.get('ratingcount', 0) > 0 or result.get('overallrating', 0) > 0 for result in final_results)
    query_lower = original_query.lower()
    if not has_ratings and ('review' in query_lower or 'rating' in query_lower or 'रेटिंग' in query_lower or 'समीक्षा' in query_lower or 'மதிப்பீடு' in query_lower or 'விமர்சனம்' in query_lower):
        response_message += "\n\nNote: Ratings and reviews are not currently available for this business."
    
    task = asyncio.create_task(notify_unverified_providers_background(final_results, searcher_contact_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    
    return final_results[:limit], response_message


chaatie_tools_names = [
    "search_providers",
    "pending_rating",
    "booknow_providers",
    "request_provider",
    "Address_confirmation",
    "generate_qr",
    "create_post",
    "booking_info",
    "perform_web_search",
    "connect_support_agent",
    "provide_etown_info",
    "searchFriendsByName",
    "searchFriendsByCategory",
    "internal_invite"
]

provider_tools_names = [
    "external_invite",
    "get_orders_list"
]

member_tools_names = [
    "external_invite",
    "get_requests_list"
]