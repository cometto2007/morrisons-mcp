import logging
import os
import re as _re
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastmcp import FastMCP, Context

from .cache import ProductCache
from .mealie_client import MealieClient
from .morrison_client import MorrisonClient, ProductNotFoundError
from .ingredient_parser import parse_ingredient
from .fuzzy_matcher import find_best_match, FRESH_PRODUCE_SYNONYMS, _FRESH_CATEGORY_KEYWORDS

# Pre-search query rewrites applied BEFORE hitting the Morrisons API.
# Use this for queries that are known to return wrong product categories
# (e.g. "green peas" returns only Cofresh snack mixes, never actual peas).
# These are unconditional — the rewrite always happens regardless of what
# the API might return.  The synonym fallback below is a second layer for
# weaker cases where the initial search may or may not succeed.
SEARCH_QUERY_REWRITES: dict[str, str] = {
    "green peas": "garden peas",
    "peas": "garden peas",
    "spring onion": "salad onion",
    "low-fat mayo": "light mayonnaise",
    "low fat mayo": "light mayonnaise",
    "mayo": "mayonnaise",
    "tomato paste": "tomato puree",
    "udon": "udon noodles",
    "udon cooked": "amoy udon noodles",
    "sun-dried tomato": "sundried tomatoes",
    "sun dried tomato": "sundried tomatoes",
    "lasagne pasta": "lasagne sheets",
}

# Ingredient synonyms for search fallback (extends the fresh-produce synonym table
# with common shopping-name substitutions and regional spelling variants).
INGREDIENT_SYNONYMS: dict[str, list[str]] = {
    "mayo": ["mayonnaise"],
    "low-fat mayo": ["light mayonnaise", "reduced fat mayonnaise"],
    "low fat mayo": ["light mayonnaise", "reduced fat mayonnaise"],
    "tomato paste": ["tomato puree", "tomato purée", "tomato concentrate"],
    "stock cube": ["stock pot", "bouillon cube"],
    "brown rice": ["wholegrain rice", "brown basmati rice"],
    "spring onion": ["salad onion"],
    "scallion": ["spring onion", "salad onion"],
    "zucchini": ["courgette"],
    "eggplant": ["aubergine"],
    "cilantro": ["coriander"],
    "arugula": ["rocket"],
    # Morrisons search for "green peas" returns only snack products (Cofresh etc.);
    # "peas" or "garden peas" returns actual vegetable products.
    "green peas": ["peas", "garden peas"],
    "peas": ["garden peas"],
}

# Qualifier words stripped from the search query on a second attempt when the
# full query returns no match.  Order matters — strip longest patterns first.
_QUALIFIER_STRIP_PATTERNS = [
    r'\blow[\s-]fat\b',
    r'\breduced[\s-]fat\b',
    r'\bfull[\s-]fat\b',
    r'\blight\b',
    r'\bdiet\b',
    r'\bzero\b',
    r'\bsugar[\s-]free\b',
    r'\bskimmed\b',
    r'\bsemi[\s-]skimmed\b',
]
from .models import (
    ParsedIngredient,
    ProductResult,
    ProductDetail,
    IngredientCost,
    RecipeCostResult,
)


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    _configure_logging()
    cache = ProductCache(db_path=os.getenv("CACHE_DB_PATH", "/data/cache.db"))
    # Drop pre-v2 product rows (could hold per-serving figures or "Unknown"
    # products); they are never read again under the bop_v2: key.
    await cache.delete_prefix("bop:")
    # Rows left by the removed recipe-nutrition fallback (Open Food Facts/USDA)
    await cache.delete_prefix("fallback")
    morrison = MorrisonClient(cache=cache)
    mealie = MealieClient(cache=cache)
    logger.info("Morrisons MCP server starting up")
    try:
        yield {"morrison": morrison, "mealie": mealie}
    finally:
        await mealie.close()
        await morrison.close()
        await cache.close()
        logger.info("Morrisons MCP server shut down")


mcp = FastMCP(
    "Morrisons Grocery MCP",
    lifespan=app_lifespan,
)


def _strip_qualifiers(query: str) -> str:
    """Return query with common quality/diet qualifiers removed."""
    q = query
    for pat in _QUALIFIER_STRIP_PATTERNS:
        q = _re.sub(pat, '', q, flags=_re.IGNORECASE)
    return _re.sub(r'\s+', ' ', q).strip()


async def _try_synonyms(
    parsed: ParsedIngredient,
    synonyms: list[str],
    morrison: MorrisonClient,
    best_confidence: float,
) -> tuple[ProductResult | None, float]:
    """Search each synonym and return the best result above threshold."""
    best_match: ProductResult | None = None
    for synonym in synonyms:
        syn_parsed = ParsedIngredient(
            original=parsed.original,
            quantity=parsed.quantity,
            unit=parsed.unit,
            name=synonym,
            search_query=synonym,
        )
        syn_products = await morrison.search(synonym, max_results=20)
        syn_match, syn_confidence = find_best_match(syn_parsed, syn_products)
        if syn_confidence > best_confidence:
            best_match, best_confidence = syn_match, syn_confidence
    return best_match, best_confidence


async def _match_with_synonym_fallback(
    parsed: ParsedIngredient,
    morrison: MorrisonClient,
) -> tuple[ProductResult | None, float]:
    """
    Try to match a parsed ingredient to a product. Applies four strategies:

    0. Pre-search query rewrite (green peas → garden peas, etc.) — applied
       unconditionally BEFORE the API call for queries known to return wrong
       product categories from Morrisons.
    1. Fresh-produce synonym table (pumpkin → butternut squash, etc.)
    2. Ingredient synonym table (tomato paste → tomato puree, etc.)
    3. Qualifier stripping (low-fat mayo → mayo → mayonnaise)
    """
    # 0a. If the unit is "can" or "tin", the Mealie format "1 can, Chickpeas" will
    # have parsed the container as the unit, losing the form descriptor.
    # Restore it by prepending "canned" to the search query (Morrisons responds
    # better to "canned chickpeas" than "tinned chickpeas" in practice).
    if parsed.unit in ("can", "tin") and not parsed.search_query.lower().startswith("canned"):
        canned_query = f"canned {parsed.search_query}"
        logger.debug(f"Container unit '{parsed.unit}' → prepending 'canned': '{canned_query}'")
        parsed = ParsedIngredient(
            original=parsed.original,
            quantity=parsed.quantity,
            unit=parsed.unit,
            name=parsed.name,
            search_query=canned_query,
        )

    # 0b. Pre-search rewrite: substitute the query before hitting the API
    query_lower = parsed.search_query.lower()
    rewritten = SEARCH_QUERY_REWRITES.get(query_lower)
    if rewritten:
        logger.debug(f"Query rewrite: '{parsed.search_query}' → '{rewritten}'")
        parsed = ParsedIngredient(
            original=parsed.original,
            quantity=parsed.quantity,
            unit=parsed.unit,
            name=rewritten,
            search_query=rewritten,
        )

    products = await morrison.search(parsed.search_query, max_results=20)
    match, confidence = find_best_match(parsed, products)

    # Decide whether to try fallbacks
    should_try_synonym = confidence < 0.5
    if not should_try_synonym and match and match.category_path:
        cat_lower = match.category_path.lower()
        has_fresh_category = any(kw in cat_lower for kw in _FRESH_CATEGORY_KEYWORDS)
        if not has_fresh_category:
            should_try_synonym = True

    if should_try_synonym:
        query_lower = parsed.search_query.lower()

        # 1. Fresh-produce synonyms
        fresh_synonyms = FRESH_PRODUCE_SYNONYMS.get(query_lower, [])
        if fresh_synonyms:
            syn_match, syn_conf = await _try_synonyms(
                parsed, fresh_synonyms, morrison, confidence or 0.0
            )
            if syn_conf > (confidence or 0.0):
                match, confidence = syn_match, syn_conf

        # 2. Ingredient synonyms (mayo, tomato paste, brown rice, etc.)
        ing_synonyms = INGREDIENT_SYNONYMS.get(query_lower, [])
        if ing_synonyms:
            syn_match, syn_conf = await _try_synonyms(
                parsed, ing_synonyms, morrison, confidence or 0.0
            )
            if syn_conf > (confidence or 0.0):
                match, confidence = syn_match, syn_conf

        # 3. Qualifier stripping: "low-fat mayo" → "mayo", "mozzarella light" → "mozzarella"
        if not match or confidence < 0.4:
            stripped = _strip_qualifiers(parsed.search_query)
            if stripped and stripped != parsed.search_query:
                stripped_parsed = ParsedIngredient(
                    original=parsed.original,
                    quantity=parsed.quantity,
                    unit=parsed.unit,
                    name=stripped,
                    search_query=stripped,
                )
                stripped_products = await morrison.search(stripped, max_results=20)
                stripped_match, stripped_conf = find_best_match(stripped_parsed, stripped_products)
                # Also try ingredient synonyms of the stripped query
                stripped_ing_syns = INGREDIENT_SYNONYMS.get(stripped.lower(), [])
                if stripped_ing_syns:
                    syn_match, syn_conf = await _try_synonyms(
                        stripped_parsed, stripped_ing_syns, morrison, stripped_conf
                    )
                    if syn_conf > stripped_conf:
                        stripped_match, stripped_conf = syn_match, syn_conf
                if stripped_conf > (confidence or 0.0):
                    match, confidence = stripped_match, stripped_conf

    return match, confidence


# ---------------------------------------------------------------------------
# Tool 1: search_products
# ---------------------------------------------------------------------------

@mcp.tool
async def search_products(query: str, ctx: Context, max_results: int = 10) -> list[ProductResult]:
    """
    Search Morrisons grocery products by name or keyword.
    Returns products with price, unit price, promotions, pack size, and category.

    Args:
        query: Search term (e.g. "chicken breast", "olive oil", "chopped tomatoes")
        max_results: Maximum number of results to return (default 10, max 30)
    """
    morrison: MorrisonClient = ctx.lifespan_context["morrison"]
    try:
        return await morrison.search(query, max_results=min(max_results, 30))
    except Exception as e:
        logger.error(f"search_products failed: {e}")
        raise


# ---------------------------------------------------------------------------
# Tool 2: get_product_detail
# ---------------------------------------------------------------------------

@mcp.tool
async def get_product_detail(retailer_product_id: str, ctx: Context) -> ProductDetail:
    """
    Get full product detail including nutrition from Morrisons.
    Uses the retailerProductId from search results (numeric string like "108444543").

    Nutrition comes only from the label's per-100g or per-100ml column;
    `nutrition_per_100g.basis` says which ("100g" or "100ml"). If the label has
    no per-100 column, `nutrition_per_100g` is null.

    `nutrition_per_100g.basis_note` is the column header's qualifier, lowercase,
    e.g. "as consumed", "as sold", "prepared", "cooked", "raw", "drained" or a
    cooking method like "grilled"; null when the header has none. Figures may
    be "as consumed" (cooked), e.g. a whole chicken's 27.3 g protein is cooked
    meat, so don't assume raw weight; check `basis_note`. Also returns the raw
    `pack_size`, a parsed `net_quantity` {value, unit} in g or ml, price,
    origin, storage and cooking info.

    If Morrisons no longer has the product, returns `found: false` with the
    other fields empty.

    Args:
        retailer_product_id: The numeric retailer product ID from search results
    """
    morrison: MorrisonClient = ctx.lifespan_context["morrison"]
    try:
        return await morrison.get_product_detail(retailer_product_id)
    except ProductNotFoundError:
        return ProductDetail(retailer_product_id=retailer_product_id, found=False)
    except Exception as e:
        logger.error(f"get_product_detail failed for {retailer_product_id}: {e}")
        raise


# ---------------------------------------------------------------------------
# Tool 3: cost_recipe
# ---------------------------------------------------------------------------

@mcp.tool
async def cost_recipe(
    ingredients: list[str],
    ctx: Context,
    servings: float | None = None,
    recipe_name: str | None = None,
) -> RecipeCostResult:
    """
    Cost a recipe by matching ingredient strings to Morrisons products.
    Takes a list of ingredient strings (e.g. ["500g chicken breast", "1 tin chopped tomatoes"])
    and returns the total cost plus per-ingredient breakdown with matched products and prices.

    Args:
        ingredients: List of ingredient strings with quantities
        servings: Number of servings the recipe makes (for per-serving cost)
        recipe_name: Optional recipe name for labelling
    """
    morrison: MorrisonClient = ctx.lifespan_context["morrison"]
    mealie: MealieClient = ctx.lifespan_context["mealie"]

    results = []
    total = 0.0
    total_excluding_pantry = 0.0
    unmatched = 0

    for ing_str in ingredients:
        parsed = parse_ingredient(ing_str)

        # Check if it's a pantry staple via Mealie (try name, then search_query)
        on_hand = await mealie.is_pantry_staple(parsed.name)
        if not on_hand and parsed.search_query != parsed.name.lower():
            on_hand = await mealie.is_pantry_staple(parsed.search_query)
        if on_hand:
            results.append(IngredientCost(
                ingredient=ing_str,
                parsed_query=parsed.search_query,
                on_hand=True,
                note="Pantry staple — already have at home",
            ))
            continue

        try:
            match, confidence = await _match_with_synonym_fallback(parsed, morrison)
        except Exception as e:
            logger.error(f"Error searching for '{parsed.search_query}': {e}")
            match, confidence = None, 0.0

        cost = match.price if match else None
        if cost is not None:
            total += cost
            total_excluding_pantry += cost
        else:
            unmatched += 1

        results.append(IngredientCost(
            ingredient=ing_str,
            parsed_query=parsed.search_query,
            matched_product=match,
            match_confidence=round(confidence, 2) if match else None,
            cost=cost,
            note="No match found" if not match else None,
        ))

    return RecipeCostResult(
        recipe_name=recipe_name,
        servings=servings,
        ingredients=results,
        total_cost=round(total, 2),
        cost_per_serving=round(total / servings, 2) if servings and servings > 0 else None,
        cost_excluding_pantry=round(total_excluding_pantry, 2),
        cost_per_serving_excluding_pantry=(
            round(total_excluding_pantry / servings, 2) if servings and servings > 0 else None
        ),
        unmatched_count=unmatched,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

MCP_PATH = "/mcp"


def create_http_app():
    """Streamable HTTP app serving MCP at /mcp.

    Stateless: every request is self-contained, so nothing breaks when a
    client idles or the server restarts.
    """
    return mcp.http_app(path=MCP_PATH, stateless_http=True)


if __name__ == "__main__":
    import uvicorn
    _configure_logging()
    uvicorn.run(create_http_app(), host="0.0.0.0", port=8000)
