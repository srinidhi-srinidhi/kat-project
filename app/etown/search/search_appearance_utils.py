"""Search Appearance Utilities - records which providers each search surfaced to which user.

Domain logging helper called from both /api/search endpoints and the Chaatie chat turn: upserts one etown_provider_search_appearances row per provider, searcher, query and IST day, which the weekly_unverified_provider_search view reads.

Features:
    - One row per provider + searcher + query + IST day; repeats bump appearances and last_seen_at
    - IST day bucket: search_date is the Asia/Kolkata date, aligned with the weekly report
    - Batch deduplication: a provider repeated on one page is collapsed, keeping its best rank
    - Key-spelling tolerant: reads both Etown_providerid and providerid style keys
    - Never raises: a write failure is logged and swallowed
    - Rows without a providerid (unlinked Places/web results) are skipped

Functions:
    _first_value(): Return the first non-empty value among candidate keys of a result row
    log_provider_search_appearances(): Record the providers one search surfaced to one user

Dependencies:
    - app.db.client: Database.execute, which owns the retry policy
    - app.etown.search.aisearch_utils: normalize_text_for_matching for the dedup key
    - app.core.logging: logger used to surface a swallowed write failure

Notes:
    appearances is incremented, not assigned, so a Database.execute retry that replays an
    already-committed statement can over-count it. The count is analytics, and losing the
    row would be worse than an occasional double increment.

Updates:
    17-Sep-2026: Initial creation - provider search-appearance logging behind the weekly unverified report.
"""

from typing import Any, Dict, Optional, Sequence

from app.core.logging import logger
from app.db.client import Database
from app.etown.search.aisearch_utils import normalize_text_for_matching

MAX_APPEARANCES_PER_PAGE = 100
MAX_QUERY_CHARS = 500

_PROVIDER_ID_KEYS = ("Etown_providerid", "Etown_providerId", "providerid")
_USER_PROFILE_ID_KEYS = ("Etown_userProfileId", "Etown_userprofileid", "userprofileid")

_LOG_APPEARANCES_SQL = """
INSERT INTO etown_provider_search_appearances
    (providerid, userprofileid, contact_id, query, normalized_query,
     search_date, endpoint, best_position)
SELECT t.providerid, t.userprofileid, $1, $2, $3,
       (now() AT TIME ZONE 'Asia/Kolkata')::date, $4, t.best_position
FROM unnest($5::text[], $6::text[], $7::int[])
     AS t(providerid, userprofileid, best_position)
ON CONFLICT (providerid, contact_id, search_date, normalized_query)
DO UPDATE SET appearances   = etown_provider_search_appearances.appearances + 1,
              last_seen_at  = now(),
              best_position = LEAST(etown_provider_search_appearances.best_position,
                                    EXCLUDED.best_position),
              userprofileid = COALESCE(EXCLUDED.userprofileid,
                                       etown_provider_search_appearances.userprofileid)
"""


def _first_value(row: Dict[str, Any], keys: Sequence[str]) -> Optional[str]:
    """Return the first non-empty value among ``keys``, as a string."""
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


async def log_provider_search_appearances(
    rows: Any,
    query: str,
    contact_id: Optional[str],
    endpoint: str,
    offset: int = 0,
) -> int:
    """Record the providers one search surfaced to one user and return how many were written, 0 on failure.

    Args:
        rows: Provider rows the search returned; anything but a list is ignored.
        query: The searched text, trimmed and truncated; blank writes nothing.
        contact_id: The searching user's contact id; nothing is written without one.
        endpoint: Serving surface: aisearch, ai_search_v2 or chaatie.
        offset: Rank of the first row within the full result set.

    Returns:
        Number of providers written.
    """
    stored_query = (query or "").strip()[:MAX_QUERY_CHARS]
    if not stored_query or not contact_id or not isinstance(rows, list):
        return 0

    normalized_query = normalize_text_for_matching(stored_query)
    if not normalized_query:
        return 0

    best: Dict[str, Dict[str, Any]] = {}
    for index, row in enumerate(rows[:MAX_APPEARANCES_PER_PAGE]):
        if not isinstance(row, dict):
            continue
        provider_id = _first_value(row, _PROVIDER_ID_KEYS)
        if not provider_id:
            continue

        position = offset + index
        profile_id = _first_value(row, _USER_PROFILE_ID_KEYS)
        seen = best.get(provider_id)
        if seen is None:
            best[provider_id] = {"userprofileid": profile_id, "position": position}
        else:
            if position < seen["position"]:
                seen["position"] = position
            if seen["userprofileid"] is None:
                seen["userprofileid"] = profile_id

    if not best:
        return 0

    provider_ids = list(best)
    profile_ids = [best[pid]["userprofileid"] for pid in provider_ids]
    positions = [best[pid]["position"] for pid in provider_ids]

    try:
        await Database.execute(
            _LOG_APPEARANCES_SQL,
            contact_id,
            stored_query,
            normalized_query,
            endpoint,
            provider_ids,
            profile_ids,
            positions,
        )
        return len(provider_ids)
    except Exception as e:
        logger.warning(
            "SEARCH_APPEARANCE | WRITE_FAILED | endpoint=%s | contact_id=%s | %s: %s",
            endpoint,
            contact_id,
            type(e).__name__,
            e,
        )
        return 0
