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


def _significant_words(text: str) -> list[str]:
    """Extract lowercase significant words from text."""
    words = re.findall(r"[a-z]+", text.lower())
    return [w for w in words if w not in _INSIGNIFICANT_WORDS and len(w) > 1]


def _all_query_words_present(query: str, product_name: str) -> bool:
    """
    For multi-word queries, require ALL significant query words to appear
    in the product name as whole words (word-boundary match).

    E.g. 'chicken breast' requires both 'chicken' AND 'breast' as whole words.
    'pea' will not match 'peas' since 'pea' is not a whole word in 'peas'
    (single-word queries still skip this check to allow free matching).
    """
    query_words = _significant_words(query)
    if len(query_words) <= 1:
        return True

    product_lower = product_name.lower()
    return all(
        bool(re.search(r"\b" + re.escape(word) + r"\b", product_lower))
        for word in query_words
    )


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

        # Availability penalty: -50 if not available
        if not product.available:
            composite -= 50

        logger.debug(
            f"  '{product.name}': name_score={name_score:.0f}, composite={composite:.0f}"
        )

        if composite > best_score:
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
