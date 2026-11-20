"""Town Image Sync Tests - unit tests for the etown_townimages Dataverse mirror.

Unit tests that check sync_town_image_to_dataverse() builds the upsert payload, binds lookups through @odata.bind navigation properties, and returns False when token acquisition fails.

Functions:
    test_sync_town_image_builds_dataverse_upsert(): Upserts the town image with the expected payload
    test_sync_town_image_binds_lookups_by_navigation_property(): Lookups use @odata.bind navigation names
    test_sync_town_image_returns_false_when_token_acquisition_fails(): Token failure returns False
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.etown.mytown.mytown_syncing import sync_town_image_to_dataverse


@pytest.mark.asyncio
async def test_sync_town_image_builds_dataverse_upsert():
    """Check the town image upsert URL, payload, verification mapping and lookup binds."""
    upsert = AsyncMock(return_value=True)

    with (
        patch("app.etown.mytown.mytown_syncing.get_dataverse_token", new=AsyncMock(return_value="token")),
        patch(
            "app.etown.mytown.mytown_syncing._build_dataverse_context",
            return_value=("https://example.crm.dynamics.com/api/data/v9.2", {"Authorization": "Bearer token"}),
        ),
        patch("app.etown.mytown.mytown_syncing._upsert_record", new=upsert),
    ):
        result = await sync_town_image_to_dataverse(
            townimageid="image-id",
            townprofileid="town-id",
            townimageurl="https://blob.example/town.jpg",
            userprofileid="user-id",
            contactid="contact-id",
            verification="pending",
            name="Alice",
        )

    assert result is True
    upsert.assert_awaited_once_with(
        {"Authorization": "Bearer token"},
        "https://example.crm.dynamics.com/api/data/v9.2/ets_townimages(image-id)",
        {
            "ets_townimageurl": "https://blob.example/town.jpg",
            "ets_verification": 0,
            "ets_name": "Alice",
            "ets_caption": None,
            "ets_TownProfileID@odata.bind": "/ets_townprofiles(town-id)",
            "ets_UserProfileID@odata.bind": "/ets_userprofiles(user-id)",
            "ets_ContactID@odata.bind": "/contacts(contact-id)",
        },
        "TOWN_IMAGE",
        "image-id",
    )


@pytest.mark.asyncio
async def test_sync_town_image_binds_lookups_by_navigation_property():
    """Check lookups are sent as @odata.bind navigation properties, never bare lookup attributes."""
    upsert = AsyncMock(return_value=True)

    with (
        patch("app.etown.mytown.mytown_syncing.get_dataverse_token", new=AsyncMock(return_value="token")),
        patch(
            "app.etown.mytown.mytown_syncing._build_dataverse_context",
            return_value=("https://example.crm.dynamics.com/api/data/v9.2", {"Authorization": "Bearer token"}),
        ),
        patch("app.etown.mytown.mytown_syncing._upsert_record", new=upsert),
    ):
        await sync_town_image_to_dataverse(
            townimageid="image-id",
            townprofileid="town-id",
            userprofileid="user-id",
            contactid="contact-id",
        )

    payload = upsert.await_args.args[2]
    for bare in ("ets_townprofileid", "ets_userprofileid", "ets_contactid"):
        assert bare not in payload, f"{bare} is a bare lookup attribute; Dataverse drops it silently"
    assert payload["ets_TownProfileID@odata.bind"] == "/ets_townprofiles(town-id)"
    assert payload["ets_UserProfileID@odata.bind"] == "/ets_userprofiles(user-id)"
    assert payload["ets_ContactID@odata.bind"] == "/contacts(contact-id)"


@pytest.mark.asyncio
async def test_sync_town_image_returns_false_when_token_acquisition_fails():
    """Check a token failure returns False without attempting the upsert."""
    with patch(
        "app.etown.mytown.mytown_syncing.get_dataverse_token",
        new=AsyncMock(side_effect=RuntimeError("token unavailable")),
    ), patch("app.etown.mytown.mytown_syncing._upsert_record", new=AsyncMock()) as upsert:
        result = await sync_town_image_to_dataverse(townimageid="image-id")

    assert result is False
    upsert.assert_not_awaited()
