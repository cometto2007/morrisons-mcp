import logging
import os

import httpx

from .cache import ProductCache

logger = logging.getLogger(__name__)


class MealieClient:
    """Lightweight Mealie API client for pantry staple detection."""

    def __init__(self, cache: ProductCache | None = None) -> None:
        self._base_url = os.getenv("MEALIE_URL", "").rstrip("/")
        self._api_key = os.getenv("MEALIE_API_KEY", "")
        self._cache = cache
        self._client: httpx.AsyncClient | None = None
        self._enabled = bool(self._base_url and self._api_key)
        if not self._enabled:
            logger.info("Mealie integration disabled — MEALIE_URL or MEALIE_API_KEY not set")

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=10,
            )
        return self._client

    async def is_pantry_staple(self, ingredient_name: str) -> bool:
        """
        Check if an ingredient is marked as a household staple in Mealie.
        Returns True if the food's householdsWithIngredientFood is non-empty.
        """
        if not self._enabled:
            return False

        name_lower = ingredient_name.lower().strip()
        cache_key = f"mealie_food:{name_lower}"

        # Check cache first
        if self._cache:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return cached

        try:
            client = await self._ensure_client()
            resp = await client.get(
                "/api/foods",
                params={"search": ingredient_name, "perPage": 10},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"Mealie food lookup failed for '{ingredient_name}': {e}")
            return False

        items = data.get("items", [])
        result = False

        for item in items:
            food_name = (item.get("name") or "").lower()
            aliases = [a.get("name", "").lower() for a in item.get("aliases", [])]
            all_names = [food_name] + aliases

            # Check if any name/alias matches the ingredient
            if any(name_lower in n or n in name_lower for n in all_names if n):
                households = item.get("householdsWithIngredientFood", [])
                if households:
                    result = True
                    break

        # Cache for 1 hour
        if self._cache:
            await self._cache.set(cache_key, result, ttl=3600)

        logger.debug(f"Mealie pantry check '{ingredient_name}': {'on_hand' if result else 'not found'}")
        return result

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
