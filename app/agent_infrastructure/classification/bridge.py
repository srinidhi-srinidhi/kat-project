"""Classification Bridge - pre-LLM seam that may answer a Chaatie turn with a direct tool call.

Classifier layer called once per turn by chat.py: classifies the prompt, logs every decision to etown_classification_decisions, and in live mode dispatches a confident, in-scope, read-only intent through the dispatcher and shaper. Any gate failure or exception returns None so the LLM answers.

Features:
    - Three-state operation via CLASSIFIER_MODE (off, shadow, live)
    - Safety gates: scope, rule-only intents, provider context, pending rating, mid-transaction
    - Decision logging on every turn, with prompt text subject to the privacy mode
    - Fail-open by construction, so the fast path can never be worse than the LLM path

Classes:
    No classes defined (module exposes one coroutine plus private helpers)

Functions:
    _scope_for(): Map a numeric user_role to 'member' / 'provider' / None
    _last_assistant_metadata(): Most recent assistant turn's metadata dict
    _last_assistant_response_type(): Most recent assistant turn's response_type
    _mid_transaction(): True when the last assistant turn is a multi-step flow or a clarifying question
    _has_pending_rating(): DB-backed pending-rating check, failing safe
    _apply_prompt_privacy(): Apply CLASSIFIER_PROMPT_LOGGING to a logged prompt
    _log_decision(): Fire-and-forget decision-log write
    classify_and_maybe_handle(): Orchestrate one turn; return a response or None

Dependencies:
    - app.agent_infrastructure.classification: classifier, config, taxonomy, dispatcher, shaper, examples_utils
    - app.agent_infrastructure.tools.tools_utils.pending_ratings: the rating pre-check
    - app.utils.performance: latency captured in the response's performance block
    - app.core.logging: structured logging

Updates:
    22-Jul-2026: Initial fail-open bridge with decision logging; 3-state CLASSIFIER_MODE (off/shadow/live).
    24-Jul-2026: Decision-log prompts pass through the CLASSIFIER_PROMPT_LOGGING privacy gate.
    10-Aug-2026: Decision log records decision_reason.
    04-Sep-2026: Small-talk kind reaches the shaper; rule-only intents and provider-on-screen guard narrow dispatch.
    07-Sep-2026: Dropped recommend_products from _CONTEXT_SENSITIVE_INTENTS.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    28-Sep-2026: generate_image counts as a mid-transaction response type.
    08-Oct-2026: Removed LangSmith tracing.
    27-Oct-2026: A reply to a clarifying question (awaiting_clarification metadata) counts as mid-transaction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Dict, List, Optional

from app.core.logging import logger
from app.utils.performance import PerformanceTimer
from app.agent_infrastructure.classification import config, taxonomy, examples_utils, dispatcher, shaper
from app.agent_infrastructure.classification.classifier import classify, Decision

_PROVIDER_ON_SCREEN_RESPONSE_TYPES = {
    "provider_service_insights",
    "enquire_provider",
}
_CONTEXT_SENSITIVE_INTENTS = {
    "search_provider",
}

_TRANSACTION_RESPONSE_TYPES = {
    "request_provider",
    "booknow_provider",
    "add_booking_instructions",
    "address_confirmation",
    "pending_rating",
    "onboard_provider",
    "create_post_from_chat",
    "create_offer_from_chat",
    "generate_image",
    "booking_info",
}


def _scope_for(user_role: int) -> Optional[str]:
    """Map a user role to its classifier scope: 2 to member, 1 to provider, else None."""
    if user_role == 2:
        return "member"
    if user_role == 1:
        return "provider"
    return None


def _last_assistant_metadata(history: Optional[List[dict]]) -> Dict[str, Any]:
    """Return the latest assistant turn's metadata dict, parsing JSON metadata, or an empty dict."""
    for record in reversed(history or []):
        if (record.get("role") or "").lower() != "assistant":
            continue
        meta = record.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                logger.debug("[classification] assistant turn metadata is not valid JSON; treating it as empty")
                meta = {}
        return meta if isinstance(meta, dict) else {}
    return {}


def _last_assistant_response_type(history: Optional[List[dict]]) -> Optional[str]:
    """Return the latest assistant turn's metadata response_type, or None."""
    return _last_assistant_metadata(history).get("response_type")


def _mid_transaction(history: Optional[List[dict]]) -> bool:
    """Return True when the last assistant turn was a transactional tool response or a clarifying question."""
    meta = _last_assistant_metadata(history)
    return meta.get("response_type") in _TRANSACTION_RESPONSE_TYPES or bool(meta.get("awaiting_clarification"))


async def _has_pending_rating(request, state: Optional[dict]) -> bool:
    """Return True if the user has a pending rating, skipping already-checked sessions and assuming pending on error."""
    if state and state.get("prompt_ratings_checked"):
        return False
    upid = getattr(request, "user_profile_id", None)
    if not upid:
        return False
    try:
        from app.agent_infrastructure.tools.tools_utils.pending_ratings import check_pending_ratings
        result = await check_pending_ratings(upid)
        return isinstance(result, dict) and result.get("status") == "pending_rating"
    except Exception as e:
        logger.warning(f"[classification] pending-rating pre-check failed; failing safe: {e}")
        return True


def _apply_prompt_privacy(text: Optional[str]) -> Optional[str]:
    """Return the prompt as the logging mode allows: verbatim, truncated, sha256-hashed, or None when off."""
    if not text:
        return text
    mode = config.prompt_logging_mode()
    if mode == "off":
        return None
    if mode == "hash":
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    if mode == "truncate":
        return text if len(text) <= config.PROMPT_TRUNCATE_LEN else text[: config.PROMPT_TRUNCATE_LEN] + "…"
    return text


def _log_decision(request, decision: Decision, action: str, shadow: bool) -> None:
    """Schedule a background insert of the classification decision row with privacy-filtered prompts."""
    record = {
        "conversation_id": getattr(request, "conversation_id", None),
        "user_role": getattr(request, "user_role", None),
        "raw_prompt": _apply_prompt_privacy(getattr(request, "prompt", None)),
        "expanded_prompt": _apply_prompt_privacy(decision.expanded_text or None),
        "predicted_intent": decision.intent,
        "top1_similarity": decision.top1_sim,
        "vote_margin": decision.margin,
        "action": action,
        "shadow": shadow,
        "decision_reason": decision.reason,
    }
    try:
        asyncio.create_task(examples_utils.log_decision(record))
    except RuntimeError:
        logger.warning("[classification] no running event loop; the decision row was not persisted", exc_info=True)


async def classify_and_maybe_handle(request, state: Optional[dict], history: Optional[List[dict]]) -> Optional[Dict[str, Any]]:
    """Classify one chat turn and return an agent-shaped response that skips the LLM, or None to fall through.

    Args:
        request: The ChaatieRequest for this turn.
        state: The LangGraph state dict for this turn.
        history: Raw conversation history rows, used for mid-transaction and on-screen checks.

    Returns:
        An agent-shaped response dict, or None; always None in shadow mode or on error.
    """
    if not config.is_master_enabled():
        return None
    scope = _scope_for(getattr(request, "user_role", 0))
    if scope is None:
        return None

    shadow = config.is_shadow_mode()
    try:
        async with PerformanceTimer("classification_bridge", category="general"):
            decision = await classify(getattr(request, "prompt", "") or "", scope, getattr(request, "language", "en") or "en")

            from_rule = (decision.reason or "").startswith("rule:")
            provider_on_screen = (
                _last_assistant_response_type(history) in _PROVIDER_ON_SCREEN_RESPONSE_TYPES
            )
            dispatchable = (
                taxonomy.is_dispatchable(decision.intent)
                and taxonomy.scope_matches(decision.intent, scope)
                and (decision.intent not in taxonomy.RULE_ONLY_DISPATCH or from_rule)
                and not (provider_on_screen
                         and not from_rule
                         and decision.intent in _CONTEXT_SENSITIVE_INTENTS)
            )
            would_handle = bool(decision.should_auto_handle and dispatchable)

            if shadow:
                _log_decision(request, decision,
                              action="auto_handled" if would_handle else "fell_through",
                              shadow=True)
                return None

            response: Optional[Dict[str, Any]] = None
            if (would_handle
                    and not _mid_transaction(history)
                    and not await _has_pending_rating(request, state)):
                if decision.intent == taxonomy.GREETING:
                    response = shaper.shape_greeting(
                        getattr(request, "language", "en") or "en",
                        (getattr(decision, "dispatch_args", None) or {}).get("smalltalk", "greeting"),
                    )
                else:
                    result = await dispatcher.dispatch(decision.intent, decision, request, state)
                    if result:
                        response = shaper.shape(decision.intent, result)

            _log_decision(request, decision,
                          action="auto_handled" if response is not None else "fell_through",
                          shadow=False)
            return response
    except Exception as e:
        logger.error(f"[classification] bridge failed open: {e}", exc_info=True)
        return None
