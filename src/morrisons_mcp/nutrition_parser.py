import logging
import re

from bs4 import BeautifulSoup

from .models import NetQuantity, NutritionPer100g

logger = logging.getLogger(__name__)

# A column header naming a per-100 g / per-100 ml basis, e.g. "per 100g",
# "Per: 100 ml", "(as consumed) per 100g", "100g". Not "1000g" or "2100g".
_PER_100_RE = re.compile(
    r"(?<![\d.])100\s*(g|grams?|ml|millilitres?|milliliters?)\b", re.IGNORECASE
)


def _extract_float(text: str) -> float | None:
    """Extract a float from a string like '10.5g', '1234kJ', 'less than 0.1g', 'nil'."""
    text = text.strip()

    # "nil" / "trace" are how UK labels write zero or negligible amounts
    if re.fullmatch(r"(nil|trace|traces?)\s*g?", text, re.IGNORECASE):
        return 0.0

    # Handle "less than X" or "< X" → use half the value as an approximation
    less_than = re.match(r"(?:less\s+than|<)\s*([\d.]+)", text, re.IGNORECASE)
    if less_than:
        try:
            return float(less_than.group(1)) / 2
        except ValueError:
            return None

    m = re.search(r"([\d.]+)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _per_100_column(cells: list[str]) -> tuple[int, str] | None:
    """Return (column index, basis) of the first per-100 g/ml header cell, if any."""
    for idx, text in enumerate(cells[1:], start=1):
        m = _PER_100_RE.search(text)
        if m:
            return idx, "100ml" if m.group(1).lower().startswith("m") else "100g"
    # "Typical values per 100g | <value>": the basis is in the label column
    if len(cells) == 2:
        m = _PER_100_RE.search(cells[0])
        if m:
            return 1, "100ml" if m.group(1).lower().startswith("m") else "100g"
    return None


def parse_nutrition_html(html: str | None) -> NutritionPer100g | None:
    """Parse a Morrisons BOP nutrition table into per-100 g/ml values.

    Only the column whose header says per 100 g (or per 100 ml) is read. A
    table without such a column (per-serving only, or no header at all)
    returns None rather than figures on an unknown basis.
    """
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.find_all("tr")

        col: int | None = None
        basis = "100g"
        result: dict[str, float | None] = {}
        prev_label = ""

        for row in rows:
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 2:
                continue

            if col is None:
                found = _per_100_column(cells)
                if found:
                    col, basis = found
                continue

            if col >= len(cells):
                continue

            label = cells[0].lower()
            value_text = re.sub(r"(\d),(\d{3})\b", r"\1\2", cells[col])

            # Energy is often split over two rows, the second with an empty
            # label: "Energy | 195 kJ" then " | 46 kcal".
            if not label and prev_label == "energy":
                label = "energy"

            if "energy" in label or label in ("kj", "kcal"):
                kj_match = re.search(r"([\d.]+)\s*kj", value_text, re.IGNORECASE)
                kcal_match = re.search(r"([\d.]+)\s*kcal", value_text, re.IGNORECASE)
                if kj_match:
                    result["energy_kj"] = float(kj_match.group(1))
                if kcal_match:
                    result["energy_kcal"] = float(kcal_match.group(1))

                # Unit in the label instead of the value: "Energy kJ | 1456"
                if not kj_match and not kcal_match:
                    plain_val = _extract_float(value_text)
                    if plain_val is not None:
                        if "kj" in label:
                            result["energy_kj"] = plain_val
                        elif "kcal" in label:
                            result["energy_kcal"] = plain_val
                prev_label = "energy"
                continue

            prev_label = label

            if label == "fat" or label.startswith("fat "):
                result["fat_g"] = _extract_float(value_text)

            elif "saturate" in label and "unsaturate" not in label:
                result["saturates_g"] = _extract_float(value_text)

            elif label.startswith("carbohydrate"):
                result["carbohydrate_g"] = _extract_float(value_text)

            elif "sugar" in label:
                result["sugars_g"] = _extract_float(value_text)

            elif "fibre" in label or "fiber" in label:
                result["fibre_g"] = _extract_float(value_text)

            elif label == "protein" or label.startswith("protein "):
                result["protein_g"] = _extract_float(value_text)

            elif label == "salt" or label.startswith("salt "):
                result["salt_g"] = _extract_float(value_text)

        if col is None:
            logger.debug("Nutrition table has no per-100g/ml column; ignoring it")
            return None

        if not any(v is not None for v in result.values()):
            logger.debug("Nutrition table parsed but no recognised nutrient rows found")
            return None

        # Fallback: derive kcal from kJ if only kJ was found
        if result.get("energy_kcal") is None and result.get("energy_kj") is not None:
            result["energy_kcal"] = round(result["energy_kj"] / 4.184, 1)

        return NutritionPer100g(basis=basis, **result)

    except Exception as e:
        logger.error(f"Failed to parse nutrition HTML: {e}")
        return None


_NET_QTY_RE = re.compile(
    r"(?:(\d+)\s*[x×]\s*)?(\d+(?:\.\d+)?)\s*(kg|g|ml|cl|l|ltr|litres?|liters?)\b",
    re.IGNORECASE,
)
_TO_BASE = {"kg": (1000, "g"), "g": (1, "g"), "ml": (1, "ml"), "cl": (10, "ml")}


def parse_net_quantity(pack_size: str | None) -> NetQuantity | None:
    """Parse a pack size like '400g', '1kg', '500ml' or '6 x 330ml' into g/ml.

    Multipacks are totalled. Returns None when no weight or volume is found
    (e.g. 'Each', '6 pack').
    """
    if not pack_size:
        return None
    m = _NET_QTY_RE.search(pack_size)
    if not m:
        return None
    count = int(m.group(1)) if m.group(1) else 1
    unit = m.group(3).lower()
    factor, base = _TO_BASE.get(unit, (1000, "ml"))  # l / ltr / litre(s)
    return NetQuantity(value=round(count * float(m.group(2)) * factor, 3), unit=base)
