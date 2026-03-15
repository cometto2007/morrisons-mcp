import pytest
from morrisons_mcp.nutrition_parser import parse_nutrition_html

SAMPLE_HTML = """
<table>
<tr><td>Energy</td><td>1046kJ / 250kcal</td></tr>
<tr><td>Fat</td><td>3.0g</td></tr>
<tr><td>of which Saturates</td><td>0.7g</td></tr>
<tr><td>Carbohydrate</td><td>28.0g</td></tr>
<tr><td>of which Sugars</td><td>1.5g</td></tr>
<tr><td>Fibre</td><td>1.8g</td></tr>
<tr><td>Protein</td><td>27.0g</td></tr>
<tr><td>Salt</td><td>0.38g</td></tr>
</table>
"""

LESS_THAN_HTML = """
<table>
<tr><td>Energy</td><td>200kJ / 47kcal</td></tr>
<tr><td>Fat</td><td>less than 0.1g</td></tr>
<tr><td>Carbohydrate</td><td>11.5g</td></tr>
<tr><td>Protein</td><td>0.5g</td></tr>
<tr><td>Salt</td><td>&lt;0.1g</td></tr>
</table>
"""

ZERO_VALUES_HTML = """
<table>
<tr><td>Energy</td><td>0kJ / 0kcal</td></tr>
<tr><td>Fat</td><td>0.0g</td></tr>
<tr><td>Carbohydrate</td><td>0.0g</td></tr>
<tr><td>Protein</td><td>0.0g</td></tr>
<tr><td>Salt</td><td>0.0g</td></tr>
</table>
"""


def test_parse_full_table():
    result = parse_nutrition_html(SAMPLE_HTML)
    assert result is not None
    assert result.energy_kcal == 250
    assert result.energy_kj == 1046
    assert result.protein_g == 27.0
    assert result.fat_g == 3.0
    assert result.saturates_g == 0.7
    assert result.carbohydrate_g == 28.0
    assert result.sugars_g == 1.5
    assert result.fibre_g == 1.8
    assert result.salt_g == 0.38


def test_parse_empty_string():
    result = parse_nutrition_html("")
    assert result is None


def test_parse_none_input():
    result = parse_nutrition_html(None)
    assert result is None


def test_parse_less_than_text_value():
    """'less than 0.1g' → 0.05 (half the limit)."""
    result = parse_nutrition_html(LESS_THAN_HTML)
    assert result is not None
    assert result.energy_kcal == 47
    assert result.fat_g == pytest.approx(0.05)


def test_parse_less_than_html_entity():
    """'&lt;0.1g' (HTML entity) should also decode to the less-than pattern."""
    result = parse_nutrition_html(LESS_THAN_HTML)
    assert result is not None
    assert result.salt_g == pytest.approx(0.05)


def test_parse_zero_values():
    """Zero nutritional values should be returned as 0.0, not None."""
    result = parse_nutrition_html(ZERO_VALUES_HTML)
    assert result is not None
    assert result.energy_kcal == 0.0
    assert result.fat_g == 0.0
    assert result.protein_g == 0.0


def test_parse_no_table():
    result = parse_nutrition_html("<p>No nutrition data available</p>")
    assert result is None
