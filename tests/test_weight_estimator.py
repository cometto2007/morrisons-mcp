from morrisons_mcp.weight_estimator import estimate_weight_grams
from morrisons_mcp.models import ParsedIngredient, ProductResult


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
    assert 240 <= w <= 300  # 6 x 44g = 264g (UK medium egg without shell)


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


def test_sheet_lasagne_weight():
    """12 lasagne sheets × 25g/sheet = 300g."""
    w = estimate_weight_grams(_make("12 sheet lasagne pasta", 12, "sheet", "lasagne pasta"))
    assert w == 300


def test_aubergine_weight():
    """1 whole aubergine ≈ 300g."""
    w = estimate_weight_grams(_make("1 whole aubergine", 1, "whole", "aubergine"))
    assert w == 300


def test_stock_cube_weight():
    """2 stock cubes × 10g = 20g."""
    w = estimate_weight_grams(_make("2 stock cubes", 2, None, "stock cubes"))
    assert w == 20


def test_stock_pot_weight():
    """1 stock pot ≈ 26g."""
    w = estimate_weight_grams(_make("1 stock pot", 1, None, "stock pot"))
    assert w == 26


def test_courgette_weight():
    """1 whole courgette ≈ 200g."""
    w = estimate_weight_grams(_make("1 courgette", 1, None, "courgette"))
    assert w == 200


def test_zucchini_weight():
    """1 zucchini ≈ 200g (same as courgette)."""
    w = estimate_weight_grams(_make("1 zucchini", 1, None, "zucchini"))
    assert w == 200


def test_bell_pepper_weight():
    """1 bell pepper ≈ 160g."""
    w = estimate_weight_grams(_make("1 bell pepper", 1, None, "bell pepper"))
    assert w == 160


def _make_product(name: str, pack_size: str | None = None) -> ProductResult:
    return ProductResult(
        product_id="1", retailer_product_id="1",
        name=name, price=1.0, pack_size=pack_size,
    )


def test_can_uses_pack_size_drained_weight():
    """2 cans chickpeas × 240g drained (from pack_size) = 480g, not 800g gross."""
    ing = _make("2 can chickpeas", 2, "can", "chickpeas")
    product = _make_product("Morrisons Chickpeas In Water 400g", pack_size="240g")
    assert estimate_weight_grams(ing, matched_product=product) == 480.0


def test_can_falls_back_to_400g_without_pack_size():
    """When pack_size is absent, 1 can = 400g (existing default)."""
    ing = _make("1 can tomatoes", 1, "can", "tomatoes")
    assert estimate_weight_grams(ing, matched_product=None) == 400.0


def test_can_falls_back_when_pack_size_not_grams():
    """When pack_size is e.g. '6 pack', cannot parse grams → use 400g default."""
    ing = _make("1 can beans", 1, "can", "beans")
    product = _make_product("Morrisons Baked Beans 4 Pack", pack_size="4 pack")
    assert estimate_weight_grams(ing, matched_product=product) == 400.0


def test_tin_uses_pack_size_drained_weight():
    """tin unit also uses pack_size when available."""
    ing = _make("1 tin lentils", 1, "tin", "lentils")
    product = _make_product("Morrisons Green Lentils In Water 400g", pack_size="235g")
    assert estimate_weight_grams(ing, matched_product=product) == 235.0
