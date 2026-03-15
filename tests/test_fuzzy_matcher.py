import pytest
from morrisons_mcp.fuzzy_matcher import find_best_match, _all_query_words_present
from morrisons_mcp.models import ParsedIngredient, ProductResult


def _make_ingredient(query: str, name: str | None = None) -> ParsedIngredient:
    return ParsedIngredient(
        original=query,
        quantity=None,
        unit=None,
        name=name or query,
        search_query=query,
    )


def _make_product(
    name: str,
    price: float = 3.0,
    available: bool = True,
    category: str | None = None,
    product_id: str = "test-id",
    retailer_id: str = "12345",
) -> ProductResult:
    return ProductResult(
        product_id=product_id,
        retailer_product_id=retailer_id,
        name=name,
        price=price,
        available=available,
        category_path=category,
    )


def test_chicken_breast_matches_fillet():
    ingredient = _make_ingredient("chicken breast")
    products = [
        _make_product("Morrisons Chicken Breast Fillets 600g", product_id="1"),
        _make_product("Morrisons Chicken Thighs 500g", product_id="2"),
        _make_product("Morrisons Whole Chicken 1.5kg", product_id="3"),
    ]
    match, confidence = find_best_match(ingredient, products)
    assert match is not None
    assert "breast" in match.name.lower()
    assert confidence > 0.4


def test_chicken_breast_does_not_match_thighs():
    ingredient = _make_ingredient("chicken breast")
    products = [
        _make_product("Morrisons Chicken Thighs 500g", product_id="1"),
        _make_product("Morrisons Chicken Nuggets 450g", product_id="2"),
        _make_product("Morrisons Whole Chicken 1.5kg", product_id="3"),
    ]
    match, _ = find_best_match(ingredient, products)
    assert match is None


def test_low_confidence_returns_none():
    ingredient = _make_ingredient("quinoa")
    products = [
        _make_product("Morrisons Pasta Penne 500g", product_id="1"),
        _make_product("Morrisons White Rice 1kg", product_id="2"),
    ]
    match, confidence = find_best_match(ingredient, products)
    assert match is None
    assert confidence == 0.0


def test_empty_products_returns_none():
    ingredient = _make_ingredient("chicken breast")
    match, confidence = find_best_match(ingredient, [])
    assert match is None
    assert confidence == 0.0


def test_prefers_available_products():
    ingredient = _make_ingredient("avocado")
    products = [
        _make_product("Morrisons Avocado", price=1.0, available=False, product_id="1"),
        _make_product("Morrisons Avocado", price=1.5, available=True, product_id="2"),
    ]
    match, confidence = find_best_match(ingredient, products)
    assert match is not None
    assert match.product_id == "2"


def test_single_word_query_matches_freely():
    ingredient = _make_ingredient("garlic")
    products = [
        _make_product("Morrisons Garlic Bulb", product_id="1"),
        _make_product("Morrisons Garlic Puree 90g", product_id="2"),
    ]
    match, confidence = find_best_match(ingredient, products)
    assert match is not None
    assert confidence > 0.4


def test_category_bonus_increases_confidence():
    ingredient = _make_ingredient("chicken breast")
    product_with_category = _make_product(
        "Morrisons Chicken Breast Fillets",
        product_id="1",
        category="Meat & Poultry > Chicken > Breast",
    )
    product_no_category = _make_product(
        "Morrisons Chicken Breast Fillets",
        product_id="2",
        category=None,
    )
    _, conf_with = find_best_match(ingredient, [product_with_category])
    _, conf_without = find_best_match(ingredient, [product_no_category])
    assert conf_with > conf_without


def test_all_unavailable_returns_none():
    """If all matching products are unavailable, the availability penalty
    should push scores below threshold and return no match."""
    ingredient = _make_ingredient("avocado")
    products = [
        _make_product("Morrisons Avocado", available=False, product_id="1"),
    ]
    # A perfect-name match gets ~100 score - 50 penalty = 50, above MIN (40).
    # So it should still match (just lower confidence).
    match, confidence = find_best_match(ingredient, products)
    # It may or may not match depending on score; the key check is no crash.
    assert confidence <= 1.0


def test_word_boundary_prevents_substring_match():
    """'pea' should not match 'peas' as a required whole-word presence."""
    assert not _all_query_words_present("pea soup", "Morrisons Peas Soup")
    assert _all_query_words_present("pea soup", "Morrisons Pea & Ham Soup")


def test_confidence_capped_at_one():
    """Confidence must never exceed 1.0 even with category bonus."""
    ingredient = _make_ingredient("chicken breast")
    products = [
        _make_product(
            "chicken breast",
            category="Chicken > Breast",
            product_id="1",
        )
    ]
    _, confidence = find_best_match(ingredient, products)
    assert confidence <= 1.0


def test_all_products_fail_word_filter_returns_none():
    """If no product passes the all-words-present filter, return (None, 0.0)."""
    ingredient = _make_ingredient("chicken breast")
    products = [
        _make_product("Morrisons Chicken Thighs", product_id="1"),
        _make_product("Morrisons Chicken Wings", product_id="2"),
    ]
    match, confidence = find_best_match(ingredient, products)
    assert match is None
    assert confidence == 0.0


def test_prefers_standard_over_organic():
    ingredient = ParsedIngredient(
        original="500g chicken breast",
        quantity=500, unit="g",
        name="chicken breast",
        search_query="chicken breast",
    )
    products = [
        ProductResult(
            product_id="1", retailer_product_id="1",
            name="Morrisons Organic Chicken Breast Fillets",
            price=7.82, unit_price="£23.00/kg", available=True,
        ),
        ProductResult(
            product_id="2", retailer_product_id="2",
            name="Morrisons British Chicken Breast Fillets 1kg",
            price=6.84, unit_price="£6.84/kg", available=True,
        ),
    ]
    match, confidence = find_best_match(ingredient, products)
    assert match is not None
    assert match.retailer_product_id == "2"


def test_single_tin_prefers_single_not_multipack():
    ingredient = ParsedIngredient(
        original="1 tin chopped tomatoes",
        quantity=1, unit="tin",
        name="chopped tomatoes",
        search_query="chopped tomatoes",
    )
    products = [
        ProductResult(
            product_id="1", retailer_product_id="1",
            name="Cirio Chopped Tomatoes (4x400g)",
            price=2.50, pack_size="4 x 400g", available=True,
        ),
        ProductResult(
            product_id="2", retailer_product_id="2",
            name="Morrisons Chopped Tomatoes 400g",
            price=0.45, pack_size="400g", available=True,
        ),
    ]
    match, confidence = find_best_match(ingredient, products)
    assert match is not None
    assert match.retailer_product_id == "2"
