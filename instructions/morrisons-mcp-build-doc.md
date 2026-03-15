# Morrisons MCP Server — Build Document

> **Purpose**: This document is a complete specification for Claude CLI / Claude Code to build the `morrisons-mcp` project from scratch. Follow it section-by-section, implementing each file fully before moving on. Do not skip or stub any section.

---

## 1. Project Overview

Build a self-hosted MCP (Model Context Protocol) server that scrapes Morrisons grocery data and exposes tools for product search, recipe costing, and nutrition analysis. It integrates with an existing Mealie instance for recipe ingredient sourcing.

**Key characteristics:**
- Python 3.12+ with FastMCP (the `fastmcp` package from PyPI, v3.x — the standalone Prefect-maintained one)
- Async throughout (httpx for HTTP, aiosqlite for caching)
- Dockerised for deployment on Unraid
- Exposed via Traefik reverse proxy + Cloudflare Tunnel (SSE transport for Claude.ai compatibility)
- Phase 1 only: anonymous endpoints (search, BOP/nutrition). Phase 2 (cart/auth) is out of scope for this build.

---

## 2. Directory Structure

Create this exact structure:

```
morrisons-mcp/
├── src/
│   └── morrisons_mcp/
│       ├── __init__.py
│       ├── server.py              # FastMCP app, tool definitions, lifespan
│       ├── morrison_client.py     # HTTP client for Morrisons API endpoints
│       ├── session_manager.py     # Cookie/session acquisition and refresh
│       ├── cache.py               # SQLite cache layer (aiosqlite)
│       ├── ingredient_parser.py   # Parse "500g chicken breast" into structured data
│       ├── fuzzy_matcher.py       # Match parsed ingredients to Morrisons search results
│       ├── nutrition_parser.py    # Parse BOP HTML nutrition tables with BeautifulSoup
│       ├── mealie_client.py       # HTTP client for Mealie API
│       └── models.py              # Pydantic models for all data types
├── tests/
│   ├── __init__.py
│   ├── test_ingredient_parser.py
│   ├── test_nutrition_parser.py
│   ├── test_fuzzy_matcher.py
│   └── test_models.py
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── README.md
└── .env.example
```

---

## 3. Dependencies (`pyproject.toml`)

```toml
[project]
name = "morrisons-mcp"
version = "0.1.0"
description = "MCP server for Morrisons grocery product search, recipe costing, and nutrition analysis"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "fastmcp>=3.0.0",
    "httpx>=0.27.0",
    "beautifulsoup4>=4.12.0",
    "rapidfuzz>=3.6.0",
    "aiosqlite>=0.20.0",
    "pydantic>=2.0.0",
    "uvicorn>=0.30.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

---

## 4. Pydantic Models (`models.py`)

Define all data structures used across the project. Every tool should return Pydantic models (FastMCP auto-serialises them).

```python
from pydantic import BaseModel, Field
from typing import Optional

# --- Ingredient Parsing ---

class ParsedIngredient(BaseModel):
    """Result of parsing a raw ingredient string like '500g chicken breast'."""
    original: str = Field(description="The original ingredient string")
    quantity: Optional[float] = Field(None, description="Numeric quantity extracted")
    unit: Optional[str] = Field(None, description="Unit of measurement (g, kg, ml, l, tbsp, tsp, etc.)")
    name: str = Field(description="The ingredient name with quantity/unit stripped")
    search_query: str = Field(description="Cleaned query optimised for Morrisons search")


# --- Product Data ---

class Promotion(BaseModel):
    """A product promotion/offer."""
    description: str
    promo_price: Optional[float] = None
    expiry: Optional[str] = None

class ProductResult(BaseModel):
    """A single product from Morrisons search results."""
    product_id: str = Field(description="UUID product ID")
    retailer_product_id: str = Field(description="Numeric string ID used for BOP endpoint")
    name: str
    brand: Optional[str] = None
    pack_size: Optional[str] = Field(None, description="e.g. '1kg', '6 pack'")
    price: float = Field(description="Current price in GBP")
    unit_price: Optional[str] = Field(None, description="e.g. '£3.50/kg'")
    promotions: list[Promotion] = Field(default_factory=list)
    category_path: Optional[str] = Field(None, description="e.g. 'Meat & Poultry > Chicken > Breast'")
    available: bool = True
    image_url: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None


# --- Nutrition ---

class NutritionPer100g(BaseModel):
    """Nutritional values per 100g parsed from BOP HTML table."""
    energy_kj: Optional[float] = None
    energy_kcal: Optional[float] = None
    fat_g: Optional[float] = None
    saturates_g: Optional[float] = None
    carbohydrate_g: Optional[float] = None
    sugars_g: Optional[float] = None
    fibre_g: Optional[float] = None
    protein_g: Optional[float] = None
    salt_g: Optional[float] = None


class ProductDetail(BaseModel):
    """Full product detail from BOP endpoint."""
    retailer_product_id: str
    name: str
    brand: Optional[str] = None
    pack_size: Optional[str] = None
    price: Optional[float] = None
    nutrition_per_100g: Optional[NutritionPer100g] = None
    country_of_origin: Optional[str] = None
    storage: Optional[str] = None
    cooking_guidelines: Optional[str] = None
    features: Optional[str] = None
    servings_info: Optional[str] = None
    promotions: list[Promotion] = Field(default_factory=list)


# --- Recipe Costing ---

class IngredientCost(BaseModel):
    """Cost breakdown for a single ingredient."""
    ingredient: str = Field(description="Original ingredient string from recipe")
    parsed_query: str = Field(description="What was searched on Morrisons")
    matched_product: Optional[ProductResult] = None
    match_confidence: Optional[float] = Field(None, description="0.0 to 1.0 fuzzy match score")
    cost: Optional[float] = Field(None, description="Price of matched product in GBP")
    note: Optional[str] = Field(None, description="e.g. 'No match found', 'Chose cheapest per-unit'")

class RecipeCostResult(BaseModel):
    """Complete recipe costing result."""
    recipe_name: Optional[str] = None
    servings: Optional[float] = None
    ingredients: list[IngredientCost]
    total_cost: float = Field(description="Sum of matched ingredient costs in GBP")
    cost_per_serving: Optional[float] = None
    unmatched_count: int = Field(description="Number of ingredients with no match")


# --- Recipe Nutrition ---

class IngredientNutrition(BaseModel):
    """Nutrition data for a single matched ingredient."""
    ingredient: str
    matched_product: Optional[str] = None
    pack_size: Optional[str] = None
    nutrition_per_100g: Optional[NutritionPer100g] = None
    estimated_weight_g: Optional[float] = Field(None, description="Estimated weight used from recipe")
    estimated_kcal: Optional[float] = None
    estimated_protein_g: Optional[float] = None
    estimated_fat_g: Optional[float] = None
    estimated_carbs_g: Optional[float] = None

class RecipeNutritionResult(BaseModel):
    """Complete nutrition analysis for a recipe."""
    recipe_name: Optional[str] = None
    servings: Optional[float] = None
    ingredients: list[IngredientNutrition]
    total_kcal: Optional[float] = None
    total_protein_g: Optional[float] = None
    total_fat_g: Optional[float] = None
    total_carbs_g: Optional[float] = None
    per_serving_kcal: Optional[float] = None
    per_serving_protein_g: Optional[float] = None
    per_serving_fat_g: Optional[float] = None
    per_serving_carbs_g: Optional[float] = None
```

---

## 5. Session Manager (`session_manager.py`)

Handles acquiring and refreshing anonymous Morrisons session cookies.

**Key requirements:**
- Use `httpx.AsyncClient` to GET `https://groceries.morrisons.com/` and capture the `set-cookie` headers
- Extract three cookies: `global_sid`, `AWSALB` (or `AWSALBCORS`), `VISITORID`
- Store them in a dict and provide them to all subsequent API calls
- Auto-refresh: if any API call returns non-200 (especially 401/403), discard old cookies and re-acquire
- Use a single consistent User-Agent: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36`
- Add 0.5s delay between requests (use `asyncio.sleep`) to avoid rate limiting
- Thread-safe: use `asyncio.Lock` to prevent concurrent session refreshes

```python
import asyncio
import time
import logging
import httpx

logger = logging.getLogger(__name__)

MORRISONS_HOME = "https://groceries.morrisons.com/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "accept": "application/json; charset=utf-8",
    "user-agent": USER_AGENT,
    "ecom-request-source": "web",
}
REQUEST_DELAY = 0.5  # seconds between requests


class SessionManager:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(15.0),
            headers={"user-agent": USER_AGENT},
        )
        self._cookies: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._last_request_time: float = 0.0

    async def _rate_limit(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < REQUEST_DELAY:
            await asyncio.sleep(REQUEST_DELAY - elapsed)
        self._last_request_time = time.monotonic()

    async def acquire_session(self) -> None:
        async with self._lock:
            logger.info("Acquiring new Morrisons session cookies...")
            await self._rate_limit()
            resp = await self._client.get(MORRISONS_HOME)
            resp.raise_for_status()
            self._cookies = {}
            for name in ("global_sid", "AWSALB", "AWSALBCORS", "VISITORID"):
                val = resp.cookies.get(name)
                if val:
                    self._cookies[name] = val
            logger.info(f"Session acquired. Cookies: {list(self._cookies.keys())}")

    async def get_cookies(self) -> dict[str, str]:
        if not self._cookies:
            await self.acquire_session()
        return dict(self._cookies)

    async def refresh_session(self) -> None:
        self._cookies = {}
        await self.acquire_session()

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_data: dict | None = None,
        extra_headers: dict | None = None,
    ) -> httpx.Response:
        """Make an authenticated request with auto-retry on session expiry."""
        cookies = await self.get_cookies()
        headers = {**DEFAULT_HEADERS, **(extra_headers or {})}

        await self._rate_limit()
        resp = await self._client.request(
            method, url, params=params, json=json_data,
            cookies=cookies, headers=headers,
        )

        # If session expired, refresh and retry once
        if resp.status_code in (401, 403, 440):
            logger.warning(f"Session expired (HTTP {resp.status_code}). Refreshing...")
            await self.refresh_session()
            cookies = await self.get_cookies()
            await self._rate_limit()
            resp = await self._client.request(
                method, url, params=params, json=json_data,
                cookies=cookies, headers=headers,
            )

        return resp

    async def close(self) -> None:
        await self._client.aclose()
```

---

## 6. Morrison Client (`morrison_client.py`)

Wraps the Morrisons API endpoints. Uses `SessionManager` for all requests and `ProductCache` for caching.

### Search Endpoint

- URL: `https://groceries.morrisons.com/api/webproductpagews/v6/product-pages/search`
- Params: `q`, `includeAdditionalPageInfo=true`, `maxPageSize=300`, `maxProductsToDecorate=30`, `tag=web`
- Returns JSON with product groups. The response structure has a `productGroups` array, each containing a `products` array of decorated product objects.

**Parsing search results — extract from each product object:**
```
name                       → product["name"]
brand                      → product.get("brand")
packSizeDescription        → product.get("packSizeDescription")
price                      → product["price"]["current"] (float, in pence — divide by 100)
                             OR check if price is already in pounds (inspect actual response)
unitPrice                  → product.get("unitPriceDescription")  
productId                  → product["productId"]  (UUID string)
retailerProductId          → product["retailerProductId"]  (numeric string)
promotions                 → product.get("promotions", []) — each has "description", optionally "price"
categoryPath               → join product.get("categoryPath", []) with " > "
available                  → product.get("available", True)
image                      → product.get("image") — may be a URL or object with "small"/"medium"/"large"
rating                     → product.get("rating", {}).get("average")
reviewCount                → product.get("rating", {}).get("count")
```

**IMPORTANT**: The exact JSON structure should be verified at build time. The field names above are based on reverse-engineering and may need minor adjustments. Log the raw response for the first few requests so you can validate. Use `.get()` everywhere with sensible defaults to handle missing fields gracefully.

### BOP Endpoint (Product Detail + Nutrition)

- URL: `https://groceries.morrisons.com/api/webproductpagews/v5/products/bop?retailerProductId={id}`
- Returns product detail including `bopData.fields` array

**Parsing BOP response:**
The `bopData.fields` is an array of objects, each with a `name` and `value` property. Iterate through and extract by name:

```
nutritionalData       → HTML string containing a <table> with nutrition per 100g
countryOfOrigin       → plain text
cookingGuidelines     → plain text or HTML
storageAndUsage       → plain text (also check "storage")
features              → plain text
otherInformation      → may contain servings info like "3-4 Servings"
manufacturer          → plain text
recyclingInformation  → plain text
```

Also check `bopPromotions` for `longDescription` (offer details with expiry).

**Implementation notes:**
- Check cache before making API calls
- Cache search results for 1 hour (3600 seconds)
- Cache BOP results for 24 hours (86400 seconds)
- The cache key for search should be the normalised search query
- The cache key for BOP should be the retailerProductId

```python
import logging
from .session_manager import SessionManager
from .cache import ProductCache
from .nutrition_parser import parse_nutrition_html
from .models import ProductResult, ProductDetail, Promotion, NutritionPer100g

logger = logging.getLogger(__name__)

SEARCH_URL = "https://groceries.morrisons.com/api/webproductpagews/v6/product-pages/search"
BOP_URL = "https://groceries.morrisons.com/api/webproductpagews/v5/products/bop"


class MorrisonClient:
    def __init__(self, cache: ProductCache) -> None:
        self.session = SessionManager()
        self.cache = cache

    async def search(self, query: str, max_results: int = 20) -> list[ProductResult]:
        """Search Morrisons products. Returns up to max_results products."""
        # Check cache first
        cache_key = f"search:{query.lower().strip()}"
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return [ProductResult.model_validate(p) for p in cached]

        params = {
            "q": query,
            "includeAdditionalPageInfo": "true",
            "maxPageSize": "300",
            "maxProductsToDecorate": "30",
            "tag": "web",
        }

        resp = await self.session.request("GET", SEARCH_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

        products = []
        # Products can appear in multiple groups — deduplicate by productId
        seen_ids = set()

        for group in data.get("productGroups", []):
            for product in group.get("products", []):
                pid = product.get("productId")
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)

                # Parse promotions
                promos = []
                for p in product.get("promotions", []):
                    promos.append(Promotion(
                        description=p.get("description", ""),
                        promo_price=p.get("price"),
                    ))

                # Parse category path
                cat_path = product.get("categoryPath")
                if isinstance(cat_path, list):
                    cat_path = " > ".join(str(c) for c in cat_path)

                # Parse image URL
                image = product.get("image")
                if isinstance(image, dict):
                    image = image.get("medium") or image.get("small") or image.get("large")

                # Parse price — inspect actual response to confirm structure
                price_data = product.get("price", {})
                if isinstance(price_data, dict):
                    price = price_data.get("current", 0)
                elif isinstance(price_data, (int, float)):
                    price = price_data
                else:
                    price = 0

                rating_data = product.get("rating", {}) or {}

                products.append(ProductResult(
                    product_id=pid,
                    retailer_product_id=str(product.get("retailerProductId", "")),
                    name=product.get("name", "Unknown"),
                    brand=product.get("brand"),
                    pack_size=product.get("packSizeDescription"),
                    price=float(price),
                    unit_price=product.get("unitPriceDescription"),
                    promotions=promos,
                    category_path=cat_path,
                    available=product.get("available", True),
                    image_url=image if isinstance(image, str) else None,
                    rating=rating_data.get("average"),
                    review_count=rating_data.get("count"),
                ))

        products = products[:max_results]

        # Cache results
        await self.cache.set(cache_key, [p.model_dump() for p in products], ttl=3600)

        return products

    async def get_product_detail(self, retailer_product_id: str) -> ProductDetail:
        """Get full product detail including nutrition from BOP endpoint."""
        cache_key = f"bop:{retailer_product_id}"
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return ProductDetail.model_validate(cached)

        params = {"retailerProductId": retailer_product_id}
        resp = await self.session.request("GET", BOP_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

        # Extract fields from bopData
        fields = {}
        for field in data.get("bopData", {}).get("fields", []):
            name = field.get("name", "")
            value = field.get("value", "")
            fields[name] = value

        # Parse nutrition HTML
        nutrition = None
        nutrition_html = fields.get("nutritionalData", "")
        if nutrition_html:
            nutrition = parse_nutrition_html(nutrition_html)

        # Parse promotions from bopPromotions
        promos = []
        for p in data.get("bopPromotions", []):
            promos.append(Promotion(
                description=p.get("longDescription", p.get("description", "")),
            ))

        # Extract servings from otherInformation
        servings_info = fields.get("otherInformation")

        detail = ProductDetail(
            retailer_product_id=retailer_product_id,
            name=data.get("name", "Unknown"),
            brand=data.get("brand"),
            pack_size=data.get("packSizeDescription"),
            price=data.get("price", {}).get("current") if isinstance(data.get("price"), dict) else data.get("price"),
            nutrition_per_100g=nutrition,
            country_of_origin=fields.get("countryOfOrigin"),
            storage=fields.get("storageAndUsage") or fields.get("storage"),
            cooking_guidelines=fields.get("cookingGuidelines"),
            features=fields.get("features"),
            servings_info=servings_info,
            promotions=promos,
        )

        # Cache for 24 hours
        await self.cache.set(cache_key, detail.model_dump(), ttl=86400)

        return detail

    async def close(self) -> None:
        await self.session.close()
```

---

## 7. Cache (`cache.py`)

SQLite-based async cache using `aiosqlite`. Stores JSON blobs with TTL.

**Schema:**
```sql
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at REAL NOT NULL
);
```

**Implementation requirements:**
- `get(key)` → returns parsed JSON or `None` if expired/missing. Also deletes expired entries on access.
- `set(key, value, ttl)` → stores JSON-serialised value with `expires_at = time.time() + ttl`
- `clear()` → deletes all entries
- `cleanup()` → deletes all expired entries (can be called periodically)
- DB file path configurable via constructor, default `/data/cache.db`
- Ensure the DB directory exists on init
- Use `json.dumps`/`json.loads` for serialisation

---

## 8. Ingredient Parser (`ingredient_parser.py`)

Parses raw ingredient strings from Mealie recipes into structured data for search.

**Input examples from Mealie** (these come from the `note` or `display` field of `recipeIngredient`):
```
"100 g, Chicken Breast Fillet"
"130 g Pot, Sticky Rice Pot"
"1 tablespoon, Soy sauce"
"0.50 medium, Avocado"
"3 spray 0.2ml, Sunflower Oil Spray"
"400g chopped tomatoes"
"2 cloves garlic"
"1 tin coconut milk"
"salt and pepper to taste"
```

**Parsing logic:**
1. Strip leading/trailing whitespace
2. Use regex to extract a leading quantity (int or float, including fractions like "1/2")
3. Match against known units: `g`, `kg`, `ml`, `l`, `litre`, `liter`, `tbsp`, `tablespoon`, `tsp`, `teaspoon`, `cup`, `oz`, `lb`, `clove`, `cloves`, `tin`, `can`, `bunch`, `pinch`, `spray`, `medium`, `large`, `small`, `piece`, `slice`, `pot`
4. The remainder after quantity+unit is the ingredient name
5. Clean up the name: strip leading commas, "of", articles, normalise whitespace
6. Generate `search_query`: the cleaned name, but also strip obvious non-searchable words. For example:
   - "Chicken Breast Fillet" → `"chicken breast fillet"`
   - "Sticky Rice Pot" → `"sticky rice"`  (remove "pot" as it's a container description)
   - "Soy sauce" → `"soy sauce"`
   - "salt and pepper to taste" → `"salt"` (mark as pantry staple, low priority for matching)

**Words to strip from search queries:** `to taste`, `for serving`, `for garnish`, `optional`, `approximately`, `about`, `fresh`, `dried` (but keep these in the `name` field)

Return a `ParsedIngredient` model.

---

## 9. Fuzzy Matcher (`fuzzy_matcher.py`)

Given a `ParsedIngredient` and a list of `ProductResult` from Morrisons search, find the best match.

**Matching strategy:**
1. Use `rapidfuzz.fuzz.token_sort_ratio` to compare the ingredient's `search_query` against each product's `name`
2. Score each product with a composite score:
   - `name_score` (0–100): fuzzy match between search query and product name
   - `category_bonus` (+10): if the product category path contains a relevant keyword (e.g. "Chicken" appears in category for a chicken ingredient)
   - `availability_penalty` (-50): if product is not available
   - `price_efficiency`: among products with `name_score > 60`, prefer cheapest per-unit price (minor tiebreaker)
3. Return the product with the highest composite score, along with confidence (normalised 0.0–1.0)
4. If the best score is below a threshold (e.g. composite < 40), return `None` (no confident match)

**Filtering rules (medium aggression):**
- "Chicken breast" should match: whole breast fillets, diced breast, mini breast fillets
- "Chicken breast" should NOT match: chicken thighs, chicken kievs, chicken nuggets, whole chickens
- Implementation: if the search query is multi-word, require that ALL significant words appear in the product name (case-insensitive). "Chicken breast" requires both "chicken" AND "breast" in the product name. Single-word queries like "avocado" can match more freely.

**Function signature:**
```python
def find_best_match(
    ingredient: ParsedIngredient,
    products: list[ProductResult],
    min_confidence: float = 0.4,
) -> tuple[ProductResult | None, float]:
    """Returns (best_match, confidence) or (None, 0.0) if no match meets threshold."""
```

---

## 10. Nutrition Parser (`nutrition_parser.py`)

Parses the HTML nutrition table from the BOP endpoint.

**The HTML structure** (from `bopData.fields` where `name == "nutritionalData"`) is a `<table>` with rows like:
```html
<tr>
  <td>Energy</td>
  <td>1234kJ / 295kcal</td>
</tr>
<tr>
  <td>Fat</td>
  <td>10.5g</td>
</tr>
```

**Implementation:**
- Use `BeautifulSoup` with `html.parser` (no lxml dependency needed)
- Find all `<tr>` elements, extract text from `<td>` cells
- Match row labels (case-insensitive) against known nutrients:
  - "energy" → extract both kJ and kcal values (they may appear as "1234kJ / 295kcal" or in separate rows)
  - "fat" → `fat_g`
  - "saturates" or "of which saturates" → `saturates_g`
  - "carbohydrate" → `carbohydrate_g`
  - "sugars" or "of which sugars" → `sugars_g`
  - "fibre" or "fiber" → `fibre_g`
  - "protein" → `protein_g`
  - "salt" → `salt_g`
- Extract numeric values using regex: `r"([\d.]+)\s*(?:kJ|kcal|g)"` — handle "less than 0.1g" as 0.05
- Return a `NutritionPer100g` model, with `None` for any missing values

```python
def parse_nutrition_html(html: str) -> NutritionPer100g | None:
    """Parse Morrisons BOP nutrition HTML table into structured data."""
```

---

## 11. Mealie Client (`mealie_client.py`)

HTTP client for the Mealie API to fetch recipe ingredients.

**Configuration (from env vars):**
- `MEALIE_URL`: base URL of the Mealie instance (e.g. `https://mealie.chrislab.it`)
- `MEALIE_API_KEY`: API key for authentication

**Endpoints used:**
- `GET /api/recipes/{slug}` → returns full recipe JSON
- Response structure includes `recipeIngredient` array where each item has:
  - `note`: string like "100 g, Chicken Breast Fillet" (this is the primary source of ingredient text)
  - `display`: string (similar to note, sometimes formatted slightly differently)
  - `quantity`: float (often 0.0 when embedded in note)
  - `unit`: object or null
  - `food`: object or null
- Also extract: `name`, `recipeServings` (float), `recipeYieldQuantity` (float)

**Implementation:**
- Use `httpx.AsyncClient` with bearer token auth: `Authorization: Bearer {api_key}`
- Method: `get_recipe_ingredients(slug: str) -> tuple[str, float | None, list[str]]` returning `(recipe_name, servings, ingredient_strings)`
- Extract ingredient strings from `note` field primarily, falling back to `display`
- Servings: use `recipeServings` if > 0, else `recipeYieldQuantity` if > 0, else `None`
- Also implement `search_recipes(query: str) -> list[dict]` for recipe discovery (hits `/api/recipes` with search param)

---

## 12. Server & Tool Definitions (`server.py`)

This is the main FastMCP application. Use the lifespan pattern to initialise shared clients.

### Lifespan

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from mcp.server.fastmcp import FastMCP
import os

@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    cache = ProductCache(db_path=os.getenv("CACHE_DB_PATH", "/data/cache.db"))
    morrison = MorrisonClient(cache=cache)
    mealie = MealieClient(
        base_url=os.getenv("MEALIE_URL", ""),
        api_key=os.getenv("MEALIE_API_KEY", ""),
    )
    try:
        yield {"morrison": morrison, "mealie": mealie, "cache": cache}
    finally:
        await morrison.close()

mcp = FastMCP(
    "Morrisons Grocery MCP",
    lifespan=app_lifespan,
)
```

### Tool 1: `search_products`

```python
@mcp.tool
async def search_products(query: str, max_results: int = 10) -> list[ProductResult]:
    """
    Search Morrisons grocery products by name or keyword.
    Returns products with price, unit price, promotions, pack size, and category.
    
    Args:
        query: Search term (e.g. "chicken breast", "olive oil", "chopped tomatoes")
        max_results: Maximum number of results to return (default 10, max 30)
    """
    ctx = mcp.get_context()
    morrison: MorrisonClient = ctx["morrison"]
    return await morrison.search(query, max_results=min(max_results, 30))
```

### Tool 2: `get_product_detail`

```python
@mcp.tool
async def get_product_detail(retailer_product_id: str) -> ProductDetail:
    """
    Get full product detail including nutrition from Morrisons.
    Uses the retailerProductId from search results (numeric string like "108444543").
    Returns nutrition per 100g (kcal, protein, fat, carbs, etc.), origin, storage, and cooking info.
    
    Args:
        retailer_product_id: The numeric retailer product ID from search results
    """
    ctx = mcp.get_context()
    morrison: MorrisonClient = ctx["morrison"]
    return await morrison.get_product_detail(retailer_product_id)
```

### Tool 3: `cost_recipe`

```python
@mcp.tool
async def cost_recipe(
    ingredients: list[str],
    servings: float | None = None,
    recipe_name: str | None = None,
) -> RecipeCostResult:
    """
    Cost a recipe by matching ingredient strings to Morrisons products.
    Takes a list of ingredient strings (e.g. ["500g chicken breast", "1 tin chopped tomatoes"])
    and returns the total cost plus per-ingredient breakdown.
    
    Args:
        ingredients: List of ingredient strings with quantities
        servings: Number of servings the recipe makes (for per-serving cost)
        recipe_name: Optional recipe name for labelling
    """
    ctx = mcp.get_context()
    morrison: MorrisonClient = ctx["morrison"]
    
    results = []
    total = 0.0
    unmatched = 0
    
    for ing_str in ingredients:
        parsed = parse_ingredient(ing_str)
        products = await morrison.search(parsed.search_query, max_results=20)
        match, confidence = find_best_match(parsed, products)
        
        cost = match.price if match else None
        if cost:
            total += cost
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
        unmatched_count=unmatched,
    )
```

### Tool 4: `cost_recipe_from_mealie`

```python
@mcp.tool
async def cost_recipe_from_mealie(recipe_slug: str) -> RecipeCostResult:
    """
    Pull a recipe from Mealie by slug and cost all its ingredients via Morrisons.
    Returns total cost, per-serving cost, and per-ingredient breakdown with matched products.
    
    Args:
        recipe_slug: The Mealie recipe slug (e.g. "chicken-poke", "spaghetti-bolognese")
    """
    ctx = mcp.get_context()
    mealie: MealieClient = ctx["mealie"]
    
    name, servings, ingredient_strings = await mealie.get_recipe_ingredients(recipe_slug)
    
    # Reuse cost_recipe logic (call internal function, not the tool itself)
    return await _cost_recipe_internal(
        ctx["morrison"], ingredient_strings, servings=servings, recipe_name=name
    )
```

Note: extract the core logic of `cost_recipe` into a shared `_cost_recipe_internal` async function that both `cost_recipe` and `cost_recipe_from_mealie` call.

### Tool 5: `get_recipe_nutrition`

```python
@mcp.tool
async def get_recipe_nutrition(
    ingredients: list[str],
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
    ctx = mcp.get_context()
    morrison: MorrisonClient = ctx["morrison"]
    
    results = []
    total_kcal = 0.0
    total_protein = 0.0
    total_fat = 0.0
    total_carbs = 0.0
    
    for ing_str in ingredients:
        parsed = parse_ingredient(ing_str)
        products = await morrison.search(parsed.search_query, max_results=20)
        match, confidence = find_best_match(parsed, products)
        
        ing_nutrition = IngredientNutrition(ingredient=ing_str)
        
        if match and confidence >= 0.4:
            # Fetch BOP detail for nutrition
            detail = await morrison.get_product_detail(match.retailer_product_id)
            ing_nutrition.matched_product = match.name
            ing_nutrition.pack_size = match.pack_size
            ing_nutrition.nutrition_per_100g = detail.nutrition_per_100g
            
            # Estimate actual nutrition based on recipe quantity
            weight_g = parsed.quantity  # crude: assume quantity is in grams if unit is g/kg/ml
            if parsed.unit in ("kg",):
                weight_g = (parsed.quantity or 0) * 1000
            elif parsed.unit in ("g", "ml"):
                weight_g = parsed.quantity
            elif parsed.unit is None and parsed.quantity:
                weight_g = parsed.quantity  # best guess
            else:
                weight_g = None  # can't estimate
            
            ing_nutrition.estimated_weight_g = weight_g
            
            if weight_g and detail.nutrition_per_100g:
                n = detail.nutrition_per_100g
                factor = weight_g / 100.0
                ing_nutrition.estimated_kcal = round(n.energy_kcal * factor, 1) if n.energy_kcal else None
                ing_nutrition.estimated_protein_g = round(n.protein_g * factor, 1) if n.protein_g else None
                ing_nutrition.estimated_fat_g = round(n.fat_g * factor, 1) if n.fat_g else None
                ing_nutrition.estimated_carbs_g = round(n.carbohydrate_g * factor, 1) if n.carbohydrate_g else None
                
                if ing_nutrition.estimated_kcal: total_kcal += ing_nutrition.estimated_kcal
                if ing_nutrition.estimated_protein_g: total_protein += ing_nutrition.estimated_protein_g
                if ing_nutrition.estimated_fat_g: total_fat += ing_nutrition.estimated_fat_g
                if ing_nutrition.estimated_carbs_g: total_carbs += ing_nutrition.estimated_carbs_g
        
        results.append(ing_nutrition)
    
    return RecipeNutritionResult(
        recipe_name=recipe_name,
        servings=servings,
        ingredients=results,
        total_kcal=round(total_kcal, 1) if total_kcal else None,
        total_protein_g=round(total_protein, 1) if total_protein else None,
        total_fat_g=round(total_fat, 1) if total_fat else None,
        total_carbs_g=round(total_carbs, 1) if total_carbs else None,
        per_serving_kcal=round(total_kcal / servings, 1) if servings and total_kcal else None,
        per_serving_protein_g=round(total_protein / servings, 1) if servings and total_protein else None,
        per_serving_fat_g=round(total_fat / servings, 1) if servings and total_fat else None,
        per_serving_carbs_g=round(total_carbs / servings, 1) if servings and total_carbs else None,
    )
```

### Running the Server

At the bottom of `server.py`:

```python
if __name__ == "__main__":
    import asyncio
    asyncio.run(
        mcp.run_async(transport="sse", host="0.0.0.0", port=8000)
    )
```

Note: We use SSE transport because Claude.ai connects to MCP servers via SSE. The URL will be `http://localhost:8000/sse`. When deployed behind Traefik, this becomes `https://morrisons-mcp.chrislab.it/sse`.

---

## 13. Docker Setup

### Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy project definition and install dependencies
COPY pyproject.toml .
RUN uv pip install --system --no-cache .

# Copy application code
COPY src/ ./src/

# Create data directory for cache
RUN mkdir -p /data

# Create non-root user
RUN useradd -m -u 1000 mcpuser && chown -R mcpuser:mcpuser /app /data
USER mcpuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/sse', timeout=5)" || exit 1

CMD ["python", "-m", "morrisons_mcp.server"]
```

### docker-compose.yml

```yaml
services:
  morrisons-mcp:
    build: .
    container_name: morrisons-mcp
    restart: unless-stopped
    ports:
      - "8045:8000"
    environment:
      - MEALIE_URL=${MEALIE_URL}
      - MEALIE_API_KEY=${MEALIE_API_KEY}
      - CACHE_DB_PATH=/data/cache.db
    volumes:
      - morrisons_data:/data
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.morrisons-mcp.rule=Host(`morrisons-mcp.chrislab.it`)"
      - "traefik.http.routers.morrisons-mcp.entrypoints=websecure"
      - "traefik.http.routers.morrisons-mcp.tls.certresolver=cloudflare"
      - "traefik.http.services.morrisons-mcp.loadbalancer.server.port=8000"
      # SSE-specific: disable buffering
      - "traefik.http.middlewares.morrisons-sse.headers.customresponseheaders.X-Accel-Buffering=no"
      - "traefik.http.routers.morrisons-mcp.middlewares=morrisons-sse"

volumes:
  morrisons_data:
```

### .env.example

```env
MEALIE_URL=https://mealie.chrislab.it
MEALIE_API_KEY=your_mealie_api_key_here
CACHE_DB_PATH=/data/cache.db
```

---

## 14. Tests

### `test_ingredient_parser.py`

Test cases:
```python
def test_simple_grams():
    result = parse_ingredient("500g chicken breast")
    assert result.quantity == 500
    assert result.unit == "g"
    assert "chicken breast" in result.name.lower()

def test_mealie_format_with_comma():
    result = parse_ingredient("100 g, Chicken Breast Fillet")
    assert result.quantity == 100
    assert result.unit == "g"
    assert "chicken breast fillet" in result.name.lower()

def test_tablespoon():
    result = parse_ingredient("1 tablespoon, Soy sauce")
    assert result.quantity == 1
    assert result.unit in ("tablespoon", "tbsp")
    assert "soy sauce" in result.name.lower()

def test_fractional():
    result = parse_ingredient("0.50 medium, Avocado")
    assert result.quantity == 0.5
    assert "avocado" in result.search_query.lower()

def test_no_quantity():
    result = parse_ingredient("salt and pepper to taste")
    assert result.quantity is None
    assert "salt" in result.search_query.lower()

def test_tin():
    result = parse_ingredient("1 tin coconut milk")
    assert result.quantity == 1
    assert result.unit == "tin"
    assert "coconut milk" in result.search_query.lower()
```

### `test_nutrition_parser.py`

Test with sample HTML:
```python
SAMPLE_HTML = """
<table>
<tr><td>Energy</td><td>1046kJ / 250kcal</td></tr>
<tr><td>Fat</td><td>3.0g</td></tr>
<tr><td>of which Saturates</td><td>0.7g</td></tr>
<tr><td>Carbohydrate</td><td>28.0g</td></tr>
<tr><td>of which Sugars</td><td>1.5g</td></tr>
<tr><td>Fibre</td><td>1.8g</td></tr>
<tr><td>Protein</td><td>27.0g</td></tr>
<tr><td>Salt</td><td>0.38g</td></tr>
</table>
"""

def test_parse_full_table():
    result = parse_nutrition_html(SAMPLE_HTML)
    assert result is not None
    assert result.energy_kcal == 250
    assert result.energy_kj == 1046
    assert result.protein_g == 27.0
    assert result.fat_g == 3.0
    assert result.carbohydrate_g == 28.0
    assert result.salt_g == 0.38
```

### `test_fuzzy_matcher.py`

Test the matching logic with mock product results:
```python
def test_chicken_breast_matches_breast():
    # Should match "Morrisons Chicken Breast Fillets" but not "Chicken Thighs"

def test_low_confidence_returns_none():
    # A query with no good matches should return None

def test_prefers_available_products():
    # Unavailable products should be penalised
```

---

## 15. README.md

Write a README covering:
1. What the project does (one paragraph)
2. MCP Tools exposed (brief table)
3. Setup: Docker build & run, env vars
4. Connecting to Claude.ai (add as MCP server URL)
5. Example usage (show sample tool calls and responses)
6. Architecture diagram (text-based)
7. Caching behaviour (TTLs, SQLite location)
8. Development: running locally, running tests

---

## 16. Key Implementation Notes

### Getting the lifespan context in tools
With FastMCP v3, use `mcp.get_context()` inside tool functions to access the lifespan state dict. Check the FastMCP docs if this pattern has changed — it may be `Context` injection via type hints instead:

```python
from fastmcp import Context

@mcp.tool
async def search_products(query: str, ctx: Context) -> list[ProductResult]:
    morrison = ctx["morrison"]
    ...
```

Verify which pattern works with the installed version.

### Error handling
- All tools should catch exceptions and return meaningful error messages rather than crashing
- If Morrisons API is down or cookies can't be acquired, return a clear error
- If Mealie is unreachable, `cost_recipe_from_mealie` should say so explicitly

### Logging
- Use Python's `logging` module throughout
- Log: session acquisition, cache hits/misses, search queries, match results, errors
- Log level configurable via `LOG_LEVEL` env var (default INFO)

### Transport
- Use SSE transport for compatibility with Claude.ai's MCP connector
- The server should be accessible at `/sse` path
- When deploying behind Traefik, ensure SSE buffering is disabled (see docker-compose labels)

---

## 17. Build Order

Implement in this order to allow incremental testing:

1. `models.py` — all Pydantic models
2. `cache.py` — SQLite cache
3. `session_manager.py` — Morrisons session/cookie management
4. `nutrition_parser.py` — HTML nutrition table parser
5. `ingredient_parser.py` — ingredient string parser
6. `morrison_client.py` — Morrisons API client
7. `fuzzy_matcher.py` — product matching logic
8. `mealie_client.py` — Mealie API client
9. `server.py` — FastMCP app with all 5 tools
10. Tests
11. `Dockerfile` + `docker-compose.yml`
12. `README.md`
13. `__init__.py` files (can be empty, just ensure packages are importable)

---

## 18. Verification Checklist

After building, verify:
- [ ] `python -m morrisons_mcp.server` starts without errors
- [ ] All 5 tools appear when connecting via MCP inspector
- [ ] `search_products("chicken breast")` returns results
- [ ] `get_product_detail("108444543")` returns nutrition data
- [ ] `cost_recipe(["500g chicken breast", "1 tin chopped tomatoes"])` returns costs
- [ ] Cache works: second call to same search is instant
- [ ] Session auto-refreshes on 401/403
- [ ] Docker build succeeds: `docker build -t morrisons-mcp .`
- [ ] Docker container starts and responds on port 8045
- [ ] All tests pass: `pytest tests/`
