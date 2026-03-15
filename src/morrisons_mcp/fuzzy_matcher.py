import logging
import re

from rapidfuzz import fuzz

from .models import ParsedIngredient, ProductResult

logger = logging.getLogger(__name__)

# Minimum composite score (raw 0–110 scale) to consider a match
MIN_COMPOSITE_SCORE = 40

# Normalisation ceiling: 100 (name) + 10 (category bonus) = 110.
# Multi-word queries can exceed this via the consecutive phrase bonus (+25);
# the confidence is capped at 1.0, so over-ceiling is fine.
_SCORE_CEILING = 110.0

# Words ignored when checking "all significant words present" filter
_INSIGNIFICANT_WORDS = frozenset({
    "a", "an", "the", "of", "in", "with", "and", "or",
    "morrisons", "best", "fresh", "free", "range",
})

# Premium product words — penalised when the query doesn't ask for them
_PREMIUM_WORDS = frozenset({
    "organic", "free range", "free-range", "finest",
    "luxury", "premium", "the best",
})

# Multipack patterns in product names or pack sizes
_MULTIPACK_PATTERNS = [
    re.compile(r"\d+\s*x\s*\d+", re.IGNORECASE),
    re.compile(r"\d+\s*pack", re.IGNORECASE),
    re.compile(r"multipack|multi pack", re.IGNORECASE),
]

# Container units that imply "single item" when quantity is 1-2
_SINGLE_CONTAINER_UNITS = frozenset({
    "tin", "can", "jar", "bottle", "pot", "bag", "box", "tub", "pack",
})

# Keywords that suggest processed/non-ingredient products (checked in category AND name)
_PROCESSED_KEYWORDS = frozenset({
    # "sauce" intentionally omitted — too broad (fish sauce, sriracha, soy sauce are
    # all legitimate condiment ingredients). "cooking sauce" below handles ready-made sauces.
    "soup", "ready meal", "meal kit", "cooking sauce",
    "crisp", "snack", "biscuit", "cracker", "cereal",
    "drink", "juice", "smoothie", "dessert", "cake",
    "mix", "paste", "powder", "stock", "seasoning",
    "seeds", "nuts", "dried fruit",
    "baby food", "pet",
    "home & garden", "kitchen", "utensils",
    "cleaning", "health & beauty",
    # Ready-meal / convenience food signals
    "pasta", "noodle", "ready to eat",
    "mac",       # mac and cheese products
    "pizza",
    "pie",       # pies as ready meals (not pie ingredients like pastry)
    "sandwich",
    "wrap",
    "meal",
    "curry",     # ready-meal curries (not curry paste/powder — those contain "paste"/"powder" already)
    # Snack / confectionery categories (Bug 1: green peas → snack mix)
    "treats", "snacks", "crisps", "chips",
    "confectionery", "chocolate", "sweets",
    "biscuits", "cookies",
})

# Hard-exclusion category keywords for products that should never match a recipe
# ingredient query.  A bag of crisps / box of sweets is never a valid ingredient
# match regardless of how close the name similarity is.
# Each keyword that fires adds -50 to the composite (on top of the standard -25
# from _PROCESSED_KEYWORDS), driving these products well below MIN_COMPOSITE_SCORE.
# Include both single words AND common multi-word Morrisons category fragments so
# that the real API category paths are caught even if the exact wording varies.
_HARD_EXCLUSION_CATEGORY_KEYWORDS = frozenset({
    # Single-word anchors (match anywhere in the category path)
    "treats", "snacks", "crisps", "chips", "confectionery",
    "chocolate", "sweets", "biscuits", "cookies",
    # Common multi-word Morrisons fragments
    "treats & snacks", "crisps & snacks", "crisps & savoury snacks",
    "savoury snacks", "sharing bags", "multipacks",
    # Nuts/seeds aisle (Cofresh products often land here)
    "nuts, seeds & dried fruit",
    # Popcorn / sweet snacks
    "popcorn",
})


# Category keywords that indicate fresh/raw produce
_FRESH_CATEGORY_KEYWORDS = frozenset({
    "veg", "fruit", "meat", "fish", "poultry", "dairy",
    "fresh", "chilled", "bakery", "eggs",
})

# Synonyms for fresh produce — used when best match confidence is low
FRESH_PRODUCE_SYNONYMS: dict[str, list[str]] = {
    "pumpkin": ["butternut squash", "squash"],
    "capsicum": ["bell pepper", "pepper"],
    "aubergine": ["eggplant"],
    "courgette": ["zucchini"],
    "coriander": ["cilantro"],
    "spring onion": ["salad onion"],
    "rocket": ["arugula"],
    "swede": ["rutabaga"],
}


def _kw_in_text(keyword: str, text: str) -> bool:
    """Return True if keyword appears as a whole token in text.

    For single-word keywords uses negative lookarounds so that short keywords
    (e.g. 'mac', 'mix', 'crisp', 'meal') are NOT matched as substrings of
    longer words ('mackerel', 'mixed', 'crispy', 'oatmeal').  The optional
    trailing `s` allows matching the keyword's own plural form without
    requiring a separate plural entry in the set (e.g. 'noodle' → 'noodles').

    For multi-word keywords plain substring matching is used — the phrase is
    specific enough that false-positive collisions are extremely unlikely.
    """
    if " " in keyword:
        return keyword in text
    return bool(re.search(r"(?<![a-z])" + re.escape(keyword) + r"s?(?![a-z])", text))


def _stem(word: str) -> str:
    """Strip common plural/verb suffixes for fuzzy word matching."""
    w = word.lower().strip()
    if w.endswith("ies") and len(w) > 5:
        return w[:-3] + "y"
    if w.endswith("ves") and len(w) > 5:
        return w[:-3] + "f"
    if w.endswith("es") and len(w) > 4:
        return w[:-2]
    if w.endswith("s") and len(w) > 3:
        return w[:-1]
    return w


def _significant_words(text: str) -> list[str]:
    """Extract lowercase significant words from text."""
    words = re.findall(r"[a-z]+", text.lower())
    return [w for w in words if w not in _INSIGNIFICANT_WORDS and len(w) > 1]


def _all_query_words_present(query: str, product_name: str) -> bool:
    """
    For multi-word queries, require ALL significant query words to appear
    in the product name (with basic stemming for plural tolerance).

    E.g. 'chicken breast fillet' matches 'Chicken Breast Fillets' because
    _stem('fillet') == _stem('fillets') == 'fillet'.

    Single-word queries skip this check to allow free matching.
    """
    query_words = _significant_words(query)
    if len(query_words) <= 1:
        return True

    product_stems = {_stem(w) for w in _significant_words(product_name)}
    return all(_stem(word) in product_stems for word in query_words)


# Product-type words that, when they appear in the product name alongside a matched
# query phrase, signal the product is a compound dish where the query ingredient is
# just a flavour component (e.g. "pesto" in "Sundried Tomato Pesto" means the
# product is a pesto, not sundried tomatoes).  Only applied for multi-word queries
# where the phrase was found; reduces the phrase bonus from +25 to +10.
# "sauce" was intentionally removed from _PROCESSED_KEYWORDS (too broad); it is
# included here at the softer −15 level, only when a phrase match fired.
_COMPOUND_PRODUCT_INDICATORS = frozenset({
    "pesto", "sauce", "dip", "spread", "dressing",
    "stew", "bake", "risotto", "vinaigrette",
})

# Food words that, when they immediately precede a query phrase in a product name,
# signal the product is a compound (e.g. "sardine" before "tomato paste" means
# this is a sardine product, NOT a tomato paste product).
_COMPOUND_FOOD_PRECEDING = frozenset({
    "sardine", "anchovy", "mackerel", "herring", "pilchard",
    "chicken", "beef", "pork", "lamb", "turkey", "duck",
    "prawn", "shrimp", "crab", "lobster",
    "mushroom", "spinach", "cheese",
})


def _consecutive_word_bonus(query: str, product_name: str) -> int:
    """
    Score adjustment when a multi-word query appears as a phrase in the product name.

    +25  the query is a word-boundary phrase match, nothing suspicious precedes or
         follows it (e.g. "tomato paste" in "Napolina Tomato Paste").
    +10  phrase matched but the product also contains a compound-product indicator
         (pesto, sauce, dressing…) not present in the query — the ingredient is a
         flavour component, not the main product
         (e.g. "sundried tomato" in "Filippo Berio Sundried Tomato Pesto").
    -10  a compound-food noun immediately precedes the phrase — this is a
         compound product, not the standalone ingredient
         (e.g. "sardine" before "tomato paste" in "Sardine & Tomato Paste").
      0  single-word queries — adjacency is trivially true, no signal.

    Uses a word-boundary pattern so "tomato paste" does NOT match inside
    "tomatopaste" (no such word, but defensive).
    The naive substring "fish sauce" in "fish pie sauce" check is intentionally
    avoided here — that product fails because "pie" interrupts the phrase.
    """
    query_lower = query.lower().strip()
    if " " not in query_lower:
        return 0

    name_lower = product_name.lower()
    # Phrase must be delimited by whitespace/punctuation on both sides.
    # The trailing `s?` tolerates a single plural suffix so that
    # "spring onion" matches "spring onions" and "chicken fillet" matches
    # "chicken fillets" — otherwise the correct (plural) product name loses
    # the bonus to a snack/flavour product that uses the singular form.
    pattern = (
        r'(?:^|[\s&,/])\s*'
        + re.escape(query_lower)
        + r's?(?=\s|[,/]|$|\d)'
    )
    m = re.search(pattern, name_lower)
    if not m:
        return 0

    # Check if a compound-food noun immediately precedes the matched phrase
    preceding_text = name_lower[:m.start()].strip().rstrip("&,/ ")
    preceding_words = re.findall(r'[a-z]+', preceding_text)
    if preceding_words and preceding_words[-1] in _COMPOUND_FOOD_PRECEDING:
        return -10

    # Check if a compound-product indicator appears anywhere in the product name
    # but is NOT part of the query (the ingredient is used as a flavour).
    query_word_set = set(query_lower.split())
    for indicator in _COMPOUND_PRODUCT_INDICATORS:
        if _kw_in_text(indicator, name_lower) and indicator not in query_word_set:
            return 10

    return 25


def find_best_match(
    ingredient: ParsedIngredient,
    products: list[ProductResult],
    min_confidence: float = 0.4,
) -> tuple[ProductResult | None, float]:
    """
    Find best matching product for a parsed ingredient.

    Returns (best_match, confidence) or (None, 0.0) if no match meets the
    threshold. Confidence is normalised to 0.0–1.0.

    Note: products with availability penalty may return None if ALL candidates
    are unavailable and their composite scores fall below MIN_COMPOSITE_SCORE.
    """
    if not products:
        return None, 0.0

    query = ingredient.search_query
    best_product: ProductResult | None = None
    best_score: float = 0.0

    for product in products:
        # Require all significant query words to appear as whole words in the product name
        if not _all_query_words_present(query, product.name):
            continue

        # Base name score (0–100)
        name_lower = product.name.lower()
        query_lower = query.lower()
        name_score = fuzz.token_sort_ratio(query, name_lower)
        composite = float(name_score)

        # Single-word exact match boost: +30 if the query is a single word
        # and it appears (stemmed) in the product name. This helps staple
        # ingredients like "eggs", "milk", "butter" which get very low
        # token_sort_ratio scores against long product names.
        query_words = _significant_words(query)
        if len(query_words) == 1:
            query_stem = _stem(query_words[0])
            product_stems = {_stem(w) for w in _significant_words(product.name)}
            if query_stem in product_stems:
                composite += 30

        # Consecutive phrase bonus: +25 if the query appears as an exact
        # substring of the product name (words are adjacent, not interleaved).
        # "fish sauce" is in "Squid Brand Fish Sauce" → +25
        # "fish sauce" is NOT in "Morrisons Fish Pie Sauce" → 0
        composite += _consecutive_word_bonus(query, product.name)

        # Category bonus: +10 if a query word appears as a whole word in the category path
        if product.category_path:
            cat_lower = product.category_path.lower()
            if any(
                re.search(r"\b" + re.escape(w) + r"\b", cat_lower)
                for w in query_words
            ):
                composite += 10

        # Processed product penalty: applied independently for name and category
        # so that a product with a bad name AND a bad category accumulates -50
        # (e.g. "Veetee Mac 'N' Cheese Sriracha" in a Pasta & Noodles category)
        # while a legitimately-named product in a clean category only loses -25
        # (e.g. "Flying Goose Sriracha Hot Chilli Sauce" — "sauce" in name).
        query_words_set = set(query_words)
        for kw in _PROCESSED_KEYWORDS:
            if _kw_in_text(kw, name_lower) and kw not in query_words_set:
                logger.debug(f"  Name penalty -25: keyword '{kw}' in '{product.name}'")
                composite -= 25
                break
        if product.category_path:
            cat_lower_chk = product.category_path.lower()
            for kw in _PROCESSED_KEYWORDS:
                if _kw_in_text(kw, cat_lower_chk) and kw not in query_words_set:
                    logger.debug(
                        f"  Category penalty -25: keyword '{kw}' "
                        f"in '{product.category_path}'"
                    )
                    composite -= 25
                    break
            # Hard-exclusion: snack/confectionery categories add an extra -50 on top,
            # ensuring they land far below MIN_COMPOSITE_SCORE even when the phrase
            # bonus fires (+25).  The standard -25 alone is cancelled by +25 and leaves
            # the snack product tied with fresh veg — this makes it decisive.
            for kw in _HARD_EXCLUSION_CATEGORY_KEYWORDS:
                if _kw_in_text(kw, cat_lower_chk) and kw not in query_words_set:
                    logger.debug(
                        f"  Hard-exclusion penalty -50: keyword '{kw}' "
                        f"in '{product.category_path}'"
                    )
                    composite -= 50
                    break

        # Premium penalty: -15 if product has premium words not in query
        for pw in _PREMIUM_WORDS:
            if pw in name_lower and pw not in query_lower:
                composite -= 15
                break

        # Multipack penalty: -20 when recipe quantity suggests a single item
        if (ingredient.quantity is not None
                and ingredient.quantity <= 2
                and ingredient.unit in _SINGLE_CONTAINER_UNITS):
            check_text = name_lower + " " + (product.pack_size or "").lower()
            for pat in _MULTIPACK_PATTERNS:
                if pat.search(check_text):
                    composite -= 20
                    break

        # Availability penalty: -50 if not available
        if not product.available:
            composite -= 50

        logger.debug(
            f"  '{product.name}': name_score={name_score:.0f}, composite={composite:.0f}"
        )

        # Prefer this product if it has a higher score, or if scores are
        # within 10 points, prefer the cheaper product (price tiebreaker)
        if composite > best_score:
            best_score = composite
            best_product = product
        elif (best_product is not None
              and abs(composite - best_score) <= 10
              and product.price is not None
              and best_product.price is not None
              and product.price < best_product.price):
            best_score = composite
            best_product = product

    if best_score < MIN_COMPOSITE_SCORE or best_product is None:
        logger.debug(f"No confident match for '{query}' (best composite: {best_score:.0f})")
        return None, 0.0

    # Normalise to 0.0–1.0 (capped at 1.0 for scores above ceiling)
    confidence = min(best_score / _SCORE_CEILING, 1.0)

    if confidence < min_confidence:
        logger.debug(
            f"Match '{best_product.name}' below min_confidence "
            f"{min_confidence}: {confidence:.2f}"
        )
        return None, 0.0

    logger.info(
        f"Matched '{query}' → '{best_product.name}' "
        f"(confidence={confidence:.2f}, price=£{best_product.price:.2f})"
    )
    return best_product, confidence
