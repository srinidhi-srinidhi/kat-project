"""Classification Preprocessor - deterministic, LLM-free normalization of classifier queries and examples.

Classifier helper applied identically to query text at classify time and example text at load time, so both share one normalized space; it keeps letters, digits and combining marks of any script and reuses strip_proximity_phrases from search_helpers.

Features:
    - preprocess(): script-agnostic normalize + strip proximity phrases (canonical entry).
    - Preserves Tamil AND Hindi (Devanagari) characters, including combining marks.
    - distill_query(): strip leading filler and trailing qualifiers so a query reads as a noun phrase.

Classes:
    No classes defined (module exposes normalization/preprocessing functions)

Functions:
    _normalize(text): NFKC + casefold + punctuation-to-space, preserving scripts/marks.
    preprocess(text): Return the normalized, proximity-stripped form of ``text``.
    distill_query(text): Return ``text`` with conversational filler and trailing qualifiers removed.

Dependencies:
    - app.etown.search.aisearch_utils.strip_proximity_phrases (multilingual)
    - unicodedata / re (stdlib)

Updates:
    22-Jul-2026: Initial preprocessor (T2.2); script-agnostic normalization replaces normalize_search_text.
    24-Jul-2026: Added distill_query() so dispatch text reads as the core noun phrase.
    07-Sep-2026: distill_query() strips more trailing qualifiers and leading forms.
"""

from __future__ import annotations

import re
import unicodedata

from app.etown.search.aisearch_utils import strip_proximity_phrases

_REPEAT_RE = re.compile(r"([a-z])\1{2,}")
_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Return NFKC-lowercased text with punctuation spaced out, long character runs capped at two and whitespace collapsed."""
    normalized = unicodedata.normalize("NFKC", str(text)).lower()
    kept: list[str] = []
    for ch in normalized:
        if ch.isspace():
            kept.append(" ")
        elif ch.isalnum() or unicodedata.category(ch).startswith("M"):
            kept.append(ch)
        else:
            kept.append(" ")
    normalized = "".join(kept)
    normalized = _REPEAT_RE.sub(r"\1\1", normalized)
    return _WS_RE.sub(" ", normalized).strip()


def preprocess(text: str | None) -> str:
    """Return a normalized, proximity-phrase-stripped query for classification, or an empty string for empty input."""
    if not text:
        return ""
    normalized = _normalize(text)
    if not normalized:
        return ""
    return strip_proximity_phrases(normalized)


_LEAD_FILLER_RE = re.compile(
    r"^(?:"
    r"i\s+(?:want|wanna|need|wish)(?:\s+to)?(?:\s+(?:buy|book|find|get|purchase|hire|see|rent))?\s+"
    r"|i\s+would\s+like(?:\s+to)?(?:\s+(?:buy|book|find|get|see))?\s+"
    r"|i\s+(?:am|m)\s+(?:looking|searching)\s+for\s+"
    r"|(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:find|get|show|help|suggest|recommend)\s+(?:me\s+)?"
    r"|please\s+(?:find|get|show|suggest|recommend)\s+(?:me\s+)?"
    r"|(?:looking|searching)\s+for\s+"
    r"|help\s+me\s+(?:find|get)\s+"
    r"|(?:find|show|get|suggest|recommend)\s+me\s+"
    r"|do\s+you\s+have\s+(?:any\s+)?"
    r"|is\s+there\s+(?:a|an|any)\s+"
    r"|i\s+needed?\s+(?:a|an|the|some|any)?\s*"
    r"|(?:can|could|will|would)\s+(?:someone|somebody|anyone|anybody)\s+(?:come\s+and\s+)?"
    r"(?:please\s+)?"
    r"|(?:someone|somebody|anyone|anybody)\s+(?:who\s+can\s+|to\s+)"
    r"|(?:find|search|show|get|need|want|buy|book|suggest|recommend)\s+"
    r")",
    re.IGNORECASE,
)
_LEAD_ARTICLE_RE = re.compile(r"^(?:a|an|the|some|any|me|new|fresh)\s+", re.IGNORECASE)

_BENEFICIARY = (
    r"child|children|kid|kids|son|daughter|baby|wife|husband|friend|mother|father|mom|dad"
    r"|parents|family|sister|brother|self|me"
)
_OCCASION = (
    r"birthday|wedding|marriage|anniversary|party|function|event|housewarming|festival"
    r"|reception|engagement|baby\s+shower"
)
_PREMISES = (
    r"house|home|flat|apartment|room|kitchen|bathroom|office|shop|building|terrace|garden"
)
_TRAIL_QUALIFIER_RE = re.compile(
    r"(?:"
    rf"\s+for\s+(?:my|our|the)\s+(?:{_BENEFICIARY})"
    rf"|\s+for\s+(?:a|an|my|our|the)\s+(?:{_OCCASION})"
    rf"|\s+(?:in|at|inside|for)\s+(?:my|our|the)\s+(?:{_PREMISES})"
    r"|\s+(?:near|around|close\s+to|nearby)(?:\s+(?:me|here|us|my\s+\w+))?"
    r"|\s+(?:do\s+you\s+know|is\s+there|are\s+there|can\s+you\s+find)\b.*"
    r")\s*$",
    re.IGNORECASE,
)


def distill_query(text: str | None) -> str:
    """Strip leading filler, articles and trailing qualifiers from an ASCII query, returning non-ASCII or emptied input unchanged."""
    if not text:
        return text or ""
    original = text.strip()
    if not original or not original.isascii():
        return original
    core = original
    prev = ""
    while prev != core:
        prev = core
        core = _LEAD_FILLER_RE.sub("", core, count=1).strip()
        core = _LEAD_ARTICLE_RE.sub("", core, count=1).strip()
        core = _TRAIL_QUALIFIER_RE.sub("", core, count=1).strip()
    return core or prev or original
