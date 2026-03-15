import logging
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastmcp import FastMCP, Context

from .cache import ProductCache
from .mealie_client import MealieClient
from .morrison_client import MorrisonClient
from .ingredient_parser import parse_ingredient
from .fuzzy_matcher import find_best_match, FRESH_PRODUCE_SYNONYMS
from .nutrition_fallback import get_fallback_nutrition
from .weight_estimator import estimate_weight_grams
from .models import (
    ParsedIngredient,
    ProductResult,
    ProductDetail,
    IngredientCost,
    RecipeCostResult,
    IngredientNutrition,
    RecipeNutritionResult,
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
    morrison = MorrisonClient(cache=cache)
    mealie = MealieClient(cache=cache)
    logger.info("Morrisons MCP server starting up")
    try:
        yield {"morrison": morrison, "cache": cache, "mealie": mealie}
    finally:
        await mealie.close()
        await morrison.close()
        await cache.close()
        logger.info("Morrisons MCP server shut down")


mcp = FastMCP(
    "Morrisons Grocery MCP",
    lifespan=app_lifespan,
)


async def _match_with_synonym_fallback(
    parsed: ParsedIngredient,
    morrison: MorrisonClient,
) -> tuple[ProductResult | None, float]:
    """
    Try to match a parsed ingredient to a product. If the best match
    confidence is below 0.5 and a fresh produce synonym exists, search
    again with the synonym and use the better result.
    """
    products = await morrison.search(parsed.search_query, max_results=20)
    match, confidence = find_best_match(parsed, products)

    if confidence < 0.5:
        synonyms = FRESH_PRODUCE_SYNONYMS.get(parsed.search_query.lower(), [])
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
            if syn_confidence > confidence:
                match, confidence = syn_match, syn_confidence
                break

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
    Returns nutrition per 100g (kcal, protein, fat, carbs, etc.), origin, storage, and cooking info.

    Args:
        retailer_product_id: The numeric retailer product ID from search results
    """
    morrison: MorrisonClient = ctx.lifespan_context["morrison"]
    try:
        return await morrison.get_product_detail(retailer_product_id)
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

        # Check if it's a pantry staple via Mealie
        on_hand = await mealie.is_pantry_staple(parsed.name)
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
# Tool 4: get_recipe_nutrition
# ---------------------------------------------------------------------------

@mcp.tool
async def get_recipe_nutrition(
    ingredients: list[str],
    ctx: Context,
    servings: float | None = None,
    recipe_name: str | None = None,
) -> RecipeNutritionResult:
    """
    Calculate nutrition for a recipe by matching ingredients to Morrisons products
    and fetching their BOP nutrition data.
    Returns total and per-serving kcal, protein, fat, and carbs.

    Args:
        ingredients: List of ingredient strings with quantities (e.g. ["500g chicken breast"])
        servings: Number of servings for per-serving calculation
        recipe_name: Optional recipe name
    """
    morrison: MorrisonClient = ctx.lifespan_context["morrison"]
    mealie: MealieClient = ctx.lifespan_context["mealie"]

    results = []
    total_kcal: float = 0.0
    total_protein: float = 0.0
    total_fat: float = 0.0
    total_carbs: float = 0.0
    has_kcal = has_protein = has_fat = has_carbs = False

    for ing_str in ingredients:
        parsed = parse_ingredient(ing_str)
        on_hand = await mealie.is_pantry_staple(parsed.name)
        ing_nutrition = IngredientNutrition(ingredient=ing_str, on_hand=on_hand)

        try:
            match, confidence = await _match_with_synonym_fallback(parsed, morrison)
        except Exception as e:
            logger.error(f"Error matching '{parsed.search_query}': {e}")
            results.append(ing_nutrition)
            continue

        if match and confidence >= 0.4:
            try:
                detail = await morrison.get_product_detail(match.retailer_product_id)
            except Exception as e:
                logger.error(f"Error fetching BOP for '{match.retailer_product_id}': {e}")
                results.append(ing_nutrition)
                continue

            ing_nutrition.matched_product = match.name
            ing_nutrition.pack_size = match.pack_size

            nutrition = detail.nutrition_per_100g
            nutrition_source = "Morrisons"

            # Fallback if Morrisons has no nutrition data
            if nutrition is None or nutrition.energy_kcal is None:
                cache: ProductCache = ctx.lifespan_context["cache"]
                fallback_nutrition, fallback_source = await get_fallback_nutrition(
                    parsed.search_query, cache=cache,
                )
                if fallback_nutrition:
                    nutrition = fallback_nutrition
                    nutrition_source = fallback_source

            ing_nutrition.nutrition_per_100g = nutrition
            ing_nutrition.nutrition_source = nutrition_source if nutrition else None

            weight_g = estimate_weight_grams(parsed)
            ing_nutrition.estimated_weight_g = weight_g

            if weight_g is not None and nutrition:
                n = nutrition
                factor = weight_g / 100.0

                if n.energy_kcal is not None:
                    ing_nutrition.estimated_kcal = round(n.energy_kcal * factor, 1)
                    total_kcal += ing_nutrition.estimated_kcal
                    has_kcal = True

                if n.protein_g is not None:
                    ing_nutrition.estimated_protein_g = round(n.protein_g * factor, 1)
                    total_protein += ing_nutrition.estimated_protein_g
                    has_protein = True

                if n.fat_g is not None:
                    ing_nutrition.estimated_fat_g = round(n.fat_g * factor, 1)
                    total_fat += ing_nutrition.estimated_fat_g
                    has_fat = True

                if n.carbohydrate_g is not None:
                    ing_nutrition.estimated_carbs_g = round(n.carbohydrate_g * factor, 1)
                    total_carbs += ing_nutrition.estimated_carbs_g
                    has_carbs = True

        results.append(ing_nutrition)

    return RecipeNutritionResult(
        recipe_name=recipe_name,
        servings=servings,
        ingredients=results,
        total_kcal=round(total_kcal, 1) if has_kcal else None,
        total_protein_g=round(total_protein, 1) if has_protein else None,
        total_fat_g=round(total_fat, 1) if has_fat else None,
        total_carbs_g=round(total_carbs, 1) if has_carbs else None,
        per_serving_kcal=round(total_kcal / servings, 1) if servings and has_kcal else None,
        per_serving_protein_g=round(total_protein / servings, 1) if servings and has_protein else None,
        per_serving_fat_g=round(total_fat / servings, 1) if servings and has_fat else None,
        per_serving_carbs_g=round(total_carbs / servings, 1) if servings and has_carbs else None,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    _configure_logging()
    asyncio.run(
        mcp.run_async(transport="sse", host="0.0.0.0", port=8000)
    )
