import pytest
from morrisons_mcp.models import (
    ParsedIngredient,
    ProductResult,
    NutritionPer100g,
    ProductDetail,
    IngredientCost,
    RecipeCostResult,
    IngredientNutrition,
    RecipeNutritionResult,
    Promotion,
)


def test_product_result_defaults():
    p = ProductResult(
        product_id="abc-123",
        retailer_product_id="12345",
        name="Test Product",
        price=2.50,
    )
    assert p.available is True
    assert p.promotions == []
    assert p.brand is None


def test_nutrition_per_100g_all_optional():
    n = NutritionPer100g()
    assert n.energy_kcal is None
    assert n.protein_g is None


def test_nutrition_per_100g_zero_values():
    """Zero nutritional values must be stored as 0.0, not treated as None."""
    n = NutritionPer100g(energy_kcal=0.0, protein_g=0.0, fat_g=0.0)
    assert n.energy_kcal == 0.0
    assert n.protein_g == 0.0
    assert n.fat_g == 0.0


def test_recipe_cost_result_unmatched_count():
    r = RecipeCostResult(
        ingredients=[],
        total_cost=0.0,
        unmatched_count=2,
    )
    assert r.unmatched_count == 2
    assert r.cost_per_serving is None


def test_parsed_ingredient_round_trip():
    p = ParsedIngredient(
        original="500g chicken breast",
        quantity=500.0,
        unit="g",
        name="chicken breast",
        search_query="chicken breast",
    )
    dumped = p.model_dump()
    restored = ParsedIngredient.model_validate(dumped)
    assert restored.quantity == 500.0
    assert restored.unit == "g"


def test_product_detail_with_nutrition():
    detail = ProductDetail(
        retailer_product_id="12345",
        name="Chicken Breast Fillets",
        nutrition_per_100g=NutritionPer100g(
            energy_kcal=165.0,
            protein_g=31.0,
            fat_g=3.6,
        ),
    )
    assert detail.nutrition_per_100g is not None
    assert detail.nutrition_per_100g.protein_g == 31.0


def test_promotion_model():
    promo = Promotion(description="3 for £5", promo_price=5.0)
    assert promo.description == "3 for £5"
    assert promo.promo_price == 5.0
    assert promo.expiry is None


def test_recipe_nutrition_result_zero_totals():
    """Zero totals should be returned as 0.0 (not None) when data is present."""
    result = RecipeNutritionResult(
        ingredients=[],
        total_kcal=0.0,
        total_protein_g=0.0,
        total_fat_g=0.0,
        total_carbs_g=0.0,
    )
    assert result.total_kcal == 0.0
    assert result.total_protein_g == 0.0


def test_recipe_nutrition_result_per_serving():
    result = RecipeNutritionResult(
        ingredients=[],
        servings=4.0,
        total_kcal=800.0,
        per_serving_kcal=200.0,
    )
    assert result.per_serving_kcal == 200.0


def test_ingredient_nutrition_all_optional():
    n = IngredientNutrition(ingredient="500g chicken breast")
    assert n.estimated_kcal is None
    assert n.matched_product is None
    assert n.estimated_weight_g is None
