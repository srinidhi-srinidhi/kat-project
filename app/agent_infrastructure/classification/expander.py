"""Query Expander - deterministic short-form and synonym expansion for the Classification Bridge.

Classification helper that appends dictionary expansions from etown_query_expansions and romanized-Tamil mappings to a query before embedding, keeping the original tokens and calling no LLM. The active dictionary is cached for an hour and can be cleared on demand.

Features:
    - expand(): additive dictionary expansion + romanized-Tamil mapping (LLM-free).
    - Confidence-gated: only entries at or above ``min_confidence`` are applied.
    - Multi-word ("ac repair") and single-token ("ac") terms both supported.

Classes:
    No classes defined (module exposes expansion functions and a cached dictionary loader)

Functions:
    _load_expansion_dict(): Load and index the active expansion dictionary (cached, 1h TTL).
    expand(text, language, min_confidence): Return the additively expanded query.
    clear_expansion_cache(): Invalidate the cached dictionary.

Dependencies:
    - app.agent_infrastructure.classification.examples_utils.fetch_active_expansions
    - app.agent_infrastructure.tools.tools_utils.search_helpers.map_romanized_tamil_terms
    - async_lru.alru_cache: 1-hour TTL cache for the loaded expansion dictionary.
    - app.core.logging: Application logger for load-failure warnings.

Updates:
    22-Jul-2026: Initial expander - additive dictionary plus romanized-Tamil mapping, no LLM.
"""

from __future__ import annotations

from typing import Dict, List

from async_lru import alru_cache

from app.agent_infrastructure.classification import examples_utils
from app.agent_infrastructure.tools.tools_utils.search_helpers import map_romanized_tamil_terms
from app.core.logging import logger

_DEFAULT_MIN_CONFIDENCE = 0.5


@alru_cache(maxsize=1, ttl=3600)
async def _load_expansion_dict() -> Dict[str, List[Dict[str, float]]]:
    """Load the active expansion dictionary, indexed by lower-cased term, cached for one hour."""
    index: Dict[str, List[Dict[str, float]]] = {}
    rows = await examples_utils.fetch_active_expansions()
    for row in rows:
        term = (row.get("term") or "").strip().lower()
        expansion = (row.get("expansion") or "").strip()
        if not term or not expansion:
            continue
        index.setdefault(term, []).append(
            {"expansion": expansion, "confidence": float(row.get("confidence", 1.0))}
        )
    logger.info(f"[classification] loaded {len(index)} expansion terms into cache")
    return index


def clear_expansion_cache() -> None:
    """Invalidate the cached expansion dictionary."""
    _load_expansion_dict.cache_clear()


async def expand(
    text: str,
    language: str = "en",
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
) -> str:
    """Append dictionary expansions and romanized-Tamil mappings to a preprocessed query, returning it unchanged on error.

    Args:
        text: Preprocessed query text.
        language: Query language tag; currently unused by the expansion logic.
        min_confidence: Minimum dictionary confidence for an expansion to apply.

    Returns:
        The stripped query with any matched expansions appended.
    """
    if not text:
        return text or ""

    try:
        index = await _load_expansion_dict()
    except Exception as e:
        logger.warning(f"[classification] expansion dictionary load failed: {e}")
        return text

    base = text.strip()
    lower = base.lower()
    tokens = set(lower.split())
    additions: List[str] = []

    for term, entries in index.items():
        matched = (term in lower) if (" " in term) else (term in tokens)
        if not matched:
            continue
        for entry in entries:
            if entry["confidence"] < min_confidence:
                continue
            expansion = entry["expansion"]
            exp_lower = expansion.lower()
            if exp_lower in lower or exp_lower in " ".join(additions).lower():
                continue
            additions.append(expansion)

    try:
        if base.isascii():
            mapped = map_romanized_tamil_terms(base)
        else:
            mapped = base
        if mapped and mapped.lower() != lower:
            for token in mapped.split():
                if token and token not in lower and token not in " ".join(additions):
                    additions.append(token)
    except Exception as e:
        logger.debug(f"[classification] romanized-Tamil mapping skipped: {e}")

    if not additions:
        return base
    return f"{base} {' '.join(additions)}".strip()
