"""Classification Examples Utilities - parameterized SQL for the intent classifier's tables.

Data layer behind the classification bridge, loader and re-mining worker: k-NN retrieval over etown_intent_examples, the query-expansion dictionary, decision logging to etown_classification_decisions, and Chaatie history reads for mining, all through the shared Database classmethods.

Tables:
    etown_intent_examples          - labeled utterances plus a 768-dim content_vector
    etown_query_expansions         - the short-form and synonym dictionary
    etown_classification_decisions - the decision log, for evaluation and shadow mode

Features:
    - ANN k-NN retrieval over intent examples, both sides unit-normalized in SQL
    - Loader support: insert, promote, activate, deactivate and stale-curated lookup
    - Decision logging, called fire-and-forget by the bridge
    - Mining support: recent etownuserchaatiehistory turns and the worker's persisted clock
    - Retry discipline: large reads use fetch_once, and no helper adds a retry decorator over Database's own

Classes:
    No classes defined (module exposes async data-access functions)

Functions:
    fetch_neighbors(): Nearest labeled examples for an embedding and scope
    fetch_active_expansions(): All active expansion rows for the expander
    insert_example() / insert_examples(): Load embedded examples
    upsert_expansion(): Insert or update one dictionary entry
    log_decision(): Append one decision-log row
    fetch_recent_turns(): Conversation-log turns from the last N hours
    fetch_last_mined_at(): Newest mined proposal's timestamp
    fetch_self_dispatched_prompts(): Turns the bridge answered itself, to exclude
    fetch_example_texts(): All stored example texts, for dedup
    fetch_examples_by_text(): id, label, source and active flag for given texts
    fetch_stale_curated(): Active curated rows whose text left the loader
    fetch_mined_examples(): Mined examples, for relabeling
    update_example_intent(): Change one example's label
    promote_examples(): Activate and restamp rows as curated
    activate_examples() / deactivate_examples(): Approve or retire staged rows
    count_examples() / count_expansions(): Loader verification counts

Dependencies:
    - app.db.client.Database: pooled asyncpg access with retry built in
    - app.core.logging: structured logging

Notes:
    Vectors bind as a Postgres vector string cast with ``$N::vector``, as in the search path.
    ``l2_normalize`` requires pgvector 0.7 or newer; dev and prod are on 0.8.0.

Updates:
    22-Jul-2026: Initial data-access layer; fetch_neighbors uses the <#> operator.
    10-Aug-2026: Unit-normalized k-NN in SQL; added activate_examples()/deactivate_examples().
    04-Sep-2026: fetch_recent_turns projects response_type; added loader clock, by-text, promote and stale-curated queries.
    27-Oct-2026: fetch_neighbors runs once without retry, since it sits on the per-turn hot path.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.db.client import Database
from app.core.logging import logger


async def fetch_neighbors(
    embedding_str: str,
    agent_scope: str,
    k: int,
) -> List[Dict[str, Any]]:
    """Return the k nearest active examples for a query embedding, including scope 'both'.

    Args:
        embedding_str: Query vector formatted by vector_to_postgres_string.
        agent_scope: 'member' or 'provider'.
        k: Number of neighbours to return.

    Returns:
        Dicts of intent_label, example_text and distance, nearest first; empty on error.
    """
    query = """
        SELECT intent_label, example_text,
               content_vector <#> l2_normalize($1::vector) AS distance
        FROM public.etown_intent_examples
        WHERE agent_scope IN ($2, 'both') AND is_active = TRUE
        ORDER BY content_vector <#> l2_normalize($1::vector) ASC
        LIMIT $3
    """
    try:
        rows = await Database.fetch_once(query, embedding_str, agent_scope, int(k))
        return [
            {
                "intent_label": r["intent_label"],
                "example_text": r["example_text"],
                "distance": float(r["distance"]),
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"[classification] fetch_neighbors failed: {e}", exc_info=True)
        return []


async def fetch_active_expansions() -> List[Dict[str, Any]]:
    """Return term, expansion, language and confidence for every active expansion row, or empty on error."""
    query = """
        SELECT term, expansion, language, confidence
        FROM public.etown_query_expansions
        WHERE is_active = TRUE
    """
    try:
        rows = await Database.fetch(query)
        return [
            {
                "term": r["term"],
                "expansion": r["expansion"],
                "language": r["language"],
                "confidence": float(r["confidence"]),
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"[classification] fetch_active_expansions failed: {e}", exc_info=True)
        return []


async def insert_example(
    intent_label: str,
    agent_scope: str,
    example_text: str,
    language: str,
    content_vector_str: str,
    source: str = "mined",
    is_active: bool = True,
) -> Optional[int]:
    """Insert one embedded labeled example with an l2-normalised vector.

    Args:
        intent_label: Canonical intent from the taxonomy.
        agent_scope: 'member', 'provider' or 'both'.
        example_text: The user utterance.
        language: Language tag such as 'en', 'ta' or 'hi'.
        content_vector_str: 768-dim vector formatted by vector_to_postgres_string.
        source: 'mined' or 'curated'.
        is_active: Whether the example is live for retrieval.

    Returns:
        The new row id, or None on failure.
    """
    query = """
        INSERT INTO public.etown_intent_examples
            (intent_label, agent_scope, example_text, language, content_vector, source, is_active)
        VALUES ($1, $2, $3, $4, l2_normalize($5::vector), $6, $7)
        RETURNING id
    """
    try:
        return await Database.fetchval(
            query,
            intent_label,
            agent_scope,
            example_text,
            language,
            content_vector_str,
            source,
            is_active,
        )
    except Exception as e:
        logger.error(f"[classification] insert_example failed: {e}", exc_info=True)
        return None


async def insert_examples(rows: List[Dict[str, Any]]) -> int:
    """Insert each row dict as an active example, one statement per row, and return how many succeeded."""
    inserted = 0
    for row in rows:
        new_id = await insert_example(
            intent_label=row["intent_label"],
            agent_scope=row["agent_scope"],
            example_text=row["example_text"],
            language=row.get("language", "en"),
            content_vector_str=row["content_vector_str"],
            source=row.get("source", "mined"),
        )
        if new_id is not None:
            inserted += 1
    return inserted


async def upsert_expansion(
    term: str,
    expansion: str,
    language: str = "en",
    confidence: float = 1.0,
    source: str = "curated",
) -> None:
    """Upsert one active expansion keyed by term, expansion and language, refreshing confidence and source."""
    query = """
        INSERT INTO public.etown_query_expansions
            (term, expansion, language, confidence, source, is_active)
        VALUES ($1, $2, $3, $4, $5, TRUE)
        ON CONFLICT (term, expansion, language)
        DO UPDATE SET confidence = EXCLUDED.confidence,
                      source = EXCLUDED.source,
                      is_active = TRUE
    """
    try:
        await Database.execute(query, term, expansion, language, confidence, source)
    except Exception as e:
        logger.error(f"[classification] upsert_expansion failed for '{term}': {e}", exc_info=True)


async def log_decision(record: Dict[str, Any]) -> None:
    """Insert one etown_classification_decisions row from a record dict, logging any failure as a warning."""
    query = """
        INSERT INTO public.etown_classification_decisions
            (conversation_id, user_role, raw_prompt, expanded_prompt,
             predicted_intent, top1_similarity, vote_margin, action, shadow, decision_reason)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
    """
    try:
        await Database.execute(
            query,
            record.get("conversation_id"),
            record.get("user_role"),
            record.get("raw_prompt"),
            record.get("expanded_prompt"),
            record.get("predicted_intent"),
            record.get("top1_similarity"),
            record.get("vote_margin"),
            record.get("action"),
            record.get("shadow", True),
            record.get("decision_reason"),
        )
    except Exception as e:
        logger.warning(f"[classification] log_decision failed: {e}")


async def fetch_recent_turns(lookback_hours: int) -> List[Dict[str, Any]]:
    """Return Chaatie history turns from the last lookback_hours, ordered by conversation and time, without retry."""
    query = """
        SELECT conversation_id, role, content, language, tools_used,
               metadata->>'response_type' AS response_type, created_at
        FROM public.etownuserchaatiehistory
        WHERE created_at >= NOW() - ($1::text || ' hours')::interval
        ORDER BY conversation_id ASC, created_at ASC
    """
    try:
        rows = await Database.fetch_once(query, str(int(lookback_hours)))
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[classification] fetch_recent_turns failed: {e}", exc_info=True)
        return []


async def fetch_last_mined_at() -> Optional[Any]:
    """Return the newest mined example's created_at, or None when none exist or the query fails."""
    query = """
        SELECT max(created_at) FROM public.etown_intent_examples WHERE source = 'mined'
    """
    try:
        return await Database.fetchval(query)
    except Exception as e:
        logger.warning(f"[classification] fetch_last_mined_at failed: {e}")
        return None


async def fetch_self_dispatched_prompts(lookback_hours: Optional[int] = None) -> set:
    """Return (conversation_id, raw_prompt) pairs the classifier auto-handled in live mode, optionally within lookback_hours."""
    query = """
        SELECT conversation_id, raw_prompt
        FROM public.etown_classification_decisions
        WHERE action = 'auto_handled' AND shadow = FALSE AND raw_prompt IS NOT NULL
    """
    params: List[Any] = []
    if lookback_hours:
        query += " AND created_at >= NOW() - ($1::text || ' hours')::interval"
        params.append(str(int(lookback_hours)))
    try:
        rows = await Database.fetch(query, *params)
        return {(r["conversation_id"], (r["raw_prompt"] or "").strip()) for r in rows}
    except Exception as e:
        logger.error(f"[classification] fetch_self_dispatched_prompts failed: {e}", exc_info=True)
        return set()


async def fetch_example_texts() -> set:
    """Return the set of every stored example_text, active or inactive."""
    try:
        rows = await Database.fetch("SELECT example_text FROM public.etown_intent_examples")
        return {r["example_text"] for r in rows}
    except Exception as e:
        logger.error(f"[classification] fetch_example_texts failed: {e}", exc_info=True)
        return set()


async def fetch_examples_by_text(texts: List[str]) -> List[Dict[str, Any]]:
    """Return id, example_text, intent_label, source and is_active for each stored example matching texts."""
    if not texts:
        return []
    try:
        rows = await Database.fetch(
            "SELECT id, example_text, intent_label, source, is_active "
            "FROM public.etown_intent_examples WHERE example_text = ANY($1::text[])",
            list(texts),
        )
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[classification] fetch_examples_by_text failed: {e}", exc_info=True)
        return []


async def fetch_stale_curated(curated_texts: List[str]) -> List[Dict[str, Any]]:
    """Return active curated examples whose text is not in curated_texts."""
    try:
        rows = await Database.fetch(
            "SELECT id, example_text, intent_label FROM public.etown_intent_examples "
            "WHERE source = 'curated' AND is_active AND NOT (example_text = ANY($1::text[]))",
            list(curated_texts),
        )
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[classification] fetch_stale_curated failed: {e}", exc_info=True)
        return []


async def promote_examples(example_ids: List[int], intent_label: str, agent_scope: str) -> int:
    """Activate the given examples as curated under intent_label and agent_scope, returning the rows changed."""
    if not example_ids:
        return 0
    try:
        status = await Database.execute(
            "UPDATE public.etown_intent_examples "
            "SET is_active = TRUE, intent_label = $2, agent_scope = $3, source = 'curated' "
            "WHERE id = ANY($1::bigint[]) "
            "  AND (is_active = FALSE OR intent_label <> $2 OR source <> 'curated')",
            [int(i) for i in example_ids], intent_label, agent_scope,
        )
        return int(str(status).rsplit(" ", 1)[-1]) if status else 0
    except Exception as e:
        logger.error(f"[classification] promote_examples failed: {e}", exc_info=True)
        return 0


async def fetch_mined_examples() -> List[Dict[str, Any]]:
    """Return id, example_text and intent_label for every mined example."""
    try:
        rows = await Database.fetch(
            "SELECT id, example_text, intent_label FROM public.etown_intent_examples "
            "WHERE source = 'mined'"
        )
        return [
            {"id": r["id"], "example_text": r["example_text"], "intent_label": r["intent_label"]}
            for r in rows
        ]
    except Exception as e:
        logger.error(f"[classification] fetch_mined_examples failed: {e}", exc_info=True)
        return []


async def update_example_intent(example_id: int, new_label: str) -> None:
    """Set one example's intent_label, logging any failure."""
    try:
        await Database.execute(
            "UPDATE public.etown_intent_examples SET intent_label = $1 WHERE id = $2",
            new_label, example_id,
        )
    except Exception as e:
        logger.error(f"[classification] update_example_intent({example_id}) failed: {e}", exc_info=True)


async def activate_examples(example_ids: List[int]) -> int:
    """Flip inactive examples with the given ids to active and return how many changed."""
    if not example_ids:
        return 0
    try:
        status = await Database.execute(
            "UPDATE public.etown_intent_examples SET is_active = TRUE "
            "WHERE id = ANY($1::bigint[]) AND is_active = FALSE",
            [int(i) for i in example_ids],
        )
        return int(str(status).rsplit(" ", 1)[-1]) if status else 0
    except Exception as e:
        logger.error(f"[classification] activate_examples failed: {e}", exc_info=True)
        return 0


async def deactivate_examples(example_ids: List[int]) -> int:
    """Flip active examples with the given ids to inactive and return how many changed."""
    if not example_ids:
        return 0
    try:
        status = await Database.execute(
            "UPDATE public.etown_intent_examples SET is_active = FALSE "
            "WHERE id = ANY($1::bigint[]) AND is_active = TRUE",
            [int(i) for i in example_ids],
        )
        return int(str(status).rsplit(" ", 1)[-1]) if status else 0
    except Exception as e:
        logger.error(f"[classification] deactivate_examples failed: {e}", exc_info=True)
        return 0


async def count_examples(active_only: bool = True) -> int:
    """Return the number of intent examples, active only by default, or 0 on error."""
    query = "SELECT COUNT(*) FROM public.etown_intent_examples"
    if active_only:
        query += " WHERE is_active = TRUE"
    try:
        return int(await Database.fetchval(query) or 0)
    except Exception as e:
        logger.error(f"[classification] count_examples failed: {e}", exc_info=True)
        return 0


async def count_expansions(active_only: bool = True) -> int:
    """Return the number of query expansion rows, active only by default, or 0 on error."""
    query = "SELECT COUNT(*) FROM public.etown_query_expansions"
    if active_only:
        query += " WHERE is_active = TRUE"
    try:
        return int(await Database.fetchval(query) or 0)
    except Exception as e:
        logger.error(f"[classification] count_expansions failed: {e}", exc_info=True)
        return 0
