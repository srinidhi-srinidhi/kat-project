"""AI Search V2 Utilities - retrieval, ranking and suggestion engine behind v2 search.

Domain layer behind /api/search/ai_search_v2: one SQL statement unions exact-name, full-text, trigram and pgvector retrievers over the whole catalogue, fuses them with Reciprocal Rank Fusion, keeps rows with term evidence, and scores relevance, proximity and trust before enrichment and pagination.

Features:
    - Providers and members are fused and returned as separate lists; member scores omit trust and quality terms
    - Single-Statement Retrieval: Four retrievers in one round trip, one pooled connection
    - Index-Shaped SQL: Parameters passed directly so expression, trigram, GIN and HNSW indexes apply
    - Reciprocal Rank Fusion: Cross-retriever agreement drives the relevance component
    - Blended Ranking: Relevance, IDF coverage, distance decay and trust, with an exact-name override
    - Term-Evidence Filtering: Vector-only rows never qualify; the rarest query term must be matched
    - Place Awareness: An in-memory gazetteer re-centres searches naming a town, city or area
    - Unbounded Reach: No distance cut-off; distance only decays a result's proximity score
    - Caching: Query embeddings, candidate rows, term statistics and the gazetteer, all TTL-bounded
    - Phone Lookup: Digit-ratio detection then an indexable last-10-digits match
    - Typeahead: Fast suggestions from the trigram index and the gazetteer, with no embedding call
    - Result Presentation: Snippets, sibling-listing grouping and query-matched area suggestions
    - Feedback Capture: Impression and click logging for future click-through ranking
    - Honest Degradation: Timeouts and query errors surface as performance_metrics.degraded

Classes:
    No classes defined (module uses function-based utilities)

Functions:
    normalize_text_for_matching(): Normalize text so Python and SQL matching agree
    _query_tokens_for_text_search(): Expand a query into searchable morphological variants
    _load_term_stats(): Cached document frequency per term, for distinctiveness and IDF
    _load_gazetteer(): Cached town, city and area coordinates for place detection
    detect_place(): Find a place name in a query and return the leftover text
    expand_synonyms(): Add domain synonyms the stemmer cannot bridge
    _embed_query(): Cached query embedding as a pgvector literal
    validate_query(): Validate and sanitize search query input
    haversine_km(): Great-circle distance between two coordinates
    _apply_distance_to_provider_results(): Add distance fields to provider results
    _provider_tier_rank(): A provider's paid-tier rank, so it outranks every result below it
    _resolve_user_coordinates(): Resolve caller coordinates from the database
    is_phone_number_query(): Detect whether a query is a phone number
    normalize_phone_number(): Reduce a phone number to its last ten digits
    fetch_provider_profiles_by_contact_ids_bulk(): Bulk-fetch provider profiles by contact ID
    fetch_member_address_details_bulk(): Bulk-fetch member addresses by contact ID
    phone_lookup_search(): Identity lookup across contact, userprofile, member and provider numbers
    bulk_fetch_mutual_connections(): Batch mutual-connection lookup
    mutual_connection_status(): Inject connection status into results
    enrich_profiles_with_rating_data(): Add rating and booking counts
    enrich_profiles_with_mycircle_count(): Add My Circle counts
    extract_city_state(): Parse city and state from an address
    format_provider_results(): Shape provider rows into the response contract
    format_member_results(): Shape member rows into the response contract
    build_keywords_list(): Suggest keywords drawn from matching business text
    extract_cities_from_townprofile_results(): Build the cities list from town rows
    extract_areas_from_provider_results(): Build the areas list from query-matched areas
    detect_query_language(): Derive a source language code from the query's script
    translate_query_to_english(): aimodel translation for non-ASCII queries
    build_snippet(): Choose the excerpt explaining why a result matched
    log_search_events(): Record impressions and clicks
    suggest(): Typeahead suggestions for a partial query
    _cached_candidates(): Run and cache the unified query
    _apply_premium_badges(): Rewrite Etown_providertype to the paid tier's badge label, after ranking
    aisearch(): Entry point - retrieve, fuse, rank, enrich and suggest

Dependencies:
    - re: Text normalization, phone detection and token extraction
    - math: Haversine distance, distance decay and IDF
    - asyncio: Concurrent enrichment and per-stage timeouts
    - logging: Search operation tracking and debugging
    - json: Provider payload parsing
    - time: Stage timing for performance metrics
    - typing: Type hints for complex data structures
    - async_lru: TTL caches for embeddings, candidates, term statistics and the gazetteer
    - fastapi: HTTPException for validation errors
    - app.core.config: Cache sizes, admission limits and the translation endpoint
    - app.db.client: Async PostgreSQL access, including the no-retry fetch_once
    - app.schemas.api: Pydantic models for AreaSuggestion and CityItem
    - app.utils.embeddings: Vector embedding generation and PGVector string formatting
    - app.utils.http_client: Shared pooled HTTP client for translation calls
    - app.etown.plans.plans_utils: premium_display and resolve_provider_badges for badge labels
    - app.etown.provider.provider_review_utils: SQL fragments holding out review-pending providers

Notes:
    Requires scripts/2026_08_{aisearch_v2_indexes,aisearch_v2_ranking,search_term_stats,search_events}.sql.
    Without them the SQL still works but scans, and term distinctiveness uses a static list.
    The term-statistics table needs a periodic refresh as the catalogue grows.

Updates:
    26-May-2026: Added module docstring.
    03-Aug-2026: Reworked for concurrent load; one indexed statement per search.
    05-Aug-2026: Added lexical and fuzzy retrievers, RRF fusion and blended relevance score.
    06-Aug-2026: Results require term evidence.
    11-Aug-2026: Realigned translation with the m2m100 gateway; original script also searched; fixed Indic name matching.
    20-Aug-2026: Removed the 20 km search radius; verified markers report badge label.
    21-Aug-2026: Replaced _is_priority_provider with _provider_tier_rank.
    26-Aug-2026: Logged silent exception handlers with tracebacks.
    31-Aug-2026: Members get lexical and fuzzy legs; translate API moved to POST /v1/translate; wider phone lookup.
    02-Sep-2026: Excludes disabled providers and towns with no live provider.
    17-Sep-2026: Providers awaiting review held out of unified and suggest queries.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    07-Oct-2026: Soft-deleted providers and provider services excluded from unified, suggest and phone queries.
    08-Oct-2026: Removed LangSmith tracing and Sentry reporting.
"""

import re
import math
import asyncio
import logging
import json
import time
from typing import Dict, List, Any, Optional, Tuple
from async_lru import alru_cache
from fastapi import HTTPException
from app.core.config import settings
from app.db.client import Database
from app.schemas.api import AreaSuggestion, CityItem
from app.etown.plans.plans_utils import premium_display, resolve_provider_badges
from app.etown.provider.provider_review_utils import (
    review_pending_exclusion,
    review_pending_provider_ids_cte,
    review_pending_userprofile_exclusion,
)

_EXCLUDE_REVIEW_PENDING_PS = "AND " + review_pending_exclusion("ps.providerid")
_EXCLUDE_REVIEW_PENDING_U = "AND " + review_pending_userprofile_exclusion("u.userprofileid")
_REVIEW_PENDING_CTE = review_pending_provider_ids_cte()
_EXCLUDE_DELETED_PS = (
    "AND ps.deleted IS DISTINCT FROM 'true'\n"
    "              AND NOT EXISTS (SELECT 1 FROM etown_provider dl"
    " WHERE dl.providerid = ps.providerid AND dl.deleted = 'true')"
)
from app.utils.embeddings import generate_embedding, vector_to_postgres_string
from app.utils.http_client import get_http_client

logger = logging.getLogger("app")

FALLBACK_KEYWORD_MIN_LENGTH = 3
FALLBACK_MAX_KEYWORDS = 5
TEXT_SEARCH_STOPWORDS = {"and", "the", "for", "with", "near", "from", "into", "your"}

EMBEDDING_TIMEOUT_SECONDS = 8.0
AISEARCH_SEARCH_TIMEOUT_SECONDS = 5.0

AISEARCH_ANN_OVERFETCH = 4
AISEARCH_CANDIDATE_POOL = 600
AISEARCH_TEXT_LEG_LIMIT = 40

RRF_K = 60
W_RELEVANCE = 0.30
W_COVERAGE = 0.15
W_PROXIMITY = 0.25
W_TRUST = 0.30
W_QUALITY = 0.0
EXACT_MATCH_BOOST = 1.0
PROXIMITY_DECAY_KM = 8.0
NO_DISTANCE_PROXIMITY = 0.35
RESULT_CACHE_HIT_SECONDS = 0.02
SUGGEST_TIMEOUT_SECONDS = 1.0

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

ASCII_PUNCTUATION_PATTERN = r'[!-/:-@[-^`{-~]'
_ASCII_PUNCTUATION_RE = re.compile(ASCII_PUNCTUATION_PATTERN)


def normalize_text_for_matching(text: str) -> str:
    """Lowercase text, replace ASCII punctuation with spaces and collapse whitespace."""
    if not text:
        return ""
    normalized = text.lower().strip()
    normalized = _ASCII_PUNCTUATION_RE.sub(" ", normalized)
    return re.sub(r'\s+', ' ', normalized).strip()


def _query_tokens_for_text_search(query: str) -> List[str]:
    """Return deduplicated query tokens with plural and suffix variants, plus word-order permutations for 2-3 word queries."""
    from itertools import permutations

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
        for perm in permutations(base_tokens):
            joined = " ".join(perm)
            if joined not in result:
                result.append(joined)

    return result


AISEARCH_CONNECTION_TIMEOUT_SECONDS = 1.0
PROVIDER_SIMILARITY_GATE = 0.15
TERM_LEGS = frozenset({'exact', 'lexical', 'fuzzy'})
UBIQUITOUS_DF_FRACTION = 0.02
NAME_LEGS = frozenset({'exact', 'fuzzy'})
TERM_STATS_TTL_SECONDS = 21600.0
FALLBACK_COMMON_TERMS = frozenset({"service", "services", "shop", "store", "best", "near", "me"})


GAZETTEER_TTL_SECONDS = 21600.0
MIN_PLACE_TOKEN_LENGTH = 4
MAX_PLACE_TOKENS = 4
PLACE_PREPOSITIONS = {"in", "at", "near", "around", "nearby", "close", "to"}

SEARCH_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "salon": ("parlour", "beauty parlour"),
    "parlour": ("salon",),
    "medical shop": ("pharmacy", "medicals", "chemist"),
    "pharmacy": ("medical shop", "medicals"),
    "two wheeler": ("bike", "scooter"),
    "cellphone": ("mobile",),
    "mobile": ("cellphone",),
    "grocery": ("supermarket", "provision store"),
    "groceries": ("supermarket", "provision store"),
    "hotel": ("lodge", "restaurant"),
    "doctor": ("clinic", "hospital"),
    "tuition": ("coaching", "tutor"),
}


@alru_cache(maxsize=1, ttl=TERM_STATS_TTL_SECONDS)
async def _load_term_stats() -> Tuple[Dict[str, int], int]:
    """Load per-term listing counts and corpus size from etown_search_term_stats, or ({}, 0) on failure."""
    try:
        rows = await Database.fetch(
            "SELECT term, ndoc, total_docs FROM etown_search_term_stats"
        )
    except Exception as e:
        logger.warning(f"Term stats unavailable, falling back to the static list: {e}")
        return {}, 0
    if not rows:
        return {}, 0
    stats = {r["term"]: int(r["ndoc"]) for r in rows}
    total = int(rows[0]["total_docs"]) or 0
    logger.info("TERM STATS loaded | %d terms over %d listings", len(stats), total)
    return stats, total


@alru_cache(maxsize=1, ttl=GAZETTEER_TTL_SECONDS)
async def _load_gazetteer() -> Dict[str, Tuple[float, float, str, str]]:
    """Load towns, cities and areas as normalized name to (lat, lon, kind, display), towns winning ties; empty on failure."""
    gazetteer: Dict[str, Tuple[float, float, str, str]] = {}
    try:
        towns = await Database.fetch(
            """
            SELECT townname AS name, cityname,
                   townlatitude::double precision AS lat, townlongitude::double precision AS lon
            FROM etown_townprofile
            WHERE townname IS NOT NULL AND townlatitude IS NOT NULL AND townlongitude IS NOT NULL
            """
        )
        cities = await Database.fetch(
            """
            SELECT btrim(city) AS name, avg(latitude) AS lat, avg(longitude) AS lon
            FROM etown_providerservices_copy
            WHERE city IS NOT NULL AND btrim(city) <> '' AND latitude IS NOT NULL
            GROUP BY 1 HAVING count(*) >= 3
            """
        )
        areas = await Database.fetch(
            """
            SELECT btrim(areaname) AS name, avg(latitude) AS lat, avg(longitude) AS lon
            FROM etown_providerservices_copy
            WHERE areaname IS NOT NULL AND btrim(areaname) <> '' AND latitude IS NOT NULL
            GROUP BY 1 HAVING count(*) >= 5
            """
        )
    except Exception as e:
        logger.error(f"Gazetteer load failed, place detection disabled: {e}", exc_info=True)
        return {}

    for kind, rows in (("town", towns), ("city", cities), ("area", areas)):
        for row in rows:
            key = normalize_text_for_matching(row["name"] or "")
            if len(key) < MIN_PLACE_TOKEN_LENGTH or key in gazetteer:
                continue
            try:
                gazetteer[key] = (float(row["lat"]), float(row["lon"]), kind, row["name"])
            except (TypeError, ValueError):
                logger.debug("aisearch v2: gazetteer row has non-numeric coordinates; skipping it")
                continue
    logger.info("GAZETTEER loaded | %d places", len(gazetteer))
    return gazetteer


def detect_place(normalized_query: str, gazetteer: Dict[str, Tuple[float, float, str, str]]):
    """Return the longest gazetteer place found in the query, or None, and the query without it and edge prepositions."""
    if not normalized_query or not gazetteer:
        return None, normalized_query

    tokens = normalized_query.split()
    for size in range(min(MAX_PLACE_TOKENS, len(tokens)), 0, -1):
        for start in range(len(tokens) - size + 1):
            candidate = " ".join(tokens[start:start + size])
            if len(candidate) < MIN_PLACE_TOKEN_LENGTH:
                continue
            hit = gazetteer.get(candidate)
            if not hit:
                continue
            residual = tokens[:start] + tokens[start + size:]
            while residual and residual[-1] in PLACE_PREPOSITIONS:
                residual.pop()
            while residual and residual[0] in PLACE_PREPOSITIONS:
                residual.pop(0)
            return hit, " ".join(residual)
    return None, normalized_query


def expand_synonyms(normalized_query: str) -> str:
    """Return the query with synonyms of any contained known terms appended."""
    extras: List[str] = []
    for term, alternatives in SEARCH_SYNONYMS.items():
        if term and term in normalized_query:
            extras.extend(a for a in alternatives if a not in normalized_query)
    if not extras:
        return normalized_query
    return normalized_query + " " + " ".join(dict.fromkeys(extras))


@alru_cache(
    maxsize=max(1, settings.AISEARCH_EMBEDDING_CACHE_SIZE),
    ttl=settings.AISEARCH_EMBEDDING_CACHE_TTL_SECONDS,
)
async def _embed_query(normalized_query: str) -> str:
    """Embed a normalized query with a timeout and return it as a pgvector literal.

    Raises:
        Exception: Propagated from the embedding provider, including a timeout.
    """
    vector = await asyncio.wait_for(
        generate_embedding(normalized_query), timeout=EMBEDDING_TIMEOUT_SECONDS
    )
    return vector_to_postgres_string(vector)


def validate_query(query: str) -> str:
    """Return the stripped query after rejecting empty or gibberish input.

    Raises:
        HTTPException: 400 when the query is empty, symbols only, over 10 digits, or repetitive.
    """
    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    cleaned_query = query.strip()
    q = cleaned_query.lower()

    has_letters = any(c.isalpha() for c in q)
    has_digits = re.search(r"[0-9]", q)

    if not has_letters:
        if not has_digits:
            raise HTTPException(status_code=400, detail="Query appears to be invalid or gibberish")
        else:
            digit_count = len(re.findall(r"[0-9]", q))
            if digit_count > 10:
                raise HTTPException(status_code=400, detail="Query appears to be invalid or gibberish")

    if re.search(r"(.)\1{4,}", q):
        raise HTTPException(status_code=400, detail="Query appears to be invalid or gibberish")

    if len(q) >= 6:
        half = len(q) // 2
        if q[:half] == q[half:]:
            raise HTTPException(status_code=400, detail="Query appears to be invalid or gibberish")

    return cleaned_query


AISEARCH_COORD_LOOKUP_TIMEOUT_SECONDS = 1.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the haversine great-circle distance between two points in kilometers."""
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
    """Set Etown_distance_km and Etown_distance_meters on each provider row in place when user coordinates are given."""
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
            logger.debug("aisearch v2: distance could not be computed for a provider row; leaving it unset", exc_info=True)
            provider["Etown_distance_km"] = None
            provider["Etown_distance_meters"] = None


def _provider_tier_rank(
    provider: Dict[str, Any],
    badge_map: Dict[str, Dict[str, Optional[str]]],
) -> int:
    """Return the provider's paid-tier rank from the badge map, or 0 for the free tier."""
    return (badge_map.get(provider.get("Etown_userProfileId")) or {}).get("tier_rank") or 0


async def _resolve_user_coordinates(
    contact_id: Optional[str],
    user_profile_id: Optional[str],
) -> Optional[Tuple[float, float]]:
    """Return the user's stored coordinates from contact, profile, provider or service rows in that order, or None."""
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
        logger.warning("aisearch v2: user coordinate lookup failed or timed out", exc_info=True)
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
        logger.warning("aisearch v2: stored user coordinates are not numeric", exc_info=True)
        return None


COMBINED_UNIFIED_QUERY = r"""
WITH

retired_ids AS MATERIALIZED (
    SELECT identity_id FROM etown_retired_identity
     WHERE deleted = 'true'
        OR (deactivated = 'true' AND reactivation IS DISTINCT FROM 'true')
),
disabled_provider_ids AS MATERIALIZED (
    SELECT userprofileid::text AS identity_id
      FROM etown_provider
     WHERE disabled
),
deleted_provider_ids AS MATERIALIZED (
    SELECT providerid::text AS providerid
      FROM etown_provider
     WHERE deleted = 'true'
),
""" + _REVIEW_PENDING_CTE + r""",
live_towns AS MATERIALIZED (
    SELECT DISTINCT
           ep.townprofileid                                             AS town_id,
           CASE WHEN ep.providerid IS NULL
                THEN lower(btrim(p.city)) END                           AS town_name
      FROM etown_provider p
      LEFT JOIN etown_etownprovider ep
             ON ep.providerid = p.providerid
            AND COALESCE(ep.townprofileid, '') <> ''
     WHERE p.deleted IS DISTINCT FROM 'true'
       AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
       AND p.disabled IS NOT TRUE
),
combined_results AS (

    (
        SELECT
            'providerservice'::text        AS source,
            ps.providerid::text            AS id,
            ps.contactid::text             AS contactid,
            ps.userprofileid::text         AS userprofileid,
            ps.businessname::text          AS name,
            ps.businessname::text          AS businessname,
            ps.businesscategory::text      AS businesscategory,
            ps.services::text              AS services,
            ps.servicedescription::text    AS servicedescription,
            ps.contactidname::text         AS contactidname,
            ps.providertype::text          AS providertype,
            ps.publicsearchprovider::text  AS publicsearchprovider,
            ps.availability::text          AS availability,
            ps.areaname::text              AS areaname,
            ps.serviceimageurl::text       AS profileimage,
            ps.mobilenumber::text          AS mobilephone,
            ps.email::text                 AS email,
            ps.address::text               AS address,
            ps.city::text                  AS city,
            ps.statecode::text             AS statecode,
            NULL::text                     AS townname,
            ps.latitude::double precision  AS latitude,
            ps.longitude::double precision AS longitude,
            NULL::text                     AS rolecodename,
            NULL::text                     AS publicsearchstatus,
            1::integer                     AS role,
            'Provider'::text               AS rolename,
            ps.createdon::timestamp        AS createdon,
            ps.source::text                AS provenance,
            'exact'::text                  AS leg,
            -1.0::double precision         AS similarity
        FROM etown_providerservices_copy ps
        WHERE $7 <> ''
          AND ps.deleted IS DISTINCT FROM 'true'
          AND ('providerservice' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
          AND trim(regexp_replace(regexp_replace(lower(ps.businessname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')) = $7
        LIMIT $3
    )

    UNION ALL

    (
        SELECT
            'provider'::text                 AS source,
            pv.providerid::text              AS id,
            pv.contactid::text               AS contactid,
            pv.userprofileid::text           AS userprofileid,
            pv.businessname::text            AS name,
            pv.businessname::text            AS businessname,
            pv.businesscategory::text        AS businesscategory,
            NULL::text                       AS services,
            pv.description::text             AS servicedescription,
            pv.contactidname::text           AS contactidname,
            pv.providertype::text            AS providertype,
            pv.publicsearchprovider::text    AS publicsearchprovider,
            pv.availability::text            AS availability,
            pv.areaname::text                AS areaname,
            pv.serviceimageurl::text         AS profileimage,
            pv.mobilenumber::text            AS mobilephone,
            pv.email::text                   AS email,
            pv.address::text                 AS address,
            pv.city::text                    AS city,
            pv.statecode::text               AS statecode,
            pv.townname::text                AS townname,
            pv.latitude::double precision    AS latitude,
            pv.longitude::double precision   AS longitude,
            NULL::text                       AS rolecodename,
            NULL::text                       AS publicsearchstatus,
            COALESCE(pv.role, 1)::integer    AS role,
            COALESCE(pv.rolename, 'Provider')::text AS rolename,
            pv.creationdate::timestamp       AS createdon,
            pv.source::text                AS provenance,
            'exact'::text                  AS leg,
            -1.0::double precision           AS similarity
        FROM etown_provider pv
        WHERE $7 <> ''
          AND pv.deleted IS DISTINCT FROM 'true'
          AND ('provider' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
          AND trim(regexp_replace(regexp_replace(lower(pv.businessname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')) = $7
        LIMIT $3
    )

    UNION ALL

    (
        SELECT
            'member'::text                  AS source,
            m.memberid::text                AS id,
            c.contactid::text               AS contactid,
            m.userprofileid::text           AS userprofileid,
            m.userprofileidname::text       AS name,
            NULL::text                      AS businessname,
            NULL::text                      AS businesscategory,
            NULL::text                      AS services,
            NULL::text                      AS servicedescription,
            m.contactidname::text           AS contactidname,
            NULL::text                      AS providertype,
            NULL::text                      AS publicsearchprovider,
            NULL::text                      AS availability,
            NULL::text                      AS areaname,
            c.profileimage::text            AS profileimage,
            c.mobilephone::text             AS mobilephone,
            c.email::text                   AS email,
            c.address::text                 AS address,
            c.city::text                    AS city,
            c.statecode::text               AS statecode,
            NULL::text                      AS townname,
            c.latitude::double precision    AS latitude,
            c.longitude::double precision   AS longitude,
            c.rolecodename::text            AS rolecodename,
            c.publicsearchstatus::text      AS publicsearchstatus,
            COALESCE(m.role, 2)::integer    AS role,
            COALESCE(m.rolename, 'Member')::text AS rolename,
            c.createdon::timestamp          AS createdon,
            NULL::text                     AS provenance,
            'exact'::text                  AS leg,
            -1.0::double precision          AS similarity
        FROM etown_member m
        JOIN etown_contact c ON m.contactid = c.contactid
        WHERE $7 <> ''
          AND ('member' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
          AND trim(regexp_replace(regexp_replace(lower(m.userprofileidname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')) = $7
          AND c.contactid::text <> COALESCE($2, '')
          AND c.verified IS NOT FALSE
        LIMIT $3
    )

    UNION ALL

    (
        SELECT
            'contact'::text                 AS source,
            c.contactid::text               AS id,
            c.contactid::text               AS contactid,
            u.userprofileid::text           AS userprofileid,
            c.firstname::text               AS name,
            NULL::text                      AS businessname,
            NULL::text                      AS businesscategory,
            NULL::text                      AS services,
            NULL::text                      AS servicedescription,
            NULL::text                      AS contactidname,
            NULL::text                      AS providertype,
            NULL::text                      AS publicsearchprovider,
            NULL::text                      AS availability,
            NULL::text                      AS areaname,
            c.profileimage::text            AS profileimage,
            c.mobilephone::text             AS mobilephone,
            c.email::text                   AS email,
            c.address::text                 AS address,
            c.city::text                    AS city,
            c.statecode::text               AS statecode,
            NULL::text                      AS townname,
            c.latitude::double precision    AS latitude,
            c.longitude::double precision   AS longitude,
            c.rolecodename::text            AS rolecodename,
            c.publicsearchstatus::text      AS publicsearchstatus,
            u.role::integer                 AS role,
            u.rolename::text                AS rolename,
            c.createdon::timestamp          AS createdon,
            NULL::text                     AS provenance,
            'exact'::text                  AS leg,
            -1.0::double precision          AS similarity
        FROM etown_contact c
        JOIN etown_userprofile u ON c.contactid = u.contactid
        WHERE $7 <> ''
          AND ('contact' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
          AND trim(regexp_replace(regexp_replace(lower(c.firstname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')) = $7
          AND c.contactid::text <> COALESCE($2, '')
          AND u.role = 2
        LIMIT $3
    )

    UNION ALL

    (
        SELECT
            'providerservice'::text        AS source,
            ps.providerid::text            AS id,
            ps.contactid::text             AS contactid,
            ps.userprofileid::text         AS userprofileid,
            ps.businessname::text          AS name,
            ps.businessname::text          AS businessname,
            ps.businesscategory::text      AS businesscategory,
            ps.services::text              AS services,
            ps.servicedescription::text    AS servicedescription,
            ps.contactidname::text         AS contactidname,
            ps.providertype::text          AS providertype,
            ps.publicsearchprovider::text  AS publicsearchprovider,
            ps.availability::text          AS availability,
            ps.areaname::text              AS areaname,
            ps.serviceimageurl::text       AS profileimage,
            ps.mobilenumber::text          AS mobilephone,
            ps.email::text                 AS email,
            ps.address::text               AS address,
            ps.city::text                  AS city,
            ps.statecode::text             AS statecode,
            NULL::text                     AS townname,
            ps.latitude::double precision  AS latitude,
            ps.longitude::double precision AS longitude,
            NULL::text                     AS rolecodename,
            NULL::text                     AS publicsearchstatus,
            1::integer                     AS role,
            'Provider'::text               AS rolename,
            ps.createdon::timestamp        AS createdon,
            ps.source::text                AS provenance,
            'vector'::text                 AS leg,
            (ps.content_vector <#> $1::vector) AS similarity
        FROM etown_providerservices_copy ps
        WHERE $1::vector IS NOT NULL
          AND ps.deleted IS DISTINCT FROM 'true'
          AND ('providerservice' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
        ORDER BY ps.content_vector <#> $1::vector
        LIMIT $3
    )

    UNION ALL

    (
        SELECT
            'provider'::text                 AS source,
            pv.providerid::text              AS id,
            pv.contactid::text               AS contactid,
            pv.userprofileid::text           AS userprofileid,
            pv.businessname::text            AS name,
            pv.businessname::text            AS businessname,
            pv.businesscategory::text        AS businesscategory,
            NULL::text                       AS services,
            pv.description::text             AS servicedescription,
            pv.contactidname::text           AS contactidname,
            pv.providertype::text            AS providertype,
            pv.publicsearchprovider::text    AS publicsearchprovider,
            pv.availability::text            AS availability,
            pv.areaname::text                AS areaname,
            pv.serviceimageurl::text         AS profileimage,
            pv.mobilenumber::text            AS mobilephone,
            pv.email::text                   AS email,
            pv.address::text                 AS address,
            pv.city::text                    AS city,
            pv.statecode::text               AS statecode,
            pv.townname::text                AS townname,
            pv.latitude::double precision    AS latitude,
            pv.longitude::double precision   AS longitude,
            NULL::text                       AS rolecodename,
            NULL::text                       AS publicsearchstatus,
            COALESCE(pv.role, 1)::integer    AS role,
            COALESCE(pv.rolename, 'Provider')::text AS rolename,
            pv.creationdate::timestamp       AS createdon,
            pv.source::text                AS provenance,
            'vector'::text                 AS leg,
            (pv.content_vector <#> $1::vector) AS similarity
        FROM etown_provider pv
        WHERE $1::vector IS NOT NULL
          AND pv.deleted IS DISTINCT FROM 'true'
          AND ('provider' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
        ORDER BY pv.content_vector <#> $1::vector
        LIMIT $3
    )

    UNION ALL

    (
        SELECT
            'contact'::text                 AS source,
            c.contactid::text               AS id,
            c.contactid::text               AS contactid,
            u.userprofileid::text           AS userprofileid,
            c.firstname::text               AS name,
            NULL::text                      AS businessname,
            NULL::text                      AS businesscategory,
            NULL::text                      AS services,
            NULL::text                      AS servicedescription,
            NULL::text                      AS contactidname,
            NULL::text                      AS providertype,
            NULL::text                      AS publicsearchprovider,
            NULL::text                      AS availability,
            NULL::text                      AS areaname,
            c.profileimage::text            AS profileimage,
            c.mobilephone::text             AS mobilephone,
            c.email::text                   AS email,
            c.address::text                 AS address,
            c.city::text                    AS city,
            c.statecode::text               AS statecode,
            NULL::text                      AS townname,
            c.latitude::double precision    AS latitude,
            c.longitude::double precision   AS longitude,
            c.rolecodename::text            AS rolecodename,
            c.publicsearchstatus::text      AS publicsearchstatus,
            u.role::integer                 AS role,
            u.rolename::text                AS rolename,
            c.createdon::timestamp          AS createdon,
            NULL::text                     AS provenance,
            'vector'::text                 AS leg,
            c.similarity                    AS similarity
        FROM (
            SELECT
                ct.contactid, ct.firstname, ct.profileimage, ct.mobilephone, ct.email,
                ct.address, ct.city, ct.statecode, ct.latitude, ct.longitude,
                ct.rolecodename, ct.publicsearchstatus, ct.createdon,
                (ct.content_vector <#> $1::vector) AS similarity
            FROM etown_contact ct
            WHERE $1::vector IS NOT NULL
              AND ('contact' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
            ORDER BY ct.content_vector <#> $1::vector
            LIMIT $8
        ) c
        JOIN etown_userprofile u ON c.contactid = u.contactid
        WHERE c.latitude IS NOT NULL
          AND c.longitude IS NOT NULL
          AND c.contactid::text <> COALESCE($2, '')
          AND u.role = 2
        ORDER BY c.similarity ASC
        LIMIT $3
    )

    UNION ALL

    (
        SELECT
            'member'::text                  AS source,
            m.memberid::text                AS id,
            c.contactid::text               AS contactid,
            m.userprofileid::text           AS userprofileid,
            m.userprofileidname::text       AS name,
            NULL::text                      AS businessname,
            NULL::text                      AS businesscategory,
            NULL::text                      AS services,
            NULL::text                      AS servicedescription,
            m.contactidname::text           AS contactidname,
            NULL::text                      AS providertype,
            NULL::text                      AS publicsearchprovider,
            NULL::text                      AS availability,
            NULL::text                      AS areaname,
            c.profileimage::text            AS profileimage,
            c.mobilephone::text             AS mobilephone,
            c.email::text                   AS email,
            c.address::text                 AS address,
            c.city::text                    AS city,
            c.statecode::text               AS statecode,
            NULL::text                      AS townname,
            c.latitude::double precision    AS latitude,
            c.longitude::double precision   AS longitude,
            c.rolecodename::text            AS rolecodename,
            c.publicsearchstatus::text      AS publicsearchstatus,
            COALESCE(m.role, 2)::integer    AS role,
            COALESCE(m.rolename, 'Member')::text AS rolename,
            c.createdon::timestamp          AS createdon,
            NULL::text                     AS provenance,
            'vector'::text                 AS leg,
            m.similarity                    AS similarity
        FROM (
            SELECT
                mb.memberid, mb.userprofileid, mb.userprofileidname, mb.contactid,
                mb.contactidname, mb.role, mb.rolename,
                (mb.content_vector <#> $1::vector) AS similarity
            FROM etown_member mb
            WHERE $1::vector IS NOT NULL
              AND ('member' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
            ORDER BY mb.content_vector <#> $1::vector
            LIMIT $8
        ) m
        JOIN etown_contact c ON m.contactid = c.contactid
        WHERE c.latitude IS NOT NULL
          AND c.longitude IS NOT NULL
          AND c.verified IS NOT FALSE
          AND c.contactid::text <> COALESCE($2, '')
        ORDER BY m.similarity ASC
        LIMIT $3
    )

    UNION ALL

    (
        SELECT
            'townprofile'::text                  AS source,
            t.townprofileid::text                AS id,
            NULL::text                           AS contactid,
            NULL::text                           AS userprofileid,
            t.townname::text                     AS name,
            NULL::text                           AS businessname,
            NULL::text                           AS businesscategory,
            NULL::text                           AS services,
            t.towndescription::text              AS servicedescription,
            NULL::text                           AS contactidname,
            NULL::text                           AS providertype,
            NULL::text                           AS publicsearchprovider,
            NULL::text                           AS availability,
            NULL::text                           AS areaname,
            t.townimage::text                    AS profileimage,
            NULL::text                           AS mobilephone,
            NULL::text                           AS email,
            NULL::text                           AS address,
            t.cityname::text                     AS city,
            NULL::text                           AS statecode,
            t.townname::text                     AS townname,
            t.townlatitude::double precision     AS latitude,
            t.townlongitude::double precision    AS longitude,
            NULL::text                           AS rolecodename,
            NULL::text                           AS publicsearchstatus,
            NULL::integer                        AS role,
            NULL::text                           AS rolename,
            t.createdon::timestamp               AS createdon,
            NULL::text                     AS provenance,
            'vector'::text                 AS leg,
            t.similarity                         AS similarity
        FROM (
            SELECT
                tp.townprofileid, tp.townname, tp.cityname, tp.townimage,
                tp.towndescription, tp.townlatitude, tp.townlongitude, tp.createdon,
                (tp.content_vector <#> $1::vector) AS similarity
            FROM etown_townprofile tp
            WHERE $1::vector IS NOT NULL
              AND ('townprofile' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
            ORDER BY tp.content_vector <#> $1::vector
            LIMIT $8
        ) t
        WHERE t.townlatitude IS NOT NULL
          AND t.townlongitude IS NOT NULL
          AND (
                t.townprofileid IN (SELECT town_id   FROM live_towns WHERE town_id   IS NOT NULL)
             OR lower(btrim(t.townname))
                                IN (SELECT town_name FROM live_towns WHERE town_name IS NOT NULL)
          )
        LIMIT $3
    )
    UNION ALL

    (
        SELECT
            'providerservice'::text        AS source,
            ps.providerid::text            AS id,
            ps.contactid::text             AS contactid,
            ps.userprofileid::text         AS userprofileid,
            ps.businessname::text          AS name,
            ps.businessname::text          AS businessname,
            ps.businesscategory::text      AS businesscategory,
            ps.services::text              AS services,
            ps.servicedescription::text    AS servicedescription,
            ps.contactidname::text         AS contactidname,
            ps.providertype::text          AS providertype,
            ps.publicsearchprovider::text  AS publicsearchprovider,
            ps.availability::text          AS availability,
            ps.areaname::text              AS areaname,
            ps.serviceimageurl::text       AS profileimage,
            ps.mobilenumber::text          AS mobilephone,
            ps.email::text                 AS email,
            ps.address::text               AS address,
            ps.city::text                  AS city,
            ps.statecode::text             AS statecode,
            NULL::text                     AS townname,
            ps.latitude::double precision  AS latitude,
            ps.longitude::double precision AS longitude,
            NULL::text                     AS rolecodename,
            NULL::text                     AS publicsearchstatus,
            1::integer                     AS role,
            'Provider'::text               AS rolename,
            ps.createdon::timestamp        AS createdon,
            ps.source::text                AS provenance,
            'lexical'::text                AS leg,
            -ts_rank_cd(to_tsvector('english',
                coalesce(ps.businessname, '') || ' ' || coalesce(ps.services, '') || ' ' ||
                coalesce(ps.areaname, '') || ' ' || coalesce(ps.city, '')), websearch_to_tsquery('english', $9))::double precision AS similarity
        FROM etown_providerservices_copy ps
        WHERE $9 <> ''
          AND ps.deleted IS DISTINCT FROM 'true'
          AND ('providerservice' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
          AND to_tsvector('english',
                coalesce(ps.businessname, '') || ' ' || coalesce(ps.services, '') || ' ' ||
                coalesce(ps.areaname, '') || ' ' || coalesce(ps.city, '')) @@ websearch_to_tsquery('english', $9)
        ORDER BY similarity ASC
        LIMIT $10
    )

    UNION ALL

    (
        SELECT
            'provider'::text                 AS source,
            pv.providerid::text              AS id,
            pv.contactid::text               AS contactid,
            pv.userprofileid::text           AS userprofileid,
            pv.businessname::text            AS name,
            pv.businessname::text            AS businessname,
            pv.businesscategory::text        AS businesscategory,
            NULL::text                       AS services,
            pv.description::text             AS servicedescription,
            pv.contactidname::text           AS contactidname,
            pv.providertype::text            AS providertype,
            pv.publicsearchprovider::text    AS publicsearchprovider,
            pv.availability::text            AS availability,
            pv.areaname::text                AS areaname,
            pv.serviceimageurl::text         AS profileimage,
            pv.mobilenumber::text            AS mobilephone,
            pv.email::text                   AS email,
            pv.address::text                 AS address,
            pv.city::text                    AS city,
            pv.statecode::text               AS statecode,
            pv.townname::text                AS townname,
            pv.latitude::double precision    AS latitude,
            pv.longitude::double precision   AS longitude,
            NULL::text                       AS rolecodename,
            NULL::text                       AS publicsearchstatus,
            COALESCE(pv.role, 1)::integer    AS role,
            COALESCE(pv.rolename, 'Provider')::text AS rolename,
            pv.creationdate::timestamp       AS createdon,
            pv.source::text                AS provenance,
            'lexical'::text                  AS leg,
            -ts_rank_cd(to_tsvector('english',
                coalesce(pv.businessname, '') || ' ' || coalesce(pv.areaname, '') || ' ' ||
                coalesce(pv.city, '')), websearch_to_tsquery('english', $9))::double precision AS similarity
        FROM etown_provider pv
        WHERE $9 <> ''
          AND pv.deleted IS DISTINCT FROM 'true'
          AND ('provider' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
          AND to_tsvector('english',
                coalesce(pv.businessname, '') || ' ' || coalesce(pv.areaname, '') || ' ' ||
                coalesce(pv.city, '')) @@ websearch_to_tsquery('english', $9)
        ORDER BY similarity ASC
        LIMIT $10
    )

    UNION ALL

    (
        SELECT
            'providerservice'::text        AS source,
            ps.providerid::text            AS id,
            ps.contactid::text             AS contactid,
            ps.userprofileid::text         AS userprofileid,
            ps.businessname::text          AS name,
            ps.businessname::text          AS businessname,
            ps.businesscategory::text      AS businesscategory,
            ps.services::text              AS services,
            ps.servicedescription::text    AS servicedescription,
            ps.contactidname::text         AS contactidname,
            ps.providertype::text          AS providertype,
            ps.publicsearchprovider::text  AS publicsearchprovider,
            ps.availability::text          AS availability,
            ps.areaname::text              AS areaname,
            ps.serviceimageurl::text       AS profileimage,
            ps.mobilenumber::text          AS mobilephone,
            ps.email::text                 AS email,
            ps.address::text               AS address,
            ps.city::text                  AS city,
            ps.statecode::text             AS statecode,
            NULL::text                     AS townname,
            ps.latitude::double precision  AS latitude,
            ps.longitude::double precision AS longitude,
            NULL::text                     AS rolecodename,
            NULL::text                     AS publicsearchstatus,
            1::integer                     AS role,
            'Provider'::text               AS rolename,
            ps.createdon::timestamp        AS createdon,
            ps.source::text                AS provenance,
            'fuzzy'::text                  AS leg,
            -similarity(trim(regexp_replace(regexp_replace(lower(ps.businessname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')), $7)::double precision AS similarity
        FROM etown_providerservices_copy ps
        WHERE $7 <> ''
          AND ps.deleted IS DISTINCT FROM 'true'
          AND ('providerservice' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
          AND trim(regexp_replace(regexp_replace(lower(ps.businessname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')) % $7
        ORDER BY similarity ASC
        LIMIT $10
    )

    UNION ALL

    (
        SELECT
            'provider'::text                 AS source,
            pv.providerid::text              AS id,
            pv.contactid::text               AS contactid,
            pv.userprofileid::text           AS userprofileid,
            pv.businessname::text            AS name,
            pv.businessname::text            AS businessname,
            pv.businesscategory::text        AS businesscategory,
            NULL::text                       AS services,
            pv.description::text             AS servicedescription,
            pv.contactidname::text           AS contactidname,
            pv.providertype::text            AS providertype,
            pv.publicsearchprovider::text    AS publicsearchprovider,
            pv.availability::text            AS availability,
            pv.areaname::text                AS areaname,
            pv.serviceimageurl::text         AS profileimage,
            pv.mobilenumber::text            AS mobilephone,
            pv.email::text                   AS email,
            pv.address::text                 AS address,
            pv.city::text                    AS city,
            pv.statecode::text               AS statecode,
            pv.townname::text                AS townname,
            pv.latitude::double precision    AS latitude,
            pv.longitude::double precision   AS longitude,
            NULL::text                       AS rolecodename,
            NULL::text                       AS publicsearchstatus,
            COALESCE(pv.role, 1)::integer    AS role,
            COALESCE(pv.rolename, 'Provider')::text AS rolename,
            pv.creationdate::timestamp       AS createdon,
            pv.source::text                AS provenance,
            'fuzzy'::text                    AS leg,
            -similarity(trim(regexp_replace(regexp_replace(lower(pv.businessname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')), $7)::double precision AS similarity
        FROM etown_provider pv
        WHERE $7 <> ''
          AND pv.deleted IS DISTINCT FROM 'true'
          AND ('provider' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
          AND trim(regexp_replace(regexp_replace(lower(pv.businessname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')) % $7
        ORDER BY similarity ASC
        LIMIT $10
    )

    UNION ALL

    (
        SELECT
            'member'::text                  AS source,
            m.memberid::text                AS id,
            c.contactid::text               AS contactid,
            m.userprofileid::text           AS userprofileid,
            m.userprofileidname::text       AS name,
            NULL::text                      AS businessname,
            NULL::text                      AS businesscategory,
            NULL::text                      AS services,
            NULL::text                      AS servicedescription,
            m.contactidname::text           AS contactidname,
            NULL::text                      AS providertype,
            NULL::text                      AS publicsearchprovider,
            NULL::text                      AS availability,
            NULL::text                      AS areaname,
            c.profileimage::text            AS profileimage,
            c.mobilephone::text             AS mobilephone,
            c.email::text                   AS email,
            c.address::text                 AS address,
            c.city::text                    AS city,
            c.statecode::text               AS statecode,
            NULL::text                      AS townname,
            c.latitude::double precision    AS latitude,
            c.longitude::double precision   AS longitude,
            c.rolecodename::text            AS rolecodename,
            c.publicsearchstatus::text      AS publicsearchstatus,
            COALESCE(m.role, 2)::integer    AS role,
            COALESCE(m.rolename, 'Member')::text AS rolename,
            c.createdon::timestamp          AS createdon,
            NULL::text                     AS provenance,
            'lexical'::text                 AS leg,
            -ts_rank_cd(to_tsvector('english',
                coalesce(m.userprofileidname, '')), websearch_to_tsquery('english', $9))::double precision AS similarity
        FROM etown_member m
        JOIN etown_contact c ON m.contactid = c.contactid
        WHERE $9 <> ''
          AND ('member' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
          AND to_tsvector('english',
                coalesce(m.userprofileidname, '')) @@ websearch_to_tsquery('english', $9)
          AND c.contactid::text <> COALESCE($2, '')
          AND c.verified IS NOT FALSE
        ORDER BY similarity ASC
        LIMIT $10
    )

    UNION ALL

    (
        SELECT
            'contact'::text                 AS source,
            c.contactid::text               AS id,
            c.contactid::text               AS contactid,
            u.userprofileid::text           AS userprofileid,
            c.firstname::text               AS name,
            NULL::text                      AS businessname,
            NULL::text                      AS businesscategory,
            NULL::text                      AS services,
            NULL::text                      AS servicedescription,
            NULL::text                      AS contactidname,
            NULL::text                      AS providertype,
            NULL::text                      AS publicsearchprovider,
            NULL::text                      AS availability,
            NULL::text                      AS areaname,
            c.profileimage::text            AS profileimage,
            c.mobilephone::text             AS mobilephone,
            c.email::text                   AS email,
            c.address::text                 AS address,
            c.city::text                    AS city,
            c.statecode::text               AS statecode,
            NULL::text                      AS townname,
            c.latitude::double precision    AS latitude,
            c.longitude::double precision   AS longitude,
            c.rolecodename::text            AS rolecodename,
            c.publicsearchstatus::text      AS publicsearchstatus,
            u.role::integer                 AS role,
            u.rolename::text                AS rolename,
            c.createdon::timestamp          AS createdon,
            NULL::text                     AS provenance,
            'lexical'::text                 AS leg,
            -ts_rank_cd(to_tsvector('english',
                coalesce(c.firstname, '') || ' ' || coalesce(c.city, '')), websearch_to_tsquery('english', $9))::double precision AS similarity
        FROM etown_contact c
        JOIN etown_userprofile u ON c.contactid = u.contactid
        WHERE $9 <> ''
          AND ('contact' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
          AND to_tsvector('english',
                coalesce(c.firstname, '') || ' ' || coalesce(c.city, '')) @@ websearch_to_tsquery('english', $9)
          AND c.contactid::text <> COALESCE($2, '')
          AND u.role = 2
        ORDER BY similarity ASC
        LIMIT $10
    )

    UNION ALL

    (
        SELECT
            'member'::text                  AS source,
            m.memberid::text                AS id,
            c.contactid::text               AS contactid,
            m.userprofileid::text           AS userprofileid,
            m.userprofileidname::text       AS name,
            NULL::text                      AS businessname,
            NULL::text                      AS businesscategory,
            NULL::text                      AS services,
            NULL::text                      AS servicedescription,
            m.contactidname::text           AS contactidname,
            NULL::text                      AS providertype,
            NULL::text                      AS publicsearchprovider,
            NULL::text                      AS availability,
            NULL::text                      AS areaname,
            c.profileimage::text            AS profileimage,
            c.mobilephone::text             AS mobilephone,
            c.email::text                   AS email,
            c.address::text                 AS address,
            c.city::text                    AS city,
            c.statecode::text               AS statecode,
            NULL::text                      AS townname,
            c.latitude::double precision    AS latitude,
            c.longitude::double precision   AS longitude,
            c.rolecodename::text            AS rolecodename,
            c.publicsearchstatus::text      AS publicsearchstatus,
            COALESCE(m.role, 2)::integer    AS role,
            COALESCE(m.rolename, 'Member')::text AS rolename,
            c.createdon::timestamp          AS createdon,
            NULL::text                     AS provenance,
            'fuzzy'::text                   AS leg,
            -similarity(trim(regexp_replace(regexp_replace(lower(m.userprofileidname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')), $7)::double precision AS similarity
        FROM etown_member m
        JOIN etown_contact c ON m.contactid = c.contactid
        WHERE $7 <> ''
          AND ('member' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
          AND trim(regexp_replace(regexp_replace(lower(m.userprofileidname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')) % $7
          AND c.contactid::text <> COALESCE($2, '')
          AND c.verified IS NOT FALSE
        ORDER BY similarity ASC
        LIMIT $10
    )

    UNION ALL

    (
        SELECT
            'contact'::text                 AS source,
            c.contactid::text               AS id,
            c.contactid::text               AS contactid,
            u.userprofileid::text           AS userprofileid,
            c.firstname::text               AS name,
            NULL::text                      AS businessname,
            NULL::text                      AS businesscategory,
            NULL::text                      AS services,
            NULL::text                      AS servicedescription,
            NULL::text                      AS contactidname,
            NULL::text                      AS providertype,
            NULL::text                      AS publicsearchprovider,
            NULL::text                      AS availability,
            NULL::text                      AS areaname,
            c.profileimage::text            AS profileimage,
            c.mobilephone::text             AS mobilephone,
            c.email::text                   AS email,
            c.address::text                 AS address,
            c.city::text                    AS city,
            c.statecode::text               AS statecode,
            NULL::text                      AS townname,
            c.latitude::double precision    AS latitude,
            c.longitude::double precision   AS longitude,
            c.rolecodename::text            AS rolecodename,
            c.publicsearchstatus::text      AS publicsearchstatus,
            u.role::integer                 AS role,
            u.rolename::text                AS rolename,
            c.createdon::timestamp          AS createdon,
            NULL::text                     AS provenance,
            'fuzzy'::text                   AS leg,
            -similarity(trim(regexp_replace(regexp_replace(lower(c.firstname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')), $7)::double precision AS similarity
        FROM etown_contact c
        JOIN etown_userprofile u ON c.contactid = u.contactid
        WHERE $7 <> ''
          AND ('contact' = ANY($6::text[]) OR array_length($6::text[], 1) IS NULL)
          AND trim(regexp_replace(regexp_replace(lower(c.firstname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')) % $7
          AND c.contactid::text <> COALESCE($2, '')
          AND u.role = 2
        ORDER BY similarity ASC
        LIMIT $10
    )

)

SELECT *
FROM combined_results
WHERE name IS NOT NULL

  AND NOT EXISTS (
        SELECT 1 FROM retired_ids ri
         WHERE ri.identity_id = combined_results.contactid
            OR ri.identity_id = combined_results.userprofileid
      )

  AND NOT EXISTS (
        SELECT 1 FROM disabled_provider_ids dp
         WHERE dp.identity_id = combined_results.userprofileid
      )

  AND NOT EXISTS (
        SELECT 1 FROM deleted_provider_ids dl
         WHERE combined_results.source IN ('provider', 'providerservice')
           AND dl.providerid = combined_results.id
      )

  AND NOT EXISTS (
        SELECT 1 FROM review_pending_ids rp
         WHERE rp.providerid = combined_results.id
      )

ORDER BY leg, similarity ASC
LIMIT $4
OFFSET $5;
"""


MAX_PHONE_DIGITS = 13
PHONE_LOOKUP_TIMEOUT_SECONDS = 5.0
AISEARCH_ENRICH_TIMEOUT_SECONDS = 1.0


def is_phone_number_query(query: str) -> bool:
    """Return True when the query has 10 to MAX_PHONE_DIGITS digits making up at least 80% of its non-separator characters."""
    if not query:
        return False

    digits = re.sub(r'\D', '', query.strip())

    if len(digits) < 10 or len(digits) > MAX_PHONE_DIGITS:
        return False

    consecutive_digits = re.findall(r'\d{3,}', query.strip())
    if not consecutive_digits:
        return False

    original_clean = (
        query.strip()
        .replace(' ', '').replace('-', '').replace('+', '')
        .replace('(', '').replace(')', '')
    )
    digit_ratio = len(digits) / len(original_clean) if original_clean else 0

    return digit_ratio >= 0.8 and len(digits) >= 10


def normalize_phone_number(phone: str) -> str:
    """Return the last 10 digits of a phone number, or all its digits when fewer than 10."""
    if not phone:
        return ""

    digits = re.sub(r'\D', '', phone.strip())

    return digits[-10:] if len(digits) >= 10 else digits


async def fetch_provider_profiles_by_contact_ids_bulk(contact_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch formatted provider listings for contact ids, one per business and city, skipping disabled and review-pending ones."""
    if not contact_ids:
        return {}
    unique_ids = list(set(str(cid) for cid in contact_ids if cid))
    if not unique_ids:
        return {}

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
        grouped: Dict[str, list] = {}
        for r in rows:
            cid = str(r.get('contactid')).strip().lower()
            if cid not in grouped:
                grouped[cid] = []
            grouped[cid].append(dict(r))
        return {cid: format_provider_results(raw_rows) for cid, raw_rows in grouped.items()}
    except Exception as e:
        logger.error(f"Error in bulk provider fetch: {e}", exc_info=True)
        return {}


async def fetch_member_address_details_bulk(contact_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Return address, city and state per lowercased contact id from etown_contact, or {} on error or timeout."""
    if not contact_ids:
        return {}
    unique_ids = list(set(str(cid) for cid in contact_ids if cid))
    if not unique_ids:
        return {}

    query = """
        SELECT contactid, address, city, statecode
        FROM etown_contact
        WHERE contactid = ANY($1::text[])
    """
    try:
        rows = await asyncio.wait_for(
            Database.fetch(query, unique_ids),
            timeout=AISEARCH_ENRICH_TIMEOUT_SECONDS,
        )
        result_map: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = str(row.get('contactid', '')).strip().lower()
            result_map[key] = {
                'address': row.get('address'),
                'city': row.get('city'),
                'statecode': row.get('statecode'),
            }
        logger.debug(f"Fetched member address details for {len(result_map)} contacts")
        return result_map
    except Exception as e:
        logger.error(f"Error fetching member address details: {e}", exc_info=True)
        return {}


async def phone_lookup_search(
    normalized_phone: str,
    contact_id: str,
    user_profile_id: str,
    limit: int = 5,
) -> Dict[str, List[Dict[str, Any]]]:
    """Find members and providers whose primary or alternate mobile numbers match the phone, excluding the caller.

    Args:
        normalized_phone: 10-digit normalized phone number.
        contact_id: Caller's contact ID, excluded from results.
        user_profile_id: Caller's profile ID; not used by the lookup.
        limit: Maximum results per category.

    Returns:
        Dict with members and providers lists, plus an error key on timeout or failure.
    """
    try:
        lookup_query = r"""
            WITH phone_contacts AS MATERIALIZED (
                SELECT c.contactid
                  FROM etown_contact c
                 WHERE c.mobilephone IS NOT NULL
                   AND right(regexp_replace(COALESCE(c.mobilephone, ''), '\D', '', 'g'), 10) = $1
                UNION
                SELECT up.contactid
                  FROM etown_userprofile up
                 WHERE up.mobilenumber IS NOT NULL
                   AND right(regexp_replace(COALESCE(up.mobilenumber, ''), '\D', '', 'g'), 10) = $1
                UNION
                SELECT mb.contactid
                  FROM etown_member mb
                 WHERE mb.mobilenumber IS NOT NULL
                   AND right(regexp_replace(COALESCE(mb.mobilenumber, ''), '\D', '', 'g'), 10) = $1
                UNION
                SELECT pv.contactid
                  FROM etown_provider pv
                 WHERE right(regexp_replace(pv.mobilenumber, '[^0-9]', '', 'g'), 10) = $1
                UNION
                SELECT c2.contactid
                  FROM etown_contact c2
                 WHERE c2.alternatemobile IS NOT NULL
                   AND right(regexp_replace(COALESCE(c2.alternatemobile, ''), '\D', '', 'g'), 10) = $1
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
                CASE WHEN u.role = 1 THEN COALESCE(
                    (SELECT prov.businessname
                       FROM etown_provider prov
                      WHERE prov.userprofileid = u.userprofileid
                        AND prov.deleted IS DISTINCT FROM 'true'
                        AND prov.businessname IS NOT NULL
                      LIMIT 1),
                    (SELECT ps.businessname
                       FROM etown_providerservices_copy ps
                      WHERE ps.userprofileid = u.userprofileid
                        AND ps.deleted IS DISTINCT FROM 'true'
                        AND ps.businessname IS NOT NULL
                      LIMIT 1),
                    c.firstname
                ) ELSE c.firstname END as businessname
            FROM etown_contact c
            JOIN etown_userprofile u ON c.contactid = u.contactid
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
            Database.fetch_once(lookup_query, normalized_phone, contact_id, limit * 2),
            timeout=PHONE_LOOKUP_TIMEOUT_SECONDS,
        )

        logger.info(
            f"Phone lookup for '{normalized_phone}' returned {len(phone_results)} rows"
        )

        member_rows = [dict(r) for r in phone_results if r['role'] == 2]
        provider_rows = [dict(r) for r in phone_results if r['role'] == 1]

        provider_contact_ids = [r['contactid'] for r in provider_rows]
        member_contact_ids = [
            r['contactid'] for r in member_rows
            if not r.get('address') and not r.get('city')
        ]

        fetch_tasks = []
        if provider_contact_ids:
            fetch_tasks.append(fetch_provider_profiles_by_contact_ids_bulk(provider_contact_ids))
        if member_contact_ids:
            fetch_tasks.append(fetch_member_address_details_bulk(member_contact_ids))

        if fetch_tasks:
            fetched = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            provider_details_map = (
                fetched[0] if provider_contact_ids and not isinstance(fetched[0], Exception) else {}
            )
            member_details_map = (
                fetched[-1]
                if member_contact_ids and len(fetched) > 0 and not isinstance(fetched[-1], Exception)
                else {}
            )
        else:
            provider_details_map = {}
            member_details_map = {}

        enriched_member_rows = []
        for row in member_rows:
            if member_details_map:
                key = str(row['contactid']).strip().lower()
                if key in member_details_map and not row.get('address'):
                    extra = member_details_map[key]
                    row = dict(row)
                    row['address'] = extra.get('address') or row.get('address')
                    row['city'] = extra.get('city') or row.get('city')
                    row['statecode'] = extra.get('statecode') or row.get('statecode')
            enriched_member_rows.append(row)

        formatted_members = format_member_results(enriched_member_rows[:limit])

        formatted_providers = []
        seen_detail_contacts = set()
        seen_provider_keys = set()
        for row in provider_rows[:limit]:
            key = str(row['contactid']).strip().lower()
            provider_details = provider_details_map.get(key, [])
            if provider_details:
                if key in seen_detail_contacts:
                    continue
                seen_detail_contacts.add(key)
                candidates = provider_details
            else:
                candidates = [{
                    'Etown_providerid': None,
                    'Etown_userProfileId': row['userprofileid'],
                    'Etown_role': 1,
                    'Etown_rolename': 'Provider',
                    'Etown_businessName': row.get('businessname') or row['firstname'] or 'Unknown Business',
                    'Etown_businessCategory': None,
                    'Etown_services': None,
                    'Etown_contactid': row['contactid'],
                    'Etown_firstname': row['firstname'],
                    'Etown_ownername': None,
                    'Etown_email': row['email'],
                    'Etown_mobilenumber': row['mobilephone'],
                    'Etown_profileimage': row['profileimage'],
                    'Etown_servicedescription': None,
                    'Etown_serviceimageurl': None,
                    'Etown_address': row['address'],
                    'Etown_city': row['city'],
                    'Etown_statecode': row['statecode'],
                    'Etown_areaname': None,
                    'Etown_latitude': str(row['latitude']) if row['latitude'] else None,
                    'Etown_longitude': str(row['longitude']) if row['longitude'] else None,
                    'Etown_publicSearchProvider': None,
                    'Etown_providertype': None,
                    'Etown_availability': None,
                    'Etown_createdon': row['createdon'],
                    'Etown_description': None,
                    'Etown_similarity': 1.0,
                    'Etown_distance_km': None,
                    'Etown_distance_meters': None,
                    'Etown_memberbookingcount': 0,
                    'Etown_ratingcount': 0,
                    'Etown_overallrating': 0.0,
                    'Etown_status': None,
                    'Etown_invitation': None,
                    'Etown_isconnected': False,
                    'Etown_mycircle_count': 0,
                    'Etown_favourite_count': 0,
                    'Etown_source': 'database',
                    'Etown_resultType': 1,
                    'membercurrent': [],
                }]

            for provider in candidates:
                dedupe_key = provider.get('Etown_providerid') or (
                    f"{provider.get('Etown_userProfileId')}|{provider.get('Etown_businessName')}"
                )
                if dedupe_key in seen_provider_keys:
                    continue
                seen_provider_keys.add(dedupe_key)
                formatted_providers.append(provider)

        return {'members': formatted_members, 'providers': formatted_providers}

    except asyncio.TimeoutError as e:
        logger.warning(
            "Phone lookup timed out after %.2fs for '%s'",
            PHONE_LOOKUP_TIMEOUT_SECONDS,
            normalized_phone,
        )
        return {'members': [], 'providers': [], 'error': 'timeout'}
    except Exception as e:
        logger.error(f"Phone lookup failed for '{normalized_phone}': {e}", exc_info=True)
        return {'members': [], 'providers': [], 'error': 'lookup_failed'}


async def bulk_fetch_mutual_connections(
    current_contact_id: str,
    target_contact_ids: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """Return the mutual member and provider connections between the caller and each target contact in one query."""
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
        counts: Dict[str, int] = {}
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


async def mutual_connection_status(
    results_dict: Dict[str, List[Dict[str, Any]]],
    current_contact_id: str,
    current_user_profile_id: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Set Etown_isconnected, Etown_invitation and Etown_status on every nested result profile from relations and pending invites."""
    if not current_user_profile_id or not results_dict:
        return results_dict

    all_user_profile_ids: set = set()

    def _collect_ids(data: Any) -> None:
        if isinstance(data, dict):
            upid = data.get('Etown_userProfileId') or data.get('Etown_userprofileid')
            if upid:
                all_user_profile_ids.add(upid)
            for v in data.values():
                _collect_ids(v)
        elif isinstance(data, list):
            for item in data:
                _collect_ids(item)

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
        rels, invs = await asyncio.gather(
            Database.fetch(rel_query, *params),
            Database.fetch(inv_query, *params),
        )
        connection_map: Dict[str, Dict[str, Any]] = {}
        for r in rels:
            oid = r['other_id']
            rtype = r['type']
            connection_map[oid] = {"status": "Connected", "role": rtype}
        for i in invs:
            oid = i['other_id']
            if oid not in connection_map:
                connection_map[oid] = {"status": "Requested", "role": None}

        def _inject(data: Any) -> None:
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
                for v in data.values():
                    _inject(v)
            elif isinstance(data, list):
                for item in data:
                    _inject(item)

        _inject(results_dict)
    except Exception as e:
        logger.error(f"Error in mutual_connection_status: {e}", exc_info=True)
    return results_dict


async def enrich_profiles_with_rating_data(profiles: List[Dict[str, Any]], current_user_profile_id: str) -> List[Dict[str, Any]]:
    """Set booking count, rating count and average rating on provider profiles in bulk, zeroing them for others."""
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


async def enrich_profiles_with_mycircle_count(profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Set Etown_mycircle_count on each profile from its member-relation count, defaulting to 0."""
    if not profiles:
        return profiles

    profile_ids = set()
    for p in profiles:
        upid = p.get('Etown_userProfileId') or p.get('Etown_userprofileid')
        if upid:
            profile_ids.add(upid.strip())

    if not profile_ids:
        for p in profiles:
            p['Etown_mycircle_count'] = 0
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
            if upid and upid.strip() in count_map:
                p['Etown_mycircle_count'] = count_map[upid.strip()]
            else:
                p['Etown_mycircle_count'] = 0
    except Exception as e:
        logger.error(f"Error enriching mycircle_count: {e}", exc_info=True)
        for p in profiles:
            p['Etown_mycircle_count'] = 0
    return profiles


def extract_city_state(address):
    """Return (city, state code) by finding an Indian state name in the address and taking the part before it."""
    if not address or not isinstance(address, str):
        return None, None

    state_abbreviations = {
        "Andhra Pradesh": "AP", "Arunachal Pradesh": "AR", "Assam": "AS", "Bihar": "BR",
        "Chhattisgarh": "CG", "Goa": "GA", "Gujarat": "GJ", "Haryana": "HR", "Himachal Pradesh": "HP",
        "Jharkhand": "JH", "Karnataka": "KA", "Kerala": "KL", "Madhya Pradesh": "MP", "Maharashtra": "MH",
        "Manipur": "MN", "Meghalaya": "ML", "Mizoram": "MZ", "Nagaland": "NL", "Odisha": "OR",
        "Punjab": "PB", "Rajasthan": "RJ", "Sikkim": "SK", "Tamil Nadu": "TN", "Telangana": "TS",
        "Tripura": "TR", "Uttar Pradesh": "UP", "Uttarakhand": "UK", "West Bengal": "WB",
        "Andaman and Nicobar Islands": "AN", "Chandigarh": "CH",
        "Dadra and Nagar Haveli and Daman and Diu": "DNHDD",
        "Delhi": "DL", "Jammu and Kashmir": "JK", "Ladakh": "LA",
        "Lakshadweep": "LD", "Puducherry": "PY"
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


def format_provider_results(db_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map provider rows to the Etown_* provider response shape, skipping rows that fail to format."""
    if not db_rows:
        return []

    formatted = []
    logger.debug(f"format_provider_results: Processing {len(db_rows)} input rows")

    for i, row in enumerate(db_rows):
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

            desc = row.get('description', '')
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
            latitude_value = (
                loc.get('lat') if isinstance(loc, dict) and loc.get('lat') is not None
                else row.get('latitude')
            )
            longitude_value = (
                loc.get('lon') if isinstance(loc, dict) and loc.get('lon') is not None
                else row.get('longitude')
            )

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
                'Etown_publicSearchProvider': (
                    row.get('publicsearchprovider') or row.get('provider_type') or row.get('providertype')
                ),
                'Etown_providertype': row.get('providertype'),
                'Etown_availability': row.get('availability'),
                'Etown_createdon': row.get('creationdate') or row.get('createdon'),
                'Etown_description': desc,
                'Etown_similarity': row.get('similarity', -1.0),
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
                'Etown_source': row.get('source', 'database'),
                'Etown_resultType': 1,
                'membercurrent': [],
            }
            formatted.append(obj)
        except Exception as e:
            logger.error(f"Error formatting provider row {i}: {e}", exc_info=True)
            continue

    logger.debug(
        f"format_provider_results: Successfully formatted {len(formatted)} rows "
        f"from {len(db_rows)} input rows"
    )
    return formatted


def format_member_results(db_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map member rows to the Etown_* member response shape, deriving city and state from the address when missing."""
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
                'mutualconnections': [],
            }
            formatted.append(obj)
        except Exception as e:
            logger.error(f"Error formatting member row: {e}", exc_info=True)
            continue
    return formatted


def build_keywords_list(
    query: str,
    providers: List[Dict[str, Any]],
    areas: List[AreaSuggestion],
    cities: List[CityItem],
) -> List[str]:
    """Return up to FALLBACK_MAX_KEYWORDS tokens from the top providers' text that extend a query token."""
    normalized_query = normalize_text_for_matching(query)
    query_tokens = [
        token
        for token in _query_tokens_for_text_search(normalized_query)
        if len(token) >= FALLBACK_KEYWORD_MIN_LENGTH
    ]
    blocked = {normalized_query} if normalized_query else set()

    candidates: List[str] = []

    provider_fields = (
        "Etown_businessName", "Etown_businessCategory",
        "Etown_services", "Etown_servicedescription", "Etown_description",
    )
    for provider in providers[:8]:
        for field in provider_fields:
            value = normalize_text_for_matching(provider.get(field) or "")
            if not value:
                continue
            for token in re.findall(r"[a-z0-9]+", value):
                if len(token) < FALLBACK_KEYWORD_MIN_LENGTH:
                    continue
                if token in TEXT_SEARCH_STOPWORDS or token in blocked:
                    continue
                if query_tokens and not any(token.startswith(qt) for qt in query_tokens):
                    continue
                candidates.append(token)

    keywords: List[str] = []
    seen: set = set()
    for c in candidates:
        if not c or c in blocked or c in seen:
            continue
        seen.add(c)
        keywords.append(c)
        if len(keywords) >= FALLBACK_MAX_KEYWORDS:
            break

    return keywords


def extract_cities_from_townprofile_results(
    townprofile_results: List[Dict[str, Any]],
    search_query: str,
    limit: int = 10,
) -> List[CityItem]:
    """Build CityItem entries from the first townprofile rows, deduplicated by name; search_query is unused."""
    if not townprofile_results:
        return []
    cities: List[CityItem] = []
    seen: set = set()
    for result in townprofile_results[:limit]:
        city_name = result.get('name') or result.get('townname')
        if not city_name or city_name.lower() in seen:
            continue
        seen.add(city_name.lower())
        lat = result.get('latitude')
        lon = result.get('longitude')
        cities.append(CityItem(
            name=city_name,
            cityname=result.get('city'),
            latitude=str(lat) if lat is not None else None,
            longitude=str(lon) if lon is not None else None,
            description=result.get('servicedescription') or '',
            image=result.get('profileimage') or '',
        ))
    return cities


def extract_areas_from_provider_results(
    provider_results: List[Dict[str, Any]],
    search_query: str,
    limit: int = 10,
) -> List[AreaSuggestion]:
    """Suggest areas matching a query token from provider rows, ordered by provider count.

    Args:
        provider_results: Provider rows; areaname is preferred over a parsed address segment.
        search_query: Query whose 3+ character tokens an area name must contain.
        limit: Maximum areas to return.

    Returns:
        AreaSuggestion list with provider, verified and unverified counts.
    """
    if not provider_results:
        return []
    query_tokens = {tok for tok in normalize_text_for_matching(search_query).split() if len(tok) >= 3}
    if not query_tokens:
        return []
    area_counts: Dict[tuple, Dict[str, Any]] = {}
    for result in provider_results:
        city_name = result.get('Etown_city') or result.get('city')
        state_code = result.get('Etown_statecode') or result.get('statecode') or ''
        area_name = (result.get('Etown_areaname') or result.get('areaname') or '').strip() or None

        if not area_name:
            address = result.get('Etown_address') or result.get('address', '')
            for part in (address or '').split(','):
                part = part.strip()
                lowered = part.lower()
                if len(part) < 3 or not re.search(r"[^\W\d_]", part):
                    continue
                if lowered in (str(city_name or '').lower(), str(state_code).lower()):
                    continue
                if re.match(r"^(no\.?|d\.?\s*no\.?|door|flat|plot|unit|shop\s*no)\b", lowered):
                    continue
                if "floor" in lowered:
                    continue
                digits = sum(ch.isdigit() for ch in part)
                if digits and digits * 2 >= sum(ch.isalnum() for ch in part):
                    continue
                area_name = part
                break

        if not area_name or area_name.lower() == str(city_name or '').lower():
            continue
        if area_name.lower() == str(state_code).lower():
            continue
        normalized_area = normalize_text_for_matching(area_name)
        if not any(tok in normalized_area for tok in query_tokens):
            continue
        area_key = (area_name, city_name)
        if area_key not in area_counts:
            area_counts[area_key] = {
                'area_name': area_name,
                'city': city_name,
                'state_code': result.get('Etown_statecode') or result.get('statecode', ''),
                'latitude': result.get('Etown_latitude') or result.get('latitude'),
                'longitude': result.get('Etown_longitude') or result.get('longitude'),
                'provider_count': 0,
                'verified_count': 0,
                'unverified_count': 0,
            }
        area_counts[area_key]['provider_count'] += 1
        if (result.get('Etown_providertype') or result.get('providertype', '')) == 'etown_verified':
            area_counts[area_key]['verified_count'] += 1
        else:
            area_counts[area_key]['unverified_count'] += 1

    areas: List[AreaSuggestion] = []
    for data in sorted(area_counts.values(), key=lambda x: x['provider_count'], reverse=True)[:limit]:
        lat = data['latitude']
        lon = data['longitude']
        areas.append(AreaSuggestion(
            area_name=data['area_name'],
            city_name=data['city'],
            state_code=data['state_code'],
            latitude=str(lat) if lat is not None else None,
            longitude=str(lon) if lon is not None else None,
            provider_count=data['provider_count'],
            verified_count=data['verified_count'],
            unverified_count=data['unverified_count'],
        ))
    return areas


TRANSLATE_TIMEOUT = 5.0
TRANSLATE_MAX_NEW_TOKENS = 128

SCRIPT_LANGUAGE_RANGES = (
    ('ऀ', 'ॿ', 'hi'),
    ('ঀ', '৿', 'bn'),
    ('਀', '੿', 'pa'),
    ('઀', '૿', 'gu'),
    ('଀', '୿', 'or'),
    ('஀', '௿', 'ta'),
    ('ఀ', '౿', 'te'),
    ('ಀ', '೿', 'kn'),
    ('ഀ', 'ൿ', 'ml'),
    ('඀', '෿', 'si'),
    ('؀', 'ۿ', 'ur'),
)

TRANSLATE_UNSUPPORTED_SOURCE = frozenset({'te'})


def detect_query_language(query: str) -> Optional[str]:
    """Return the language code for the first mapped Indic or Urdu script in the query, or None."""
    for start, end, language in SCRIPT_LANGUAGE_RANGES:
        if any(start <= char <= end for char in query):
            return language
    return None


async def translate_query_to_english(query: str) -> Tuple[str, bool]:
    """Translate a non-ASCII query to English via the translate API with its detected source language.

    Returns:
        Tuple of (query, was_translated); the original query and False when skipped or failed.
    """
    if query.isascii():
        return query, False

    source_lang = detect_query_language(query)
    if source_lang is None:
        return query, False
    if source_lang in TRANSLATE_UNSUPPORTED_SOURCE:
        logger.info(
            "TRANSLATE_SKIPPED | unsupported source language '%s' | query='%s'",
            source_lang,
            query,
        )
        return query, False

    try:
        _api_key = settings.madlad_api_key or ""
        _headers = {"Content-Type": "application/json"}
        if _api_key:
            _headers["X-API-Key"] = _api_key

        async with get_http_client() as client:
            response = await asyncio.wait_for(
                client.post(
                    settings.madlad_translate_url,
                    json={
                        "text": query,
                        "target_lang": "en",
                        "source_lang": source_lang,
                        "max_new_tokens": TRANSLATE_MAX_NEW_TOKENS,
                    },
                    headers=_headers,
                ),
                timeout=TRANSLATE_TIMEOUT,
            )
            if response.status_code == 200:
                data = response.json()
                translations = data.get("translations") or []
                first = translations[0] if isinstance(translations, list) and translations else {}
                translated = first.get("translated_text") if isinstance(first, dict) else None
                if translated and isinstance(translated, str) and translated.strip():
                    logger.info(
                        "QUERY_TRANSLATED | source_lang=%s | original='%s' | translated='%s'",
                        source_lang,
                        query,
                        translated,
                    )
                    return translated.strip(), True
            else:
                logger.warning(
                    "TRANSLATE_ERROR | status=%d | source_lang=%s | body='%s' | query='%s'",
                    response.status_code,
                    source_lang,
                    response.text[:300],
                    query,
                )
    except Exception as exc:
        logger.warning("TRANSLATE_FAILED | query='%s' | error=%s", query, exc)

    return query, False


SNIPPET_FIELDS = ("Etown_services", "Etown_servicedescription", "Etown_businessCategory",
                  "Etown_address")
SNIPPET_MAX_CHARS = 140
SNIPPET_MIN_CHARS = 12
SNIPPET_DESCRIPTIVE_FIELDS = ("Etown_servicedescription", "Etown_services",
                              "Etown_businessCategory")


def build_snippet(provider: Dict[str, Any], query_tokens: List[str]) -> str:
    """Return an excerpt of the field with the most query-token hits, else the longest descriptive field, or empty."""
    best_text, best_hits = "", 0
    for field in SNIPPET_FIELDS:
        value = (provider.get(field) or "").strip()
        if len(value) < SNIPPET_MIN_CHARS:
            continue
        hits = sum(1 for token in query_tokens if token in value.lower())
        if hits > best_hits:
            best_text, best_hits = value, hits
    if not best_text:
        candidates = [(provider.get(f) or "").strip() for f in SNIPPET_DESCRIPTIVE_FIELDS]
        best_text = max(candidates, key=len, default="")
        if len(best_text) < SNIPPET_MIN_CHARS:
            return ""
    if len(best_text) <= SNIPPET_MAX_CHARS:
        return best_text
    lowered = best_text.lower()
    start = min((lowered.find(tok) for tok in query_tokens if tok in lowered), default=0)
    start = max(0, start - 40)
    return ("…" if start else "") + best_text[start:start + SNIPPET_MAX_CHARS].strip() + "…"


async def log_search_events(
    event_type: str,
    query: str,
    items: List[Dict[str, Any]],
    contact_id: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> int:
    """Insert up to 100 impression or click events into etown_search_events and return the rows written, 0 on failure."""
    if event_type not in ("impression", "click") or not items:
        return 0
    capped = items[:100]
    provider_ids = [str(i.get("providerid") or "") or None for i in capped]
    profile_ids = [str(i.get("userprofileid") or "") or None for i in capped]
    positions = [int(i["position"]) if str(i.get("position") or "").isdigit() else None
                 for i in capped]
    try:
        await Database.execute(
            """
            INSERT INTO etown_search_events
                (event_type, query, normalized_query, providerid, userprofileid,
                 position, contactid, latitude, longitude)
            SELECT $1, $2, $3, t.pid, t.upid, t.pos, $7, $8, $9
            FROM unnest($4::text[], $5::text[], $6::int[]) AS t(pid, upid, pos)
            """,
            event_type, query, normalize_text_for_matching(query),
            provider_ids, profile_ids, positions, contact_id, latitude, longitude,
        )
        return len(capped)
    except Exception as e:
        logger.warning(f"search event log failed ({event_type}): {e}")
        return 0


SUGGEST_MIN_PREFIX = 2
SUGGEST_CACHE_TTL_SECONDS = 300.0

SUGGEST_QUERY = r"""
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
""" + _REVIEW_PENDING_CTE + r"""
SELECT ps.businessname AS name,
       ps.city AS city,
       (trim(regexp_replace(regexp_replace(lower(ps.businessname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')) LIKE $1 || '%') AS is_prefix,
       similarity(trim(regexp_replace(regexp_replace(lower(ps.businessname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')), $1) AS sim
FROM etown_providerservices_copy ps
WHERE trim(regexp_replace(regexp_replace(lower(ps.businessname), '[!-/:-@[-^`{-~]', ' ', 'g'), '\s+', ' ', 'g')) ILIKE '%' || $1 || '%'
  AND ps.businessname IS NOT NULL
  AND ps.deleted IS DISTINCT FROM 'true'
  AND NOT EXISTS (SELECT 1 FROM etown_provider dl WHERE dl.providerid = ps.providerid AND dl.deleted = 'true')

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
ORDER BY is_prefix DESC, sim DESC, length(ps.businessname) ASC
LIMIT $2
"""


@alru_cache(maxsize=1024, ttl=SUGGEST_CACHE_TTL_SECONDS)
async def suggest(prefix: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Return up to limit typeahead suggestions: matching business names first, then gazetteer places."""
    normalized = normalize_text_for_matching(prefix)
    if len(normalized) < SUGGEST_MIN_PREFIX:
        return []

    suggestions: List[Dict[str, Any]] = []
    seen: set = set()
    try:
        rows = await asyncio.wait_for(
            Database.fetch_once(SUGGEST_QUERY, normalized, limit * 3),
            timeout=SUGGEST_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.warning(f"Suggest failed for {prefix!r}: {e}")
        rows = []

    for row in rows:
        name = (row["name"] or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        suggestions.append({"value": name, "kind": "business", "detail": row["city"] or ""})
        if len(suggestions) >= limit:
            break

    if len(suggestions) < limit:
        for key, (_lat, _lon, kind, display) in (await _load_gazetteer()).items():
            if key.startswith(normalized) and display.lower() not in seen:
                seen.add(display.lower())
                suggestions.append({"value": display, "kind": kind, "detail": ""})
                if len(suggestions) >= limit:
                    break
    return suggestions


@alru_cache(
    maxsize=max(1, settings.AISEARCH_RESULT_CACHE_SIZE),
    ttl=settings.AISEARCH_RESULT_CACHE_TTL_SECONDS,
)
async def _cached_candidates(
    embed_str: Optional[str],
    per_source_limit: int,
    sources: Tuple[str, ...],
    exact_text: str,
    query_text: str,
) -> List[Dict[str, Any]]:
    """Run the unified candidate query once per identical search and return its rows, cached independently of caller and location."""
    rows = await Database.fetch_once(
        COMBINED_UNIFIED_QUERY,
        embed_str, '', per_source_limit,
        AISEARCH_CANDIDATE_POOL, 0, list(sources), exact_text,
        per_source_limit * AISEARCH_ANN_OVERFETCH, query_text, AISEARCH_TEXT_LEG_LIMIT,
    )
    return [dict(r) for r in rows]


async def _apply_premium_badges(response: Dict[str, Any]) -> None:
    """Rewrite Etown_providertype on provider cards and members' providercurrent cards to their paid-tier badge, in place."""
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
) -> Dict[str, Any]:
    """Search providers, members, towns and areas for one query, short-circuiting phone-number queries to a direct lookup.

    Args:
        search_query: Raw user query, any script.
        limit: Maximum providers and maximum members to return.
        offset: Rows to skip in the ranked candidate set.
        contact_id: Searching user's contact ID, excluded from provider and member results.
        user_profile_id: Searching user's profile ID, used for rating enrichment.
        mycircle: Restrict results to connected profiles and add mutual connections.
        latitude: Caller-supplied latitude, or None to resolve from the profile.
        longitude: Caller-supplied longitude, or None to resolve from the profile.

    Returns:
        Dict of member, provider, areas, cities, keywords and performance_metrics with a degraded flag.

    Raises:
        HTTPException: 400 when the query is empty or gibberish.
    """
    search_query = validate_query(search_query)

    query_was_translated = False
    original_script_query = search_query
    t_translate = 0.0
    if not search_query.isascii():
        _t_translate_start = time.time()
        search_query, query_was_translated = await translate_query_to_english(search_query)
        t_translate = time.time() - _t_translate_start

    t0 = time.time()

    performance_metrics: Dict[str, Any] = {
        "translation_time": t_translate,
        "embedding_time": 0.0,
        "embedding_cache_hit": False,
        "result_cache_hit": False,
        "search_time": 0.0,
        "phone_lookup_time": 0.0,
        "enrichment_time": 0.0,
        "total_time": 0.0,
        "degraded": False,
    }

    logger.info(f"SEARCH START | Query: '{search_query}' | Limit: {limit} | Offset: {offset}")

    if is_phone_number_query(search_query):
        logger.info(f"PHONE LOOKUP DETECTED | Query: '{search_query}'")
        t_phone_start = time.time()
        normalized_phone = normalize_phone_number(search_query)
        phone_results = await phone_lookup_search(
            normalized_phone, contact_id, user_profile_id, max(5, limit)
        )
        if phone_results.get('error'):
            logger.warning(
                f"PHONE LOOKUP ERROR | Type: {phone_results['error']} | Number: '{normalized_phone}'"
            )
            performance_metrics["degraded"] = True
        members = phone_results.get('members', [])
        providers = phone_results.get('providers', [])
        for profile in members + providers:
            profile['Etown_isconnected'] = False
            profile['Etown_invitation'] = None
            profile['Etown_status'] = None
        performance_metrics["phone_lookup_time"] = time.time() - t_phone_start
        performance_metrics["total_time"] = time.time() - t0
        logger.info(
            f"PHONE LOOKUP COMPLETE | Members: {len(members)}, Providers: {len(providers)} | "
            f"Total: {performance_metrics['total_time']:.2f}s"
        )
        if members or providers:
            await _apply_premium_badges({"member": members, "provider": providers})
            return {
                "member": members[offset:offset + limit],
                "provider": providers[offset:offset + limit],
                "areas": [],
                "cities": [],
                "keywords": [],
                "performance_metrics": performance_metrics,
                "search_type": "phone_lookup",
                "corrected_query": normalized_phone,
                "original_query": search_query,
            }
        logger.info(
            f"PHONE LOOKUP EMPTY | Number: '{normalized_phone}' | falling through to normal search"
        )

    if latitude is None or longitude is None:
        inferred_coords = await _resolve_user_coordinates(contact_id, user_profile_id)
        if inferred_coords is not None:
            inferred_lat, inferred_lon = inferred_coords
            if latitude is None:
                latitude = inferred_lat
            if longitude is None:
                longitude = inferred_lon
    caller_latitude, caller_longitude = latitude, longitude

    term_stats, corpus_size = await _load_term_stats()
    gazetteer = await _load_gazetteer()

    async def execute_search(
        query_text: str,
        embed_str: Optional[str],
        include_members: bool = True,
        lexical_text: Optional[str] = None,
    ) -> Tuple[List[Dict], List[Dict], List[Dict], bool,
               Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        """Run the unified query and split its rows into provider and member shapes."""
        exact_text = normalize_text_for_matching(query_text)
        caller_contact = str(contact_id or '').strip().lower()
        if not embed_str and not exact_text:
            return [], [], [], False, {}, {}

        per_source_limit = min(limit * 2, 24) if limit < 50 else min(limit, 50)
        sources = ['providerservice', 'provider', 'townprofile']
        if include_members:
            sources.extend(['contact', 'member'])

        t_search_start = time.time()
        try:
            rows = await asyncio.wait_for(
                _cached_candidates(
                    embed_str, per_source_limit, tuple(sources), exact_text,
                    lexical_text or query_text,
                ),
                timeout=AISEARCH_SEARCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as e:
            logger.warning(
                "Unified search timed out after %.2fs for '%s'",
                AISEARCH_SEARCH_TIMEOUT_SECONDS, query_text,
            )
            return [], [], [], True, {}, {}
        except Exception as e:
            logger.error(f"Unified search failed for '{query_text}': {e}", exc_info=True)
            return [], [], [], True, {}, {}

        logger.debug(
            "DB search finished in %.3fs | %d rows", time.time() - t_search_start, len(rows)
        )
        if caller_contact:
            rows = [
                row for row in rows
                if str(row.get('contactid') or '').strip().lower() != caller_contact
            ]

        fusion: Dict[str, Dict[str, Any]] = {}
        member_fusion: Dict[str, Dict[str, Any]] = {}
        leg_position: Dict[str, int] = {}
        member_leg_position: Dict[str, int] = {}
        for row in rows:
            source = row.get('source')
            if source in ('providerservice', 'provider'):
                target, positions = fusion, leg_position
                key = str(row.get('userprofileid') or row.get('id') or '').strip().lower()
            elif source in ('contact', 'member'):
                target, positions = member_fusion, member_leg_position
                key = str(row.get('userprofileid') or row.get('contactid') or '').strip().lower()
            else:
                continue
            leg = row.get('leg') or 'vector'
            positions[leg] = positions.get(leg, 0) + 1
            if not key:
                continue
            entry = target.setdefault(key, {'rrf': 0.0, 'legs': set()})
            entry['rrf'] += 1.0 / (RRF_K + positions[leg])
            entry['legs'].add(leg)

        provider_results: List[Dict] = []
        member_results: List[Dict] = []
        for row in rows:
            source = row.get('source', '')
            if source in ('providerservice', 'provider'):
                provider_results.append({
                    'providerid': row.get('id'),
                    'contactid': row.get('contactid'),
                    'userprofileid': row.get('userprofileid'),
                    'businessname': row.get('businessname'),
                    'businesscategory': row.get('businesscategory'),
                    'services': row.get('services'),
                    'servicedescription': row.get('servicedescription'),
                    'description': row.get('servicedescription'),
                    'contactidname': row.get('contactidname'),
                    'email': row.get('email'),
                    'mobilenumber': row.get('mobilephone'),
                    'serviceimageurl': row.get('profileimage'),
                    'address': row.get('address'),
                    'city': row.get('city'),
                    'statecode': row.get('statecode'),
                    'areaname': row.get('areaname'),
                    'latitude': row.get('latitude'),
                    'longitude': row.get('longitude'),
                    'publicsearchprovider': row.get('publicsearchprovider'),
                    'providertype': row.get('providertype'),
                    'availability': row.get('availability'),
                    'createdon': row.get('createdon'),
                    'source': row.get('provenance') or 'database',
                    'role': row.get('role') or 1,
                    'rolename': row.get('rolename') or 'Provider',
                    'similarity': row.get('similarity'),
                })
            elif source in ('contact', 'member') and include_members:
                member_results.append({
                    'leg': row.get('leg') or 'vector',
                    'contactid': row.get('contactid') or row.get('id'),
                    'userprofileid': row.get('userprofileid'),
                    'firstname': row.get('name'),
                    'email': row.get('email'),
                    'profileimage': row.get('profileimage'),
                    'mobilephone': row.get('mobilephone'),
                    'address': row.get('address'),
                    'city': row.get('city'),
                    'statecode': row.get('statecode'),
                    'latitude': row.get('latitude'),
                    'longitude': row.get('longitude'),
                    'rolecodename': row.get('rolecodename'),
                    'publicsearchstatus': row.get('publicsearchstatus'),
                    'createdon': row.get('createdon'),
                    'role': row.get('role'),
                })

        return provider_results, member_results, rows, False, fusion, member_fusion

    async def prepare_and_search(query_text: str, use_embedding: bool = True) -> Dict[str, Any]:
        """Plan and run one search attempt for `query_text`.

        Every piece of state the ranking below needs is derived here and returned
        together with the rows, so a fallback attempt on a different query brings its
        own place, coordinates, embedding and IDF weights instead of being scored
        against the query that failed.

        Args:
            query_text: The query this attempt should search for.
            use_embedding: False to skip the embedding call and run the name and
                lexical legs only. Every vector leg is guarded by
                `$1::vector IS NOT NULL`, so a null embedding disables them without
                affecting the rest, which makes a name-only attempt cheap.
        """
        normalized_query = normalize_text_for_matching(query_text)

        attempt_lat, attempt_lon = caller_latitude, caller_longitude
        place, residual_query = detect_place(normalized_query, gazetteer)
        search_location: Optional[Dict[str, Any]] = None
        if place:
            place_lat, place_lon, place_kind, place_name = place
            attempt_lat, attempt_lon = place_lat, place_lon
            search_location = {"name": place_name, "kind": place_kind,
                               "latitude": place_lat, "longitude": place_lon}
            logger.info("PLACE DETECTED | %s (%s) | residual='%s'", place_name, place_kind, residual_query)

        embedding_text = expand_synonyms(residual_query or normalized_query)
        lexical_terms = [term for term in _query_tokens_for_text_search(expand_synonyms(normalized_query))
                         if " " not in term]
        if corpus_size:
            ubiquitous_at = corpus_size * UBIQUITOUS_DF_FRACTION
            distinctive = [t for t in lexical_terms if term_stats.get(t, 0) < ubiquitous_at]
        else:
            distinctive = [t for t in lexical_terms if t not in FALLBACK_COMMON_TERMS]
        lexical_text = " OR ".join(distinctive or lexical_terms) or normalized_query
        coverage_source = residual_query if place else normalized_query
        term_variants = {
            term: {v for v in _query_tokens_for_text_search(term) if " " not in v} | {term}
            for term in dict.fromkeys(coverage_source.split()) if len(term) >= 3
        }
        query_idf = {
            term: math.log(1 + (corpus_size / (1 + term_stats.get(term, 0))))
            for term in term_variants
        } if corpus_size else {}

        t1 = time.time()
        embedding_string: Optional[str] = None
        cache_hits_before = _embed_query.cache_info().hits
        try:
            if use_embedding:
                embedding_string = await _embed_query(embedding_text)
                performance_metrics["embedding_cache_hit"] = (
                    _embed_query.cache_info().hits > cache_hits_before
                )
        except asyncio.TimeoutError as e:
            performance_metrics["degraded"] = True
            logger.warning("Embedding timed out for query '%s'", embedding_text)
        except Exception as e:
            performance_metrics["degraded"] = True
            logger.error(f"Embedding failed: {e}", exc_info=True)
        performance_metrics["embedding_time"] += time.time() - t1

        t_search = time.time()
        prov_results, mem_results, all_results, search_degraded, fusion, member_fusion = await execute_search(
            query_text, embedding_string, lexical_text=lexical_text,
        )
        t_search_elapsed = time.time() - t_search
        performance_metrics["search_time"] += t_search_elapsed
        performance_metrics["result_cache_hit"] = t_search_elapsed < RESULT_CACHE_HIT_SECONDS
        performance_metrics["degraded"] = performance_metrics["degraded"] or search_degraded
        logger.info(
            f"SEARCH ROWS | providers={len(prov_results)} members={len(mem_results)} for '{query_text}'"
        )

        return {
            "query_text": query_text,
            "normalized_query": normalized_query,
            "latitude": attempt_lat,
            "longitude": attempt_lon,
            "place": place,
            "search_location": search_location,
            "term_variants": term_variants,
            "query_idf": query_idf,
            "prov_results": prov_results,
            "mem_results": mem_results,
            "all_results": all_results,
            "fusion": fusion,
            "member_fusion": member_fusion,
        }

    attempt = await prepare_and_search(search_query)

    if query_was_translated:
        native = await prepare_and_search(original_script_query, use_embedding=False)

        def _has_leg(a: Dict[str, Any], wanted: frozenset) -> bool:
            """True when any provider or member row in this attempt matched by name."""
            if any(entry['legs'] & wanted for entry in a["fusion"].values()):
                return True
            return any(row.get('leg') in wanted for row in a["mem_results"])

        native_exact = _has_leg(native, frozenset({'exact'}))
        translated_found_nothing = not attempt["prov_results"] and not attempt["mem_results"]
        native_found_something = bool(native["prov_results"] or native["mem_results"])

        if native_exact or (translated_found_nothing and native_found_something):
            logger.info(
                "TRANSLATION_DISCARDED | native_exact=%s | translated='%s' (%d providers) "
                "| using original='%s' (%d providers)",
                native_exact, search_query, len(attempt["prov_results"]),
                original_script_query, len(native["prov_results"]),
            )
            attempt = native
            query_was_translated = False

    search_query = attempt["query_text"]
    normalized_query = attempt["normalized_query"]
    latitude = attempt["latitude"]
    longitude = attempt["longitude"]
    place = attempt["place"]
    search_location = attempt["search_location"]
    term_variants = attempt["term_variants"]
    query_idf = attempt["query_idf"]
    total_idf = sum(query_idf.values())
    key_term = max(query_idf, key=query_idf.get) if query_idf else None
    prov_results = attempt["prov_results"]
    mem_results = attempt["mem_results"]
    all_results = attempt["all_results"]
    fusion = attempt["fusion"]
    member_fusion = attempt["member_fusion"]

    try:
        provider_data = format_provider_results(prov_results)
    except Exception as e:
        logger.error(f"Error in format_provider_results: {e}", exc_info=True)
        provider_data = []

    unique_providers: Dict[str, Dict] = {}
    for provider in provider_data:
        upid = provider.get('Etown_userProfileId')
        if not upid:
            continue
        kept = unique_providers.get(upid)
        if kept is None:
            provider['Etown_other_listings'] = []
            unique_providers[upid] = provider
            continue
        name = (provider.get('Etown_services') or provider.get('Etown_businessName') or '').strip()
        siblings = kept['Etown_other_listings']
        if name and name != kept.get('Etown_businessName') and name not in siblings:
            siblings.append(name)
    provider_data = list(unique_providers.values())

    mem_results = [m for m in mem_results if m.get('leg') in TERM_LEGS]

    def _member_key(row: Dict) -> str:
        """Fusion key for a person, shared by their member and contact rows."""
        return str(row.get('userprofileid') or row.get('contactid') or '').strip().lower()

    def _match_member(row: Dict) -> Tuple[float, bool]:
        """Return the person's share of query IDF and whether they matched the rarest term."""
        if not total_idf:
            return 1.0, True
        haystack = " ".join(str(row.get(f) or "") for f in
                            ("firstname", "city", "statecode", "address")).lower()
        hit = {term for term, variants in term_variants.items()
               if any(v in haystack for v in variants)}
        covered = sum(w for term, w in query_idf.items() if term in hit) / total_idf
        return covered, (key_term in hit if key_term else True)

    max_member_rrf = max((v['rrf'] for v in member_fusion.values()), default=0.0) or 1.0

    def member_score(row: Dict, coverage: float, legs: set) -> float:
        """Score a person the way `provider_score` scores a listing, minus trust.

        A member carries no verified-provider badge and no booking history, so the
        trust and quality terms have nothing to read; the remaining three weights are
        left at their provider values because members are ranked only against members.
        """
        entry = member_fusion.get(_member_key(row))
        relevance = (entry['rrf'] / max_member_rrf) if entry else 0.0

        lat, lon = row.get('latitude'), row.get('longitude')
        if latitude is not None and longitude is not None and lat is not None and lon is not None:
            dist = haversine_km(latitude, longitude, float(lat), float(lon))
            proximity = math.exp(-dist / PROXIMITY_DECAY_KM)
        else:
            proximity = NO_DISTANCE_PROXIMITY

        score = W_RELEVANCE * relevance + W_COVERAGE * coverage + W_PROXIMITY * proximity
        if 'exact' in legs:
            score += EXACT_MATCH_BOOST
        return score

    gated_mem_results = []
    for row in mem_results:
        legs = member_fusion.get(_member_key(row), {}).get('legs', set())
        coverage, key_matched = _match_member(row)
        if not (legs & NAME_LEGS or key_matched):
            continue
        row['_score'] = member_score(row, coverage, legs)
        gated_mem_results.append(row)
    if len(gated_mem_results) != len(mem_results):
        logger.info(
            "Member relevance gate removed %s rows for query '%s' (kept %s)",
            len(mem_results) - len(gated_mem_results), search_query, len(gated_mem_results),
        )
    gated_mem_results.sort(key=lambda r: -r['_score'])
    mem_results = gated_mem_results

    try:
        member_data = format_member_results(mem_results)
    except Exception as e:
        logger.error(f"Error in format_member_results: {e}", exc_info=True)
        member_data = []

    unique_members: Dict[str, Dict] = {}
    for member in member_data:
        upid = member.get('Etown_userprofileid')
        if upid and upid not in unique_members:
            unique_members[upid] = member
    member_data = list(unique_members.values())

    _apply_distance_to_provider_results(provider_data, latitude, longitude)

    def _match_profile(provider: Dict) -> Tuple[float, bool]:
        """Return the result's share of query IDF and whether it matched the rarest term."""
        if not total_idf:
            return 1.0, True
        haystack = " ".join(str(provider.get(f) or "") for f in
                            ("Etown_businessName", "Etown_services", "Etown_areaname",
                             "Etown_city", "Etown_businessCategory")).lower()
        hit = {term for term, variants in term_variants.items()
               if any(v in haystack for v in variants)}
        covered = sum(w for term, w in query_idf.items() if term in hit) / total_idf
        return covered, (key_term in hit if key_term else True)

    gated_provider_data = []
    for provider in provider_data:
        legs = fusion.get(
            str(provider.get('Etown_userProfileId') or provider.get('Etown_providerid') or '')
            .strip().lower(), {}
        ).get('legs', set())
        if not legs & TERM_LEGS:
            continue
        provider['_coverage'], key_matched = _match_profile(provider)
        if legs & NAME_LEGS or key_matched:
            gated_provider_data.append(provider)
    if len(gated_provider_data) != len(provider_data):
        logger.info(
            "Relevance gate removed %s vector-only rows for query '%s' (kept %s)",
            len(provider_data) - len(gated_provider_data), search_query, len(gated_provider_data),
        )
        provider_data = gated_provider_data

    max_rrf = max((v['rrf'] for v in fusion.values()), default=0.0) or 1.0

    def provider_score(provider: Dict) -> float:
        """Blend retrieval agreement, proximity and trust into one 0..1+ score.

        Proximity is a smooth decay rather than a distance bucket, so a better match
        slightly further away can still win — the bucketed sort could not express that.
        """
        key = str(provider.get('Etown_userProfileId') or provider.get('Etown_providerid') or '').strip().lower()
        entry = fusion.get(key)
        relevance = (entry['rrf'] / max_rrf) if entry else 0.0
        legs = entry['legs'] if entry else set()

        dist = provider.get('Etown_distance_km')
        proximity = math.exp(-dist / PROXIMITY_DECAY_KM) if dist is not None else NO_DISTANCE_PROXIMITY

        verified = str(provider.get('Etown_publicSearchProvider') or provider.get('Etown_providertype') or '').lower()
        trust = 1.0 if 'verified' in verified else 0.5

        coverage = provider.get('_coverage', 0.0)

        quality = 0.0
        score = (W_RELEVANCE * relevance + W_COVERAGE * coverage + W_PROXIMITY * proximity
                 + W_TRUST * trust + W_QUALITY * quality)
        if 'exact' in legs:
            score += EXACT_MATCH_BOOST
        return score

    for provider in provider_data:
        provider['Etown_score'] = round(provider_score(provider), 6)
    priority_badge_map = await resolve_provider_badges(
        p.get('Etown_userProfileId') for p in provider_data
    )
    provider_data.sort(
        key=lambda p: (-_provider_tier_rank(p, priority_badge_map), -p['Etown_score'])
    )

    final_providers = provider_data[offset:offset + limit]
    final_members = member_data[offset:offset + limit]

    seen_provider_ids: set = set()
    deduped_providers = []
    for provider in final_providers:
        pid = provider.get('Etown_providerid')
        if pid is None or pid not in seen_provider_ids:
            deduped_providers.append(provider)
            if pid is not None:
                seen_provider_ids.add(pid)
    final_providers = deduped_providers

    seen_member_ids: set = set()
    deduped_members = []
    for member in final_members:
        mid = member.get('Etown_contactid')
        if mid is None or mid not in seen_member_ids:
            deduped_members.append(member)
            if mid is not None:
                seen_member_ids.add(mid)
    final_members = deduped_members

    snippet_tokens = [tok for tok in normalized_query.split() if len(tok) >= 3]
    for provider in final_providers:
        provider['Etown_snippet'] = build_snippet(provider, snippet_tokens)
        provider.pop('_coverage', None)

    all_member_contact_ids = [m.get('Etown_contactid') for m in final_members if m.get('Etown_contactid')]
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

    for member in final_members:
        cid = str(member.get('Etown_contactid') or '').strip().lower()
        member['providercurrent'] = bulk_providers_map.get(cid, [])
        member['mutualconnections'] = mutual_conn_map.get(member.get('Etown_contactid', ''), [])

    t_enrich_start = time.time()
    if final_providers:
        enrich_tasks = [enrich_profiles_with_rating_data(final_providers, user_profile_id)]
        if mycircle:
            enrich_tasks.append(enrich_profiles_with_mycircle_count(final_providers))
        try:
            await asyncio.wait_for(
                asyncio.gather(*enrich_tasks),
                timeout=AISEARCH_ENRICH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as e:
            performance_metrics["degraded"] = True
            logger.warning("Enrichment timed out for query '%s'", search_query)
        except Exception as e:
            performance_metrics["degraded"] = True
            logger.warning(f"Enrichment failed for query '{search_query}': {e}")
    performance_metrics["enrichment_time"] = time.time() - t_enrich_start

    formatted_areas: List[AreaSuggestion] = []
    formatted_cities: List[CityItem] = []
    if all_results:
        town_tokens = {tok for tok in normalized_query.split() if len(tok) >= 3}
        townprofile_rows = [
            r for r in all_results
            if r.get('source') == 'townprofile'
            and (
                (place and (r.get('name') or '').strip().lower() == place[3].strip().lower())
                or any(tok in normalize_text_for_matching(r.get('name') or '') for tok in town_tokens)
            )
        ]
        formatted_areas = extract_areas_from_provider_results(provider_data, normalized_query, limit)
        town_cities = extract_cities_from_townprofile_results(townprofile_rows, normalized_query, limit)

        city_dict: Dict[str, CityItem] = {}
        for city in town_cities:
            if latitude is not None and longitude is not None and city.latitude and city.longitude:
                try:
                    city.distance_km = round(
                        haversine_km(latitude, longitude, float(city.latitude), float(city.longitude)), 2
                    )
                except Exception:
                    logger.debug("aisearch v2: city distance could not be computed; leaving it unset", exc_info=True)
                    city.distance_km = None
            key = (city.name or '').lower()
            if key not in city_dict or (city.provider_count or 0) > (city_dict[key].provider_count or 0):
                city_dict[key] = city
        formatted_cities = list(city_dict.values())

        if latitude is not None and longitude is not None:
            def city_sort_key(city: CityItem) -> Tuple:
                dist = city.distance_km if city.distance_km is not None else 1000.0
                if dist <= 50:
                    return (0, dist)
                if dist <= 150:
                    return (1, -(city.provider_count or 0), dist)
                return (2, -(city.provider_count or 0), dist)
            formatted_cities.sort(key=city_sort_key)
        else:
            formatted_cities.sort(key=lambda c: -(c.provider_count or 0))

    keywords: List[str] = []
    if offset == 0:
        keywords = build_keywords_list(
            normalized_query, final_providers, formatted_areas, formatted_cities
        )

    final_response: Dict[str, Any] = {
        "member": final_members,
        "provider": final_providers,
        "areas": formatted_areas,
        "cities": formatted_cities,
        "keywords": keywords,
    }

    if final_members or final_providers:
        if mycircle:
            final_response = await mutual_connection_status(final_response, contact_id, user_profile_id)
            final_response['member'] = [m for m in final_response['member'] if m.get('Etown_isconnected')]
            final_response['provider'] = [p for p in final_response['provider'] if p.get('Etown_isconnected')]
        else:
            try:
                final_response = await asyncio.wait_for(
                    mutual_connection_status(final_response, contact_id, user_profile_id),
                    timeout=AISEARCH_CONNECTION_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as e:
                logger.debug("Skipping connection-status enrichment — too slow for non-mycircle search")

    await _apply_premium_badges(final_response)

    performance_metrics["total_time"] = time.time() - t0
    final_response["search_location"] = search_location
    final_response["original_query"] = original_script_query
    final_response["correction_applied"] = False
    final_response["correction_confidence"] = 0.0
    final_response["translated_query"] = search_query if query_was_translated else None
    final_response["query_was_translated"] = query_was_translated
    final_response["performance_metrics"] = performance_metrics

    logger.info(
        "SEARCH COMPLETE | Total: %.3fs | Providers: %d | Members: %d | Degraded: %s",
        performance_metrics["total_time"],
        len(final_response["provider"]),
        len(final_response["member"]),
        performance_metrics["degraded"],
    )
    return final_response
