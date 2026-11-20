"""Web Search Tool - real-time web search with safety filtering, caching and quality guards.

Member- and provider-agent tool behind web_search: answers nearby-trade queries from Google Places, otherwise runs gemini_2_5_flash with Google Search grounding behind keyword safety filters and a TTL cache, retrying once with a rewritten query when a quality guard finds missing evidence.

Features:
    - Real-time web search via the Gemini Flash client with Google Search grounding
    - Local-business shortcut: nearby-trade queries are answered from Google Places instead of prose
    - Multi-category keyword safety filtering (illegal, adult, harmful)
    - In-memory caching with TTL and oldest-entry eviction
    - Quality Guards: checks a response carries the evidence a query demands, with one precision retry
    - Contextual fallback and suggestion system for blocked/unsafe queries
    - Structured, user-friendly responses for agent tool consumption
    - Comprehensive logging and error handling

Classes:
    WebSearchService(): Singleton running the Gemini search with safety filtering and TTL caching

Functions:
    handle_web_search(): web_search tool entry; validates, tries local shortcut, searches, retries
    _search_local_businesses(): Answers a nearby-trade query from Google Places, or returns None
    _validate_search_input(): Validates the search query for type, length, and safety
    _format_tool_response(): Formats a search result or quality-guard block into the tool response
    _build_quality_guard_message(): Returns guard text when a configured rule lacks required evidence
    _get_failed_quality_guard_rule(): Returns the first triggered rule lacking evidence in the response
    _should_attempt_precision_retry(): Decides whether a deterministic second-pass search should run
    _rewrite_query_for_factual_precision(): Rewrites a query using the failed rule's rewrite template
    _query_matches_rule(): Checks whether a query matches a quality-guard rule's trigger pattern
    _response_has_rule_evidence(): Checks whether a response contains the evidence a rule requires
    _has_exact_market_lookback_evidence(): Validates a dated, unhedged price near a lookback marker
    _has_non_exact_language_near_span(): Checks for approximation language near one evidence span
    _format_error_response(): Formats the error response when the search tool fails

Dependencies:
    - app.agent_infrastructure.infrastructure.llm_clients: gemini_2_5_flash search model
    - app.utils.web_search_quality_rules: keyword filters, quality-guard rules and templates
    - app.agent_infrastructure.tools.tools_utils.web_search_utils: google_map_service for local results
    - geopy: distance calculation for the local-business results
    - app.core.logging: Application logger

Updates:
    12-Mar-2026: Created with Gemini Flash web search; moved to tools_utils; upgraded to Gemini 2.5 Flash.
    27-Mar-2026: Extracted keyword/regex config to utility modules; response templates to config module.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    08-Oct-2026: Removed LangSmith tracing.
"""

import re
import asyncio
import threading
from typing import Dict, Any, Optional
from datetime import datetime

from app.agent_infrastructure.infrastructure.llm_clients import gemini_2_5_flash
from app.utils.web_search_quality_rules import (
    QUALITY_GUARD_RULES,
    contains_non_exact_language,
    CONTENT_FILTER_KEYWORDS,
    REALTIME_KEYWORDS,
    REALTIME_PHRASES,
    CONTEXTUAL_SUGGESTION_RULES,
    DEFAULT_CONTEXTUAL_SUGGESTION,
    PRECISION_REWRITE_TEMPLATES,
    DEFAULT_PRECISION_REWRITE_TEMPLATE,
    QUALITY_GUARD_RESPONSE_TEMPLATE,
    SUCCESS_USER_MESSAGE_TEMPLATE,
    ERROR_FALLBACK_TEMPLATE,
    MALICIOUS_INPUT_PATTERN,
    REALTIME_QUESTION_PATTERN,
    LOOKBACK_MARKER_PATTERN,
    INR_PRICE_PATTERN,
    LOOKBACK_DIRECTION_PATTERN,
    is_local_business_query,
)
from app.core.logging import logger

_LOCAL_SEARCH_RADIUS_METERS = 50000
_LOCAL_SEARCH_MAX_KM = 100.0
_LOCAL_SEARCH_MAX_RESULTS = 8


class WebSearchService:
    """
    Core service for real-time web search, safety filtering, and in-memory caching.

    This class provides the main logic for executing web searches using Gemini 2.5 Flash,
    applying multi-category keyword safety filters, and caching results for performance.
    It is designed to be used as a singleton (see `web_search_service` below).

    Attributes:
        _query_cache (Dict[str, Dict[str, Any]]): In-memory cache for search results.
        _cache_timestamps (Dict[str, float]): Cache entry timestamps for TTL management.
        _CACHE_TTL (int): Time-to-live for cache entries in seconds.
        _CACHE_MAX_SIZE (int): Maximum number of cache entries (LRU eviction).
        content_filters (Dict[str, List[str]]): Keyword lists for safety filtering.
        _malicious_pattern (Pattern): Regex for detecting malicious input patterns.
        _realtime_keywords (frozenset): Set of keywords indicating real-time queries.
        _realtime_phrases (List[str]): List of phrases indicating real-time queries.
        _realtime_question_pattern (Pattern): Regex for real-time question detection.

    Methods:
        perform_web_search(query, user_context):
            Execute a web search with caching and safety filtering.
        validate_search_query(query):
            Validate query for length and malicious patterns.
        is_search_appropriate(query):
            Determine if query warrants real-time search.
    """

    _query_cache: Dict[str, Dict[str, Any]] = {}
    _cache_timestamps: Dict[str, float] = {}
    _cache_lock = threading.RLock()
    _CACHE_TTL = 300
    _CACHE_MAX_SIZE = 200
    _MAX_SEARCH_ATTEMPTS = 3
    _RETRY_BACKOFF_SECONDS = 0.4

    def __init__(self):
        """Load the content filters, malicious-input pattern and real-time query keywords, phrases and pattern."""
        self.content_filters = CONTENT_FILTER_KEYWORDS
        self._malicious_pattern = MALICIOUS_INPUT_PATTERN
        self._realtime_keywords = frozenset(REALTIME_KEYWORDS)
        self._realtime_phrases = REALTIME_PHRASES
        self._realtime_question_pattern = REALTIME_QUESTION_PATTERN


    async def perform_web_search(self, query: str, user_context: Optional[Dict] = None) -> Dict[str, Any]:
        """Run a cached, keyword-filtered web search and return the result, or a safe fallback on block or failure."""
        logger.info(f"Web search requested for query: '{query}'")

        try:
            cache_key = query.strip().lower()
            cached = self._get_cached_result(cache_key)
            if cached is not None:
                logger.info(f"Web search cache hit for query: '{query}'")
                return cached

            search_response = await self._execute_web_search(query)

            safety = self._quick_content_filter(search_response)
            if not safety["is_safe"]:
                logger.warning(f"Content blocked: {safety['category']} - {safety['reason']}")
                return self._safe_fallback(f"Content filtered: {safety['category']}", query)

            result = {
                "success": True,
                "content": search_response,
                "source": "web_search",
                "query": query,
                "timestamp": datetime.now().isoformat(),
                "safety_status": "approved",
            }
            self._put_cached_result(cache_key, result)
            return result

        except Exception as e:
            logger.error(f"Web search failed for query '{query}': {e}", exc_info=True)
            return self._safe_fallback("Search temporarily unavailable", query)

    def validate_search_query(self, query: str) -> bool:
        """Return True for a 2-500 character string query that matches no malicious pattern."""
        if not query or not isinstance(query, str):
            return False
        query = query.strip()
        if len(query) < 2 or len(query) > 500:
            return False
        return not self._malicious_pattern.search(query)

    def is_search_appropriate(self, query: str) -> bool:
        """Return True when the query contains a real-time keyword, phrase or question pattern."""
        query_lower = query.lower()
        if any(keyword in query_lower for keyword in self._realtime_keywords):
            return True
        for phrase in self._realtime_phrases:
            if phrase in query_lower:
                return True
        return bool(self._realtime_question_pattern.search(query_lower))


    async def _execute_web_search(self, query: str) -> str:
        """Ask Gemini 2.5 Flash with Google Search for a summary, retrying with backoff.

        Raises:
            Exception: When every attempt fails or returns empty content.
        """
        prompt = (
            f"Search the web for: {query}. "
            "Provide a concise, factual summary with the most current information available."
        )

        last_error: Optional[Exception] = None
        for attempt in range(1, self._MAX_SEARCH_ATTEMPTS + 1):
            try:
                response = await gemini_2_5_flash.ainvoke(prompt, tools=[{"google_search": {}}])
                response_text = self._extract_response_text(response)
                if response_text:
                    return response_text

                last_error = ValueError("Empty web search response content")
                logger.warning(
                    "Gemini 2.5 Flash returned empty content for query '%s' on attempt %s/%s",
                    query,
                    attempt,
                    self._MAX_SEARCH_ATTEMPTS,
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    "Gemini 2.5 Flash web search attempt %s/%s failed for query '%s': %s",
                    attempt,
                    self._MAX_SEARCH_ATTEMPTS,
                    query,
                    e,
                )

            if attempt < self._MAX_SEARCH_ATTEMPTS:
                await asyncio.sleep(self._RETRY_BACKOFF_SECONDS * attempt)

        logger.error(
            "Gemini 2.5 Flash web search failed after %s attempts for query '%s': %s",
            self._MAX_SEARCH_ATTEMPTS,
            query,
            last_error,
        )
        raise Exception("Web search service temporarily unavailable") from last_error

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        """Flatten a model response's string or list content into stripped text, or an empty string."""
        if response is None:
            return ""

        if isinstance(response, str):
            return response.strip()

        if hasattr(response, "content"):
            content = getattr(response, "content", None)
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, str):
                        text = item.strip()
                        if text:
                            parts.append(text)
                        continue
                    if isinstance(item, dict):
                        text = str(item.get("text", "")).strip()
                        if text:
                            parts.append(text)
                        continue
                    text = str(getattr(item, "text", "")).strip()
                    if text:
                        parts.append(text)
                return "\n".join(parts).strip()

        return str(response).strip()

    def _quick_content_filter(self, content: str) -> Dict[str, Any]:
        """Scan content for unsafe-category keywords and return is_safe, category, reason and confidence."""
        content_lower = content.lower()
        for category, keywords in self.content_filters.items():
            for kw in keywords:
                if kw in content_lower:
                    return {"is_safe": False, "category": category,
                            "reason": f"Contains {category} content: {kw}", "confidence": 0.9}
        return {"is_safe": True, "category": "safe", "reason": "Passed keyword filtering", "confidence": 0.7}

    def _safe_fallback(self, reason: str, original_query: str) -> Dict[str, Any]:
        """Return a blocked search result with a contextual message, the reason and Etown suggestions."""
        ctx = self._contextual_suggestion(original_query)
        return {
            "success": False,
            "content": f"I'm unable to provide information about that topic. {ctx}",
            "source": "safety_filter",
            "query": original_query,
            "timestamp": datetime.now().isoformat(),
            "safety_status": "blocked",
            "reason": reason,
            "suggestions": [
                "I can help you find local services and businesses on Etown instead.",
                "Would you like to search for service providers in your area?",
                "I can help you with booking appointments or finding contact information.",
            ],
        }

    @staticmethod
    def _contextual_suggestion(query: str) -> str:
        """Return the first suggestion rule message whose keywords appear in the query, else the default."""
        q = query.lower()
        for rule in CONTEXTUAL_SUGGESTION_RULES:
            if any(keyword in q for keyword in rule["keywords"]):
                return rule["message"]
        return DEFAULT_CONTEXTUAL_SUGGESTION


    def _get_cached_result(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Return a cached result within the TTL, evicting and returning None when stale or absent."""
        now = asyncio.get_event_loop().time()
        with self._cache_lock:
            ts = self._cache_timestamps.get(cache_key)
            if ts is not None and (now - ts) < self._CACHE_TTL:
                return self._query_cache.get(cache_key)
            self._query_cache.pop(cache_key, None)
            self._cache_timestamps.pop(cache_key, None)
            return None

    def _put_cached_result(self, cache_key: str, result: Dict[str, Any]) -> None:
        """Cache a search result, evicting the oldest entry when the cache is full."""
        now = asyncio.get_event_loop().time()
        with self._cache_lock:
            if len(self._query_cache) >= self._CACHE_MAX_SIZE and self._cache_timestamps:
                oldest = min(self._cache_timestamps, key=self._cache_timestamps.get)
                self._query_cache.pop(oldest, None)
                self._cache_timestamps.pop(oldest, None)
            self._query_cache[cache_key] = result
            self._cache_timestamps[cache_key] = now


web_search_service = WebSearchService()


async def handle_web_search(
    search_query: str,
    state: Optional[dict] = None,
    config: Optional[dict] = None,
) -> Dict[str, Any]:
    """Answer a web_search call via Places for local trades, else a web search with one precision retry."""
    logger.info(f"Web search tool invoked with query: '{search_query}'")

    try:
        user_context: Dict[str, Any] = {}
        if state:
            user_context = {
                "user_id": state.get("user_id"),
                "user_profile_id": state.get("user_profile_id"),
                "contact_id": state.get("contact_id"),
                "user_language": state.get("user_language", "en"),
                "user_location": {
                    "latitude": state.get("user_latitude"),
                    "longitude": state.get("user_longitude"),
                },
            }

        validation = _validate_search_input(search_query)
        if not validation["is_valid"]:
            return _format_error_response(validation["reason"], search_query)

        uid = user_context.get("user_id", "anonymous")
        lang = user_context.get("user_language", "unknown")
        logger.info(f"Web search request - User: {uid}, Language: {lang}, Query: '{search_query}'")

        loc = (user_context.get("user_location") or {})
        if is_local_business_query(search_query) and loc.get("latitude") and loc.get("longitude"):
            places_response = await _search_local_businesses(
                search_query, float(loc["latitude"]), float(loc["longitude"])
            )
            if places_response is not None:
                return places_response
            logger.info(
                "Places lookup returned nothing for local query '%s'; falling back to prose search",
                search_query,
            )

        search_result = await web_search_service.perform_web_search(search_query, user_context)

        tool_response = _format_tool_response(search_result, search_query)
        if _should_attempt_precision_retry(tool_response, search_query):
            failed_rule = _get_failed_quality_guard_rule(search_query, search_result.get("content", ""))
            failed_rule_name = failed_rule.get("name") if failed_rule else "unknown"
            logger.info(
                "Quality guard '%s' triggered for query '%s'. Attempting deterministic second-pass web search.",
                failed_rule_name,
                search_query,
            )

            rewritten_query = _rewrite_query_for_factual_precision(search_query, failed_rule_name)
            if rewritten_query and rewritten_query.strip().lower() != search_query.strip().lower():
                retry_result = await web_search_service.perform_web_search(rewritten_query, user_context)
                retry_response = _format_tool_response(retry_result, search_query)

                if retry_response.get("status") == "success":
                    logger.info(
                        "Second-pass web search succeeded for query '%s' with rewritten query '%s'.",
                        search_query,
                        rewritten_query,
                    )
                else:
                    logger.info(
                        "Second-pass web search still insufficient for query '%s' (status=%s).",
                        search_query,
                        retry_response.get("status"),
                    )

                tool_response = retry_response

        logger.info(f"Web search completed successfully for query: '{search_query}'")
        return tool_response

    except Exception as e:
        logger.error(f"Web search tool failed for query '{search_query}': {e}", exc_info=True)
        return _format_error_response(f"Search service temporarily unavailable: {e}", search_query)


async def _search_local_businesses(
    search_query: str, latitude: float, longitude: float
) -> Optional[Dict[str, Any]]:
    """Return nearby Google Places listings, nearest first, as a web-search tool response, or None when none qualify."""
    from geopy.distance import geodesic
    from app.agent_infrastructure.tools.tools_utils.web_search_utils import google_map_service

    try:
        map_response = await google_map_service.place_search_with_fallback({
            "textQuery": search_query,
            "pageSize": 10,
            "locationBias": {
                "circle": {
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": _LOCAL_SEARCH_RADIUS_METERS,
                }
            },
        })
    except Exception as e:
        logger.warning("Places lookup failed for local query '%s': %s", search_query, e)
        return None

    listings = []
    for place in map_response.get("places", []) or []:
        name = (place.get("displayName") or {}).get("text")
        phone = place.get("nationalPhoneNumber")
        location = place.get("location") or {}
        lat, lon = location.get("latitude"), location.get("longitude")
        if not name or not lat or not lon:
            continue
        distance_km = geodesic((latitude, longitude), (lat, lon)).km
        if distance_km > _LOCAL_SEARCH_MAX_KM:
            continue
        listings.append({
            "name": name,
            "distance_km": round(distance_km, 2),
            "phone": phone,
            "rating": place.get("rating"),
            "rating_count": place.get("userRatingCount"),
            "address": place.get("formattedAddress"),
        })

    if not listings:
        return None

    listings.sort(key=lambda item: item["distance_km"])
    lines = []
    for item in listings[:_LOCAL_SEARCH_MAX_RESULTS]:
        rating = (
            f" ⭐ {item['rating']}" + (f" ({item['rating_count']})" if item["rating_count"] else "")
            if item["rating"] else ""
        )
        phone = f" — {item['phone']}" if item["phone"] else ""
        lines.append(
            f"- **{item['name']}**{rating} — {item['distance_km']:.1f} km away{phone}\n"
            f"  {item['address'] or ''}".rstrip()
        )
    content = "\n".join(lines)
    user_message = (
        f"[These businesses are NOT registered on Etown — they came from a public web "
        f"listing. Present them as web results, quote the distances exactly as given, and "
        f"tell the user you can start a booking with any of them by name via "
        f"booknow_provider.]\n\n"
        f"Here's what I found on the web for '{search_query}':\n\n{content}"
    )
    logger.info(
        "Local business web search for '%s' returned %s listing(s)", search_query, len(listings)
    )
    return {
        "status": "success",
        "content": content,
        "search_query": search_query,
        "source": "places_search",
        "timestamp": datetime.now().isoformat(),
        "safety_status": "approved",
        "listings": listings[:_LOCAL_SEARCH_MAX_RESULTS],
        "user_message": user_message,
    }


def _validate_search_input(search_query: str) -> Dict[str, Any]:
    """Check the query's type, 2-500 character length and safety, returning is_valid and a reason."""
    if not search_query or not isinstance(search_query, str):
        return {"is_valid": False, "reason": "Search query is required and must be a string"}
    search_query = search_query.strip()
    if len(search_query) < 2:
        return {"is_valid": False, "reason": "Search query must be at least 2 characters long"}
    if len(search_query) > 500:
        return {"is_valid": False, "reason": "Search query is too long (maximum 500 characters)"}
    if not web_search_service.validate_search_query(search_query):
        return {"is_valid": False, "reason": "Search query contains invalid characters or patterns"}
    return {"is_valid": True, "reason": "Search query is valid"}


def _format_tool_response(search_result: Dict[str, Any], original_query: str) -> Dict[str, Any]:
    """Shape a search result into a success, insufficient_data, blocked or failed tool response."""
    if search_result.get("success"):
        quality_guard_message = _build_quality_guard_message(original_query, search_result["content"])
        if quality_guard_message:
            guarded_user_message = QUALITY_GUARD_RESPONSE_TEMPLATE.format(
                query=original_query,
                guard_message=quality_guard_message,
            )
            return {
                "status": "insufficient_data",
                "content": guarded_user_message,
                "search_query": original_query,
                "source": "web_search",
                "timestamp": search_result.get("timestamp", datetime.now().isoformat()),
                "safety_status": search_result.get("safety_status", "approved"),
                "user_message": guarded_user_message,
            }

        return {
            "status": "success",
            "content": search_result["content"],
            "search_query": original_query,
            "source": "web_search",
            "timestamp": search_result.get("timestamp", datetime.now().isoformat()),
            "safety_status": search_result.get("safety_status", "approved"),
            "user_message": SUCCESS_USER_MESSAGE_TEMPLATE.format(
                query=original_query,
                content=search_result["content"],
            ),
        }

    safety_status = search_result.get("safety_status", "blocked")
    reason = search_result.get("reason", "Content not available")
    suggestions = search_result.get("suggestions", [])
    fallback = search_result["content"]
    if suggestions:
        fallback += "\n\nHere are some ways I can help you instead:\n" + "\n".join(
            f"• {s}" for s in suggestions
        )
    return {
        "status": "blocked" if safety_status == "blocked" else "failed",
        "content": fallback,
        "search_query": original_query,
        "source": "safety_filter" if safety_status == "blocked" else "error",
        "timestamp": search_result.get("timestamp", datetime.now().isoformat()),
        "safety_status": safety_status,
        "reason": reason,
        "suggestions": suggestions,
        "user_message": fallback,
    }

def _build_quality_guard_message(original_query: str, response_content: str) -> Optional[str]:
    """Return the failed quality guard rule's message, or None when every matching rule has evidence."""
    failed_rule = _get_failed_quality_guard_rule(original_query, response_content)
    return failed_rule["message"] if failed_rule else None


def _get_failed_quality_guard_rule(original_query: str, response_content: str) -> Optional[Dict[str, Any]]:
    """Return the first matching quality guard rule that lacks required evidence."""
    for rule in QUALITY_GUARD_RULES:
        if not _query_matches_rule(original_query, rule):
            continue
        if not _response_has_rule_evidence(response_content, rule):
            return rule
    return None


def _should_attempt_precision_retry(tool_response: Dict[str, Any], original_query: str) -> bool:
    """Return True when the response is insufficient_data and the query matches a quality guard rule."""
    if tool_response.get("status") != "insufficient_data":
        return False
    return any(_query_matches_rule(original_query, rule) for rule in QUALITY_GUARD_RULES)


def _rewrite_query_for_factual_precision(original_query: str, failed_rule_name: str) -> str:
    """Rewrite the query with the failed rule's precision template, or the default template."""
    template = PRECISION_REWRITE_TEMPLATES.get(failed_rule_name, DEFAULT_PRECISION_REWRITE_TEMPLATE)
    return template.format(query=original_query)

def _query_matches_rule(original_query: str, rule: Dict[str, Any]) -> bool:
    """Return True when the rule's query_pattern matches the query."""
    pattern = rule.get("query_pattern")
    return bool(pattern and pattern.search(original_query))


def _response_has_rule_evidence(response_content: str, rule: Dict[str, Any]) -> bool:
    """Return True when the response content carries the evidence a quality guard rule requires."""
    content = response_content or ""
    rule_name = rule.get("name")
    if rule_name == "exact_market_lookback":
        return _has_exact_market_lookback_evidence(content)

    evidence_pattern = rule.get("evidence_pattern")
    return bool(evidence_pattern and evidence_pattern.search(content))


def _has_exact_market_lookback_evidence(content: str) -> bool:
    """Return True when a lookback marker has a nearby INR price not surrounded by approximation language."""
    window_before = 120
    window_after = 220
    for marker in LOOKBACK_MARKER_PATTERN.finditer(content):
        start = max(0, marker.start() - window_before)
        end = min(len(content), marker.end() + window_after)
        context_window = content[start:end]
        marker_offset = marker.start() - start
        marker_text = marker.group(0).lower()
        prefer_after_marker = bool(LOOKBACK_DIRECTION_PATTERN.search(marker_text))

        price_candidates = list(INR_PRICE_PATTERN.finditer(context_window))
        if prefer_after_marker:
            after_candidates = [m for m in price_candidates if m.start() >= marker_offset]
            if after_candidates:
                price_candidates = after_candidates

        for price_match in sorted(
            price_candidates,
            key=lambda m: abs(((m.start() + m.end()) // 2) - marker_offset),
        ):
            global_price_start = start + price_match.start()
            global_price_end = start + price_match.end()
            if _has_non_exact_language_near_span(content, global_price_start, global_price_end):
                continue
            return True

    return False


def _has_non_exact_language_near_span(content: str, span_start: int, span_end: int, radius: int = 25) -> bool:
    """Return True when approximation language appears within radius characters of the span."""
    start = max(0, span_start - radius)
    end = min(len(content), span_end + radius)
    return contains_non_exact_language(content[start:end])

def _format_error_response(error_message: str, original_query: str) -> Dict[str, Any]:
    """Return an error tool response carrying the fallback message and the reason."""
    fallback = ERROR_FALLBACK_TEMPLATE.format(error_message=error_message)
    return {
        "status": "error",
        "content": fallback,
        "search_query": original_query,
        "source": "error",
        "timestamp": datetime.now().isoformat(),
        "safety_status": "error",
        "reason": error_message,
        "user_message": fallback,
    }