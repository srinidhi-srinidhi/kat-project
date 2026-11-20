"""Recommend Products Tool - Chaatie product-listing recommendations from an LLM-expanded catalog search.

Member-agent tool handler that expands a purchase term into related product phrases, vector-searches product listings in etown_providerservices_copy for each, then dedupes, LLM-filters, distance-caps, ranks and enriches the results; unlike search_provider it returns individual products, not businesses.

Features:
    - LLM Term Expansion: Turns "ac" into ["split ac", "voltage stabilizer", "ac cover", ...]
    - Product-only Vector Search: Semantic search scoped to product listings
    - Platform-wide Sourcing: Products from any provider near the user, relevance + distance ranked
    - Complementary Items: Surfaces cross-sell products, not just exact matches
    - Geographic Filtering: Distance cap (verified providers get a wider radius)
    - Result Enrichment: Ratings + extended provider details, shaped like search results
    - Bookable Output: Each product carries its providerservicesid for request_provider
    - Provider Exclusion: Skips disabled providers and those with an unapproved review row

Classes:
    No classes defined (module uses function-based utilities)

Functions:
    handle_recommend_products(): Main handler — expand, search, dedupe, filter, rank, enrich
    _expand_product_terms(): LLM expansion of a base term into related product phrases
    _search_products_for_term(): Product-only vector search for a single expanded term
    _filter_relevant_products(): Chunked LLM relevance filter keeping related and complementary items
    _filter_chunk(): Single small-batch KEEP/REMOVE classification pass
    _product_sort_key(): Relevance + distance ranking key (lower is better)
    _build_message(): Builds the user-facing recommendation message

Dependencies:
    - asyncio: Concurrent per-term searches
    - json / re: LLM response parsing
    - geopy: Geographic distance calculations
    - app.db.client: Database operations
    - app.utils.embeddings: Vector embedding generation
    - app.agent_infrastructure.infrastructure.llm_clients: gpt4o_mini for term expansion
    - app.agent_infrastructure.tools.tools_utils.search_provider: shared enrichment helpers
    - app.agent_infrastructure.tools.tools_utils.search_helpers: rating enrichment, normalization
    - app.logs.enhanced_logging: db_query_monitor decorator for query performance monitoring
    - app.etown.provider.provider_review_utils: review_pending_exclusion SQL fragment
    - app.core.logging: Centralized logging

Updates:
    20-Jul-2026: Initial creation - product-catalog recommendations, distinct from provider search.
    20-Aug-2026: Products expose Etown_providertype; the raw key is renamed at the response boundary.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    02-Sep-2026: Products from a provider with etown_provider.disabled are not recommended.
    08-Sep-2026: A purchase no longer returns repair businesses.
    21-Sep-2026: Products from providers with an unapproved etown_provider_review row are excluded.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    08-Oct-2026: Removed LangSmith tracing.
"""

import re
import json
import asyncio
from typing import Any, Dict, List, Optional, Tuple

from geopy.distance import geodesic

from app.core.logging import logger
from app.db.client import Database
from app.etown.provider.provider_review_utils import review_pending_exclusion
from app.utils.embeddings import generate_embedding, vector_to_postgres_string
from app.agent_infrastructure.infrastructure.llm_clients import gpt4o_mini
from app.agent_infrastructure.tools.tools_utils.search_helpers import (
    enrich_profiles_with_rating_data,
    normalize_search_text,
)
from app.agent_infrastructure.tools.tools_utils.search_provider import (
    is_provider_verified,
    _parse_jsonb,
    _enrich_results_with_provider_details,
)
from app.logs.enhanced_logging import db_query_monitor

MAX_EXPANSION_TERMS = 6


async def _expand_product_terms(search_term: str) -> List[str]:
    """Return the base term plus LLM-suggested related product phrases, up to MAX_EXPANSION_TERMS, base only on failure."""
    base = (search_term or "").strip()
    if not base:
        return []

    terms: List[str] = [base]
    try:
        prompt = (
            f'A user wants to buy "{base}" (the term may be in English, Tamil, or Hindi).\n'
            f"List specific PRODUCT types to recommend: the main product itself PLUS closely "
            f'related or complementary products someone buying "{base}" would also consider.\n'
            f"Return the phrases in ENGLISH (they are used for catalog search).\n"
            f"Return ONLY a JSON array of up to {MAX_EXPANSION_TERMS} short product search phrases "
            f'(2-4 words each), most relevant first. Example for "ac": '
            f'["split ac", "air conditioner", "ac voltage stabilizer", "ac cover", "ac remote", "inverter ac"].\n'
            f"No explanation, no markdown."
        )
        response = await gpt4o_mini.ainvoke(prompt)
        content = (response.content or "").strip()
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            seen = {base.lower()}
            for item in json.loads(match.group()):
                phrase = str(item).strip()
                if phrase and phrase.lower() not in seen:
                    seen.add(phrase.lower())
                    terms.append(phrase)
    except Exception as e:
        logger.warning(f"[recommend_products] term expansion failed for '{base}': {e}")

    return terms[:MAX_EXPANSION_TERMS]


async def _search_products_for_term(
    query_text: str,
    term_rank: int,
    limit: int,
    contact_id: Optional[str],
    user_latitude: Optional[float],
    user_longitude: Optional[float],
    max_distance_meters: int,
    ignore_distance: bool = False,
) -> List[Dict[str, Any]]:
    """Hybrid vector-search product listings for one expanded term and annotate rows with distance and rank.

    Args:
        query_text: The expanded product phrase to search for.
        term_rank: Index of this phrase in the expansion list, 0 for the base term.
        limit: Requested result count; three times this (at least 15) is fetched.
        contact_id: Searcher's contact id, whose own listings are excluded.
        user_latitude: User latitude for distance calculation.
        user_longitude: User longitude for distance calculation.
        max_distance_meters: Sourcing radius, widened 1.5x for verified providers.
        ignore_distance: Keep matches beyond the radius, for the zero-result fallback.

    Returns:
        Product rows with distance_km, distance_meters and ranking metadata.
    """
    try:
        embedding_vector = await generate_embedding(query_text)
        embedding_string = vector_to_postgres_string(embedding_vector)
    except Exception as e:
        logger.warning(f"[recommend_products] embedding failed for '{query_text}': {e}")
        return []

    sql_params: List[Any] = ["%product%"]
    conditions = [
        "businesscategory ILIKE $1",
        "content_vector IS NOT NULL",
        "NOT EXISTS (SELECT 1 FROM etown_provider dp"
        " WHERE dp.disabled"
        " AND dp.userprofileid = etown_providerservices_copy.userprofileid)",
        review_pending_exclusion("etown_providerservices_copy.providerid"),
    ]
    if contact_id:
        sql_params.append(contact_id)
        conditions.append(f"contactid != ${len(sql_params)}")

    where_clause = " AND ".join(conditions)
    escaped_term = query_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like_pattern = f"%{escaped_term}%"
    vector_idx = len(sql_params) + 1
    like_idx = len(sql_params) + 2
    limit_idx = len(sql_params) + 3

    sql = f"""
        SELECT subq.*, COALESCE(ep.trusted, false) AS trusted, COALESCE(ep.boosted, false) AS boosted
        FROM (
            SELECT *,
                   (content_vector <#> ${vector_idx}::vector)
                   + CASE WHEN services ILIKE ${like_idx} THEN -0.5 ELSE 0.0 END
                   + CASE WHEN providertype IN ('etown_verified', 'verified') THEN -0.2 ELSE 0.0 END AS hybrid_score
            FROM etown_providerservices_copy
            WHERE {where_clause}
            ORDER BY hybrid_score ASC
            LIMIT ${limit_idx}
        ) subq
        LEFT JOIN etown_provider ep ON ep.providerid = subq.providerid
        ORDER BY subq.hybrid_score ASC
    """
    params = sql_params + [embedding_string, like_pattern, max(limit * 3, 15)]

    try:
        rows = await Database.fetch(sql, *params)
    except Exception as e:
        logger.warning(f"[recommend_products] product search failed for '{query_text}': {e}")
        return []

    products: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        r = dict(row)
        r.pop("content_vector", None)
        r.pop("hybrid_score", None)

        distance_km: Optional[float] = None
        if user_latitude and user_longitude and r.get("latitude") and r.get("longitude"):
            try:
                dist = geodesic(
                    (user_latitude, user_longitude),
                    (float(r["latitude"]), float(r["longitude"])),
                ).km
                is_verified = is_provider_verified(r.get("providertype"))
                allowed = (max_distance_meters / 1000) * 1.5 if is_verified else (max_distance_meters / 1000)
                if not ignore_distance and dist > allowed and not bool(r.get("boosted", False)):
                    continue
                distance_km = round(dist, 2)
            except Exception:
                logger.debug("recommend_products: dropping a row with unusable coordinates", exc_info=True)
                continue

        r["distance_km"] = distance_km
        r["distance_meters"] = int(distance_km * 1000) if distance_km is not None else None
        r["_matched_term"] = query_text
        r["_term_rank"] = term_rank
        r["_vector_rank"] = index
        products.append(r)

    return products


def _product_sort_key(item: Dict[str, Any]) -> tuple:
    """Return a sort key ranking boosted, then verified, then term rank, vector rank and distance."""
    distance = item.get("distance_km")
    return (
        0 if item.get("boosted") else 1,
        0 if is_provider_verified(item.get("providertype")) else 1,
        int(item.get("_term_rank", 999)),
        int(item.get("_vector_rank", 9999)),
        distance if isinstance(distance, (int, float)) else 9999,
    )


_FILTER_CHUNK_SIZE = 6


async def _filter_chunk(original_term: str, products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return the chunk's products an LLM marks KEEP, or the whole chunk on any error or mismatch."""
    try:
        lines = []
        for i, p in enumerate(products):
            name = (p.get("services") or p.get("businessname") or "").strip()
            cat = (p.get("servicecategory") or p.get("businesscategory") or "").strip()
            desc = (p.get("servicedescription") or "")[:120].strip()
            lines.append(f'[{i}] name="{name}" category="{cat}" desc="{desc}"')
        prompt = (
            f'A user wants to buy "{original_term}". Judge each product strictly.\n\n'
            f"KEEP only if the product clearly is one of:\n"
            f'  1. "{original_term}" itself, or a specific type/variant/brand/model of it;\n'
            f"  2. an accessory, part, or consumable made specifically FOR it;\n"
            f'  3. a product from the SAME domain that a buyer of "{original_term}" would '
            f"realistically add to the same purchase.\n"
            f"REMOVE if it belongs to a DIFFERENT product domain and only matches by a loose or "
            f"coincidental keyword. Be strict: when in doubt, REMOVE.\n"
            f'Example for "ac" (air conditioner): KEEP split ac, inverter ac, ac stabilizer, ac '
            f"cover; REMOVE cultivator attachment, mobile accessories, food, clothing.\n\n"
            f"Products:\n" + "\n".join(lines) + "\n\n"
            f'Respond ONLY with valid JSON: {{"decisions": ["KEEP" or "REMOVE", ...]}} — one entry '
            f"per product in the same order. No explanation, no markdown."
        )
        response = await gpt4o_mini.bind(temperature=0).ainvoke(prompt)
        content = (response.content or "").strip()
        m = re.search(r'\{.*?"decisions".*?\}', content, re.DOTALL)
        if not m:
            return products
        decisions = json.loads(m.group()).get("decisions", [])
        if len(decisions) != len(products):
            return products
        return [p for p, d in zip(products, decisions) if str(d).strip().upper() == "KEEP"]
    except Exception as e:
        logger.warning(f"[recommend_products] relevance filter chunk failed for '{original_term}': {e}")
        return products


_REPAIR_CATEGORIES = frozenset({"appliance_repair", "electronics_repair", "repair_services"})
_REPAIR_TERM_RE = re.compile(
    r"(?:repair|repairing|service|servicing|spare|spares|parts?|fix|fixing|maintenance)",
    re.IGNORECASE,
)


async def _filter_relevant_products(
    original_term: str,
    products: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return the products an LLM judges relevant to the term, filtering in parallel chunks that fail open."""
    if len(products) <= 1:
        return products

    chunks = [products[i:i + _FILTER_CHUNK_SIZE] for i in range(0, len(products), _FILTER_CHUNK_SIZE)]
    chunk_results = await asyncio.gather(
        *[_filter_chunk(original_term, chunk) for chunk in chunks],
        return_exceptions=True,
    )

    kept: List[Dict[str, Any]] = []
    for chunk, result in zip(chunks, chunk_results):
        if isinstance(result, Exception):
            kept.extend(chunk)
        else:
            kept.extend(result)

    removed = len(products) - len(kept)
    if removed:
        logger.info(f"[recommend_products] relevance filter removed {removed}/{len(products)} for '{original_term}'")
    return kept


def _build_message(original_term: str, products: List[Dict[str, Any]], used_fallback: bool = False) -> str:
    """Return the English recommendation message for one product, several, or the out-of-range fallback."""
    if used_fallback:
        dists = [p.get("distance_km") for p in products if isinstance(p.get("distance_km"), (int, float))]
        nearest = min(dists) if dists else None
        near_note = f" The nearest is about **{nearest:.0f} km away**." if nearest is not None else ""
        return (
            f"I couldn't find any '{original_term}' products within our standard 25 km range, so here "
            f"are the closest related options available on Etown.{near_note} Browse them below and tell "
            f"me which one you'd like to know more about or order."
        )
    if len(products) == 1:
        return (
            f"Here's a product related to '{original_term}' that I found on Etown. "
            f"Tap it for details, or let me know if you'd like to order it or see more options."
        )
    return (
        f"Here are {len(products)} products related to '{original_term}' available on Etown, "
        f"including some complementary items you might also need. Browse the options below and "
        f"tell me which one you'd like to know more about or order."
    )


@db_query_monitor('product_recommendation')
async def handle_recommend_products(
    search_term: str,
    limit: int = 10,
    max_distance_meters: int = 25000,
    user_latitude: Optional[float] = None,
    user_longitude: Optional[float] = None,
    contact_id: Optional[str] = None,
    shown_product_ids: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """Expand a term, search, dedupe, filter, rank and enrich related catalog products, widening platform-wide when none are near.

    Args:
        search_term: The core item the user wants to buy.
        limit: Maximum number of products to return.
        max_distance_meters: Sourcing radius in meters.
        user_latitude: User latitude for distance calculation.
        user_longitude: User longitude for distance calculation.
        contact_id: Searcher's contact id, whose own listings are excluded.
        shown_product_ids: Providerservicesids already shown, to exclude.

    Returns:
        Tuple of (product dicts, user-facing message).
    """
    original_term = (search_term or "").strip()
    if not original_term:
        return [], "Please tell me which product you're looking for so I can recommend related items."

    _shown = {str(pid) for pid in shown_product_ids} if shown_product_ids else set()

    expanded_terms = await _expand_product_terms(original_term) or [original_term]
    logger.info(f"[recommend_products] '{original_term}' expanded to: {expanded_terms}")

    pool_cap = max(limit * 3, 18)

    async def _run_pass(ignore_distance: bool) -> List[Dict[str, Any]]:
        """Search every expanded term (in parallel), then round-robin interleave the results.

        Interleaving (best-of-each-term, then second-best-of-each-term, ...) prevents one
        noisy short term (e.g. 'ac') from flooding the candidate pool and crowding out real
        matches surfaced by a more specific term (e.g. 'air conditioner') before the cap.
        """
        per_term_results = await asyncio.gather(
            *[
                _search_products_for_term(
                    term, term_rank, limit, contact_id,
                    user_latitude, user_longitude, max_distance_meters,
                    ignore_distance=ignore_distance,
                )
                for term_rank, term in enumerate(expanded_terms)
            ],
            return_exceptions=True,
        )
        term_lists = [r for r in per_term_results if isinstance(r, list) and r]

        interleaved: List[Dict[str, Any]] = []
        seen = set(_shown)
        seen_name_biz = set()
        depth = 0
        while len(interleaved) < pool_cap:
            progressed = False
            for lst in term_lists:
                if depth >= len(lst):
                    continue
                progressed = True
                product = lst[depth]
                key = str(product.get("providerservicesid") or normalize_search_text(product.get("services", "")))
                name_biz = normalize_search_text(f"{product.get('services', '')} {product.get('businessname', '')}")
                if not key or key in seen or (name_biz and name_biz in seen_name_biz):
                    continue
                seen.add(key)
                if name_biz:
                    seen_name_biz.add(name_biz)
                interleaved.append(product)
                if len(interleaved) >= pool_cap:
                    break
            if not progressed:
                break
            depth += 1
        return interleaved

    candidates = await _run_pass(ignore_distance=False)

    used_fallback = False
    if not candidates and user_latitude and user_longitude:
        logger.info(f"[recommend_products] no products within radius for '{original_term}', extending platform-wide")
        candidates = await _run_pass(ignore_distance=True)
        used_fallback = True

    if candidates and not _REPAIR_TERM_RE.search(original_term or ""):
        before = len(candidates)
        candidates = [
            c for c in candidates
            if str(c.get("normalized_category") or "").strip().lower() not in _REPAIR_CATEGORIES
        ]
        if len(candidates) != before:
            logger.info(
                "[recommend_products] dropped %s repair business(es) from a purchase for '%s'",
                before - len(candidates), original_term,
            )

    if candidates:
        candidates = await _filter_relevant_products(original_term, candidates)

    if not candidates:
        return [], (
            f"I couldn't find any products related to '{original_term}' on Etown right now. "
            f"Would you like me to search for providers instead?"
        )

    if used_fallback:
        candidates.sort(
            key=lambda p: (
                0 if p.get("boosted") else 1,
                p.get("distance_km") if isinstance(p.get("distance_km"), (int, float)) else 9999,
            )
        )
    else:
        candidates.sort(key=_product_sort_key)

    products = candidates[:limit]

    products = await enrich_profiles_with_rating_data(products)
    products = await _enrich_results_with_provider_details(products)

    for p in products:
        p["source"] = "database"
        p["category"] = "product"
        p["serviceitems"] = _parse_jsonb(p.get("serviceitems"))
        if "id" not in p and p.get("providerservicesid"):
            p["id"] = p["providerservicesid"]
        p.setdefault("name", p.get("services"))
        p.setdefault("business_name", p.get("businessname"))
        p.pop("_matched_term", None)
        p.pop("_term_rank", None)
        p.pop("_vector_rank", None)
        p["Etown_providertype"] = p.pop("providertype", None)

    return products, _build_message(original_term, products, used_fallback)
