"""Memory Utilities - Postgres and mem0 logic for Chaatie's long-term memory and provider affinity.

Domain layer behind /api/chat and /api/memory: scores mem0 search results into floor-filtered similarities, reads previously booked provider-services from etown_allrequests, shapes both into prompt text, and handles memory listing, deletion, the on/off switch and the per-turn decision log.

Features:
    - Distance-to-Similarity: converts mem0's pgvector cosine distance into a 0-1 similarity
    - Relevance Floor: drops weakly-matching memories instead of padding to a fixed count
    - Provider Affinity: previously-booked provider-services ranked from etown_allrequests
    - Deterministic Name Resolution: fuzzy-matches an imprecisely named provider against own bookings
    - Prompt Shaping: renders both halves into the plain-text blocks the prompt template expects
    - User Control: list, delete and switch off memory, plus the explicit remember/forget path
    - Decision Log: one append-only row per turn with retrieval candidates and extraction outcome
    - No mem0 Threshold: mem0's threshold= keeps the worst pgvector matches, so it is never passed

Classes:
    No classes defined (module exposes function-based utilities)

Functions:
    score_memories(): Convert a mem0 search result into similarity-scored, floor-filtered facts
    format_memories_for_prompt(): Render scored memories as prompt lines
    fetch_provider_affinity(): Read the member's previously-booked provider-services from Postgres
    _identifier_tokens(): Extract the identifying words at the head of a business name
    match_affinity_mention(): Resolve an imprecisely-named provider against the user's bookings
    format_affinity_for_prompt(): Render affinity rows as prompt lines
    list_memories(): One page of a user's stored memories, newest first
    delete_memory(): Remove one memory, scoped to its owner
    delete_all_memories(): Remove everything stored about a user
    is_memory_enabled(): Whether long-term memory is on for a user (fails open)
    set_memory_enabled(): Turn long-term memory on or off for a user
    _sync_remember_fact(): Store one already-phrased fact verbatim (blocking)
    _sync_find_memory_to_forget(): Locate the memory a user asked to forget (blocking)
    _as_decimal(): Coerce a float to a Decimal so a numeric column stores the decimal meant
    build_candidate_log(): Render every retrieved candidate with its similarity for the log
    log_memory_decision(): Record the retrieval half of a turn (fire-and-forget)
    record_extraction_outcome(): Fill in the extraction half of a turn (fire-and-forget)

Dependencies:
    - rapidfuzz: fuzz.ratio and JaroWinkler for provider-name matching
    - app.agent_infrastructure.tools.tools_utils.search_helpers: normalize_search_text
    - app.db.client: fetch_once for the affinity read; fetch/execute/fetchval for memory controls
    - app.core.memory: retrieval tuning constants and get_memory_client (lazy import)

Updates:
    07-Sep-2026: New module for mem0 scoring, provider affinity, memory-control routes and decision logging.
"""

import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

from app.agent_infrastructure.tools.tools_utils.search_helpers import normalize_search_text
from app.core.memory import (
    MEMORY_INJECT_LIMIT,
    MEMORY_SEARCH_LIMIT,
    MEMORY_SIMILARITY_FLOOR,
)
from app.db.client import Database

logger = logging.getLogger(__name__)

AFFINITY_LIMIT = 8

AFFINITY_MATCH_FLOOR = 85
AFFINITY_MIN_MENTION_CHARS = 3
AFFINITY_GENERIC_TOKENS = frozenset({
    "and", "the", "for", "of", "services", "service", "works", "work", "shop", "store",
    "co", "company", "centre", "center", "enterprises", "agency", "pvt", "ltd", "limited",
})

_PROVIDER_AFFINITY_SQL = """
SELECT
    ps.providerservicesid,
    ps.businessname,
    ps.services,
    ps.city,
    COUNT(*)              AS bookingcount,
    MAX(ar.createdon)     AS lastbookedon
FROM etown_allrequests ar
INNER JOIN etown_requestservicelists rsl
    ON rsl.bookingid = ar.allrequestid
INNER JOIN etown_providerservices ps
    ON ps.providerservicesid = rsl.providerservices
WHERE (ar.requestorid = $1 OR ar.requestoruserprofileid = $2)
  AND ps.businessname IS NOT NULL
  AND NOT etown_is_deleted(ps.contactid)
  AND NOT etown_is_deleted(ps.providerid)
GROUP BY ps.providerservicesid, ps.businessname, ps.services, ps.city
ORDER BY bookingcount DESC, lastbookedon DESC NULLS LAST
LIMIT $3
"""


def score_memories(raw_result: Any) -> List[Dict[str, Any]]:
    """Convert mem0 search distances into up to MEMORY_INJECT_LIMIT similarity-ranked memories above the floor."""
    if not raw_result:
        return []

    entries = raw_result.get("results", []) if isinstance(raw_result, dict) else raw_result
    if not entries:
        return []

    scored: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        text = (entry.get("memory") or "").strip()
        if not text:
            continue
        try:
            distance = float(entry.get("score"))
        except (TypeError, ValueError):
            continue
        similarity = 1.0 - distance
        if similarity < MEMORY_SIMILARITY_FLOOR:
            continue
        scored.append({"memory": text, "similarity": similarity})

    scored.sort(key=lambda item: item["similarity"], reverse=True)
    return scored[:MEMORY_INJECT_LIMIT]


def format_memories_for_prompt(scored: List[Dict[str, Any]]) -> str:
    """Render scored memories as one "- fact" prompt line each, or "" when there are none."""
    return "\n".join(f"- {item['memory']}" for item in scored)


async def fetch_provider_affinity(
    contact_id: str,
    user_profile_id: str,
    limit: int = AFFINITY_LIMIT,
) -> List[Dict[str, Any]]:
    """Return the non-deleted provider-services this member has booked before, most-booked first."""
    if not contact_id and not user_profile_id:
        return []

    rows = await Database.fetch_once(
        _PROVIDER_AFFINITY_SQL, contact_id or "", user_profile_id or "", limit
    )
    return [dict(row) for row in rows]


def _identifier_tokens(businessname: str, services: str) -> List[str]:
    """Return the leading identity words of a business name, joining initialisms and stopping at the first trade word."""
    tokens = normalize_search_text(businessname).split()
    if not tokens:
        return []

    trade = set(normalize_search_text(services).split()) | AFFINITY_GENERIC_TOKENS

    initials, index = [], 0
    while index < len(tokens) and len(tokens[index]) <= 2:
        initials.append(tokens[index])
        index += 1

    head: List[str] = []
    if len(initials) >= 2:
        head.append("".join(initials))
    for token in tokens[index:]:
        if token in trade or any(fuzz.ratio(token, word) >= 85 for word in trade):
            break
        head.append(token)
    if not head and initials:
        head.append("".join(initials))

    return [token for token in head if len(token) >= AFFINITY_MIN_MENTION_CHARS]


def match_affinity_mention(
    prompt: str,
    rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Fuzzy-match one- and two-word runs of the prompt to booked providers' identifiers and return the best row, or None."""
    normalized = normalize_search_text(prompt)
    if not normalized or not rows:
        return None

    words = normalized.split()
    mentions = {
        " ".join(words[start:start + size])
        for size in (1, 2)
        for start in range(len(words) - size + 1)
    }
    mentions = {m for m in mentions if len(m) >= AFFINITY_MIN_MENTION_CHARS}
    if not mentions:
        return None

    best: Optional[Dict[str, Any]] = None
    for row in rows:
        identifiers = _identifier_tokens(
            row.get("businessname") or "", row.get("services") or ""
        )
        if not identifiers:
            continue
        joined = " ".join(identifiers)
        for mention in mentions:
            targets = {identifiers[0]} if " " not in mention else {identifiers[0], joined}
            score = max(
                max(fuzz.ratio(mention, target), JaroWinkler.similarity(mention, target) * 100)
                for target in targets
            )
            if score >= AFFINITY_MATCH_FLOOR and (best is None or score > best["matchscore"]):
                best = {**row, "matchscore": score, "matchedmention": mention}

    if best:
        logger.info(
            "Affinity mention resolved: %r -> %s (score %.1f)",
            best["matchedmention"], best.get("businessname"), best["matchscore"],
        )
    return best


def format_affinity_for_prompt(
    rows: List[Dict[str, Any]],
    matched: Optional[Dict[str, Any]] = None,
) -> str:
    """Render affinity rows as previously-booked prompt lines plus a RESOLVED line for a matched mention, or ""."""
    lines = []
    for row in rows:
        name = (row.get("businessname") or "").strip()
        if not name:
            continue
        service = (row.get("services") or "").strip()
        count = row.get("bookingcount") or 0
        times = "once" if count == 1 else f"{count} times"
        detail = f" for {service}" if service else ""
        lines.append(
            f"- {name}{detail} — booked {times} "
            f"(providerservicesid: {row.get('providerservicesid')})"
        )

    if not lines:
        return ""

    if matched:
        lines.append(
            f"\nRESOLVED: the user's wording \"{matched['matchedmention']}\" refers to "
            f"{matched.get('businessname')} (providerservicesid: "
            f"{matched.get('providerservicesid')}) from the list above. Use that provider."
        )
    return "\n".join(lines)



_LIST_MEMORIES_SQL = """
SELECT id::text AS memory_id, facts AS memory, created_at
FROM etown_mem0_memories
WHERE userprofileid = $1 AND facts IS NOT NULL AND btrim(facts) <> ''
ORDER BY created_at DESC NULLS LAST
LIMIT $2 OFFSET $3
"""

_COUNT_MEMORIES_SQL = """
SELECT count(*) FROM etown_mem0_memories
WHERE userprofileid = $1 AND facts IS NOT NULL AND btrim(facts) <> ''
"""

_DELETE_MEMORY_SQL = """
DELETE FROM etown_mem0_memories WHERE id = $1::uuid AND userprofileid = $2
"""

_DELETE_ALL_MEMORIES_SQL = """
DELETE FROM etown_mem0_memories WHERE userprofileid = $1
"""

_READ_SETTING_SQL = """
SELECT enabled FROM etown_memory_settings WHERE user_id = $1
"""

_WRITE_SETTING_SQL = """
INSERT INTO etown_memory_settings (user_id, enabled)
VALUES ($1, $2)
ON CONFLICT (user_id) DO UPDATE
    SET enabled = EXCLUDED.enabled, updated_at = CURRENT_TIMESTAMP
"""


async def list_memories(
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Return one newest-first page of a user's stored memories from Postgres plus the total count."""
    if not user_id:
        return {"memories": [], "total": 0}

    rows = await Database.fetch(_LIST_MEMORIES_SQL, user_id, limit, offset)
    total = await Database.fetchval(_COUNT_MEMORIES_SQL, user_id)
    return {"memories": [dict(row) for row in rows], "total": total or 0}


async def delete_memory(user_id: str, memory_id: str) -> bool:
    """Delete one of a user's memories and return whether a row went; a non-uuid id returns False."""
    if not user_id or not memory_id:
        return False

    try:
        uuid.UUID(str(memory_id))
    except (ValueError, AttributeError, TypeError):
        return False

    status = await Database.execute(_DELETE_MEMORY_SQL, memory_id, user_id)
    return status.rsplit(" ", 1)[-1] != "0"


async def delete_all_memories(user_id: str) -> int:
    """Delete every memory stored for a user and return how many were removed."""
    if not user_id:
        return 0

    status = await Database.execute(_DELETE_ALL_MEMORIES_SQL, user_id)
    try:
        return int(status.rsplit(" ", 1)[-1])
    except ValueError:
        return 0


async def is_memory_enabled(user_id: str) -> bool:
    """Return whether memory is on for a user, treating a missing row or failed lookup as enabled."""
    if not user_id:
        return False

    try:
        enabled = await Database.fetchval(_READ_SETTING_SQL, user_id)
    except Exception:
        logger.warning(
            "Memory setting lookup failed for %s; treating memory as enabled", user_id,
            exc_info=True,
        )
        return True
    return True if enabled is None else bool(enabled)


async def set_memory_enabled(user_id: str, enabled: bool) -> bool:
    """Upsert a user's memory on/off setting without deleting stored memories and return the new state."""
    await Database.execute(_WRITE_SETTING_SQL, user_id, enabled)
    logger.info("Memory %s for user %s", "enabled" if enabled else "disabled", user_id)
    return enabled


FORGET_SIMILARITY_FLOOR = 0.68
FORGET_CONFIRM_FLOOR = 0.60


def _sync_remember_fact(user_id: str, fact: str, metadata: Dict[str, Any]) -> str:
    """Store one phrased fact verbatim in mem0 with infer=False (blocking) and return it."""
    from app.core.memory import get_memory_client

    get_memory_client().add(
        messages=[{"role": "user", "content": fact}],
        user_id=user_id,
        metadata=metadata,
        infer=False,
    )
    return fact


def _sync_find_memory_to_forget(user_id: str, description: str) -> Optional[Dict[str, Any]]:
    """Return the closest stored memory at or above FORGET_CONFIRM_FLOOR for a forget request (blocking), or None."""
    from app.core.memory import get_memory_client

    result = get_memory_client().search(
        query=description, user_id=user_id, limit=MEMORY_SEARCH_LIMIT
    )
    entries = result.get("results", []) if isinstance(result, dict) else (result or [])

    best = None
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        try:
            similarity = 1.0 - float(entry.get("score"))
        except (TypeError, ValueError):
            continue
        if similarity < FORGET_CONFIRM_FLOOR:
            continue
        if best is None or similarity > best["similarity"]:
            best = {
                "memory_id": str(entry["id"]),
                "memory": (entry.get("memory") or "").strip(),
                "similarity": similarity,
            }
    return best


DECISION_FACT_MAX_CHARS = 300

_LOG_DECISION_SQL = """
INSERT INTO public.etown_memory_decisions
    (turn_id, conversation_id, user_id, user_role, raw_prompt,
     memory_enabled, retrieval_outcome, retrieved_count, injected_count,
     best_similarity, similarity_floor, candidates, retrieval_ms,
     affinity_count, affinity_matched, affinity_score)
VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13, $14, $15, $16)
ON CONFLICT (turn_id) DO NOTHING
"""

_UPDATE_DECISION_SQL = """
UPDATE public.etown_memory_decisions
   SET extraction_outcome = $2,
       facts_extracted    = $3::jsonb,
       events             = $4::jsonb
 WHERE turn_id = $1::uuid
"""


def build_candidate_log(raw_result: Any) -> List[Dict[str, Any]]:
    """Return every mem0 candidate, including below-floor ones, as clipped memory text and rounded similarity, best first."""
    if not raw_result:
        return []

    entries = raw_result.get("results", []) if isinstance(raw_result, dict) else raw_result
    out: List[Dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        text = (entry.get("memory") or "").strip()
        if not text:
            continue
        try:
            similarity = 1.0 - float(entry.get("score"))
        except (TypeError, ValueError):
            continue
        out.append({
            "memory": text[:DECISION_FACT_MAX_CHARS],
            "similarity": round(similarity, 4),
        })
    out.sort(key=lambda item: item["similarity"], reverse=True)
    return out


def _as_decimal(value: Any, places: int = 4) -> Optional[Decimal]:
    """Round a number to a Decimal with the given places for a numeric column, or None if unusable."""
    if value is None:
        return None
    try:
        return Decimal(str(round(float(value), places)))
    except (TypeError, ValueError, InvalidOperation):
        return None


async def log_memory_decision(record: Dict[str, Any]) -> None:
    """Insert a turn's memory retrieval row into etown_memory_decisions, logging and swallowing any failure."""
    try:
        await Database.execute(
            _LOG_DECISION_SQL,
            record.get("turn_id"),
            record.get("conversation_id"),
            record.get("user_id"),
            record.get("user_role"),
            (record.get("raw_prompt") or "")[:2000] or None,
            record.get("memory_enabled"),
            record.get("retrieval_outcome"),
            record.get("retrieved_count"),
            record.get("injected_count"),
            _as_decimal(record.get("best_similarity")),
            _as_decimal(record.get("similarity_floor")),
            json.dumps(record.get("candidates") or [], ensure_ascii=False),
            record.get("retrieval_ms"),
            record.get("affinity_count"),
            record.get("affinity_matched"),
            _as_decimal(record.get("affinity_score"), places=2),
        )
    except Exception as exc:
        logger.warning("log_memory_decision failed: %s", exc)


async def record_extraction_outcome(
    turn_id: str,
    outcome: str,
    facts: Optional[List[str]] = None,
    events: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Update a turn's decision row with the extraction outcome, facts and events, swallowing any failure.

    Args:
        turn_id: Turn uuid passed to log_memory_decision.
        outcome: "ok", "error" or "skipped".
        facts: Fact strings mem0's extractor produced, when known.
        events: mem0's per-memory ADD / UPDATE / DELETE results.
    """
    try:
        await Database.execute(
            _UPDATE_DECISION_SQL,
            turn_id,
            outcome,
            json.dumps(facts or [], ensure_ascii=False),
            json.dumps(events or [], ensure_ascii=False),
        )
    except Exception as exc:
        logger.warning("record_extraction_outcome failed for %s: %s", turn_id, exc)
