import re
import logging

from .models import ParsedIngredient

logger = logging.getLogger(__name__)

# Known units in singular canonical form (longer forms first to prevent partial
# matches). Plurals are handled by the `s?` suffix in _extract_unit — the
# canonical singular form is always what the parser returns as the unit.
KNOWN_UNITS = [
    "tablespoon", "teaspoon", "tbsp", "tsp",
    "litre", "liter", "ml", "kg", "lb", "oz",
    "clove", "bunch", "pinch", "spray", "dash",
    "medium", "large", "small", "whole",
    "piece", "slice",
    "cup", "pot", "tin", "can",
    "g", "l",
]

# Phrases to strip from search queries (but keep in name)
SEARCH_STRIP_PHRASES = [
    "to taste", "for serving", "for garnish", "optional",
    "approximately", "about",
    "good quality", "finely chopped", "roughly chopped",
]

# Words to strip from search queries individually
SEARCH_STRIP_WORDS = {
    "fresh", "dried", "raw", "plain",
    "chopped", "diced", "sliced", "minced", "crushed", "grated",
    "peeled", "deseeded", "trimmed", "boneless", "skinless",
}

# Words to strip only if removing them leaves at least one other word
_CONDITIONAL_STRIP_WORDS = {"whole", "finely", "roughly"}

# Container words to remove from search queries
CONTAINER_WORDS = {"pot", "tin", "can", "jar", "bag", "pack", "packet", "spray",
                   "bottle", "bunch", "box", "tub"}

# Container words that appear between unit and comma in Mealie format
# e.g. "130 g Pot, Sticky Rice Pot" — "Pot" here is a container descriptor
_CONTAINER_DESCRIPTORS_RE = re.compile(
    r"^(pot|pack|tin|can|bag|bottle|jar|bunch|box|tub|packet)\b\s*",
    re.IGNORECASE,
)

# Simple pantry staples — when the query resolves to one of these,
# keep only the first significant word.
PANTRY_STAPLES = {"salt and pepper", "salt", "pepper", "water", "oil"}

# Articles and filler words to strip from the ingredient name
_STRIP_ARTICLES_RE = re.compile(r"\b(a|an|the|of|some|few)\b", re.IGNORECASE)

# Residual quantity+unit pattern left over after primary unit extraction
# e.g. "0.2ml" in "3 spray 0.2ml, Sunflower Oil Spray"
_RESIDUAL_QTY_RE = re.compile(r"^\d+\.?\d*\s*[a-zA-Z]+\s*")


def _extract_quantity(text: str) -> tuple[float | None, int]:
    """
    Extract leading quantity from text.
    Returns (quantity, chars_consumed).
    Handles: integers, floats, simple fractions (1/2), mixed numbers (1 1/2).
    """
    # Try mixed number first: "1 1/2" → 1.5
    m = re.match(r"^(\d+)\s+(\d+)/(\d+)", text)
    if m:
        whole = float(m.group(1))
        num, den = float(m.group(2)), float(m.group(3))
        return whole + (num / den if den else 0), m.end()

    # Try simple fraction: "1/2" → 0.5
    m = re.match(r"^(\d+)/(\d+)", text)
    if m:
        num, den = float(m.group(1)), float(m.group(2))
        return num / den if den else None, m.end()

    # Try decimal or integer: "0.50", "500"
    m = re.match(r"^(\d+(?:\.\d+)?)", text)
    if m:
        return float(m.group(1)), m.end()

    return None, 0


def _extract_unit(text: str) -> tuple[str | None, int]:
    """
    Try to match a known unit at the start of text.
    Consumes any trailing plural 's' on units that don't already have it.
    Returns (canonical_unit, chars_consumed).
    """
    text_lower = text.lower()
    for unit in KNOWN_UNITS:
        # Match the unit then optionally consume a plural suffix:
        # "es" (bunches, pinches) or "s" (cloves, tablespoons, cups).
        # The lookahead ensures we don't match a unit that's a prefix of a
        # longer word (e.g. "g" shouldn't match "grams").
        pattern = r"^" + re.escape(unit) + r"(?:es|s)?(?=\s|,|$)"
        m = re.match(pattern, text_lower)
        if m:
            return unit, m.end()
    return None, 0


def _clean_name(name: str) -> str:
    """Strip leading commas, articles, extra whitespace from ingredient name."""
    name = name.strip().lstrip(",").strip()
    name = _STRIP_ARTICLES_RE.sub("", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _build_search_query(name: str) -> str:
    """Build a cleaned search query from ingredient name."""
    query = name.lower()

    # Strip non-searchable phrases
    for phrase in SEARCH_STRIP_PHRASES:
        query = query.replace(phrase, "")

    # Strip punctuation from each word, then filter modifier and container words
    words = query.split()
    words = [re.sub(r"[^a-z]", "", w) for w in words]  # strip commas, etc.
    words = [w for w in words if w and w not in SEARCH_STRIP_WORDS and w not in CONTAINER_WORDS]

    # Conditionally strip words like "whole" only if other words remain
    remaining = [w for w in words if w not in _CONDITIONAL_STRIP_WORDS]
    if remaining:
        words = remaining

    query = " ".join(words).strip()

    # Detect pantry staples and reduce to first significant word.
    # Use exact-match to avoid "oil" matching "sunflower oil".
    for staple in PANTRY_STAPLES:
        if query == staple:
            query = staple.split()[0]  # e.g. "salt and pepper" → "salt"
            break

    query = re.sub(r"\s+", " ", query).strip()
    return query


def parse_ingredient(raw: str) -> ParsedIngredient:
    """
    Parse a raw ingredient string into structured data.

    Handles formats like:
    - "500g chicken breast"
    - "100 g, Chicken Breast Fillet"
    - "1 tablespoon, Soy sauce"
    - "0.50 medium, Avocado"
    - "1 1/2 cups flour"
    - "3 spray 0.2ml, Sunflower Oil Spray"
    - "salt and pepper to taste"
    - "2 cloves garlic"
    """
    text = raw.strip()

    quantity: float | None = None
    unit: str | None = None
    remainder = text

    # Try to extract leading quantity
    qty_val, qty_len = _extract_quantity(text)
    if qty_val is not None:
        quantity = qty_val
        remainder = text[qty_len:].lstrip()

        # Try to extract a unit immediately following the quantity
        unit_val, unit_len = _extract_unit(remainder)
        if unit_val:
            unit = unit_val
            remainder = remainder[unit_len:].lstrip()

            # Strip container descriptor between unit and comma
            # e.g. "130 g Pot, Sticky Rice Pot" — consume "Pot" here
            remainder = _CONTAINER_DESCRIPTORS_RE.sub("", remainder)

            # Strip any residual quantity+unit annotation (e.g. "0.2ml" in
            # "3 spray 0.2ml, Sunflower Oil Spray" after extracting "spray")
            remainder = _RESIDUAL_QTY_RE.sub("", remainder)

    name = _clean_name(remainder)
    search_query = _build_search_query(name)

    logger.debug(
        f"Parsed '{raw}' → qty={quantity}, unit={unit}, "
        f"name='{name}', query='{search_query}'"
    )

    return ParsedIngredient(
        original=raw,
        quantity=quantity,
        unit=unit,
        name=name,
        search_query=search_query,
    )
