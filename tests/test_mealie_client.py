from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from morrisons_mcp.mealie_client import MealieClient


def _mock_foods_response(foods):
    """Build a mock Mealie /api/foods response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {"items": foods}
    resp.raise_for_status.return_value = None
    return resp


@pytest.mark.asyncio
async def test_pantry_staple_detected():
    """A food with non-empty householdsWithIngredientFood is on_hand."""
    client = MealieClient()
    client._enabled = True
    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.get.return_value = _mock_foods_response([
        {
            "name": "Salt",
            "aliases": [],
            "householdsWithIngredientFood": ["family"],
        }
    ])
    client._client = mock_http

    result = await client.is_pantry_staple("salt")
    assert result is True


@pytest.mark.asyncio
async def test_non_pantry_food():
    """A food with empty householdsWithIngredientFood is not on_hand."""
    client = MealieClient()
    client._enabled = True
    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.get.return_value = _mock_foods_response([
        {
            "name": "Chicken Breast",
            "aliases": [],
            "householdsWithIngredientFood": [],
        }
    ])
    client._client = mock_http

    result = await client.is_pantry_staple("chicken breast")
    assert result is False


@pytest.mark.asyncio
async def test_alias_match():
    """Match should work against food aliases too."""
    client = MealieClient()
    client._enabled = True
    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.get.return_value = _mock_foods_response([
        {
            "name": "Extra Virgin Olive Oil",
            "aliases": [{"name": "Olive Oil"}, {"name": "EVOO"}],
            "householdsWithIngredientFood": ["family"],
        }
    ])
    client._client = mock_http

    result = await client.is_pantry_staple("olive oil")
    assert result is True


@pytest.mark.asyncio
async def test_no_foods_found():
    """When Mealie returns no matching foods, return False."""
    client = MealieClient()
    client._enabled = True
    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.get.return_value = _mock_foods_response([])
    client._client = mock_http

    result = await client.is_pantry_staple("dragon fruit")
    assert result is False


@pytest.mark.asyncio
async def test_disabled_returns_false():
    """When Mealie is not configured, always return False."""
    client = MealieClient()
    client._enabled = False

    result = await client.is_pantry_staple("salt")
    assert result is False


@pytest.mark.asyncio
async def test_api_error_returns_false():
    """When the Mealie API fails, return False gracefully."""
    client = MealieClient()
    client._enabled = True
    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.get.side_effect = httpx.ConnectError("Connection refused")
    client._client = mock_http

    result = await client.is_pantry_staple("salt")
    assert result is False


@pytest.mark.asyncio
async def test_onion_not_pantry_staple():
    """'onion' with empty householdsWithIngredientFood must NOT match 'onion powder'."""
    client = MealieClient()
    client._enabled = True
    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.get.return_value = _mock_foods_response([
        {
            "name": "onion",
            "aliases": [],
            "householdsWithIngredientFood": [],
        },
        {
            "name": "onion powder",
            "aliases": [],
            "householdsWithIngredientFood": ["family"],
        },
    ])
    client._client = mock_http

    result = await client.is_pantry_staple("onion")
    assert result is False


@pytest.mark.asyncio
async def test_onion_powder_is_pantry_staple():
    """'onion powder' with non-empty householdsWithIngredientFood should be on_hand."""
    client = MealieClient()
    client._enabled = True
    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.get.return_value = _mock_foods_response([
        {
            "name": "onion",
            "aliases": [],
            "householdsWithIngredientFood": [],
        },
        {
            "name": "onion powder",
            "aliases": [],
            "householdsWithIngredientFood": ["family"],
        },
    ])
    client._client = mock_http

    result = await client.is_pantry_staple("onion powder")
    assert result is True
