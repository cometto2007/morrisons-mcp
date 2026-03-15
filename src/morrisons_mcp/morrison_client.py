import json
import logging
import re

from .session_manager import SessionManager
from .cache import ProductCache
from .nutrition_parser import parse_nutrition_html
from .models import ProductResult, ProductDetail, Promotion, NutritionPer100g

logger = logging.getLogger(__name__)

SEARCH_URL = "https://groceries.morrisons.com/api/webproductpagews/v6/product-pages/search"
BOP_URL = "https://groceries.morrisons.com/api/webproductpagews/v5/products/bop"

_SEARCH_TTL = 3600    # 1 hour
_BOP_TTL = 86400      # 24 hours


def _parse_price(price_data) -> float:
    """Parse Morrisons price object {'amount': '4.50', 'currency': 'GBP'} → 4.50."""
    if isinstance(price_data, dict):
        raw = price_data.get("amount")
        try:
            return float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            return 0.0
    if isinstance(price_data, (int, float)):
        return float(price_data)
    return 0.0


def _parse_unit_price(unit_price_data) -> str | None:
    """
    Parse unitPrice object:
      {'price': {'amount': '10.00', 'currency': 'GBP'}, 'unit': 'fop.price.per.kg'}
    → '£10.00/kg'
    """
    if not isinstance(unit_price_data, dict):
        return None
    try:
        amount = unit_price_data.get("price", {}).get("amount")
        unit = unit_price_data.get("unit", "")
        # Strip 'fop.price.per.' prefix to get the human unit
        unit = re.sub(r"^fop\.price\.per\.", "", unit)
        if amount:
            return f"£{amount}/{unit}" if unit else f"£{amount}"
    except Exception:
        pass
    return None


def _parse_image(image_data) -> str | None:
    """Parse image field: {'src': 'https://...'} or plain URL string."""
    if isinstance(image_data, dict):
        return image_data.get("src")
    if isinstance(image_data, str):
        return image_data
    return None


def _parse_product(product: dict) -> ProductResult | None:
    """Parse a single decorated product dict from the search response."""
    pid = product.get("productId")
    if not pid:
        return None

    # Parse promotions
    promos = []
    for p in product.get("promotions", []):
        desc = p.get("description", "")
        promo_price = None
        try:
            ep = p.get("equivalentPrice") or {}
            tp = ep.get("totalPrice") or {}
            raw = tp.get("amount")
            if raw:
                promo_price = float(raw)
        except (TypeError, ValueError):
            pass
        promos.append(Promotion(description=desc, promo_price=promo_price))

    # Parse category path
    cat_path = product.get("categoryPath")
    if isinstance(cat_path, list):
        cat_path = " > ".join(str(c) for c in cat_path)

    # Parse rating
    rating_data = product.get("ratingSummary") or {}
    rating = None
    try:
        r = rating_data.get("overallRating")
        if r is not None:
            rating = float(r)
    except (TypeError, ValueError):
        pass

    return ProductResult(
        product_id=pid,
        retailer_product_id=str(product.get("retailerProductId", "")),
        name=product.get("name", "Unknown"),
        brand=product.get("brand"),
        pack_size=product.get("packSizeDescription"),
        price=_parse_price(product.get("price")),
        unit_price=_parse_unit_price(product.get("unitPrice")),
        promotions=promos,
        category_path=cat_path,
        available=product.get("available", True),
        image_url=_parse_image(product.get("image")),
        rating=rating,
        review_count=rating_data.get("count"),
    )


class MorrisonClient:
    def __init__(self, cache: ProductCache) -> None:
        self.session = SessionManager()
        self.cache = cache

    async def search(self, query: str, max_results: int = 20) -> list[ProductResult]:
        """Search Morrisons products. Returns up to max_results products."""
        cache_key = f"search:{query.lower().strip()}"
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return [ProductResult.model_validate(p) for p in cached[:max_results]]

        params = {
            "q": query,
            "includeAdditionalPageInfo": "true",
            "maxPageSize": "300",
            "maxProductsToDecorate": "30",
            "tag": "web",
        }

        resp = await self.session.request("GET", SEARCH_URL, params=params)
        resp.raise_for_status()

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error(
                f"Non-JSON search response (status={resp.status_code}): {resp.text[:200]}"
            )
            raise RuntimeError(
                "Morrisons returned an unexpected response. Try again shortly."
            ) from exc

        products: list[ProductResult] = []
        seen_ids: set[str] = set()

        for group in data.get("productGroups", []):
            # API uses 'decoratedProducts' as the product array key
            for product in group.get("decoratedProducts", []):
                pid = product.get("productId")
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)
                parsed = _parse_product(product)
                if parsed:
                    products.append(parsed)

        await self.cache.set(cache_key, [p.model_dump() for p in products], ttl=_SEARCH_TTL)

        logger.info(
            f"Search '{query}': {len(products)} products found "
            f"(returning {min(max_results, len(products))})"
        )
        return products[:max_results]

    async def get_product_detail(self, retailer_product_id: str) -> ProductDetail:
        """Get full product detail including nutrition from BOP endpoint."""
        cache_key = f"bop:{retailer_product_id}"
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return ProductDetail.model_validate(cached)

        params = {"retailerProductId": retailer_product_id}
        resp = await self.session.request("GET", BOP_URL, params=params)
        resp.raise_for_status()

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error(
                f"Non-JSON BOP response for {retailer_product_id} "
                f"(status={resp.status_code}): {resp.text[:200]}"
            )
            raise RuntimeError(
                f"Morrisons returned an unexpected response for product {retailer_product_id}."
            ) from exc

        # BOP wraps the core product under 'product'
        prod = data.get("product") or {}

        # BOP fields use 'title' / 'content' (not 'name' / 'value')
        fields: dict[str, str] = {}
        for field in data.get("bopData", {}).get("fields", []):
            title = field.get("title") or field.get("name") or ""
            content = field.get("content") or field.get("value") or ""
            if title:
                fields[title] = content

        # Parse nutrition HTML (look for a field whose content contains a <table>)
        nutrition: NutritionPer100g | None = None
        for content in fields.values():
            if "<table" in content.lower():
                nutrition = parse_nutrition_html(content)
                if nutrition:
                    break

        # Parse promotions from bopPromotions
        promos = []
        for p in data.get("bopPromotions", []):
            promos.append(Promotion(
                description=p.get("longDescription") or p.get("description") or "",
            ))

        # Parse price from the nested product object
        price_raw = prod.get("price")
        price = _parse_price(price_raw) if price_raw else None

        detail = ProductDetail(
            retailer_product_id=retailer_product_id,
            name=prod.get("name", "Unknown"),
            brand=prod.get("brand"),
            pack_size=prod.get("packSizeDescription"),
            price=price,
            nutrition_per_100g=nutrition,
            country_of_origin=fields.get("Country of Origin") or fields.get("countryOfOrigin"),
            storage=fields.get("Storage") or fields.get("storageAndUsage") or fields.get("storage"),
            cooking_guidelines=fields.get("Cooking Guidelines") or fields.get("cookingGuidelines"),
            features=fields.get("Features") or fields.get("features"),
            servings_info=fields.get("Other Information") or fields.get("otherInformation"),
            promotions=promos,
        )

        await self.cache.set(cache_key, detail.model_dump(), ttl=_BOP_TTL)

        logger.info(f"BOP detail fetched for product {retailer_product_id}")
        return detail

    async def close(self) -> None:
        await self.session.close()
