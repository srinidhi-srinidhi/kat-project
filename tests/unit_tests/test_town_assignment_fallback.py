"""Town Assignment Fallback Tests - unit tests for the nearest-town fallback in mytown_utils.

Database-free tests that mock Database.fetchrow to check find_nearest_town_by_distance binds coordinates as text for the all-text etown_town_distance_km function, caps the radius in SQL, and that the fallback radius exceeds the dedup radius.

Functions:
    test_fallback_radius_is_wider_than_the_dedup_radius(): TOWN_FALLBACK_MAX_KM exceeds TOWN_DEDUP_RADIUS_KM
    test_nearest_town_passes_coordinates_as_text(): Coordinates bind as text, radius as float
    test_nearest_town_returns_a_plain_dict_or_none(): Returns the fetched row or None
    test_nearest_town_excludes_rows_beyond_the_radius_in_sql(): SQL caps radius, skips null coords, takes nearest
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.etown.mytown import mytown_utils


def test_fallback_radius_is_wider_than_the_dedup_radius():
    """Assert the nearest-town fallback radius is wider than the town dedup radius."""
    assert mytown_utils.TOWN_FALLBACK_MAX_KM > mytown_utils.TOWN_DEDUP_RADIUS_KM


@pytest.mark.asyncio
async def test_nearest_town_passes_coordinates_as_text():
    """Assert coordinates bind as text casts and the radius binds as a float."""
    fetchrow = AsyncMock(return_value=None)
    with patch.object(mytown_utils.Database, "fetchrow", new=fetchrow):
        await mytown_utils.Database.find_nearest_town_by_distance(11.25, 79.75, 50.0)

    sql, lat, lon, radius = fetchrow.await_args.args
    assert (lat, lon) == ("11.25", "79.75"), "coordinates must be text for the all-text distance function"
    assert isinstance(radius, float)
    assert "$1::text" in sql and "$2::text" in sql


@pytest.mark.asyncio
async def test_nearest_town_returns_a_plain_dict_or_none():
    """Assert the lookup returns the fetched row unchanged, or None when no row is found."""
    row = {"townprofileid": "t1", "townname": "Palakkad", "cityname": "Palakkad", "distance_km": 5.6}
    with patch.object(mytown_utils.Database, "fetchrow", new=AsyncMock(return_value=row)):
        assert await mytown_utils.Database.find_nearest_town_by_distance(1.0, 2.0) == row

    with patch.object(mytown_utils.Database, "fetchrow", new=AsyncMock(return_value=None)):
        assert await mytown_utils.Database.find_nearest_town_by_distance(1.0, 2.0) is None


@pytest.mark.asyncio
async def test_nearest_town_excludes_rows_beyond_the_radius_in_sql():
    """Assert the SQL caps the radius, skips null coordinates and returns only the nearest town."""
    fetchrow = AsyncMock(return_value=None)
    with patch.object(mytown_utils.Database, "fetchrow", new=fetchrow):
        await mytown_utils.Database.find_nearest_town_by_distance(1.0, 2.0, 50.0)

    sql = fetchrow.await_args.args[0]
    assert "<= $3" in sql
    assert "IS NOT NULL" in sql, "a town with unparseable coordinates must be skipped, not ranked"
    assert "ORDER BY 4 ASC" in sql, "the nearest town must win"
    assert "LIMIT 1" in sql
