"""Town Image Verification Tests - unit tests for town-photo moderation and its state vocabulary.

Unit tests for mytown_utils and mytown_syncing covering normalize_verification() over API words and Dataverse choice numbers, the approve and reject writes of handle_verify_town_image(), and the verification-only Dataverse mirror.

Functions:
    test_normalize_verification(): Stored values reduce to pending/approved/rejected
    test_unreadable_state_is_never_approved(): Unrecognised values never normalise to approved
    test_verify_writes_the_resolved_state(): Approve/reject writes the resolved state
    test_verify_refuses_anything_but_approve_or_reject(): Other action modes are refused
    test_verify_404s_on_an_unknown_image(): An unknown image id raises 404
    test_verification_mirror_patches_only_that_field(): The CRM mirror patches only verification
    test_verification_mirror_skips_a_non_guid(): A non-GUID image id skips the mirror
    test_choice_values_match_the_dataverse_global_choice(): Choice values are Pending=0, Approved=1, Rejected=2
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.etown.mytown import mytown_utils
from app.etown.mytown.mytown_syncing import (
    _verification_to_int,
    sync_town_image_verification_to_dataverse,
)

ROW = {
    "townimageid": "image-id",
    "townprofileid": "town-id",
    "townimageurl": "https://blob.example/town.jpg",
    "userprofileid": "user-id",
    "contactid": "contact-id",
    "name": "Alice",
    "verification": "pending",
}


@pytest.mark.parametrize(
    "stored,expected",
    [
        ("pending", "pending"), ("approved", "approved"), ("rejected", "rejected"),
        ("Approved", "approved"), ("REJECTED", "rejected"), ("  Approved  ", "approved"),
        ("0", "pending"), ("1", "approved"), ("2", "rejected"),
        (None, "pending"), ("", "pending"), ("nonsense", "pending"), (0, "pending"), (1, "approved"),
    ],
)
def test_normalize_verification(stored, expected):
    """Assert stored words and choice numbers reduce to pending, approved or rejected."""
    assert mytown_utils.normalize_verification(stored) == expected


def test_unreadable_state_is_never_approved():
    """Assert unrecognised verification values never normalise to approved."""
    for junk in (None, "", "  ", "yes", "true", "verified", "9", -1):
        assert mytown_utils.normalize_verification(junk) != "approved"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actionmode,expected",
    [("approve", "approved"), ("approved", "approved"), ("APPROVE", "approved"),
     ("reject", "rejected"), ("rejected", "rejected"), ("  Reject  ", "rejected")],
)
async def test_verify_writes_the_resolved_state(actionmode, expected):
    """Assert approve and reject action modes write and return the resolved state."""
    execute = AsyncMock(return_value="UPDATE 1")
    with (
        patch.object(mytown_utils.Database, "fetchrow", new=AsyncMock(return_value=dict(ROW))),
        patch.object(mytown_utils.Database, "execute", new=execute),
    ):
        result = await mytown_utils.handle_verify_town_image("image-id", actionmode)

    assert result["verification"] == expected
    assert result["previous_verification"] == "pending"
    assert execute.await_args.args[1:] == ("image-id", expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("actionmode", ["delete", "", None, "pending", "approve_all", "drop"])
async def test_verify_refuses_anything_but_approve_or_reject(actionmode):
    """Assert other action modes raise 400 without writing."""
    execute = AsyncMock()
    with (
        patch.object(mytown_utils.Database, "fetchrow", new=AsyncMock(return_value=dict(ROW))),
        patch.object(mytown_utils.Database, "execute", new=execute),
    ):
        with pytest.raises(HTTPException) as exc:
            await mytown_utils.handle_verify_town_image("image-id", actionmode)

    assert exc.value.status_code == 400
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_404s_on_an_unknown_image():
    """Assert an unknown image id raises 404 without writing."""
    execute = AsyncMock()
    with (
        patch.object(mytown_utils.Database, "fetchrow", new=AsyncMock(return_value=None)),
        patch.object(mytown_utils.Database, "execute", new=execute),
    ):
        with pytest.raises(HTTPException) as exc:
            await mytown_utils.handle_verify_town_image("nope", "approve")

    assert exc.value.status_code == 404
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_verification_mirror_patches_only_that_field():
    """Assert the verification mirror PATCHes only ets_verification."""
    upsert = AsyncMock(return_value=True)
    with (
        patch("app.etown.mytown.mytown_syncing.get_dataverse_token", new=AsyncMock(return_value="token")),
        patch(
            "app.etown.mytown.mytown_syncing._build_dataverse_context",
            return_value=("https://example.crm.dynamics.com/api/data/v9.2", {"Authorization": "Bearer token"}),
        ),
        patch("app.etown.mytown.mytown_syncing._upsert_record", new=upsert),
    ):
        assert await sync_town_image_verification_to_dataverse(
            "3f2504e0-4f89-11d3-9a0c-0305e82c3301", "approved") is True

    payload = upsert.await_args.args[2]
    assert payload == {"ets_verification": 1}


@pytest.mark.asyncio
async def test_verification_mirror_skips_a_non_guid():
    """Assert a non-GUID image id skips the mirror and returns False."""
    upsert = AsyncMock()
    with patch("app.etown.mytown.mytown_syncing._upsert_record", new=upsert):
        assert await sync_town_image_verification_to_dataverse("not-a-guid", "approved") is False
    upsert.assert_not_awaited()


def test_choice_values_match_the_dataverse_global_choice():
    """Assert verification maps to Dataverse choices Pending=0, Approved=1, Rejected=2."""
    assert _verification_to_int("pending") == 0
    assert _verification_to_int("Approved") == 1
    assert _verification_to_int("rejected") == 2
