"""Classification Re-mining Worker - stages new intent examples mined from recent Chaatie turns.

Background worker for the classification bridge: periodically reads recent conversation turns, labels them from tools_used or response_type, and inserts unseen examples into etown_intent_examples with is_active = FALSE for human review. It is a no-op unless CLASSIFIER_MODE is shadow or live.

Features:
    - Gated by CLASSIFIER_MODE; cadence and window are constants, not settings
    - Read-only over the conversation log; writes only inactive proposals
    - Cadence held in the data, so a restart does not re-run the pass
    - Reuses the package's preprocess, expand and cached-embed path for consistent vectors
    - Excludes turns the bridge answered itself, which are not independent evidence

Classes:
    No classes defined (module exposes start/stop control plus pass-level helpers)

Functions:
    _decode_json(): Parse a JSON string, dict or list column, else None
    _pick_label(): Canonical intent from an assistant turn's tools_used or response_type
    _run_remine(): One pass, proposing new inactive examples from the recent window
    _worker_loop(): Startup grace then the periodic loop until shutdown
    start_classification_remining_worker(): Start the loop; no-op if disabled or running
    stop_classification_remining_worker(): Signal and await graceful shutdown

Dependencies:
    - app.core.config.settings: the CLASSIFIER_MODE switch
    - app.agent_infrastructure.classification: examples_utils, taxonomy, preprocessor, expander
    - app.agent_infrastructure.classification.classifier._embed_query_cached: cached embeddings
    - app.utils.embeddings.vector_to_postgres_string: vector binding

Updates:
    22-Jul-2026: Initial re-mining worker (T6.1).
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    04-Sep-2026: Cadence keyed off the newest mined row; label reads the projected response_type.
"""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.agent_infrastructure.classification import examples_utils, taxonomy
from app.agent_infrastructure.classification.preprocessor import preprocess
from app.agent_infrastructure.classification.expander import expand
from app.agent_infrastructure.classification.classifier import _embed_query_cached
from app.utils.embeddings import vector_to_postgres_string

logger = logging.getLogger("app.agent_infrastructure.classification.remining_worker")

_worker_task: Optional[asyncio.Task] = None
_shutdown_event: Optional[asyncio.Event] = None

_STARTUP_GRACE_SECONDS = 90
_MAX_PROPOSALS_PER_TICK = 500
_META_TOOLS = {"pending_ratings", "check_pending_ratings", "pending_rating"}
_INTERVAL_HOURS = 24
_LOOKBACK_HOURS = 48


def _decode_json(value: Any) -> Any:
    """Return a dict or list as-is, parse a JSON string, and return None for anything else."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            logger.debug("[classification] re-mining could not decode a JSON column; treating it as null")
            return None
    return None


def _pick_label(tools_used: Any, response_type: Any) -> Optional[str]:
    """Return the first known canonical intent from a turn's non-meta tools_used, else its response_type, or None."""
    tools = _decode_json(tools_used) or []
    if isinstance(tools, list):
        for raw in tools:
            if not raw or str(raw).strip().lower() in _META_TOOLS:
                continue
            canon = taxonomy.canonicalize_label(raw)
            if canon and taxonomy.is_known_intent(canon):
                return canon
    if response_type and str(response_type).strip().lower() not in _META_TOOLS:
        canon = taxonomy.canonicalize_label(response_type)
        if canon and taxonomy.is_known_intent(canon):
            return canon
    return None


async def _run_remine() -> None:
    """Mine labelled user turns from the lookback window into inactive intent examples, unless the last pass is too recent."""
    last_mined = await examples_utils.fetch_last_mined_at()
    if last_mined is not None:
        age_hours = (datetime.now(timezone.utc) - last_mined).total_seconds() / 3600.0
        if age_hours < _INTERVAL_HOURS:
            logger.info(
                "[classification] re-mining skipped; last pass was %.1fh ago (interval %dh)",
                age_hours, _INTERVAL_HOURS)
            return

    lookback = _LOOKBACK_HOURS
    rows = await examples_utils.fetch_recent_turns(lookback)
    if not rows:
        return

    by_conv: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_conv[r.get("conversation_id")].append(r)

    existing = await examples_utils.fetch_example_texts()
    self_dispatched = await examples_utils.fetch_self_dispatched_prompts(lookback)
    batch_seen: set = set()
    candidates: List[tuple] = []
    skipped_self = 0

    for conv_rows in by_conv.values():
        for i, row in enumerate(conv_rows):
            if (row.get("role") or "").lower() != "user":
                continue
            if (row.get("conversation_id"), (row.get("content") or "").strip()) in self_dispatched:
                skipped_self += 1
                continue
            assistant = None
            for follow in conv_rows[i + 1:]:
                role = (follow.get("role") or "").lower()
                if role == "assistant":
                    assistant = follow
                    break
                if role == "user":
                    break
            if assistant is None:
                continue
            text = (row.get("content") or "").strip()
            if not text or text in existing or text in batch_seen:
                continue
            label = _pick_label(assistant.get("tools_used"), assistant.get("response_type"))
            if not label:
                continue
            batch_seen.add(text)
            candidates.append((text, label, taxonomy.agent_scope_for(label),
                               (row.get("language") or "en").strip().lower() or "en"))

    candidates = candidates[:_MAX_PROPOSALS_PER_TICK]
    inserted = 0
    for text, label, scope, lang in candidates:
        pre = preprocess(text)
        if not pre:
            continue
        try:
            expanded = await expand(pre, language=lang)
            embedding = await _embed_query_cached(expanded)
        except Exception as e:
            logger.debug(f"[classification] re-mine embed skipped: {e}")
            continue
        new_id = await examples_utils.insert_example(
            intent_label=label, agent_scope=scope, example_text=text, language=lang,
            content_vector_str=vector_to_postgres_string(embedding),
            source="mined", is_active=False,
        )
        if new_id:
            inserted += 1

    logger.info(
        "[classification] re-mining proposed %d new examples (is_active=FALSE, "
        "awaiting review) from the last %dh; skipped %d self-dispatched turn(s)",
        inserted, lookback, skipped_self
    )


async def _worker_loop(interval_seconds: float) -> None:
    """Wait out the startup grace period, then run a re-mining pass every interval until shutdown."""
    assert _shutdown_event is not None
    try:
        await asyncio.wait_for(_shutdown_event.wait(), timeout=_STARTUP_GRACE_SECONDS)
        return
    except asyncio.TimeoutError:
        pass

    while not _shutdown_event.is_set():
        try:
            await _run_remine()
        except Exception as e:
            logger.error(f"[classification] re-mining pass failed: {e}", exc_info=True)
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue


async def start_classification_remining_worker() -> None:
    """Start the re-mining loop task unless CLASSIFIER_MODE is off or it is already running."""
    global _worker_task, _shutdown_event

    if settings.CLASSIFIER_MODE == "off":
        logger.info("Classification re-mining disabled (CLASSIFIER_MODE=off)")
        return
    if _worker_task is not None and not _worker_task.done():
        return

    _shutdown_event = asyncio.Event()
    interval_seconds = max(1, _INTERVAL_HOURS) * 3600
    _worker_task = asyncio.create_task(_worker_loop(interval_seconds))
    logger.info(
        "Classification re-mining started (every %dh, lookback %dh)",
        _INTERVAL_HOURS, _LOOKBACK_HOURS,
    )


async def stop_classification_remining_worker() -> None:
    """Signal shutdown, wait up to 10 seconds for the loop to exit, then cancel it."""
    global _worker_task, _shutdown_event

    if _shutdown_event is not None:
        _shutdown_event.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning("[classification] re-mining worker did not stop within 10s; cancelling it")
            _worker_task.cancel()
        except Exception as e:
            logger.debug(f"Re-mining worker stop error: {e}")
    _worker_task = None
    _shutdown_event = None
