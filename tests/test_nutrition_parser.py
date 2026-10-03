import pytest
from morrisons_mcp.nutrition_parser import parse_nutrition_html

SAMPLE_HTML = """
<table><tr><th>Typical Values</th><th>Per 100g</th></tr>
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
<table><tr><th>Typical Values</th><th>Per 100g</th></tr>
<tr><td>Energy</td><td>200kJ / 47kcal</td></tr>
<tr><td>Fat</td><td>less than 0.1g</td></tr>
<tr><td>Carbohydrate</td><td>11.5g</td></tr>
<tr><td>Protein</td><td>0.5g</td></tr>
<tr><td>Salt</td><td>&lt;0.1g</td></tr>
</table>
"""

ZERO_VALUES_HTML = """
<table><tr><th>Typical Values</th><th>Per 100g</th></tr>
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


def test_energy_kj_only():
    html = """<table><tr><th>Typical Values</th><th>Per 100g</th></tr><tr><td>Energy</td><td>1456kJ</td></tr></table>"""
    result = parse_nutrition_html(html)
    assert result is not None
    assert result.energy_kj == 1456.0
    assert result.energy_kcal is not None
    assert abs(result.energy_kcal - 348.0) < 2  # 1456 / 4.184 ≈ 348


def test_energy_combined_format():
    html = """<table><tr><th>Typical Values</th><th>Per 100g</th></tr><tr><td>Energy</td><td>1046kJ / 250kcal</td></tr></table>"""
    result = parse_nutrition_html(html)
    assert result.energy_kj == 1046.0
    assert result.energy_kcal == 250.0


def test_energy_with_spaces():
    html = """<table><tr><th>Typical Values</th><th>Per 100g</th></tr><tr><td>Energy</td><td>1046 kJ / 250 kcal</td></tr></table>"""
    result = parse_nutrition_html(html)
    assert result.energy_kj == 1046.0
    assert result.energy_kcal == 250.0


def test_energy_unit_in_label_kj():
    """When kJ is in the label and the value is just a number."""
    html = """<table><tr><th>Typical Values</th><th>Per 100g</th></tr>
    <tr><td>Energy kJ</td><td>1456</td></tr>
    <tr><td>Energy kcal</td><td>348</td></tr>
    </table>"""
    result = parse_nutrition_html(html)
    assert result is not None
    assert result.energy_kj == 1456.0
    assert result.energy_kcal == 348.0


def test_energy_unit_in_label_kj_only():
    """When only kJ label row exists, kcal should be derived."""
    html = """<table><tr><th>Typical Values</th><th>Per 100g</th></tr><tr><td>Energy kJ</td><td>1456</td></tr></table>"""
    result = parse_nutrition_html(html)
    assert result is not None
    assert result.energy_kj == 1456.0
    assert result.energy_kcal is not None
    assert abs(result.energy_kcal - 348.0) < 2


# --- Column selection: only the per-100g / per-100ml column is trusted ---

# Real Morrisons shape (whole chicken, retailerProductId 108444543)
PER_100G_LABEL = (
    '<table class="nutrition"><tbody><tr><th>Typical Values</th>'
    '<th>(as consumed) per 100g</th><th>%RI</th><th>your RI*</th></tr>'
    '<tr><td>Energy</td><td>742kJ/177kcal</td><td>9%</td><td>8400kJ/2000kcal</td></tr>'
    '<tr><td>Fat</td><td>7.5g</td><td>11%</td><td>70g</td></tr>'
    '<tr><td>of which saturates</td><td>2.1g</td><td>11%</td><td>20g</td></tr>'
    '<tr><td>Carbohydrate</td><td>nil</td><td></td><td>90g</td></tr>'
    '<tr><td>of which sugars</td><td>nil</td><td>0%</td><td>90g</td></tr>'
    '<tr><td>Fibre</td><td>nil</td><td></td><td></td></tr>'
    '<tr><td>Protein</td><td>27.3g</td><td></td><td></td></tr>'
    '<tr><td>Salt</td><td>0.2g</td><td>3%</td><td>6g</td></tr>'
    '<tr><td>*Reference intake of an average adult (8400kJ/2000kcal) (RI)</td>'
    '<td></td><td></td><td></td></tr></tbody></table>'
)

# Real Morrisons shape (Coca-Cola Cherry 500ml): kcal on an unlabelled row
PER_100ML_LABEL = (
    '<table class="nutrition"><tbody><tr><th>Typical Values</th><th>Per: 100 ml</th>'
    '<th>Per: 250 ml</th><th>(%*)</th></tr>'
    '<tr><td>Energy</td><td>195 kJ</td><td>488 kJ</td><td>(6%)</td></tr>'
    '<tr><td></td><td>46 kcal</td><td>115 kcal</td><td>(6%)</td></tr>'
    '<tr><td>Fat</td><td>0 g</td><td>0 g</td><td>(0%)</td></tr>'
    '<tr><td>of which saturates</td><td>0 g</td><td>0 g</td><td>(0%)</td></tr>'
    '<tr><td>Carbohydrate</td><td>11.4 g</td><td>29 g</td><td>(11%)</td></tr>'
    '<tr><td>of which sugars</td><td>11.4 g</td><td>29 g</td><td>(32%)</td></tr>'
    '<tr><td>Protein</td><td>0 g</td><td>0 g</td><td>(0%)</td></tr>'
    '<tr><td>Salt</td><td>0.01 g</td><td>0.03 g</td><td>(0%)</td></tr></tbody></table>'
)

SERVING_FIRST_LABEL = """
<table>
<tr><th>Typical Values</th><th>Per 30g serving</th><th>Per 100g</th></tr>
<tr><td>Energy</td><td>480kJ / 114kcal</td><td>1600kJ / 380kcal</td></tr>
<tr><td>Fat</td><td>0.6g</td><td>2.0g</td></tr>
<tr><td>of which saturates</td><td>0.1g</td><td>0.4g</td></tr>
<tr><td>of which mono-unsaturates</td><td>0.3g</td><td>1.0g</td></tr>
<tr><td>Carbohydrate</td><td>24.0g</td><td>80.0g</td></tr>
<tr><td>of which sugars</td><td>3.0g</td><td>10.0g</td></tr>
<tr><td>Fibre</td><td>1.5g</td><td>5.0g</td></tr>
<tr><td>Protein</td><td>2.4g</td><td>8.0g</td></tr>
<tr><td>Salt</td><td>&lt;0.01g</td><td>0.03g</td></tr>
</table>
"""

SERVING_ONLY_LABEL = """
<table>
<tr><th>Typical Values</th><th>Per bar (40g)</th><th>%RI</th></tr>
<tr><td>Energy</td><td>800kJ / 190kcal</td><td>10%</td></tr>
<tr><td>Protein</td><td>10g</td><td></td></tr>
</table>
"""


def test_per_100g_label():
    n = parse_nutrition_html(PER_100G_LABEL)
    assert n is not None
    assert n.basis == "100g"
    assert n.energy_kj == 742 and n.energy_kcal == 177
    assert n.fat_g == 7.5
    assert n.saturates_g == 2.1
    assert n.carbohydrate_g == 0.0  # "nil"
    assert n.sugars_g == 0.0
    assert n.fibre_g == 0.0
    assert n.protein_g == 27.3
    assert n.salt_g == 0.2


def test_per_100ml_drink_label():
    n = parse_nutrition_html(PER_100ML_LABEL)
    assert n is not None
    assert n.basis == "100ml"
    assert n.energy_kj == 195
    assert n.energy_kcal == 46  # from the unlabelled second energy row
    assert n.carbohydrate_g == 11.4
    assert n.sugars_g == 11.4
    assert n.salt_g == 0.01
    assert n.fat_g == 0.0


def test_first_value_column_per_serving_is_skipped():
    n = parse_nutrition_html(SERVING_FIRST_LABEL)
    assert n is not None
    assert n.basis == "100g"
    assert n.energy_kcal == 380
    assert n.fat_g == 2.0
    assert n.saturates_g == 0.4  # not overwritten by mono-unsaturates
    assert n.carbohydrate_g == 80.0
    assert n.sugars_g == 10.0
    assert n.fibre_g == 5.0
    assert n.protein_g == 8.0
    assert n.salt_g == 0.03


def test_per_serving_only_label_returns_none():
    assert parse_nutrition_html(SERVING_ONLY_LABEL) is None


def test_headerless_table_returns_none():
    html = "<table><tr><td>Energy</td><td>1046kJ / 250kcal</td></tr></table>"
    assert parse_nutrition_html(html) is None


def test_basis_in_label_column():
    html = """<table>
    <tr><th>Typical values per 100ml</th><th></th></tr>
    <tr><td>Energy</td><td>264kJ / 63kcal</td></tr>
    <tr><td>Protein</td><td>3.4g</td></tr>
    </table>"""
    n = parse_nutrition_html(html)
    assert n is not None and n.basis == "100ml"
    assert n.energy_kcal == 63 and n.protein_g == 3.4


def test_per_1000g_is_not_per_100g():
    html = """<table>
    <tr><th>Typical Values</th><th>Per 1000g</th></tr>
    <tr><td>Energy</td><td>1046kJ / 250kcal</td></tr>
    </table>"""
    assert parse_nutrition_html(html) is None


# --- Net quantity ---

from morrisons_mcp.nutrition_parser import parse_net_quantity  # noqa: E402


@pytest.mark.parametrize(
    "pack_size, value, unit",
    [
        ("400g", 400, "g"),
        ("1kg", 1000, "g"),
        ("1.45kg", 1450, "g"),
        ("500ml", 500, "ml"),
        ("6 x 330ml", 1980, "ml"),
        ("4 x 125g", 500, "g"),
        ("2L", 2000, "ml"),
        ("75cl", 750, "ml"),
    ],
)
def test_net_quantity(pack_size, value, unit):
    q = parse_net_quantity(pack_size)
    assert q is not None
    assert q.value == value
    assert q.unit == unit


@pytest.mark.parametrize("pack_size", [None, "", "Each", "6 pack", "Per kg"])
def test_net_quantity_unparseable(pack_size):
    assert parse_net_quantity(pack_size) is None
