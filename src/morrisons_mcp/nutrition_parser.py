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


# Header words that mean the column is not plain per-100 of the product as sold,
# e.g. "Per 30g with 100ml milk", "Per 100g as prepared", "Per portion".
_NOT_PER_100_RE = re.compile(r"serving|\bwith\b|portion|pack|as prepared", re.IGNORECASE)
# Wording of a row-label column header ("Typical values", "Nutrition")
_LABEL_HEADER_RE = re.compile(r"typical|values?\b|nutrition", re.IGNORECASE)


def _per_100_basis(text: str) -> str | None:
    """Return "100g"/"100ml" if this header cell names a plain per-100 column."""
    m = _PER_100_RE.search(text)
    if not m or _NOT_PER_100_RE.search(text):
        return None
    return "100ml" if m.group(1).lower().startswith("m") else "100g"


# Qualifiers a per-100 header can carry, e.g. "(as consumed) per 100g" or
# "Per 100g (grilled)", mapped to the `basis_note` returned. First match wins.
_QUALIFIERS = [
    (re.compile(r"as consumed", re.IGNORECASE), "as consumed"),
    (re.compile(r"as sold", re.IGNORECASE), "as sold"),
    (re.compile(r"\bprepared\b|made up", re.IGNORECASE), "prepared"),
    (re.compile(r"uncooked|\braw\b", re.IGNORECASE), "raw"),
    (re.compile(r"\bcooked\b", re.IGNORECASE), "cooked"),
    (re.compile(r"drained", re.IGNORECASE), "drained"),
]
# A cooking method in brackets, e.g. "Per 100g (grilled)", "(oven baked)"
_COOKING_METHOD_RE = re.compile(
    r"\(\s*((?:oven[ -])?(?:grilled|roasted|baked|fried|boiled|steamed|"
    r"microwaved|poached|barbecued))\s*\)",
    re.IGNORECASE,
)


def _basis_note(text: str) -> str | None:
    """Return the per-100 header's qualifier ("as consumed", "grilled", ...) or None."""
    for pattern, note in _QUALIFIERS:
        if pattern.search(text):
            return note
    m = _COOKING_METHOD_RE.search(text)
    return m.group(1).lower() if m else None


def _rows(table) -> list[list[str]]:
    return [
        [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        for tr in table.find_all("tr")
    ]


def _find_per_100_column(rows: list[list[str]]) -> tuple[int, int, str] | None:
    """Locate the per-100 column in a table.

    Returns (header row index, data column index, header cell text), or None
    when the table has no unambiguous per-100 g/ml column.
    """
    for h, header in enumerate(rows):
        if not any(_PER_100_RE.search(c) for c in header):
            continue
        widths = [len(r) for r in rows[h + 1:] if len(r) >= 2]
        if not widths:
            return None
        data_width = max(set(widths), key=widths.count)
        # A header without a label cell sits one cell short of the data rows
        offset = data_width - len(header)
        if offset not in (0, 1):
            return None

        for i, text in enumerate(header):
            if not _per_100_basis(text):
                continue
            if offset == 1:
                # "Typical values per 100g" one cell short could be a label
                # header or a shifted value header: too ambiguous to trust.
                if i == 0 and _LABEL_HEADER_RE.search(text):
                    return None
                return h, i + 1, text
            if i >= 1:
                return h, i, text
            # "Typical values per 100g | <blank>": the basis is in the label
            # cell. Only trust it when there is a single value column whose
            # own header names no other basis.
            if data_width == 2 and not re.search(r"\d|per", header[1], re.IGNORECASE):
                return h, 1, text
        return None
    return None


def parse_nutrition_html(html: str | None) -> NutritionPer100g | None:
    """Parse a Morrisons BOP nutrition table into per-100 g/ml values.

    Only the column whose header says per 100 g (or per 100 ml) is read, from
    the first table that has one; other tables (per serving, as prepared) are
    ignored. A label without such a column returns None rather than figures
    on an unknown basis.
    """
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table") or [soup]

        rows: list[list[str]] = []
        found = None
        for table in tables:
            rows = _rows(table)
            found = _find_per_100_column(rows)
            if found:
                break
        if not found:
            logger.debug("Nutrition label has no per-100g/ml column; ignoring it")
            return None
        header_idx, col, header_text = found

        result: dict[str, float | None] = {}
        sodium_g: float | None = None
        prev_label = ""

        for cells in rows[header_idx + 1:]:
            if len(cells) < 2 or col >= len(cells):
                continue

            label = cells[0].lower()
            value_text = re.sub(r"(\d),(\d{3})(?=\D|$)", r"\1\2", cells[col])

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

            elif label.startswith("sodium"):
                sodium_g = _extract_float(value_text)
                if sodium_g is not None and "mg" in f"{label} {value_text}".lower():
                    sodium_g /= 1000

        # Sodium-only labels: salt = sodium x 2.5
        if result.get("salt_g") is None and sodium_g is not None:
            result["salt_g"] = round(sodium_g * 2.5, 3)

        if not any(v is not None for v in result.values()):
            logger.debug("Nutrition table parsed but no recognised nutrient rows found")
            return None

        # Fallback: derive kcal from kJ if only kJ was found
        if result.get("energy_kcal") is None and result.get("energy_kj") is not None:
            result["energy_kcal"] = round(result["energy_kj"] / 4.184, 1)

        return NutritionPer100g(
            basis=_per_100_basis(header_text),
            basis_note=_basis_note(header_text),
            **result,
        )

    except Exception as e:
        logger.error(f"Failed to parse nutrition HTML: {e}")
        return None


# One weight/volume, not preceded by a digit, "." or "/" (so "1/2 kg" is skipped)
_QTY_RE = re.compile(
    r"(?<![\d./])(\d+(?:\.\d+)?)\s*(kg|g|ml|cl|l|ltr|litres?|liters?)\b", re.IGNORECASE
)
# A multipack count next to its multiplier: "6 x", "x 6", "4pk", "(6 pack)"
_COUNT_RE = re.compile(
    r"(\d+)\s*[x×]|[x×]\s*(\d+)|(\d+)\s*(?:pk|packs?)\b", re.IGNORECASE
)
_TO_BASE = {"kg": (1000, "g"), "g": (1, "g"), "ml": (1, "ml"), "cl": (10, "ml")}


def parse_net_quantity(pack_size: str | None) -> NetQuantity | None:
    """Parse a pack size like '400g', '1kg', '6 x 330ml' or '330ml (6pk)' into g/ml.

    Multipacks are totalled. Returns None when there is no single weight or
    volume, or when a number is left over that isn't a multipack count
    (never a single unit of a multipack).
    """
    if not pack_size:
        return None
    qtys = list(_QTY_RE.finditer(pack_size))
    if len(qtys) != 1:
        return None
    m = qtys[0]
    rest = pack_size[: m.start()] + " " + pack_size[m.end():]

    count = 1
    counts = list(_COUNT_RE.finditer(rest))
    if len(counts) > 1:
        return None
    if counts:
        c = counts[0]
        count = int(next(g for g in c.groups() if g))
        rest = rest[: c.start()] + rest[c.end():]
    if re.search(r"\d", rest):
        return None  # an unexplained number, e.g. "1/2 kg" or "2 x 4 x 125g"

    unit = m.group(2).lower()
    factor, base = _TO_BASE.get(unit, (1000, "ml"))  # l / ltr / litre(s)
    return NetQuantity(value=round(count * float(m.group(1)) * factor, 3), unit=base)
