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
        if kcal is None or protein is None:
            continue

        kj = n.get("energy-kj_100g")
        if kcal is None and kj is not None:
            kcal = round(kj / 4.184, 1)

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
    api_key = os.getenv("USDA_FDC_API_KEY", "DEMO_KEY")
    try:
        resp = await client.post(
            _USDA_SEARCH_URL,
            params={"api_key": api_key},
            json={
                "query": query,
                "dataType": ["Foundation", "SR Legacy"],
                "pageSize": 5,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"USDA FDC search failed for '{query}': {e}")
        return None

    foods = data.get("foods", [])
    if not foods:
        return None

    # Prefer Foundation over SR Legacy
    food = next((f for f in foods if f.get("dataType") == "Foundation"), foods[0])
    nutrients = {n["nutrientName"]: n for n in food.get("foodNutrients", [])}

    energy_kcal = _usda_nutrient(nutrients, "Energy", "KCAL")
    energy_kj = _usda_nutrient(nutrients, "Energy", "kJ")
    if energy_kcal is None and energy_kj is not None:
        energy_kcal = round(energy_kj / 4.184, 1)

    protein = _usda_nutrient(nutrients, "Protein", "G")
    if energy_kcal is None or protein is None:
        return None

    # Sodium in mg → salt in g (salt = sodium × 2.5 / 1000)
    sodium_mg = _usda_nutrient(nutrients, "Sodium, Na", "MG")
    salt_g = round(sodium_mg * 2.5 / 1000, 2) if sodium_mg is not None else None

    return NutritionPer100g(
        energy_kcal=energy_kcal,
        energy_kj=energy_kj,
        fat_g=_usda_nutrient(nutrients, "Total lipid (fat)", "G"),
        saturates_g=_usda_nutrient(nutrients, "Fatty acids, total saturated", "G"),
        carbohydrate_g=_usda_nutrient(nutrients, "Carbohydrate, by difference", "G"),
        sugars_g=_usda_nutrient(nutrients, "Sugars, total including NLEA", "G"),
        fibre_g=_usda_nutrient(nutrients, "Fiber, total dietary", "G"),
        protein_g=protein,
        salt_g=salt_g,
    )


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
    cache_key = f"fallback:{ingredient_name.lower().strip()}"

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
