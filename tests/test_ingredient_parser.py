import pytest
from morrisons_mcp.ingredient_parser import parse_ingredient


def test_simple_grams():
    result = parse_ingredient("500g chicken breast")
    assert result.quantity == 500
    assert result.unit == "g"
    assert "chicken breast" in result.name.lower()


def test_mealie_format_with_comma():
    result = parse_ingredient("100 g, Chicken Breast Fillet")
    assert result.quantity == 100
    assert result.unit == "g"
    assert "chicken breast fillet" in result.name.lower()


def test_tablespoon():
    result = parse_ingredient("1 tablespoon, Soy sauce")
    assert result.quantity == 1
    assert result.unit == "tablespoon"
    assert "soy sauce" in result.name.lower()


def test_tablespoons_plural_consumed():
    """Plural 's' should be consumed so it doesn't appear in the name."""
    result = parse_ingredient("2 tablespoons soy sauce")
    assert result.unit == "tablespoon"
    assert not result.name.lower().startswith("s ")
    assert "soy sauce" in result.name.lower()


def test_fractional():
    result = parse_ingredient("0.50 medium, Avocado")
    assert result.quantity == 0.5
    assert "avocado" in result.search_query.lower()


def test_mixed_number():
    """'1 1/2 cups flour' should parse to quantity=1.5."""
    result = parse_ingredient("1 1/2 cups flour")
    assert result.quantity == pytest.approx(1.5)
    assert result.unit == "cup"
    assert "flour" in result.name.lower()


def test_simple_fraction():
    """'1/2 tsp salt' should parse to quantity=0.5."""
    result = parse_ingredient("1/2 tsp salt")
    assert result.quantity == pytest.approx(0.5)
    assert result.unit == "tsp"


def test_no_quantity():
    result = parse_ingredient("salt and pepper to taste")
    assert result.quantity is None
    assert "salt" in result.search_query.lower()


def test_tin():
    result = parse_ingredient("1 tin coconut milk")
    assert result.quantity == 1
    assert result.unit == "tin"
    assert "coconut milk" in result.search_query.lower()


def test_kg_unit():
    result = parse_ingredient("1.5kg beef mince")
    assert result.quantity == 1.5
    assert result.unit == "kg"
    assert "beef mince" in result.name.lower()


def test_ml_unit():
    result = parse_ingredient("200ml double cream")
    assert result.quantity == 200
    assert result.unit == "ml"
    assert "double cream" in result.name.lower()


def test_cloves():
    result = parse_ingredient("2 cloves garlic")
    assert result.quantity == 2
    assert result.unit == "clove"  # canonical form, not plural
    assert "garlic" in result.search_query.lower()


def test_bunches():
    """'bunches' should resolve to canonical unit 'bunch'."""
    result = parse_ingredient("2 bunches coriander")
    assert result.unit == "bunch"
    assert "coriander" in result.name.lower()


def test_spray_residual_quantity_stripped():
    """'3 spray 0.2ml, Sunflower Oil Spray' — the '0.2ml' annotation must not
    appear in either the name or search_query."""
    result = parse_ingredient("3 spray 0.2ml, Sunflower Oil Spray")
    assert result.quantity == 3
    assert result.unit == "spray"
    # The residual '0.2ml' must be stripped from the name
    assert "0.2" not in result.name
    assert "0.2" not in result.search_query
    assert "sunflower oil" in result.search_query.lower()


def test_pot_container_stripped_from_search():
    """Container word 'pot' should be stripped from search_query but can remain in name."""
    result = parse_ingredient("130 g Pot, Sticky Rice Pot")
    assert "pot" not in result.search_query.lower()
    assert "sticky rice" in result.search_query.lower()


def test_to_taste_stripped_from_search():
    result = parse_ingredient("salt and pepper to taste")
    assert "to taste" not in result.search_query
