import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import httpx

from morrisons_mcp.nutrition_fallback import (
    get_fallback_nutrition,
    _search_open_food_facts,
    _search_usda_fdc,
)


def _mock_off_response(kcal=160, protein=2.0, fat=15.0, carbs=8.5):
    """Build a mock Open Food Facts search response."""
    return {
        "products": [
            {
                "product_name": "Avocado",
                "nutriments": {
                    "energy-kcal_100g": kcal,
                    "proteins_100g": protein,
                    "fat_100g": fat,
                    "saturated-fat_100g": 2.1,
                    "carbohydrates_100g": carbs,
                    "sugars_100g": 0.7,
                    "fiber_100g": 6.7,
                    "salt_100g": 0.01,
                    "energy-kj_100g": 670,
                },
            }
        ]
    }


def _mock_usda_response(kcal=160, protein=2.0, fat=14.7, carbs=8.5):
    """Build a mock USDA FDC search response."""
    return {
        "foods": [
            {
                "dataType": "Foundation",
                "description": "Avocados, raw, all commercial varieties",
                "foodNutrients": [
                    {"nutrientName": "Energy", "value": kcal, "unitName": "KCAL"},
                    {"nutrientName": "Protein", "value": protein, "unitName": "G"},
                    {"nutrientName": "Total lipid (fat)", "value": fat, "unitName": "G"},
                    {"nutrientName": "Fatty acids, total saturated", "value": 2.1, "unitName": "G"},
                    {"nutrientName": "Carbohydrate, by difference", "value": carbs, "unitName": "G"},
                    {"nutrientName": "Sugars, total including NLEA", "value": 0.7, "unitName": "G"},
                    {"nutrientName": "Fiber, total dietary", "value": 6.7, "unitName": "G"},
                    {"nutrientName": "Sodium, Na", "value": 7.0, "unitName": "MG"},
                ],
            }
        ]
    }


def _make_mock_response(json_data, status_code=200):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


@pytest.mark.asyncio
async def test_open_food_facts_returns_nutrition():
    mock_resp = _make_mock_response(_mock_off_response())
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = mock_resp

    result = await _search_open_food_facts("avocado", client)
    assert result is not None
    assert result.energy_kcal == 160
    assert result.protein_g == 2.0
    assert result.fat_g == 15.0
    assert result.carbohydrate_g == 8.5


@pytest.mark.asyncio
async def test_open_food_facts_no_products():
    mock_resp = _make_mock_response({"products": []})
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = mock_resp

    result = await _search_open_food_facts("xyznonexistent", client)
    assert result is None


@pytest.mark.asyncio
async def test_open_food_facts_missing_required_fields():
    """Products without kcal AND protein should be skipped."""
    mock_resp = _make_mock_response({
        "products": [
            {"product_name": "Test", "nutriments": {"fat_100g": 5.0}},
        ]
    })
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = mock_resp

    result = await _search_open_food_facts("test", client)
    assert result is None


@pytest.mark.asyncio
async def test_usda_fdc_returns_nutrition():
    mock_resp = _make_mock_response(_mock_usda_response())
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = mock_resp

    result = await _search_usda_fdc("avocado", client)
    assert result is not None
    assert result.energy_kcal == 160
    assert result.protein_g == 2.0
    assert result.fat_g == 14.7
    # Sodium 7mg → salt = 7 * 2.5 / 1000 = 0.0175 → 0.02
    assert result.salt_g == 0.02


@pytest.mark.asyncio
async def test_usda_fdc_no_foods():
    mock_resp = _make_mock_response({"foods": []})
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = mock_resp

    result = await _search_usda_fdc("xyznonexistent", client)
    assert result is None


@pytest.mark.asyncio
async def test_fallback_tries_off_first_then_usda():
    """When OFF succeeds, USDA should not be called."""
    off_resp = _make_mock_response(_mock_off_response())
    usda_resp = _make_mock_response(_mock_usda_response())

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = off_resp
    mock_client.post.return_value = usda_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("morrisons_mcp.nutrition_fallback.httpx.AsyncClient", return_value=mock_client):
        result, source = await get_fallback_nutrition("avocado")

    assert result is not None
    assert source == "Open Food Facts"
    assert result.energy_kcal == 160
    # USDA should not have been called
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_fallback_uses_usda_when_off_fails():
    """When OFF returns no results, USDA should be tried."""
    off_resp = _make_mock_response({"products": []})
    usda_resp = _make_mock_response(_mock_usda_response(kcal=170, protein=1.8))

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = off_resp
    mock_client.post.return_value = usda_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("morrisons_mcp.nutrition_fallback.httpx.AsyncClient", return_value=mock_client):
        result, source = await get_fallback_nutrition("avocado")

    assert result is not None
    assert source == "USDA FoodData Central"
    assert result.energy_kcal == 170


@pytest.mark.asyncio
async def test_fallback_returns_none_when_both_fail():
    off_resp = _make_mock_response({"products": []})
    usda_resp = _make_mock_response({"foods": []})

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = off_resp
    mock_client.post.return_value = usda_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("morrisons_mcp.nutrition_fallback.httpx.AsyncClient", return_value=mock_client):
        result, source = await get_fallback_nutrition("xyznonexistent")

    assert result is None
    assert source is None
