"""Classifier Config - runtime switch and fixed gate constants for the intent classifier.

Configuration layer for the classification bridge: reads CLASSIFIER_MODE (off, shadow or live) live from the settings singleton and exposes the k-NN neighbour count, confidence-gate thresholds, embedding timeout and prompt-logging mode through one accessor each.

Features:
    - Modes: off skips the classifier, shadow only logs its decision, live short-circuits confident dispatchable queries
    - Constants are re-derived with scripts/classification/evaluate_classifier.py --sweep, never guessed
    - A single operational switch, with the gate parameters held as constants
    - Accessor per parameter, so call sites never read the constants directly
    - Live reads of the settings singleton, so a mode change needs no restart

Classes:
    No classes defined (module exposes constants and accessor functions)

Functions:
    is_enabled() / is_master_enabled(): The classifier runs at all (shadow or live)
    is_shadow_mode() / is_live(): Which of the two running modes is active
    k_neighbours(): Number of k-NN neighbours to retrieve
    tau_sim(): Minimum similarity for the winning intent's best neighbour
    tau_margin(): Minimum normalised vote-margin
    min_support(): Minimum supporting neighbours for the winner
    neighbor_sim_floor(): Minimum per-neighbour similarity required to vote
    top1_agreement_delta(): How far the winner's best neighbour may trail the neighbourhood
    embed_timeout_seconds(): Embedding timeout before failing open to the LLM
    neighbour_timeout_seconds(): k-NN retrieval timeout before failing open to the LLM
    prompt_logging_mode(): Decision-log privacy mode (full | truncate | hash | off)

Dependencies:
    - app.core.config.settings: the CLASSIFIER_MODE and CLASSIFIER_PROMPT_LOGGING switches

Updates:
    22-Jul-2026: Initial config accessors (T2.1); collapsed to the 3-state CLASSIFIER_MODE (off/shadow/live).
    24-Jul-2026: Added prompt_logging_mode() and PROMPT_TRUNCATE_LEN for the decision-log privacy gate.
    10-Aug-2026: Re-tuned the gate for cosine similarity; added NEIGHBOR_SIM_FLOOR and TOP1_AGREEMENT_DELTA.
    04-Sep-2026: Lowered TAU_SIM.
    27-Oct-2026: Added NEIGHBOUR_TIMEOUT_MS and neighbour_timeout_seconds().
"""

from __future__ import annotations

from app.core.config import settings

K_NEIGHBOURS = 15
TAU_SIM = 0.78
TAU_MARGIN = 0.10
MIN_SUPPORT = 2
NEIGHBOR_SIM_FLOOR = 0.60
TOP1_AGREEMENT_DELTA = 0.015
EMBED_TIMEOUT_MS = 1500
NEIGHBOUR_TIMEOUT_MS = 1000


PROMPT_TRUNCATE_LEN = 120


def is_enabled() -> bool:
    """Return True when the classifier runs at all (mode is shadow or live)."""
    return settings.CLASSIFIER_MODE in ("shadow", "live")


def is_shadow_mode() -> bool:
    """Return True in shadow mode (classify + log only; never short-circuit)."""
    return settings.CLASSIFIER_MODE == "shadow"


def is_live() -> bool:
    """Return True in live mode (short-circuit confident dispatchable queries)."""
    return settings.CLASSIFIER_MODE == "live"


def is_master_enabled() -> bool:
    """Return True when the classifier runs at all, as an alias of is_enabled()."""
    return is_enabled()


def k_neighbours() -> int:
    """Return the number of k-NN neighbours to retrieve."""
    return K_NEIGHBOURS


def tau_sim() -> float:
    """Return the minimum top-1 similarity threshold."""
    return TAU_SIM


def tau_margin() -> float:
    """Return the minimum normalised vote-margin threshold."""
    return TAU_MARGIN


def min_support() -> int:
    """Return the minimum number of supporting neighbours for the winning intent."""
    return MIN_SUPPORT


def neighbor_sim_floor() -> float:
    """Return the minimum per-neighbour cosine similarity required to cast a vote."""
    return NEIGHBOR_SIM_FLOOR


def top1_agreement_delta() -> float:
    """Return how far the winning intent's best neighbour may trail the overall top similarity."""
    return TOP1_AGREEMENT_DELTA


def embed_timeout_seconds() -> float:
    """Return the embedding timeout in seconds, floored at 0.1, before failing open to the LLM."""
    return max(0.1, EMBED_TIMEOUT_MS / 1000.0)


def neighbour_timeout_seconds() -> float:
    """Return the k-NN retrieval timeout in seconds, floored at 0.1, before failing open to the LLM."""
    return max(0.1, NEIGHBOUR_TIMEOUT_MS / 1000.0)


def prompt_logging_mode() -> str:
    """Return the decision-log privacy mode ('full' | 'truncate' | 'hash' | 'off')."""
    return settings.CLASSIFIER_PROMPT_LOGGING
