"""Web Search Quality Rules Config - shared quality guards, keywords, patterns and templates for web search.

Configuration module for web_search_tool: holds the quality-guard rules, safety and real-time keyword lists, validation and evidence regexes, suggestion and retry templates, plus helpers that detect non-exact lookback wording and local-business queries.

Features:
    - Rule-based quality guard definitions for rainfall windows, UV index, and exact market lookbacks
    - Safety filter keyword lists and real-time query hint collections
    - Centralized regex patterns for query validation and evidence extraction
    - Contextual suggestion and retry-rewrite templates
    - Shared helper to detect non-exact language for exact timestamp lookbacks
    - Local-business query detection to route "near me" style queries away from factual web search

Classes:
    No classes defined (module exports constants and helpers)

Functions:
    contains_non_exact_language(): Detect approximation/range language for exact lookback policy
    is_local_business_query(): Distinguish "find me a nearby business" from an informational query

Dependencies:
    - re: Regular expressions for query/evidence pattern matching
    - typing: Type hints for exported mappings

Updates:
    27-Mar-2026: Initial creation in app/utils; expanded UV evidence patterns; consolidated keyword and pattern modules.
"""

import re
from typing import Dict, Tuple


CONTENT_FILTER_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "illegal": (
        "drug trafficking",
        "weapon sales",
        "terrorism",
        "child exploitation",
        "human trafficking",
        "illegal gambling",
        "fraud",
        "money laundering",
    ),
    "adult": (
        "pornography",
        "explicit content",
        "adult services",
        "sexual content",
        "escort services",
        "adult entertainment",
        "strip club",
        "sex work",
    ),
    "harmful": (
        "self harm",
        "suicide methods",
        "violence",
        "hate speech",
        "extremism",
        "harassment",
        "discrimination",
        "dangerous substance",
    ),
}


REALTIME_KEYWORDS: Tuple[str, ...] = (
    "current",
    "latest",
    "recent",
    "today",
    "now",
    "breaking",
    "weather",
    "temperature",
    "forecast",
    "stock",
    "price",
    "news",
    "update",
    "happening",
    "live",
    "tomorrow",
    "yesterday",
    "date",
    "day",
    "time",
    "calendar",
    "week",
    "month",
    "year",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "weekend",
)


REALTIME_PHRASES: Tuple[str, ...] = (
    "this year",
    "today's",
    "what day",
    "what date",
    "what time",
    "tomorrow is",
    "yesterday was",
    "current date",
    "current time",
    "today is",
    "this week",
    "next week",
    "last week",
    "this month",
)


CONTEXTUAL_SUGGESTION_RULES = (
    {
        "keywords": ("weather", "temperature", "forecast"),
        "message": "Please verify local weather authority updates for exact station readings.",
    },
    {
        "keywords": ("news", "current", "events"),
        "message": "Please retry with a specific publication, date, or location for higher precision.",
    },
    {
        "keywords": ("price", "cost", "money"),
        "message": "Please retry with a specific exchange, market, and timestamp.",
    },
)

DEFAULT_CONTEXTUAL_SUGGESTION = "However, I can help you discover amazing local services and businesses on Etown."


PRECISION_REWRITE_TEMPLATES = {
    "uv_index_now": (
        "{query} Include an explicit numeric UV index value for the location now, "
        "with observation time and source name."
    ),
    "rainfall_window": (
        "{query} Include an explicit rainfall total with unit (mm/cm/inches) "
        "for the requested window, with source and timestamp."
    ),
    "exact_market_lookback": (
        "{query} Provide exact INR value for now and exact INR value for the requested lookback timestamp "
        "(for example, 10 minutes ago), including time markers and no approximation terms."
    ),
}

DEFAULT_PRECISION_REWRITE_TEMPLATE = (
    "{query} Provide explicit numeric values with units and timestamps from a clearly named source."
)


QUALITY_GUARD_RESPONSE_TEMPLATE = (
    "I found related live information for '{query}', but I cannot verify an exact "
    "source-grounded value from the current search summary.\n\n"
    "{guard_message}\n\n"
    "Please ask me to retry with a stricter source hint (for example, official weather authority or exchange page)."
)


SUCCESS_USER_MESSAGE_TEMPLATE = (
    "Here's the latest information I found about '{query}':\n\n"
    "{content}\n\n"
    "*This information is from real-time web search and may be subject to change.*"
)


ERROR_FALLBACK_TEMPLATE = (
    "I'm unable to perform a web search right now. {error_message}\n\n"
    "Please try again in a moment, or refine the query with a specific source, location, or timestamp."
)


MALICIOUS_INPUT_PATTERN = re.compile(
    r"<script.*?>.*?</script>|javascript:|data:.*base64|[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]",
    re.IGNORECASE,
)


REALTIME_QUESTION_PATTERN = re.compile(
    r"what.*(happening|going on|new|day|date|time)"
    r"|how.*(today|current|now)"
    r"|when.*(next|upcoming|soon|today|tomorrow)"
    r"|where.*(currently|now|today)"
    r"|what day.*(today|tomorrow|yesterday|is)"
    r"|what date.*(today|tomorrow|yesterday|is)"
    r"|what time.*(now|current)",
    re.IGNORECASE,
)


LOOKBACK_MARKER_PATTERN = re.compile(
    r"\b\d+\s*(minute|min|minutes)\s*(ago|earlier|before|back)\b"
    r"|\b(?:at|as\s+of)\s+\d{1,2}:\d{2}(?:\s*(?:am|pm|ist|utc))?\b",
    re.IGNORECASE,
)


INR_PRICE_PATTERN = re.compile(
    r"(?:₹|rs\.?|inr)\s*\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s*(?:₹|rs\.?|inr)\b",
    re.IGNORECASE,
)


LOOKBACK_DIRECTION_PATTERN = re.compile(r"ago|earlier|before|back", re.IGNORECASE)

LOCAL_BUSINESS_QUERY_PATTERN = re.compile(
    r"\b(near\s*(me|by|here)|around\s+me|in\s+my\s+area|close\s+by)\b"
    r"|\b(shop|shops|store|stores|showroom|dealer|dealers|outlet|workshop|garage|"
    r"repair|repairs|repairing|servicing|service\s*cent(?:er|re)|"
    r"plumber|electrician|carpenter|mechanic|technician|painter|welder|tailor|"
    r"salon|parlour|parlor|spa|clinic|hospital|pharmacy|chemist|"
    r"restaurant|hotel|bakery|caterer|catering|"
    r"contractor|builder|mover|packers|laundry|dry\s*clean)\b",
    re.IGNORECASE,
)

INFORMATIONAL_QUERY_PATTERN = re.compile(
    r"\b(weather|rainfall|temperature|uv\s*index|forecast|news|headline|"
    r"score|match|fixture|tournament|stock|share\s*price|exchange\s*rate|"
    r"who\s+is|what\s+is|what\s+are|why\s+is|how\s+(?:do|does|to)|when\s+(?:is|was|did)|"
    r"meaning|definition|history|recipe)\b",
    re.IGNORECASE,
)


def is_local_business_query(query: str) -> bool:
    """Return True when a web-search query matches the local-business pattern and not the informational one."""
    if not query:
        return False
    if INFORMATIONAL_QUERY_PATTERN.search(query):
        return False
    return bool(LOCAL_BUSINESS_QUERY_PATTERN.search(query))


QUALITY_GUARD_RULES = [
    {
        "name": "rainfall_window",
        "query_pattern": re.compile(r"\brainfall\b.*\blast\s+\d+\s*(hour|hours|day|days)\b", re.IGNORECASE),
        "evidence_pattern": re.compile(
            r"(?:\b(rainfall|precipitation)\b[^.!?\n]{0,100}\b\d+(?:\.\d+)?\s*(mm|cm|inch|inches)\b)"
            r"|(?:\b\d+(?:\.\d+)?\s*(mm|cm|inch|inches)\b[^.!?\n]{0,100}\b(rainfall|precipitation)\b)",
            re.IGNORECASE,
        ),
        "message": "I could not find an explicit rainfall measurement with unit (for example, mm) for the requested time window.",
    },
    {
        "name": "uv_index_now",
        "query_pattern": re.compile(r"\buv\s*index\b", re.IGNORECASE),
        "evidence_pattern": re.compile(
            r"(?:\b(?:uv|ultraviolet)(?:\s+(?:index|level|radiation|rays|reading))?\b"
            r"[^.!?\n]{0,80}\b(?:is|at|of|value(?:\s+of)?|:|=)?\s*\d+(?:\.\d+)?\b)"
            r"|(?:\b\d+(?:\.\d+)?\b[^.!?\n]{0,40}\b(?:uv|uv\s+index|ultraviolet(?:\s+index|\s+level)?)\b)",
            re.IGNORECASE,
        ),
        "message": "I could not find a clearly stated UV index value in the current live search result.",
    },
    {
        "name": "exact_market_lookback",
        "query_pattern": re.compile(
            r"\b(bitcoin|btc|crypto|stock|price)\b.*\b(now|current|right\s+now)\b.*\b\d+\s*(minute|min|minutes)\s+ago\b",
            re.IGNORECASE,
        ),
        "evidence_pattern": re.compile(
            r"(?:\b\d+\s*(minute|min|minutes)\s+ago\b[^.!?\n]{0,100}(?:₹|rs\.?|inr)\s*\d[\d,]*(?:\.\d+)?)"
            r"|(?:\b(?:at|as\s+of)\s+\d{1,2}:\d{2}\b[^.!?\n]{0,100}(?:₹|rs\.?|inr)\s*\d[\d,]*(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        "message": (
            "I found current market information, but the exact value for the requested prior timestamp "
            "is not directly available in the live source summary."
        ),
    },
]


def contains_non_exact_language(response_content: str) -> bool:
    """Return True when web search content contains approximation, range or not-available language."""
    content_lower = (response_content or "").lower()
    return bool(
        re.search(
            r"\b(not directly available|not available|approximately|approx\.?|around|about|between|range|similar range|estimated|estimate|roughly)\b",
            content_lower,
        )
    )