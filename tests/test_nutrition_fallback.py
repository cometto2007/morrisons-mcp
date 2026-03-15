import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import httpx

from morrisons_mcp.nutrition_fallback import (
    _validate_usda_result,
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


@pytest.mark.asyncio
async def test_usda_rejects_egg_whites():
    """USDA result for 'eggs' with low kcal/fat should be rejected (egg whites)."""
    # Egg whites: ~52 kcal, ~0.2g fat — should fail sanity check
    egg_white_resp = _make_mock_response({
        "foods": [
            {
                "dataType": "Foundation",
                "description": "Egg, white, raw, fresh",
                "foodNutrients": [
                    {"nutrientName": "Energy", "value": 52, "unitName": "KCAL"},
                    {"nutrientName": "Protein", "value": 10.9, "unitName": "G"},
                    {"nutrientName": "Total lipid (fat)", "value": 0.2, "unitName": "G"},
                    {"nutrientName": "Carbohydrate, by difference", "value": 0.7, "unitName": "G"},
                    {"nutrientName": "Sodium, Na", "value": 166, "unitName": "MG"},
                ],
            },
            {
                "dataType": "Foundation",
                "description": "Egg, whole, raw, fresh",
                "foodNutrients": [
                    {"nutrientName": "Energy", "value": 143, "unitName": "KCAL"},
                    {"nutrientName": "Protein", "value": 12.6, "unitName": "G"},
                    {"nutrientName": "Total lipid (fat)", "value": 9.5, "unitName": "G"},
                    {"nutrientName": "Carbohydrate, by difference", "value": 0.7, "unitName": "G"},
                    {"nutrientName": "Sodium, Na", "value": 142, "unitName": "MG"},
                ],
            },
        ]
    })
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = egg_white_resp

    result = await _search_usda_fdc("eggs", client)
    assert result is not None
    # Should pick the whole egg (143 kcal), not the white (52 kcal)
    assert result.energy_kcal >= 100
    assert result.fat_g >= 5


def test_validation_rejects_egg_whites():
    from morrisons_mcp.models import NutritionPer100g
    fake_whites = NutritionPer100g(energy_kcal=52, fat_g=0.2, protein_g=10.9)
    assert _validate_usda_result("eggs", fake_whites) is False


def test_validation_accepts_whole_eggs():
    from morrisons_mcp.models import NutritionPer100g
    fake_whole = NutritionPer100g(energy_kcal=143, fat_g=9.5, protein_g=12.6)
    assert _validate_usda_result("eggs", fake_whole) is True


def test_validation_rejects_salt_with_calories():
    from morrisons_mcp.models import NutritionPer100g
    fake_butter = NutritionPer100g(energy_kcal=717, fat_g=81.0, protein_g=0.9)
    assert _validate_usda_result("salt", fake_butter) is False


def test_validation_accepts_salt_zero_cal():
    from morrisons_mcp.models import NutritionPer100g
    real_salt = NutritionPer100g(energy_kcal=0, fat_g=0, protein_g=0)
    assert _validate_usda_result("salt", real_salt) is True


@pytest.mark.asyncio
async def test_usda_salt_rejects_butter_result():
    """USDA result for 'salt' returning butter (717 kcal, 81g fat) should be rejected."""
    butter_resp = _make_mock_response({
        "foods": [
            {
                "dataType": "SR Legacy",
                "description": "Butter, salted",
                "foodNutrients": [
                    {"nutrientName": "Energy", "value": 717, "unitName": "KCAL"},
                    {"nutrientName": "Protein", "value": 0.9, "unitName": "G"},
                    {"nutrientName": "Total lipid (fat)", "value": 81.0, "unitName": "G"},
                    {"nutrientName": "Sodium, Na", "value": 643, "unitName": "MG"},
                ],
            },
            {
                "dataType": "Foundation",
                "description": "Salt, table",
                "foodNutrients": [
                    {"nutrientName": "Energy", "value": 0, "unitName": "KCAL"},
                    {"nutrientName": "Protein", "value": 0, "unitName": "G"},
                    {"nutrientName": "Total lipid (fat)", "value": 0, "unitName": "G"},
                    {"nutrientName": "Sodium, Na", "value": 38758, "unitName": "MG"},
                ],
            },
        ]
    })
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = butter_resp

    result = await _search_usda_fdc("salt", client)
    assert result is not None
    assert result.energy_kcal == 0
    assert result.fat_g == 0


def test_validation_rejects_squash_seeds():
    """Butternut squash flesh is ~40 kcal, 0.1g fat.
    A result with 612 kcal / 49g fat (pumpkin seeds) must be rejected."""
    from morrisons_mcp.models import NutritionPer100g
    seeds = NutritionPer100g(energy_kcal=612, fat_g=49.0, protein_g=19.0)
    assert _validate_usda_result("butternut squash", seeds) is False


def test_validation_accepts_butternut_squash_flesh():
    """Butternut squash raw flesh (~40 kcal, 0.1g fat) must pass validation."""
    from morrisons_mcp.models import NutritionPer100g
    flesh = NutritionPer100g(energy_kcal=40, fat_g=0.1, protein_g=0.9)
    assert _validate_usda_result("butternut squash", flesh) is True


def test_validation_rejects_high_fat_aubergine():
    """Aubergine raw is ~25 kcal, 0.2g fat — a high-fat result is wrong food."""
    from morrisons_mcp.models import NutritionPer100g
    wrong = NutritionPer100g(energy_kcal=200, fat_g=15.0, protein_g=3.0)
    assert _validate_usda_result("aubergine", wrong) is False


def test_validation_accepts_aubergine():
    from morrisons_mcp.models import NutritionPer100g
    real = NutritionPer100g(energy_kcal=25, fat_g=0.2, protein_g=1.0)
    assert _validate_usda_result("aubergine", real) is True


@pytest.mark.asyncio
async def test_usda_rejects_squash_seeds_returns_correct_result():
    """USDA search for butternut squash: seed result (612 kcal) rejected,
    raw flesh result (~40 kcal) accepted."""
    resp = _make_mock_response({
        "foods": [
            {
                "dataType": "SR Legacy",
                "description": "Seeds, pumpkin and squash seed kernels, roasted",
                "foodNutrients": [
                    {"nutrientName": "Energy", "value": 612, "unitName": "KCAL"},
                    {"nutrientName": "Protein", "value": 19.0, "unitName": "G"},
                    {"nutrientName": "Total lipid (fat)", "value": 49.0, "unitName": "G"},
                    {"nutrientName": "Carbohydrate, by difference", "value": 14.7, "unitName": "G"},
                    {"nutrientName": "Sodium, Na", "value": 18, "unitName": "MG"},
                ],
            },
            {
                "dataType": "Foundation",
                "description": "Squash, winter, butternut, raw",
                "foodNutrients": [
                    {"nutrientName": "Energy", "value": 40, "unitName": "KCAL"},
                    {"nutrientName": "Protein", "value": 0.9, "unitName": "G"},
                    {"nutrientName": "Total lipid (fat)", "value": 0.1, "unitName": "G"},
                    {"nutrientName": "Carbohydrate, by difference", "value": 10.5, "unitName": "G"},
                    {"nutrientName": "Sodium, Na", "value": 4, "unitName": "MG"},
                ],
            },
        ]
    })
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = resp

    result = await _search_usda_fdc("butternut squash", client)
    assert result is not None
    assert result.energy_kcal < 60   # raw flesh, not seeds
    assert result.fat_g < 1.0
