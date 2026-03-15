import logging

import httpx

logger = logging.getLogger(__name__)


class MealieClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,
        )

    async def get_recipe_ingredients(
        self, slug: str
    ) -> tuple[str, float | None, list[str]]:
        """
        Fetch a recipe from Mealie and return (recipe_name, servings, ingredient_strings).
        Ingredient strings are extracted from the 'note' field primarily, falling back to
        'display'. Servings are derived from recipeServings, then recipeYieldQuantity.
        """
        resp = await self._client.get(f"/api/recipes/{slug}")
        resp.raise_for_status()
        data = resp.json()

        recipe_name: str = data.get("name") or slug

        # Determine servings — guard against non-numeric or string values
        servings: float | None = None
        for field in ("recipeServings", "recipeYieldQuantity"):
            raw = data.get(field)
            if raw is not None:
                try:
                    val = float(raw)
                    if val > 0:
                        servings = val
                        break
                except (TypeError, ValueError):
                    logger.warning(
                        f"Recipe '{slug}' has non-numeric {field}: {raw!r}"
                    )

        # Extract ingredient strings
        ingredient_strings: list[str] = []
        for item in data.get("recipeIngredient", []):
            note = (item.get("note") or "").strip()
            display = (item.get("display") or "").strip()
            text = note or display
            if text:
                ingredient_strings.append(text)

        logger.info(
            f"Fetched recipe '{recipe_name}' from Mealie: "
            f"{len(ingredient_strings)} ingredients, {servings} servings"
        )
        return recipe_name, servings, ingredient_strings

    async def search_recipes(self, query: str) -> list[dict]:
        """Search Mealie recipes by keyword."""
        resp = await self._client.get(
            "/api/recipes",
            params={"search": query, "perPage": 20},
        )
        resp.raise_for_status()
        data = resp.json()
        items: list[dict] = data.get("items", [])
        logger.info(f"Mealie recipe search '{query}': {len(items)} results")
        return items

    async def close(self) -> None:
        await self._client.aclose()
