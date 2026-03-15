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
        logger.info(f"Checking pantry staple: '{ingredient_name}' (enabled={self._enabled})")

        if not self._enabled:
            return False

        name_lower = ingredient_name.lower().strip()

        # Guard against trivially short strings that could fuzzy-match anything
        if len(name_lower) < 2:
            return False

        # v2 key intentionally busts any pre-fix-round stale cache entries
        # (old code could have cached "onion" → True incorrectly)
        cache_key = f"mealie_food_v2:{name_lower}"

        # Check cache first
        if self._cache:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return cached

        result = await self._check_mealie_food(name_lower)

        # Cache for 1 hour
        if self._cache:
            await self._cache.set(cache_key, result, ttl=3600)

        logger.info(
            f"Mealie pantry check '{ingredient_name}': "
            f"{'on_hand' if result else 'not on_hand'}"
        )
        return result

    async def _check_mealie_food(self, name_lower: str) -> bool:
        """Query Mealie foods API and check if any match is a household staple."""
        try:
            client = await self._ensure_client()
            resp = await client.get(
                "/api/foods",
                params={"search": name_lower, "perPage": 20},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"Mealie food lookup failed for '{name_lower}': {e}")
            return False

        items = data.get("items", [])
        logger.info(f"Mealie search '{name_lower}' returned {len(items)} foods")

        for item in items:
            food_name = (item.get("name") or "").lower().strip()
            households = item.get("householdsWithIngredientFood") or []

            # Exact match on food name — returns immediately (True or False)
            # so that "onion" does NOT fall through to match "onion powder".
            if food_name == name_lower:
                if households:
                    logger.debug(
                        f"  Exact name match '{item.get('name')}' "
                        f"households={households}"
                    )
                return bool(households)

            # Exact match on any alias
            for alias in (item.get("aliases") or []):
                alias_name = (
                    (alias.get("name") or "") if isinstance(alias, dict) else str(alias)
                ).lower().strip()
                if alias_name == name_lower:
                    if households:
                        logger.debug(
                            f"  Alias match '{alias_name}' on food '{item.get('name')}' "
                            f"households={households}"
                        )
                    return bool(households)

        return False

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
