"""Chaatie Classification Package - pre-LLM intent classification and direct tool dispatch.

Agent-layer package called from chat.py before the LLM: rules and a k-NN vote classify each turn, and a confident, dispatchable read-only intent runs its tool handler directly and returns an agent-shaped response, while everything else falls through to the member or provider agent.

Features:
    - Fail-open and flag-gated: CLASSIFIER_MODE off/shadow/live, default off; any error or low confidence runs the LLM.
    - k-NN over gemini-embedding-001 (768-dim) embeddings via a pgvector HNSW index.
    - Deterministic short-form / synonym expansion before classification (e.g. "ac").
    - Contract-preserving: keeps response_type, pending-rating guardrail and /api/chaatie schema.

Modules:
    taxonomy: Canonical intents, agent scope, dispatchable allow-list, label map.
    config: Thresholds and flags resolved from app.core.config.settings.
    examples_utils: Database access for the classifier tables (SQL lives only here).
    preprocessor: Normalization + proximity-phrase stripping (Unicode-preserving).
    expander: Deterministic dictionary short-form / synonym expansion.
    rules: Regex fast-path that matches frontend template prompts before any embedding call.
    classifier: rules -> embed -> ANN k-NN -> distance-weighted vote -> confidence gate.
    dispatcher: (Phase 3) direct calls into existing tool handlers.
    shaper: (Phase 3) builds agent-shaped response dicts.
    bridge: (Phase 3) classify_and_maybe_handle(request, state, history) orchestrator.
    remining_worker: (Phase 6) scheduled worker re-mining turns into reviewable labeled examples.
    gold_annotator: Offline LLM annotation of evaluation labels (not on the request path).

Dependencies:
    - app.utils.embeddings: generate_embedding / vector_to_postgres_string.
    - app.db.client.Database: pooled asyncpg access (@with_retry).
    - app.core.config.settings: feature flags and thresholds.

Updates:
    22-Jul-2026: Initial package scaffold (Phase 0/1/2 core).

Notes:
    Do not import request-path submodules eagerly here; import them where used.
"""
