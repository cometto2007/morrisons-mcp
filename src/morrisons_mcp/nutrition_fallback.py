import logging
import os
import re

import httpx

from .cache import ProductCache
from .models import NutritionPer100g

logger = logging.getLogger(__name__)

_OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
_USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
_OFF_FIELDS = "product_name,nutriments"
_USER_AGENT = "MorrisonsMCP/1.0 (chris@chrislab.it)"

# Cache TTL for fallback nutrition data: 7 days
_FALLBACK_TTL = 604_800

# USDA search overrides for ambiguous single-word ingredients
_USDA_SEARCH_OVERRIDES: dict[str, str] = {
    "eggs": "egg whole raw",
    "egg": "egg whole raw",
    "salt": "salt table",
    "milk": "milk whole 3.25%",
    "cream": "cream heavy whipping",
    "butter": "butter salted",
    "flour": "wheat flour all purpose",
    "sugar": "sugar granulated",
    "rice": "rice white long grain cooked",
    "chicken": "chicken breast meat raw",
    "beef": "beef ground 80 lean raw",
    "oil": "olive oil",
    "honey": "honey",
    "garlic": "garlic raw",
    "onion": "onion raw",
    "pepper": "spices pepper black",
    "black pepper": "spices pepper black",
    # Vegetables where USDA returns seeds/processed forms by default
    "butternut squash": "butternut squash raw",
    "aubergine": "eggplant raw",
    "eggplant": "eggplant raw",
    "lemon": "lemon raw without peel",
}

# Low-calorie vegetables: max expected kcal and fat per 100g of raw flesh.
# If USDA returns a result exceeding these thresholds the food is seeds,
# a processed product, or the wrong food entirely.
_LOW_CAL_VEG_KCAL_LIMIT = 100   # raw veg flesh is never >100 kcal/100g
_LOW_CAL_VEG_FAT_LIMIT = 5      # raw veg flesh is never >5g fat/100g
_LOW_CAL_VEG_KEYWORDS = frozenset({
    "squash", "aubergine", "eggplant", "courgette", "zucchini", "pumpkin",
})


def _word_in(word: str, text: str) -> bool:
    """Return True if word appears as a whole word in text (not a substring of a longer word)."""
    return bool(re.search(r"\b" + re.escape(word) + r"\b", text))


def _validate_usda_result(query: str, nutrition: NutritionPer100g) -> bool:
    """Reject USDA results that are clearly the wrong food."""
    q = query.lower().strip()
    kcal = nutrition.energy_kcal
    fat = nutrition.fat_g

    # Eggs should have significant fat (whole eggs ~9-11g, whites <1g)
    if q in ("eggs", "egg", "egg whole raw") and fat is not None and fat < 3.0:
        return False

    # Salt should have essentially zero calories
    if q in ("salt", "table salt", "salt table") and kcal is not None and kcal > 10:
        return False

    # Pepper (spice) should be <400 kcal — use word boundary so "bell pepper" is exempt
    if _word_in("pepper", q) and kcal is not None and kcal > 500:
        return False

    # Oil should be very high fat (>80g/100g) — word boundary avoids "olive oil" false-match
    if _word_in("oil", q) and fat is not None and fat < 50:
        return False

    # Butter must be high fat — word boundary avoids matching "butternut squash"
    if _word_in("butter", q) and fat is not None and fat < 50:
        return False

    # Low-calorie vegetables: seeds and processed forms are far too energy-dense.
    # Butternut squash flesh ≈ 40 kcal, 0.1g fat; pumpkin seeds ≈ 612 kcal, 49g fat.
    if any(_word_in(kw, q) for kw in _LOW_CAL_VEG_KEYWORDS):
        if kcal is not None and kcal > _LOW_CAL_VEG_KCAL_LIMIT:
            return False
        if fat is not None and fat > _LOW_CAL_VEG_FAT_LIMIT:
            return False

    return True


async def _search_open_food_facts(
    query: str, client: httpx.AsyncClient,
) -> NutritionPer100g | None:
    """Search Open Food Facts for nutrition data per 100g."""
    try:
        resp = await client.get(
            _OFF_SEARCH_URL,
            params={
                "search_terms": query,
                "search_simple": "1",
                "action": "process",
                "json": "1",
                "page_size": "5",
                "fields": _OFF_FIELDS,
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Open Food Facts search failed for '{query}': {e}")
        return None

    for product in data.get("products", []):
        n = product.get("nutriments", {})
        kcal = n.get("energy-kcal_100g")
        protein = n.get("proteins_100g")
        kj = n.get("energy-kj_100g")

        # Derive kcal from kJ if missing
        if kcal is None and kj is not None:
            kcal = round(kj / 4.184, 1)

        if kcal is None or protein is None:
            continue

        return NutritionPer100g(
            energy_kcal=_to_float(kcal),
            energy_kj=_to_float(kj),
            fat_g=_to_float(n.get("fat_100g")),
            saturates_g=_to_float(n.get("saturated-fat_100g")),
            carbohydrate_g=_to_float(n.get("carbohydrates_100g")),
            sugars_g=_to_float(n.get("sugars_100g")),
            fibre_g=_to_float(n.get("fiber_100g")),
            protein_g=_to_float(protein),
            salt_g=_to_float(n.get("salt_100g")),
        )

    return None


async def _search_usda_fdc(
    query: str, client: httpx.AsyncClient,
) -> NutritionPer100g | None:
    """Search USDA FoodData Central for nutrition data per 100g."""
    # Use override query for ambiguous single-word ingredients
    query_lower = query.lower().strip()
    search_query = _USDA_SEARCH_OVERRIDES.get(query_lower, query)

    api_key = os.getenv("USDA_FDC_API_KEY", "DEMO_KEY")
    try:
        resp = await client.post(
            _USDA_SEARCH_URL,
            params={"api_key": api_key},
            json={
                "query": search_query,
                "dataType": ["Foundation", "SR Legacy"],
                "pageSize": 10,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"USDA FDC search failed for '{search_query}': {e}")
        return None

    foods = data.get("foods", [])
    if not foods:
        return None

    # Sort: Foundation first, then SR Legacy
    foods.sort(key=lambda f: 0 if f.get("dataType") == "Foundation" else 1)

    for food in foods:
        nutrients = {n["nutrientName"]: n for n in food.get("foodNutrients", [])}

        energy_kcal = _usda_nutrient(nutrients, "Energy", "KCAL")
        energy_kj = _usda_nutrient(nutrients, "Energy", "kJ")
        if energy_kcal is None and energy_kj is not None:
            energy_kcal = round(energy_kj / 4.184, 1)

        protein = _usda_nutrient(nutrients, "Protein", "G")
        if energy_kcal is None or protein is None:
            continue

        fat_g = _usda_nutrient(nutrients, "Total lipid (fat)", "G")

        # Build a preliminary result to validate
        preliminary = NutritionPer100g(
            energy_kcal=energy_kcal, fat_g=fat_g, protein_g=protein,
        )
        if not _validate_usda_result(query_lower, preliminary):
            logger.debug(
                f"USDA '{food.get('description')}' rejected by validation "
                f"(kcal={energy_kcal}, fat={fat_g})"
            )
            continue

        # Sodium in mg → salt in g (salt = sodium × 2.5 / 1000)
        sodium_mg = _usda_nutrient(nutrients, "Sodium, Na", "MG")
        salt_g = round(sodium_mg * 2.5 / 1000, 2) if sodium_mg is not None else None

        return NutritionPer100g(
            energy_kcal=energy_kcal,
            energy_kj=energy_kj,
            fat_g=fat_g,
            saturates_g=_usda_nutrient(nutrients, "Fatty acids, total saturated", "G"),
            carbohydrate_g=_usda_nutrient(nutrients, "Carbohydrate, by difference", "G"),
            sugars_g=_usda_nutrient(nutrients, "Sugars, total including NLEA", "G"),
            fibre_g=_usda_nutrient(nutrients, "Fiber, total dietary", "G"),
            protein_g=protein,
            salt_g=salt_g,
        )

    return None


def _usda_nutrient(
    nutrients: dict, name: str, unit: str,
) -> float | None:
    """Extract a USDA nutrient value by name, optionally filtering by unit."""
    entry = nutrients.get(name)
    if entry is None:
        return None
    if unit and entry.get("unitName", "").upper() != unit.upper():
        return None
    val = entry.get("value")
    return float(val) if val is not None else None


def _to_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


async def get_fallback_nutrition(
    ingredient_name: str,
    cache: ProductCache | None = None,
) -> tuple[NutritionPer100g | None, str | None]:
    """
    Try Open Food Facts, then USDA FDC.
    Returns (nutrition, source_label) where source_label is
    "Open Food Facts", "USDA FoodData Central", or None if both fail.

    Results are cached for 7 days using the provided ProductCache.
    """
    # v2: bumped to invalidate stale pre-fix USDA/OFF results (wrong squash/aubergine data)
    cache_key = f"fallback_v2:{ingredient_name.lower().strip()}"

    if cache:
        cached = await cache.get(cache_key)
        if cached is not None:
            return NutritionPer100g(**cached["nutrition"]), cached["source"]

    async with httpx.AsyncClient() as client:
        # Tier 1: Open Food Facts
        result = await _search_open_food_facts(ingredient_name, client)
        if result:
            source = "Open Food Facts"
            if cache:
                await cache.set(
                    cache_key,
                    {"nutrition": result.model_dump(), "source": source},
                    ttl=_FALLBACK_TTL,
                )
            return result, source

        # Tier 2: USDA FoodData Central
        result = await _search_usda_fdc(ingredient_name, client)
        if result:
            source = "USDA FoodData Central"
            if cache:
                await cache.set(
                    cache_key,
                    {"nutrition": result.model_dump(), "source": source},
                    ttl=_FALLBACK_TTL,
                )
            return result, source

    return None, None
