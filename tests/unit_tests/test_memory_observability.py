"""Memory Observability Tests - unit tests for the Chaatie memory decision log, embedding cache and tool gate.

Unit tests asserting that memory_utils records every candidate with a Decimal similarity, including those below the floor, and that both log writers fail silently; they also cover the mem0 query-embedding cache in app.core.memory and chat.py's memory-tool gate.

Classes:
    _CountingEmbedder(): Stand-in for mem0's Gemini embedder that counts calls
    _FakeClient(): Minimal mem0 client holding a _CountingEmbedder

Functions:
    test_every_candidate_is_logged_not_just_the_injected_ones(): Below-floor candidates are logged too
    test_candidates_are_similarities_not_distances_and_are_ordered(): Candidates carry ordered similarities
    test_a_recorded_floor_sweep_can_be_computed_offline(): A floor sweep is computable from logged rows
    test_long_facts_are_clipped(): Overlong facts are clipped to DECISION_FACT_MAX_CHARS
    test_candidate_log_degrades_to_empty(): Malformed search results yield an empty log
    test_a_float_becomes_the_decimal_that_was_meant(): _as_decimal stores 0.62 as 0.62
    test_the_live_floor_round_trips_readably(): MEMORY_SIMILARITY_FLOOR round-trips as written
    test_similarity_keeps_four_places(): Similarities are rounded to four places
    test_unusable_values_become_null(): Unusable values coerce to None
    test_places_is_configurable_for_the_coarser_match_score(): _as_decimal honours places
    test_a_missing_decisions_table_does_not_raise(): log_memory_decision swallows a missing table
    test_the_decision_row_carries_the_floor_and_the_candidates(): Decision row stores floor and candidates
    test_an_overlong_prompt_is_clipped(): Overlong prompts are clipped before logging
    test_extraction_outcome_targets_the_turn(): record_extraction_outcome updates the right turn
    test_a_repeated_prompt_costs_no_second_embedding_call(): Embedding cache serves a repeated prompt
    test_different_text_or_action_is_a_separate_entry(): Cache keys on text and action
    test_the_cache_hands_back_a_list_the_caller_cannot_poison(): Cache returns a defensive copy
    test_cache_info_reports_hits(): Cache info reports hits
    test_the_memory_tool_gate_names_both_writing_tools(): _MEMORY_TOOLS names both writing tools
    test_only_an_explicit_memory_turn_skips_extraction(): Only explicit memory turns skip extraction
    test_a_skipped_turn_still_records_its_outcome(): A skipped extraction still records its outcome

Run:
    pytest tests/unit_tests/test_memory_observability.py -v
"""

from decimal import Decimal

import pytest

from app.core.memory import MEMORY_SIMILARITY_FLOOR
from app.etown.memory import memory_utils
from app.etown.memory.memory_utils import (
    DECISION_FACT_MAX_CHARS,
    _as_decimal,
    build_candidate_log,
    log_memory_decision,
    record_extraction_outcome,
    score_memories,
)


SEARCH_RESULT = {
    "results": [
        {"memory": "Is vegetarian", "score": 0.407},
        {"memory": "Is allergic to peanuts", "score": 0.468},
        {"memory": "Has two children", "score": 0.515},
        {"memory": "Lives in Anna Nagar, Chennai", "score": 0.560},
    ]
}


def test_every_candidate_is_logged_not_just_the_injected_ones():
    """Check the candidate log keeps below-floor candidates, not just the injected ones."""
    candidates = build_candidate_log(SEARCH_RESULT)
    assert len(candidates) == 4
    injected = score_memories(SEARCH_RESULT)
    assert len(candidates) > len(injected), "the log must be wider than what reached the prompt"
    assert any(c["similarity"] < MEMORY_SIMILARITY_FLOOR for c in candidates)


def test_candidates_are_similarities_not_distances_and_are_ordered():
    """Check candidates carry similarities rather than distances, sorted best first."""
    candidates = build_candidate_log(SEARCH_RESULT)
    assert candidates[0]["memory"] == "Is vegetarian"
    assert candidates[0]["similarity"] == pytest.approx(0.593, abs=1e-3)
    assert [c["similarity"] for c in candidates] == sorted(
        (c["similarity"] for c in candidates), reverse=True
    )


def test_a_recorded_floor_sweep_can_be_computed_offline():
    """Check a floor sweep can be computed from the logged candidates alone."""
    candidates = build_candidate_log(SEARCH_RESULT)
    admitted = {
        floor: sum(1 for c in candidates if c["similarity"] >= floor)
        for floor in (0.40, 0.50, 0.55, 0.62, 0.70)
    }
    assert admitted[0.40] == 4
    assert admitted[0.55] == 1
    assert admitted[0.70] == 0
    assert admitted[0.40] >= admitted[0.50] >= admitted[0.55] >= admitted[0.62]


def test_long_facts_are_clipped():
    """Check logged candidate text is clipped to DECISION_FACT_MAX_CHARS."""
    long_fact = "x" * (DECISION_FACT_MAX_CHARS + 500)
    candidates = build_candidate_log({"results": [{"memory": long_fact, "score": 0.1}]})
    assert len(candidates[0]["memory"]) == DECISION_FACT_MAX_CHARS


@pytest.mark.parametrize(
    "raw",
    [None, {}, [], {"results": []}, {"results": ["nope"]},
     {"results": [{"memory": "x", "score": None}]},
     {"results": [{"memory": "   ", "score": 0.1}]}],
)
def test_candidate_log_degrades_to_empty(raw):
    """Check empty or malformed search results yield an empty candidate log."""
    assert build_candidate_log(raw) == []


def test_a_float_becomes_the_decimal_that_was_meant():
    """Check 0.62 converts to the Decimal 0.62, not its binary float expansion."""
    result = _as_decimal(0.62)
    assert isinstance(result, Decimal)
    assert str(result) == "0.62"


def test_the_live_floor_round_trips_readably():
    """Check the configured similarity floor converts to a Decimal with the same text."""
    assert str(_as_decimal(MEMORY_SIMILARITY_FLOOR)) == str(MEMORY_SIMILARITY_FLOOR)


def test_similarity_keeps_four_places():
    """Check _as_decimal rounds to four places by default."""
    assert str(_as_decimal(0.5248000000000000442)) == "0.5248"


@pytest.mark.parametrize("bad", [None, "abc", object()])
def test_unusable_values_become_null(bad):
    """Check None and non-numeric values convert to None."""
    assert _as_decimal(bad) is None


def test_places_is_configurable_for_the_coarser_match_score():
    """Check the places argument controls rounding for the match score."""
    assert str(_as_decimal(83.33333, places=2)) == "83.33"


@pytest.mark.asyncio
async def test_a_missing_decisions_table_does_not_raise(monkeypatch):
    """Check both decision-log writers swallow a missing-table error."""
    async def execute(query, *args):
        raise RuntimeError('relation "etown_memory_decisions" does not exist')

    monkeypatch.setattr(memory_utils.Database, "execute", execute)
    assert await log_memory_decision({"turn_id": "t"}) is None
    assert await record_extraction_outcome("t", "ok", ["a fact"], []) is None


@pytest.mark.asyncio
async def test_the_decision_row_carries_the_floor_and_the_candidates(monkeypatch):
    """Check the decision insert passes Decimal similarities and all candidates, including sub-floor ones."""
    seen = {}

    async def execute(query, *args):
        seen["query"] = query
        seen["args"] = args
        return "INSERT 0 1"

    monkeypatch.setattr(memory_utils.Database, "execute", execute)
    await log_memory_decision({
        "turn_id": "11111111-1111-1111-1111-111111111111",
        "conversation_id": "conv", "user_id": "u", "user_role": 2,
        "raw_prompt": "somewhere to eat?",
        "memory_enabled": True, "retrieval_outcome": "ok",
        "retrieved_count": 4, "injected_count": 1,
        "best_similarity": 0.593, "similarity_floor": MEMORY_SIMILARITY_FLOOR,
        "candidates": build_candidate_log(SEARCH_RESULT), "retrieval_ms": 812,
        "affinity_count": 2, "affinity_matched": "THIRU COMPUTERS", "affinity_score": 100.0,
    })
    assert "etown_memory_decisions" in seen["query"]
    assert "ON CONFLICT (turn_id) DO NOTHING" in seen["query"]
    args = seen["args"]
    assert args[0] == "11111111-1111-1111-1111-111111111111"
    assert isinstance(args[9], Decimal) and str(args[9]) == "0.593"
    assert isinstance(args[10], Decimal)
    assert "Is vegetarian" in args[11]
    assert "Lives in Anna Nagar" in args[11], "sub-floor candidates must be stored too"


@pytest.mark.asyncio
async def test_an_overlong_prompt_is_clipped(monkeypatch):
    """Check the logged raw prompt is clipped to 2000 characters."""
    seen = {}

    async def execute(query, *args):
        seen["args"] = args
        return "INSERT 0 1"

    monkeypatch.setattr(memory_utils.Database, "execute", execute)
    await log_memory_decision({"turn_id": "t", "raw_prompt": "p" * 5000})
    assert len(seen["args"][4]) == 2000


@pytest.mark.asyncio
async def test_extraction_outcome_targets_the_turn(monkeypatch):
    """Check the extraction update targets the turn id and carries outcome, facts and events."""
    seen = {}

    async def execute(query, *args):
        seen["query"] = query
        seen["args"] = args
        return "UPDATE 1"

    monkeypatch.setattr(memory_utils.Database, "execute", execute)
    await record_extraction_outcome(
        "22222222-2222-2222-2222-222222222222", "ok",
        ["Is vegetarian"], [{"event": "ADD", "memory": "Is vegetarian"}],
    )
    assert "UPDATE public.etown_memory_decisions" in seen["query"]
    assert "WHERE turn_id" in seen["query"]
    assert seen["args"][0] == "22222222-2222-2222-2222-222222222222"
    assert seen["args"][1] == "ok"
    assert "Is vegetarian" in seen["args"][2]
    assert "ADD" in seen["args"][3]


class _CountingEmbedder:
    """Stands in for mem0's Gemini embedder and counts the calls that reach it."""

    def __init__(self):
        """Start the call counter at zero."""
        self.calls = 0

    def embed(self, text, memory_action=None):
        """Count the call and return a small vector derived from the text length."""
        self.calls += 1
        return [float(len(text)), 0.5, float(self.calls)]


class _FakeClient:
    def __init__(self):
        """Attach a fresh counting embedder as the embedding model."""
        self.embedding_model = _CountingEmbedder()


def test_a_repeated_prompt_costs_no_second_embedding_call():
    """Check a repeated identical embed is served from the cache without a second call."""
    from app.core.memory import _install_embedding_cache

    client = _FakeClient()
    inner = client.embedding_model
    _install_embedding_cache(client)

    first = client.embedding_model.embed("book a cab", "search")
    second = client.embedding_model.embed("book a cab", "search")
    assert inner.calls == 1, "the second identical embed must be served from the cache"
    assert first == second


def test_different_text_or_action_is_a_separate_entry():
    """Check a different text or memory action misses the cache."""
    from app.core.memory import _install_embedding_cache

    client = _FakeClient()
    inner = client.embedding_model
    _install_embedding_cache(client)

    client.embedding_model.embed("book a cab", "search")
    client.embedding_model.embed("book a taxi", "search")
    client.embedding_model.embed("book a cab", "add")
    assert inner.calls == 3


def test_the_cache_hands_back_a_list_the_caller_cannot_poison():
    """Check mutating a returned vector does not change the cached entry."""
    from app.core.memory import _install_embedding_cache

    client = _FakeClient()
    _install_embedding_cache(client)

    first = client.embedding_model.embed("book a cab", "search")
    assert isinstance(first, list)
    first.append(999.0)
    second = client.embedding_model.embed("book a cab", "search")
    assert 999.0 not in second


def test_cache_info_reports_hits():
    """Check the returned cache_info reports hits and misses."""
    from app.core.memory import _install_embedding_cache

    client = _FakeClient()
    cache_info = _install_embedding_cache(client)
    client.embedding_model.embed("x", "search")
    client.embedding_model.embed("x", "search")
    client.embedding_model.embed("y", "search")
    info = cache_info()
    assert info.hits == 1
    assert info.misses == 2


def test_the_memory_tool_gate_names_both_writing_tools():
    """Check the memory tool gate is exactly remember_preference and forget_preference."""
    from app.api.v1.endpoints.chat import _MEMORY_TOOLS

    assert _MEMORY_TOOLS == {"remember_preference", "forget_preference"}


@pytest.mark.parametrize(
    "tools_used, expect_skip",
    [
        (["remember_preference"], True),
        (["forget_preference"], True),
        (["search_provider", "remember_preference"], True),
        (["search_provider"], False),
        ([], False),
        (["booknow_provider", "request_provider"], False),
    ],
)
def test_only_an_explicit_memory_turn_skips_extraction(tools_used, expect_skip):
    """Check extraction is skipped only when the turn used a memory-writing tool."""
    from app.api.v1.endpoints.chat import _MEMORY_TOOLS

    assert bool(_MEMORY_TOOLS.intersection(tools_used)) is expect_skip


@pytest.mark.asyncio
async def test_a_skipped_turn_still_records_its_outcome(monkeypatch):
    """Check a skipped extraction still records its outcome with empty facts."""
    seen = {}

    async def execute(query, *args):
        seen["args"] = args
        return "UPDATE 1"

    monkeypatch.setattr(memory_utils.Database, "execute", execute)
    await record_extraction_outcome("33333333-3333-3333-3333-333333333333", "skipped_explicit")
    assert seen["args"][0] == "33333333-3333-3333-3333-333333333333"
    assert seen["args"][1] == "skipped_explicit"
    assert seen["args"][2] == "[]"
