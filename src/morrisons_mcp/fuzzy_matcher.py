import logging
import re

from rapidfuzz import fuzz

from .models import ParsedIngredient, ProductResult

logger = logging.getLogger(__name__)

# Minimum composite score (raw 0–110 scale) to consider a match
MIN_COMPOSITE_SCORE = 40

# Normalisation ceiling: 100 (name) + 10 (category bonus) = 110
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


def _stem(word: str) -> str:
    """Very basic stemming — strip common English suffixes for matching."""
    w = word.lower()
    for suffix in ("ies", "es", "s", "ing"):
        if w.endswith(suffix) and len(w) > len(suffix) + 2:
            return w[:-len(suffix)]
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
        name_score = fuzz.token_sort_ratio(query, product.name.lower())
        composite = float(name_score)

        # Category bonus: +10 if a query word appears as a whole word in the category path
        if product.category_path:
            cat_lower = product.category_path.lower()
            if any(
                re.search(r"\b" + re.escape(w) + r"\b", cat_lower)
                for w in _significant_words(query)
            ):
                composite += 10

        # Premium penalty: -15 if product has premium words not in query
        query_lower = query.lower()
        name_lower = product.name.lower()
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
