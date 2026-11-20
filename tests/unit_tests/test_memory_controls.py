"""Memory Controls Tests - unit tests for Chaatie memory switch, deletes and forget matching.

Hermetic pytest suite with the database and mem0 client monkeypatched: checks that the memory switch fails open, deletes are owner-scoped and reject malformed ids, forget resolves in three confidence bands, and remember or forget refuse cleanly instead of raising.

Classes:
    _FakeMemoryClient(): Stand-in mem0 client returning cosine-distance scores

Functions:
    test_no_settings_row_means_enabled(): Missing settings row leaves memory enabled
    test_a_failing_settings_query_means_enabled(): A failing settings query fails open
    test_an_explicit_false_is_respected(): An explicit disabled setting turns memory off
    test_no_user_id_is_not_enabled(): No user id means memory is not enabled
    test_a_malformed_memory_id_never_reaches_postgres(): Malformed ids are rejected before SQL
    test_delete_passes_both_id_and_owner(): Delete query is scoped to memory id and owner
    test_delete_reports_a_miss(): Deleting an unknown memory reports a miss
    test_delete_all_reports_the_count(): Delete-all returns the number of rows removed
    test_delete_all_needs_an_owner(): Delete-all without an owner does nothing
    test_listing_without_an_owner_returns_empty(): Listing without an owner returns empty
    test_the_confirmation_band_exists(): Confirm floor sits below the delete floor
    _install_fake_client(): Monkeypatch the memory client with _FakeMemoryClient
    test_a_confident_match_is_returned_for_deletion(): A close match is returned for deletion
    test_a_borderline_match_is_returned_but_below_the_delete_floor(): Borderline match needs confirmation
    test_an_unrelated_request_matches_nothing(): An unrelated request finds no memory
    test_the_closest_memory_wins(): The closest-scoring memory is selected
    test_malformed_search_results_are_skipped(): Malformed search results are ignored
    test_remember_refuses_bad_input_without_raising(): Bad remember input is refused, not raised
    test_remember_is_refused_while_memory_is_off(): Remember is refused when memory is off
    test_a_failing_write_is_reported_not_raised(): A failing memory write is reported, not raised
    test_a_borderline_forget_asks_and_deletes_nothing(): Borderline forget asks and deletes nothing
    test_a_confident_forget_deletes_and_quotes_it_back(): Confident forget deletes and quotes the memory
    test_forget_reports_not_found_without_asking(): Forget with no match reports not found

Run:
    pytest tests/unit_tests/test_memory_controls.py -v
"""

import pytest

from app.etown.memory import memory_utils
from app.etown.memory.memory_utils import (
    FORGET_CONFIRM_FLOOR,
    FORGET_SIMILARITY_FLOOR,
    delete_all_memories,
    delete_memory,
    is_memory_enabled,
    list_memories,
    _sync_find_memory_to_forget,
)


@pytest.mark.asyncio
async def test_no_settings_row_means_enabled(monkeypatch):
    """Check that a user with no memory settings row counts as enabled."""
    async def fetchval(query, *args):
        return None

    monkeypatch.setattr(memory_utils.Database, "fetchval", fetchval)
    assert await is_memory_enabled("user-1") is True


@pytest.mark.asyncio
async def test_a_failing_settings_query_means_enabled(monkeypatch):
    """Check that a failing settings query leaves memory enabled."""
    async def fetchval(query, *args):
        raise RuntimeError('relation "etown_memory_settings" does not exist')

    monkeypatch.setattr(memory_utils.Database, "fetchval", fetchval)
    assert await is_memory_enabled("user-1") is True


@pytest.mark.asyncio
async def test_an_explicit_false_is_respected(monkeypatch):
    """Check that an explicit False setting disables memory."""
    async def fetchval(query, *args):
        return False

    monkeypatch.setattr(memory_utils.Database, "fetchval", fetchval)
    assert await is_memory_enabled("user-1") is False


@pytest.mark.asyncio
async def test_no_user_id_is_not_enabled(monkeypatch):
    """Check that an empty user id is not enabled and never reaches the database."""
    called = False

    async def fetchval(query, *args):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(memory_utils.Database, "fetchval", fetchval)
    assert await is_memory_enabled("") is False
    assert not called, "an empty owner should not reach the database"


VALID_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff"


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_id", ["not-a-uuid", "12345", "", None, "  ", VALID_ID[:-1]])
async def test_a_malformed_memory_id_never_reaches_postgres(monkeypatch, memory_id):
    """Check that delete_memory rejects a malformed memory id before querying."""
    async def execute(query, *args):
        raise AssertionError("a malformed id must be rejected before the query")

    monkeypatch.setattr(memory_utils.Database, "execute", execute)
    assert await delete_memory("user-1", memory_id) is False


@pytest.mark.asyncio
async def test_delete_passes_both_id_and_owner(monkeypatch):
    """Check that delete_memory binds both the memory id and the owner and scopes by userprofileid."""
    seen = {}

    async def execute(query, *args):
        seen["query"] = query
        seen["args"] = args
        return "DELETE 1"

    monkeypatch.setattr(memory_utils.Database, "execute", execute)
    assert await delete_memory("user-1", VALID_ID) is True
    assert seen["args"] == (VALID_ID, "user-1")
    assert "userprofileid" in seen["query"], "the delete must be scoped to its owner"


@pytest.mark.asyncio
async def test_delete_reports_a_miss(monkeypatch):
    """Check that delete_memory returns False when no row was deleted."""
    async def execute(query, *args):
        return "DELETE 0"

    monkeypatch.setattr(memory_utils.Database, "execute", execute)
    assert await delete_memory("user-1", VALID_ID) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status, expected", [("DELETE 0", 0), ("DELETE 1", 1), ("DELETE 12", 12)])
async def test_delete_all_reports_the_count(monkeypatch, status, expected):
    """Check that delete_all_memories returns the row count parsed from the command status."""
    async def execute(query, *args):
        return status

    monkeypatch.setattr(memory_utils.Database, "execute", execute)
    assert await delete_all_memories("user-1") == expected


@pytest.mark.asyncio
async def test_delete_all_needs_an_owner(monkeypatch):
    """Check that delete_all_memories returns 0 without querying when the owner is empty."""
    async def execute(query, *args):
        raise AssertionError("must not run without an owner")

    monkeypatch.setattr(memory_utils.Database, "execute", execute)
    assert await delete_all_memories("") == 0


@pytest.mark.asyncio
async def test_listing_without_an_owner_returns_empty(monkeypatch):
    """Check that list_memories returns an empty result without querying when the owner is empty."""
    async def fetch(query, *args):
        raise AssertionError("must not run without an owner")

    monkeypatch.setattr(memory_utils.Database, "fetch", fetch)
    assert await list_memories("") == {"memories": [], "total": 0}


def test_the_confirmation_band_exists():
    """Check that the forget confirmation floor sits below the delete similarity floor."""
    assert FORGET_CONFIRM_FLOOR < FORGET_SIMILARITY_FLOOR


class _FakeMemoryClient:
    """Stands in for mem0's client. Scores are cosine DISTANCES, as pgvector returns them."""

    def __init__(self, entries):
        """Store the canned search results to return."""
        self._entries = entries

    def search(self, query, user_id, limit):
        """Return the canned entries in mem0's results envelope, ignoring the arguments."""
        return {"results": self._entries}


def _install_fake_client(monkeypatch, entries):
    """Patch get_memory_client to return a _FakeMemoryClient serving the given entries."""
    import app.core.memory as core_memory

    monkeypatch.setattr(core_memory, "get_memory_client", lambda: _FakeMemoryClient(entries))


def test_a_confident_match_is_returned_for_deletion(monkeypatch):
    """Check that a close match is returned with similarity at or above the delete floor."""
    _install_fake_client(monkeypatch, [
        {"id": VALID_ID, "memory": "Home address is 12 Second Street", "score": 0.20},
    ])
    found = _sync_find_memory_to_forget("user-1", "that I live on Second Street")
    assert found is not None
    assert found["similarity"] >= FORGET_SIMILARITY_FLOOR


def test_a_borderline_match_is_returned_but_below_the_delete_floor(monkeypatch):
    """Check that a borderline match is returned with similarity between the confirm and delete floors."""
    borderline = 1.0 - ((FORGET_SIMILARITY_FLOOR + FORGET_CONFIRM_FLOOR) / 2)
    _install_fake_client(monkeypatch, [
        {"id": VALID_ID, "memory": "Is vegetarian and avoids onion", "score": borderline},
    ])
    found = _sync_find_memory_to_forget("user-1", "the onion thing")
    assert found is not None
    assert FORGET_CONFIRM_FLOOR <= found["similarity"] < FORGET_SIMILARITY_FLOOR


def test_an_unrelated_request_matches_nothing(monkeypatch):
    """Check that memories just beyond the similarity floor produce no match."""
    _install_fake_client(monkeypatch, [
        {"id": VALID_ID, "memory": "Has two children", "score": 0.407},
        {"id": VALID_ID, "memory": "Prefers appointments before 10am", "score": 0.44},
    ])
    assert _sync_find_memory_to_forget("user-1", "that I own a helicopter") is None


def test_the_closest_memory_wins(monkeypatch):
    """Check that the memory with the smallest distance is chosen."""
    _install_fake_client(monkeypatch, [
        {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "memory": "further", "score": 0.30},
        {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "memory": "closest", "score": 0.05},
    ])
    assert _sync_find_memory_to_forget("user-1", "x")["memory"] == "closest"


@pytest.mark.parametrize("entries", [
    [],
    [{"memory": "no id at all", "score": 0.01}],
    [{"id": VALID_ID, "memory": "bad score", "score": None}],
    [{"id": VALID_ID, "memory": "bad score", "score": "abc"}],
    ["not a dict"],
])
def test_malformed_search_results_are_skipped(monkeypatch, entries):
    """Check that empty or malformed search results yield no match."""
    _install_fake_client(monkeypatch, entries)
    assert _sync_find_memory_to_forget("user-1", "x") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fact, owner", [("", "user-1"), ("   ", "user-1"), ("Is vegetarian", "")])
async def test_remember_refuses_bad_input_without_raising(monkeypatch, fact, owner):
    """Check that a blank fact or owner is refused without touching the memory layer."""
    from app.agent_infrastructure.tools.tools_utils import remember_preference as handlers

    async def never(*args, **kwargs):
        raise AssertionError("must not reach the memory layer")

    monkeypatch.setattr(handlers, "is_memory_enabled", never)
    result = await handlers.handle_remember_preference(fact, user_profile_id=owner)
    assert result["success"] is False
    assert result["remembered"] is None
    assert result["message"]


@pytest.mark.asyncio
async def test_remember_is_refused_while_memory_is_off(monkeypatch):
    """Check that remembering is refused with a switched-off message when memory is disabled."""
    from app.agent_infrastructure.tools.tools_utils import remember_preference as handlers

    async def disabled(user_id):
        return False

    def never(*args, **kwargs):
        raise AssertionError("must not write while memory is off")

    monkeypatch.setattr(handlers, "is_memory_enabled", disabled)
    monkeypatch.setattr(handlers, "_sync_remember_fact", never)
    result = await handlers.handle_remember_preference("Is vegetarian", user_profile_id="user-1")
    assert result["success"] is False
    assert "switched off" in result["message"]


@pytest.mark.asyncio
async def test_a_failing_write_is_reported_not_raised(monkeypatch):
    """Check that a failing memory write returns an unsuccessful result instead of raising."""
    from app.agent_infrastructure.tools.tools_utils import remember_preference as handlers

    async def enabled(user_id):
        return True

    def boom(*args, **kwargs):
        raise RuntimeError("embedding service down")

    monkeypatch.setattr(handlers, "is_memory_enabled", enabled)
    monkeypatch.setattr(handlers, "_sync_remember_fact", boom)
    result = await handlers.handle_remember_preference("Is vegetarian", user_profile_id="user-1")
    assert result["success"] is False
    assert result["remembered"] is None


@pytest.mark.asyncio
async def test_a_borderline_forget_asks_and_deletes_nothing(monkeypatch):
    """Check that a borderline forget asks for confirmation and deletes nothing."""
    from app.agent_infrastructure.tools.tools_utils import remember_preference as handlers

    borderline = (FORGET_SIMILARITY_FLOOR + FORGET_CONFIRM_FLOOR) / 2

    def find(user_id, description):
        return {"memory_id": VALID_ID, "memory": "Is vegetarian", "similarity": borderline}

    async def never(*args, **kwargs):
        raise AssertionError("nothing may be deleted on a borderline match")

    monkeypatch.setattr(handlers, "_sync_find_memory_to_forget", find)
    monkeypatch.setattr(handlers, "delete_memory", never)
    result = await handlers.handle_forget_preference("the veg thing", user_profile_id="user-1")
    assert result["success"] is False
    assert result["needs_confirmation"] is True
    assert result["candidate"] == "Is vegetarian"
    assert result["forgotten"] is None


@pytest.mark.asyncio
async def test_a_confident_forget_deletes_and_quotes_it_back(monkeypatch):
    """Check that a confident forget deletes the owner's memory and quotes it in the message."""
    from app.agent_infrastructure.tools.tools_utils import remember_preference as handlers

    def find(user_id, description):
        return {"memory_id": VALID_ID, "memory": "Is vegetarian", "similarity": 0.95}

    async def delete(owner, memory_id):
        assert (owner, memory_id) == ("user-1", VALID_ID)
        return True

    monkeypatch.setattr(handlers, "_sync_find_memory_to_forget", find)
    monkeypatch.setattr(handlers, "delete_memory", delete)
    result = await handlers.handle_forget_preference("that I'm vegetarian", user_profile_id="user-1")
    assert result["success"] is True
    assert result["forgotten"] == "Is vegetarian"
    assert "vegetarian" in result["message"]


@pytest.mark.asyncio
async def test_forget_reports_not_found_without_asking(monkeypatch):
    """Check that a forget with no match reports nothing stored and asks no confirmation."""
    from app.agent_infrastructure.tools.tools_utils import remember_preference as handlers

    def find(user_id, description):
        return None

    monkeypatch.setattr(handlers, "_sync_find_memory_to_forget", find)
    result = await handlers.handle_forget_preference("that I own a jet", user_profile_id="user-1")
    assert result["success"] is False
    assert result.get("needs_confirmation") is None
    assert "don't have anything stored" in result["message"]
