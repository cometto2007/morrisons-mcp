from morrisons_mcp.weight_estimator import estimate_weight_grams
from morrisons_mcp.models import ParsedIngredient


def _make(original, quantity, unit, name, query=None):
    return ParsedIngredient(
        original=original, quantity=quantity, unit=unit,
        name=name, search_query=query or name,
    )


def test_grams_passthrough():
    assert estimate_weight_grams(_make("600g pumpkin", 600, "g", "pumpkin")) == 600


def test_kg_conversion():
    assert estimate_weight_grams(_make("1.5kg beef", 1.5, "kg", "beef")) == 1500


def test_ml_passthrough():
    assert estimate_weight_grams(_make("200ml cream", 200, "ml", "cream")) == 200


def test_tbsp_oil_weight():
    w = estimate_weight_grams(_make("1 tbsp olive oil", 1, "tbsp", "olive oil"))
    assert w is not None
    assert 10 <= w <= 20  # ~13g for oil


def test_tsp_sugar_weight():
    w = estimate_weight_grams(_make("2 tsp sugar", 2, "tsp", "sugar"))
    assert w is not None
    assert 5 <= w <= 12  # 2 x ~4g


def test_tbsp_generic_weight():
    w = estimate_weight_grams(_make("1 tbsp water", 1, "tbsp", "water"))
    assert w == 15


def test_whole_eggs_weight():
    w = estimate_weight_grams(_make("6 eggs", 6, None, "eggs"))
    assert w is not None
    assert 300 <= w <= 400  # 6 x 60g = 360g


def test_pinch_salt():
    w = estimate_weight_grams(_make("1 pinch salt", 1, "pinch", "salt"))
    assert w is not None
    assert w < 1  # ~0.5g


def test_clove_garlic():
    w = estimate_weight_grams(_make("2 cloves garlic", 2, "clove", "garlic"))
    assert w is not None
    assert 8 <= w <= 12  # 2 x 5g


def test_tin_weight():
    w = estimate_weight_grams(_make("1 tin tomatoes", 1, "tin", "tomatoes"))
    assert w == 400


def test_medium_avocado():
    w = estimate_weight_grams(_make("1 medium avocado", 1, "medium", "avocado"))
    assert w is not None
    assert 100 <= w <= 200


def test_no_quantity_returns_none():
    assert estimate_weight_grams(_make("salt", None, None, "salt")) is None


def test_unknown_unit_no_match():
    assert estimate_weight_grams(_make("1 handful parsley", 1, "handful", "parsley")) is None


def test_tbsp_honey_weight():
    w = estimate_weight_grams(_make("1 tbsp honey", 1, "tbsp", "honey"))
    assert w == 21
