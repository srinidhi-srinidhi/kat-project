"""Town Reference Grounding Tests - unit tests for town-card Google grounding and background card creation.

Unit tests for mytown_utils that check vision verdicts parse from strings and content blocks, Google or vision failures fall back to the description-only prompt, and background card creation stores no data URLs, swallows model failures and mirrors only writes that hit their row.

Functions:
    _photo(): Build one Places photo fixture
    _places_client(): Fake HTTP client answering text search, place details, then photo bytes
    test_vision_content_blocks_are_parsed(): Content-block vision responses are parsed
    test_plain_string_content_is_parsed(): Plain-string vision responses are parsed
    test_photo_url_never_carries_the_api_key(): Returned photo URL omits the Google API key
    test_unusable_photo_falls_through_to_description_only(): A rejected photo falls back to description
    test_unreadable_verdict_falls_through_to_description_only(): An unparseable verdict falls back to description
    test_google_failure_fails_open(): A Google failure falls back to description
    test_missing_api_key_skips_the_stage(): No API key skips the grounding stage
    test_landscape_candidates_are_judged_first(): Landscape photos are judged before portrait ones
    test_candidates_are_capped(): Vision calls are capped per town
    test_update_town_profile_image_reports_a_missed_row(): A missed row write is reported
    test_creation_stores_the_card_and_mirrors_it(): Creation writes townimage then mirrors ets_townimage
    test_creation_never_mirrors_when_the_row_write_missed(): No mirror when the row write missed
    test_creation_rejects_an_inline_data_url(): Inline data URLs are never stored
    test_creation_swallows_a_generation_failure(): Generation failures leave townimage empty
    test_scheduling_is_deduped_and_needs_a_description(): Scheduling is deduped and needs a description
    test_get_town_details_returns_no_photo_url(): get_town_details no longer returns a photo URL
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.etown.mytown import mytown_utils


def _photo(reference, width, height):
    """Return a fake Places photo dict with the given reference and dimensions."""
    return {"photo_reference": reference, "width": width, "height": height}


def _places_client(photos):
    """Return a fake get_http_client whose GETs answer text search, place details and photo bytes."""
    def _get(url, **kwargs):
        if "textsearch" in url:
            payload = {"status": "OK", "results": [{"place_id": "place-1", "photos": photos}]}
        elif "details" in url:
            payload = {"status": "OK", "result": {"name": "Testtown", "photos": photos}}
        else:
            return AsyncMock(return_value=SimpleNamespace(
                content=b"\xff\xd8\xff-jpeg-bytes",
                headers={"Content-Type": "image/jpeg"},
                raise_for_status=lambda: None,
            ))()
        return AsyncMock(return_value=SimpleNamespace(json=lambda: payload))()

    client = SimpleNamespace(get=_get)
    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    return lambda *a, **k: context


VERDICT_JSON = (
    '{"usable": true, "reason": "wide street view", '
    '"description": "A flat urban street corner with a red bus and leafy trees."}'
)


@pytest.mark.asyncio
async def test_vision_content_blocks_are_parsed():
    """Check that a verdict delivered as a list of content blocks is parsed."""
    response = SimpleNamespace(content=[
        {"type": "text", "text": VERDICT_JSON},
        {"type": "text", "extras": {"signature": "opaque"}},
    ])

    with (
        patch.object(mytown_utils.settings, "google_maps_api_key", "test-key"),
        patch.object(mytown_utils, "get_http_client", _places_client([_photo("ref-0", 1600, 900)])),
        patch.object(mytown_utils, "gemini_2_5_flash_vision", SimpleNamespace(ainvoke=AsyncMock(return_value=response))),
    ):
        result = await mytown_utils.fetch_google_town_reference("Testtown", "Testcity", "Tamil Nadu")

    assert result is not None
    assert result["description"].startswith("A flat urban street corner")
    assert result["verdict"] == "pass: wide street view"


@pytest.mark.asyncio
async def test_plain_string_content_is_parsed():
    """Check that a verdict delivered as a fenced plain string is parsed."""
    response = SimpleNamespace(content="```json\n" + VERDICT_JSON + "\n```")

    with (
        patch.object(mytown_utils.settings, "google_maps_api_key", "test-key"),
        patch.object(mytown_utils, "get_http_client", _places_client([_photo("ref-0", 1600, 900)])),
        patch.object(mytown_utils, "gemini_2_5_flash_vision", SimpleNamespace(ainvoke=AsyncMock(return_value=response))),
    ):
        result = await mytown_utils.fetch_google_town_reference("Testtown")

    assert result is not None
    assert result["verdict"] == "pass: wide street view"


@pytest.mark.asyncio
async def test_photo_url_never_carries_the_api_key():
    """Check that the returned photo URL carries the photo reference but never the API key."""
    response = SimpleNamespace(content=[{"type": "text", "text": VERDICT_JSON}])

    with (
        patch.object(mytown_utils.settings, "google_maps_api_key", "SECRET-KEY"),
        patch.object(mytown_utils, "get_http_client", _places_client([_photo("ref-0", 1600, 900)])),
        patch.object(mytown_utils, "gemini_2_5_flash_vision", SimpleNamespace(ainvoke=AsyncMock(return_value=response))),
    ):
        result = await mytown_utils.fetch_google_town_reference("Testtown")

    assert "SECRET-KEY" not in result["photo_url"]
    assert "photoreference=ref-0" in result["photo_url"]


@pytest.mark.asyncio
async def test_unusable_photo_falls_through_to_description_only():
    """Check that a photo the vision model rejects yields no reference."""
    rejection = SimpleNamespace(content=[
        {"type": "text", "text": '{"usable": false, "reason": "menu board", "description": ""}'},
    ])

    with (
        patch.object(mytown_utils.settings, "google_maps_api_key", "test-key"),
        patch.object(mytown_utils, "get_http_client", _places_client([_photo("ref-0", 1600, 900)])),
        patch.object(mytown_utils, "gemini_2_5_flash_vision", SimpleNamespace(ainvoke=AsyncMock(return_value=rejection))),
    ):
        assert await mytown_utils.fetch_google_town_reference("Testtown") is None


@pytest.mark.asyncio
async def test_unreadable_verdict_falls_through_to_description_only():
    """Check that an unparseable verdict rejects the candidate instead of raising."""
    garbage = SimpleNamespace(content=[{"type": "text", "text": "{'usable': True}"}])

    with (
        patch.object(mytown_utils.settings, "google_maps_api_key", "test-key"),
        patch.object(mytown_utils, "get_http_client", _places_client([_photo("ref-0", 1600, 900)])),
        patch.object(mytown_utils, "gemini_2_5_flash_vision", SimpleNamespace(ainvoke=AsyncMock(return_value=garbage))),
    ):
        assert await mytown_utils.fetch_google_town_reference("Testtown") is None


@pytest.mark.asyncio
async def test_google_failure_fails_open():
    """Check that a Places client error returns None instead of raising."""
    def _boom(*a, **k):
        raise RuntimeError("places is down")

    with (
        patch.object(mytown_utils.settings, "google_maps_api_key", "test-key"),
        patch.object(mytown_utils, "get_http_client", _boom),
    ):
        assert await mytown_utils.fetch_google_town_reference("Testtown") is None


@pytest.mark.asyncio
async def test_missing_api_key_skips_the_stage():
    """Check that an empty Google Maps API key skips the reference lookup."""
    with patch.object(mytown_utils.settings, "google_maps_api_key", ""):
        assert await mytown_utils.fetch_google_town_reference("Testtown") is None


@pytest.mark.asyncio
async def test_landscape_candidates_are_judged_first():
    """Check that a landscape photo is judged and chosen ahead of a portrait one."""
    seen = []

    async def _judge(messages):
        seen.append(messages)
        return SimpleNamespace(content=[{"type": "text", "text": VERDICT_JSON}])

    photos = [_photo("portrait", 900, 1600), _photo("landscape", 1600, 900)]
    with (
        patch.object(mytown_utils.settings, "google_maps_api_key", "test-key"),
        patch.object(mytown_utils, "get_http_client", _places_client(photos)),
        patch.object(mytown_utils, "gemini_2_5_flash_vision", SimpleNamespace(ainvoke=_judge)),
    ):
        result = await mytown_utils.fetch_google_town_reference("Testtown")

    assert "photoreference=landscape" in result["photo_url"]


@pytest.mark.asyncio
async def test_candidates_are_capped():
    """Check that vision calls are capped at _TOWN_REFERENCE_MAX_CANDIDATES."""
    calls = []

    async def _judge(messages):
        calls.append(messages)
        return SimpleNamespace(content=[{"type": "text", "text": '{"usable": false, "reason": "x", "description": ""}'}])

    photos = [_photo(f"ref-{i}", 1600, 900) for i in range(10)]
    with (
        patch.object(mytown_utils.settings, "google_maps_api_key", "test-key"),
        patch.object(mytown_utils, "get_http_client", _places_client(photos)),
        patch.object(mytown_utils, "gemini_2_5_flash_vision", SimpleNamespace(ainvoke=_judge)),
    ):
        await mytown_utils.fetch_google_town_reference("Testtown")

    assert len(calls) == mytown_utils._TOWN_REFERENCE_MAX_CANDIDATES


@pytest.mark.asyncio
async def test_update_town_profile_image_reports_a_missed_row():
    """Check that update_town_profile_image returns False for no matched row or a DB error."""
    with patch.object(mytown_utils.Database, "execute", new=AsyncMock(return_value="UPDATE 0")):
        assert await mytown_utils.update_town_profile_image("no-such-town", "https://blob/x.jpg") is False

    with patch.object(mytown_utils.Database, "execute", new=AsyncMock(return_value="UPDATE 1")):
        assert await mytown_utils.update_town_profile_image("town-id", "https://blob/x.jpg") is True

    with patch.object(mytown_utils.Database, "execute", new=AsyncMock(side_effect=RuntimeError("pool closed"))):
        assert await mytown_utils.update_town_profile_image("town-id", "https://blob/x.jpg") is False


@pytest.mark.asyncio
async def test_creation_stores_the_card_and_mirrors_it():
    """Check that card generation stores townimage and then mirrors it to Dataverse."""
    card = {"image_url": "https://blob.example/ai_generated/x.jpg", "reference": {"verdict": "pass: x"}}
    update = AsyncMock(return_value=True)
    mirror = AsyncMock(return_value=True)

    with (
        patch.object(mytown_utils, "generate_town_card", new=AsyncMock(return_value=card)),
        patch.object(mytown_utils, "update_town_profile_image", new=update),
        patch("app.etown.mytown.mytown_syncing.sync_town_profile_image_to_dataverse", new=mirror),
    ):
        await mytown_utils._generate_and_store_town_card(
            townprofileid="town-id", townname="Testtown", cityname="Testcity",
            statecode="Tamil Nadu", towndescription="A town.", latitude=1.0, longitude=2.0,
        )

    update.assert_awaited_once_with("town-id", "https://blob.example/ai_generated/x.jpg")
    mirror.assert_awaited_once_with("town-id", "https://blob.example/ai_generated/x.jpg")


@pytest.mark.asyncio
async def test_creation_never_mirrors_when_the_row_write_missed():
    """Check that the Dataverse mirror is skipped when the townimage write matched no row."""
    card = {"image_url": "https://blob.example/ai_generated/x.jpg", "reference": None}
    mirror = AsyncMock(return_value=True)

    with (
        patch.object(mytown_utils, "generate_town_card", new=AsyncMock(return_value=card)),
        patch.object(mytown_utils, "update_town_profile_image", new=AsyncMock(return_value=False)),
        patch("app.etown.mytown.mytown_syncing.sync_town_profile_image_to_dataverse", new=mirror),
    ):
        await mytown_utils._generate_and_store_town_card(
            townprofileid="gone", townname="Testtown", cityname=None, statecode=None,
            towndescription="A town.", latitude=None, longitude=None,
        )

    mirror.assert_not_awaited()


@pytest.mark.asyncio
async def test_creation_rejects_an_inline_data_url():
    """Check that a data: URL image is never written to townimage."""
    card = {"image_url": "data:image/jpeg;base64,AAAA", "reference": None}
    update = AsyncMock(return_value=True)

    with (
        patch.object(mytown_utils, "generate_town_card", new=AsyncMock(return_value=card)),
        patch.object(mytown_utils, "update_town_profile_image", new=update),
    ):
        await mytown_utils._generate_and_store_town_card(
            townprofileid="town-id", townname="Testtown", cityname=None, statecode=None,
            towndescription="A town.", latitude=None, longitude=None,
        )

    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_creation_swallows_a_generation_failure():
    """Check that a card generation error is swallowed and townimage is left unwritten."""
    update = AsyncMock(return_value=True)

    with (
        patch.object(mytown_utils, "generate_town_card", new=AsyncMock(side_effect=RuntimeError("403"))),
        patch.object(mytown_utils, "update_town_profile_image", new=update),
    ):
        await mytown_utils._generate_and_store_town_card(
            townprofileid="town-id", townname="Testtown", cityname=None, statecode=None,
            towndescription="A town.", latitude=None, longitude=None,
        )

    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduling_is_deduped_and_needs_a_description():
    """Check that card scheduling runs one task per town and skips blank descriptions."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow(**kwargs):
        started.set()
        await release.wait()

    with patch.object(mytown_utils, "_generate_and_store_town_card", new=_slow):
        mytown_utils.schedule_town_card_generation(
            townprofileid="town-id", townname="Testtown", towndescription="A town.")
        await started.wait()
        assert len(mytown_utils._TOWN_CARD_TASKS) == 1

        mytown_utils.schedule_town_card_generation(
            townprofileid="town-id", townname="Testtown", towndescription="A town.")
        assert len(mytown_utils._TOWN_CARD_TASKS) == 1, "a second schedule started a second generation"

        mytown_utils.schedule_town_card_generation(
            townprofileid="other-id", townname="Othertown", towndescription="   ")
        assert "other-id" not in mytown_utils._TOWN_CARD_TASKS

        release.set()
        await mytown_utils._TOWN_CARD_TASKS["town-id"]

    assert mytown_utils._TOWN_CARD_TASKS == {}


@pytest.mark.asyncio
async def test_get_town_details_returns_no_photo_url():
    """Check that get_town_details returns coordinates with an empty image and no keyed photo URL."""
    from app.etown.services import services_utils

    def _get(url, **kwargs):
        payload = {"status": "OK", "results": [{"geometry": {"location": {"lat": 1.5, "lng": 2.5}}}]}
        return AsyncMock(return_value=SimpleNamespace(status_code=200, json=lambda: payload))()

    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=SimpleNamespace(get=_get))
    context.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(services_utils.settings, "google_maps_api_key", "SECRET-KEY"),
        patch.object(services_utils, "get_http_client", lambda *a, **k: context),
    ):
        details = await services_utils.get_town_details("Testtown", "Tamil Nadu")

    assert details == {"latitude": 1.5, "longitude": 2.5, "image": ""}
