"""Search History Utilities - Postgres logic for recording and reading a contact's recent searches.

Domain layer behind GET /api/search/history: search endpoints log each query to etown_search_history as a background task, keeping one row per distinct query and only the most recent N per contact, and the history route reads them back newest first.

Features:
    - One row per distinct query: re-searching a term (case-insensitive) bumps its timestamp
    - Exact history cap: the newly written row is excluded from the trim ranking
    - Idempotent write: the upsert plus a deterministic delete set can be replayed safely
    - Never raises: a write failure is logged and swallowed, safe as a background task
    - Query normalisation: trimmed, truncated to 500 chars, and blank queries are skipped

Functions:
    log_search_history(): Record a search, keeping only the N most recent distinct queries
    get_search_history(): Return a contact's recent searches, newest first

Dependencies:
    - app.db.client: Database.execute/Database.fetch, which own the retry policy
    - app.core.logging: logger used to surface a swallowed write failure

Updates:
    04-Sep-2026: History deduped via ON CONFLICT upsert; dropped nested retry; write failures logged.
"""

from app.core.logging import logger
from app.db.client import Database

_LOG_SEARCH_SQL = """
WITH upserted AS (
    INSERT INTO etown_search_history (contact_id, query, created_at)
    VALUES ($1, $2, NOW())
    ON CONFLICT (contact_id, lower(query))
    DO UPDATE SET query = EXCLUDED.query,
                  created_at = EXCLUDED.created_at
    RETURNING id
),
doomed AS (
    SELECT h.id
    FROM etown_search_history h
    WHERE h.contact_id = $1
      AND h.id <> (SELECT id FROM upserted)
    ORDER BY h.created_at DESC, h.id DESC
    OFFSET ($3::int - 1)
)
DELETE FROM etown_search_history
WHERE id IN (SELECT id FROM doomed)
"""

_GET_SEARCH_HISTORY_SQL = """
SELECT query, created_at
FROM etown_search_history
WHERE contact_id = $1
ORDER BY created_at DESC, id DESC
LIMIT $2
"""


async def log_search_history(
    contact_id: str,
    query: str,
    max_history: int = 10,
) -> None:
    """Upsert a user's search query and trim their history to the newest distinct queries, logging failures.

    Args:
        contact_id: User whose history is written.
        query: Search text; trimmed, truncated to 500 chars, blank ignored.
        max_history: Maximum distinct queries kept per user.
    """
    normalised_query = query.strip()[:500]
    if not normalised_query:
        return

    try:
        await Database.execute(
            _LOG_SEARCH_SQL,
            contact_id,
            normalised_query,
            max_history,
        )
    except Exception as e:
        logger.warning(
            "SEARCH_HISTORY | WRITE_FAILED | contact_id=%s | %s: %s",
            contact_id,
            type(e).__name__,
            e,
        )


async def get_search_history(
    contact_id: str,
    user_profile_id: str,
    limit: int = 10,
) -> list[dict]:
    """Return up to limit of the contact's recent searches with ISO timestamps; user_profile_id is unused."""
    rows = await Database.fetch(
        _GET_SEARCH_HISTORY_SQL,
        contact_id,
        limit,
    )
    return [
        {
            "query": row["query"],
            "timestamp": row["created_at"].isoformat(),
        }
        for row in rows
    ]
