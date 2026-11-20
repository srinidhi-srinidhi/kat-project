"""Intent Classifier - two-stage rule and k-NN confidence gate for the Classification Bridge.

Classification layer behind the chat pre-LLM seam: matches frontend templates and closed-set shapes with deterministic rules, otherwise embeds the expanded query and runs a similarity-floored k-NN vote over etown_intent_examples. It returns a Decision and never dispatches; safety checks, the allow-list and feature flags stay with the bridge.

Features:
    - Rule fast path: exact intent + arguments with zero network cost.
    - Decision dataclass carrying intent, top1_sim, margin, support, and the gate result.
    - classify(): the full pipeline, fail-open on every error path.
    - Similarity floor: distant neighbours are dropped before voting.
    - Clarification check: underspecified searches are not auto-dispatched.
    - Decision.should_auto_handle means only that the confidence gate passed, not that the LLM may be skipped.
    - Query and example vectors are unit-normalized, so similarity (-1 * pgvector <#> distance) is cosine on a fixed scale.

Classes:
    Decision: Immutable result of one classification.

Functions:
    _embed_query_cached(text): Cached (1h TTL), unit-normalized query embedding lookup.
    _fallthrough(reason, ...): Build a never-auto-handle Decision for a given fail reason.
    classify(text, agent_scope, language): Classify one query into a Decision.
    score_and_gate(neighbors, ...): Pure vote + confidence gate (shared with the harness).

Dependencies:
    - app.utils.embeddings: generate_embedding / normalize_vector / vector_to_postgres_string.
    - app.agent_infrastructure.classification: rules, preprocessor, expander, examples_utils, config, taxonomy.
    - async_lru.alru_cache: TTL cache for embedded queries (_embed_query_cached).
    - app.core.logging: Application logger for timeout/error/exception fall-throughs.

Updates:
    22-Jul-2026: Initial classifier core (T2.5). No dispatch; confidence gate only.
    10-Aug-2026: Added stage-1 rule layer, normalized query vectors, two-way margin; top1_sim from winning intent.
    08-Sep-2026: Consults rules.needs_clarification() before dispatching a search.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    08-Oct-2026: Removed LangSmith tracing.
    27-Oct-2026: Neighbour retrieval has its own timeout and falls through as neighbors_timeout.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from async_lru import alru_cache

from app.utils.embeddings import generate_embedding, normalize_vector, vector_to_postgres_string
from app.agent_infrastructure.classification import config, examples_utils, rules, taxonomy
from app.agent_infrastructure.classification.preprocessor import preprocess
from app.agent_infrastructure.classification.expander import expand
from app.core.logging import logger


@alru_cache(maxsize=2048, ttl=3600)
async def _embed_query_cached(text: str) -> list[float]:
    """Return the unit-normalized embedding of a query, cached for an hour per identical text."""
    return normalize_vector(await generate_embedding(text))


@dataclass(frozen=True)
class Decision:
    """Result of one classification.

    Attributes:
        intent: Winning canonical intent, or None when there is nothing to act on.
        top1_sim: Cosine similarity of the single best neighbour (``-distance``). Set to
            1.0 for a rule match, which is certain by construction.
        margin: Two-way vote lead of the winning intent over the runner-up (0..1).
        support: Number of voting neighbours labeled with the winning intent.
        should_auto_handle: True iff the confidence gate passed (NOT the final go-ahead).
        preprocessed_text: Text after preprocessing.
        expanded_text: Text after expansion (what was embedded).
        neighbor_count: Number of neighbours retrieved.
        error: True if the pipeline failed open (embedding/retrieval error).
        reason: Short machine-readable reason ('ok' | 'rule:<name>' | 'low_confidence' |
            'no_neighbors' | 'neighbors_timeout' | 'context_dependent' | 'needs_clarification' | 'embed_timeout' |
            'embed_error' | 'empty_query' | 'exception').
        dispatch_args: Handler arguments extracted deterministically from the prompt (rule
            matches only). The dispatcher prefers these over its own heuristics; empty
            means "work the arguments out as usual".
    """

    intent: Optional[str]
    top1_sim: float = 0.0
    margin: float = 0.0
    support: int = 0
    should_auto_handle: bool = False
    preprocessed_text: str = ""
    expanded_text: str = ""
    neighbor_count: int = 0
    error: bool = False
    reason: str = "ok"
    dispatch_args: Dict[str, Any] = field(default_factory=dict)


def _fallthrough(reason: str, *, error: bool = False, **kwargs) -> Decision:
    """Return a Decision with no intent that never auto-handles, tagged with the given reason."""
    return Decision(intent=None, should_auto_handle=False, error=error, reason=reason, **kwargs)


async def classify(text: str, agent_scope: str, language: str = "en") -> Decision:
    """Classify a prompt via deterministic rules, then a k-NN vote, returning a fall-through Decision on any failure.

    Args:
        text: Raw user prompt.
        agent_scope: 'member' or 'provider', used to filter neighbour examples.
        language: Request language tag passed to query expansion.
    """
    try:
        rule = rules.match(text)
        if rule is not None:
            return Decision(
                intent=rule.intent,
                top1_sim=1.0,
                margin=1.0,
                support=config.min_support(),
                should_auto_handle=True,
                preprocessed_text=(text or "").strip(),
                expanded_text="",
                neighbor_count=0,
                reason=f"rule:{rule.rule}",
                dispatch_args=dict(rule.args),
            )

        if rules.is_context_dependent(text):
            return _fallthrough("context_dependent")

        if rules.needs_clarification(text):
            return _fallthrough("needs_clarification")

        pre = preprocess(text)
        if not pre:
            return _fallthrough("empty_query")

        expanded = await expand(pre, language=language)

        try:
            embedding = await asyncio.wait_for(
                _embed_query_cached(expanded),
                timeout=config.embed_timeout_seconds(),
            )
        except asyncio.TimeoutError:
            logger.warning("[classification] embedding timed out; falling through")
            return _fallthrough("embed_timeout", error=True,
                                preprocessed_text=pre, expanded_text=expanded)
        except Exception as e:
            logger.warning(f"[classification] embedding failed: {e}; falling through")
            return _fallthrough("embed_error", error=True,
                                preprocessed_text=pre, expanded_text=expanded)

        embedding_str = vector_to_postgres_string(embedding)
        try:
            neighbors = await asyncio.wait_for(
                examples_utils.fetch_neighbors(embedding_str, agent_scope, config.k_neighbours()),
                timeout=config.neighbour_timeout_seconds(),
            )
        except asyncio.TimeoutError:
            logger.warning("[classification] neighbour retrieval timed out; falling through")
            return _fallthrough("neighbors_timeout", error=True,
                                preprocessed_text=pre, expanded_text=expanded)
        if not neighbors:
            return _fallthrough("no_neighbors", preprocessed_text=pre, expanded_text=expanded)

        scored = score_and_gate(
            neighbors,
            tau_sim=config.tau_sim(),
            tau_margin=config.tau_margin(),
            min_support=config.min_support(),
            sim_floor=config.neighbor_sim_floor(),
            top1_agreement_delta=config.top1_agreement_delta(),
        )
        return Decision(
            intent=scored["intent"],
            top1_sim=round(scored["top1_sim"], 6),
            margin=round(scored["margin"], 6),
            support=scored["support"],
            should_auto_handle=scored["passed"],
            preprocessed_text=pre,
            expanded_text=expanded,
            neighbor_count=len(neighbors),
            reason="ok" if scored["passed"] else "low_confidence",
        )
    except Exception as e:
        logger.error(f"[classification] classify() failed open: {e}", exc_info=True)
        return _fallthrough("exception", error=True)


def score_and_gate(
    neighbors: List[dict],
    *,
    tau_sim: float,
    tau_margin: float,
    min_support: int,
    sim_floor: float = 0.0,
    top1_agreement_delta: Optional[float] = None,
) -> dict:
    """Run a similarity-floored weighted k-NN vote over neighbours and apply the confidence gate.

    Args:
        neighbors: Dicts with intent_label and distance, nearest first.
        tau_sim: Minimum similarity of the winning intent's own best neighbour.
        tau_margin: Minimum two-way vote margin over the runner-up.
        min_support: Minimum voting neighbours for the winning intent.
        sim_floor: Minimum per-neighbour similarity needed to vote.
        top1_agreement_delta: Allowed trail behind the overall closest neighbour; None uses config.

    Returns:
        dict with intent, top1_sim, margin, support and passed.
    """
    if not neighbors:
        return {"intent": None, "top1_sim": 0.0, "margin": 0.0, "support": 0, "passed": False}
    if top1_agreement_delta is None:
        top1_agreement_delta = config.top1_agreement_delta()

    sims: List[tuple[str, float]] = []
    for n in neighbors:
        label = taxonomy.canonicalize_label(n["intent_label"]) or n["intent_label"]
        sims.append((label, -float(n["distance"])))

    voting = [(label, s) for label, s in sims if s >= sim_floor]
    if not voting:
        return {"intent": None, "top1_sim": max(s for _, s in sims), "margin": 0.0,
                "support": 0, "passed": False}

    votes: dict[str, float] = defaultdict(float)
    support_counts: dict[str, int] = defaultdict(int)
    for label, s in voting:
        votes[label] += max(s, 0.0)
        support_counts[label] += 1

    ranked = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
    best_intent, best_vote = ranked[0]
    second_vote = ranked[1][1] if len(ranked) > 1 else 0.0
    denominator = best_vote + second_vote
    margin = (best_vote - second_vote) / denominator if denominator > 0 else 1.0
    support = support_counts[best_intent]

    neighbourhood_top1 = max(s for _, s in voting)
    winner_top1 = max((s for label, s in voting if label == best_intent), default=0.0)
    passed = (
        winner_top1 >= tau_sim
        and winner_top1 + top1_agreement_delta >= neighbourhood_top1
        and margin >= tau_margin
        and support >= min_support
    )

    return {
        "intent": best_intent,
        "top1_sim": winner_top1,
        "margin": margin,
        "support": support,
        "passed": passed,
    }
