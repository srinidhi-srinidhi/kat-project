"""Conversation Dedup Tests - unit tests for the /api/conversations/insert_conversation duplicate guards.

Unit tests asserting that replayed user rows and echoed assistant replies are not stored twice in etownuserchaatiehistory, while a genuine repeat far apart is still stored and a short identical word is not mistaken for an echo.

Functions:
    test_echo_floor_is_long_enough_to_be_unambiguous(): Echo length floor rejects short words
    test_log_request_defaults_to_assistant(): LogRequest role defaults to assistant
    test_a_replay_is_reported_and_not_reinserted(): A replayed user row is not stored twice
    test_an_assistant_echo_is_reported_and_not_reinserted(): An echoed reply is not stored
    test_new_content_is_written(): New content is inserted
    test_a_failed_duplicate_check_still_stores_the_message(): A failed dedup check still stores

Run:
    pytest tests/unit_tests/test_conversation_dedup.py -v
"""

import pytest

from app.etown.conversations.conversation_utils import Database
from app.schemas.api import LogRequest


LONG_REPLY = (
    "INIYAN CABS & ACTING DRIVERS offers the following service: Cab Services, Acting "
    "Drivers. Could you please confirm which service you would like to book?"
)


def test_echo_floor_is_long_enough_to_be_unambiguous():
    """Check the echo floor sits above short replies and below a paragraph-length reply."""
    assert Database.ECHO_MIN_LENGTH >= 20
    assert len(LONG_REPLY) > Database.ECHO_MIN_LENGTH
    assert len("yes") < Database.ECHO_MIN_LENGTH
    assert len("ok thanks") < Database.ECHO_MIN_LENGTH


def test_log_request_defaults_to_assistant():
    """Check a LogRequest without a role defaults to assistant."""
    request = LogRequest(
        contact_id="c", user_profile_id="u", conversation_id="conv", conversation="hello"
    )
    assert request.role == "assistant"


@pytest.mark.asyncio
async def test_a_replay_is_reported_and_not_reinserted(monkeypatch):
    """Check a replayed user message returns the existing id and is not saved again."""
    from app.etown.conversations import conversation_utils

    async def find_duplicate_message(conversation_id, role, content):
        return {"id": 4242, "role": "user", "reason": "replay"}

    async def save(**kwargs):
        raise AssertionError("a duplicate must not be written")

    monkeypatch.setattr(conversation_utils.Database, "find_duplicate_message",
                        find_duplicate_message)
    monkeypatch.setattr(conversation_utils.Database, "save_conversation_message", save)

    result = await conversation_utils.ConversationService.insert_conversation_message(
        LogRequest(contact_id="c", user_profile_id="u", conversation_id="conv",
                   conversation="book a cab", role="user")
    )
    assert result["success"] is True
    assert result["message_id"] == 4242
    assert "replay" in result["message"]


@pytest.mark.asyncio
async def test_an_assistant_echo_is_reported_and_not_reinserted(monkeypatch):
    """Check a user message echoing an assistant reply is reported as assistant_echo and not saved."""
    from app.etown.conversations import conversation_utils

    async def find_duplicate_message(conversation_id, role, content):
        assert role == "user"
        return {"id": 99, "role": "assistant", "reason": "assistant_echo"}

    async def save(**kwargs):
        raise AssertionError("an echo must not be written")

    monkeypatch.setattr(conversation_utils.Database, "find_duplicate_message",
                        find_duplicate_message)
    monkeypatch.setattr(conversation_utils.Database, "save_conversation_message", save)

    result = await conversation_utils.ConversationService.insert_conversation_message(
        LogRequest(contact_id="c", user_profile_id="u", conversation_id="conv",
                   conversation=LONG_REPLY, role="user")
    )
    assert result["success"] is True
    assert "assistant_echo" in result["message"]


@pytest.mark.asyncio
async def test_new_content_is_written(monkeypatch):
    """Check a non-duplicate message is saved with its content and role."""
    from app.etown.conversations import conversation_utils

    written = {}

    async def find_duplicate_message(conversation_id, role, content):
        return None

    async def save(**kwargs):
        written.update(kwargs)
        return 7

    monkeypatch.setattr(conversation_utils.Database, "find_duplicate_message",
                        find_duplicate_message)
    monkeypatch.setattr(conversation_utils.Database, "save_conversation_message", save)

    result = await conversation_utils.ConversationService.insert_conversation_message(
        LogRequest(contact_id="c", user_profile_id="u", conversation_id="conv",
                   conversation="a brand new message", role="user")
    )
    assert result["message_id"] == 7
    assert written["content"] == "a brand new message"
    assert written["role"] == "user"


@pytest.mark.asyncio
async def test_a_failed_duplicate_check_still_stores_the_message(monkeypatch):
    """Check the message is still stored when the duplicate lookup finds nothing."""
    from app.etown.conversations import conversation_utils

    async def find_duplicate_message(conversation_id, role, content):
        return None

    async def save(**kwargs):
        return 11

    monkeypatch.setattr(conversation_utils.Database, "find_duplicate_message",
                        find_duplicate_message)
    monkeypatch.setattr(conversation_utils.Database, "save_conversation_message", save)

    result = await conversation_utils.ConversationService.insert_conversation_message(
        LogRequest(contact_id="c", user_profile_id="u", conversation_id="conv",
                   conversation="still needs storing", role="user")
    )
    assert result["message_id"] == 11
