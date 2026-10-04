# Morrisons MCP Server

A self-hosted MCP (Model Context Protocol) server that scrapes Morrisons grocery data and exposes tools for product search, per-product label nutrition, and recipe costing. Answers questions like "how much will this recipe cost at Morrisons?" or "what does this product's label say per 100 g?"

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `search_products` | Search Morrisons products by keyword. Returns price, unit price, promotions, pack size, category. |
| `get_product_detail` | Full product detail: per-100 g/ml nutrition from the label, raw and parsed pack size, price, origin, storage. Returns `found: false` for a gone product. |
| `cost_recipe` | Cost a recipe from a list of ingredient strings. Returns total cost + per-ingredient breakdown. |
| `pick_products` | Product cards per ingredient (photo, name, size, price) rendered in the chat as an [MCP App](https://claude.com/docs/connectors/building/mcp-apps/quickstart); the user taps the product they buy and Submit posts the picks back as their message. Clients without MCP Apps get the same candidates as data. |

### `get_product_detail` response

| Field | Meaning |
|-------|---------|
| `retailer_product_id` | The ID asked for. |
| `found` | `false` when Morrisons has no product with this ID (404, empty payload, or a payload for a different product). Every other field is then empty. Not-found results are never cached. |
| `name`, `brand`, `price` | From the product payload. `price` is GBP. |
| `pack_size` | Raw `packSizeDescription`, e.g. `"6 x 330ml"`. |
| `net_quantity` | Parsed pack size `{value, unit}` with `unit` `g` or `ml`; multipacks are totalled (`"6 x 330ml"` → `{1980, "ml"}`). `null` when unparseable (`"Each"`, `"6 pack"`). |
| `nutrition_per_100g` | Figures from the label column headed per 100 g or per 100 ml only, never a per-serving column. `null` when the label has no per-100 column. |
| `nutrition_per_100g.basis` | `"100g"` or `"100ml"`. Drinks are usually `"100ml"`; the figures are then per 100 ml, not per 100 g. |
| `nutrition_per_100g.basis_note` | The per-100 column header's qualifier, lowercase: `"as consumed"`, `"as sold"`, `"prepared"` (also "when prepared", "made up"), `"cooked"`, `"raw"` (also "uncooked"), `"drained"`, or a bracketed cooking method such as `"grilled"` from "per 100g (grilled)". `null` when the header has none. Figures may be **as consumed (cooked)**: the whole chicken's label is "(as consumed) per 100g", so don't assume raw weight. |
| `nutrition_per_100g.*` | `energy_kj`, `energy_kcal`, `fat_g`, `saturates_g`, `carbohydrate_g`, `sugars_g`, `fibre_g`, `protein_g`, `salt_g`. Each is `null` if the label lacks it. `nil`/`trace` read as 0; `<0.1g` reads as half the bound (0.05). |
| `country_of_origin`, `storage`, `cooking_guidelines`, `features`, `servings_info`, `promotions` | Label text where present. |

Morrisons' product payload carries no GTIN/EAN, so none is returned.

### `pick_products` and the picker UI

`pick_products(ingredients, max_results=8)` takes food names without quantities (at most 15; 1–12 candidates each) and returns `{ ingredients: [{ ingredient, query, results: [ProductResult] }] }`. Every `ProductResult` carries `url` (`https://groceries.morrisons.com/products/<retailerProductId>`, which Morrisons redirects to the slug URL). The tool declares `_meta.ui.resourceUri = ui://morrisons/picker`; that resource is the picker page (`picker_html.py`, MIME `text/html;profile=mcp-app`). It loads the MCP Apps browser client from unpkg and product images from groceries.morrisons.com, both declared in its CSP. Submit calls `sendMessage` with one line per ingredient: `- <ingredient> → <name> (id <retailerProductId>, <url>)`, or `none of these` / `not chosen`. A failed search gives that ingredient an empty `results` list rather than failing the call.

---

## Setup

### Endpoint

The server speaks MCP over **Streamable HTTP** at `POST /mcp` on port 8000 (host `0.0.0.0`), in **stateless** mode: each request stands alone, so idle clients and server restarts don't break sessions. There is no SSE endpoint.

### Environment Variables

All optional. Copy `.env.example` to `.env` to set them.

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | Logging level (DEBUG/INFO/WARNING) | `INFO` |
| `CACHE_DB_PATH` | SQLite cache file | `/data/cache.db` |
| `MEALIE_URL`, `MEALIE_API_KEY` | Enable the Mealie pantry-staple check in `cost_recipe`. The check is off unless both are set. | unset |

### Docker (recommended)

```bash
docker compose up -d
```

The image (`ghcr.io/cometto2007/morrisons-mcp`, `linux/amd64` and `linux/arm64`) listens on port 8000 inside the container. Compose publishes no ports; Traefik routes to it, so MCP clients use `https://<traefik-host>/mcp`.

### Running Locally

```bash
# Install dependencies (Python 3.12+)
pip install -e ".[dev]"

# Start the server on http://0.0.0.0:8000/mcp
python -m morrisons_mcp.server
```

---

## Example Usage

**Search for products:**
```
search_products("chicken breast", max_results=5)
→ [
    { name: "Morrisons Chicken Breast Fillets 600g", price: 4.50, unit_price: "£7.50/kg", ... },
    ...
  ]
```

**Get nutrition data:**
```
get_product_detail("108444543")
→ { found: true, name: "Morrisons British Whole Chicken Medium 1.45kg", pack_size: "1.45kg",
    net_quantity: { value: 1450, unit: "g" },
    nutrition_per_100g: { basis: "100g", basis_note: "as consumed", energy_kcal: 177, protein_g: 27.3, fat_g: 7.5,
                          saturates_g: 2.1, carbohydrate_g: 0, sugars_g: 0, fibre_g: 0, salt_g: 0.2, ... }, ... }

get_product_detail("999999999999")
→ { retailer_product_id: "999999999999", found: false, name: null, ... }
```

**Cost a recipe:**
```
cost_recipe(
    ingredients=["500g chicken breast", "1 tin chopped tomatoes", "2 cloves garlic"],
    servings=4,
    recipe_name="Chicken Tomato"
)
→ { total_cost: 6.25, cost_per_serving: 1.56, unmatched_count: 0, ingredients: [...] }
```

---

## Architecture

```
MCP client / gateway
    │  Streamable HTTP POST /mcp (MCP protocol)
    ▼
morrisons-mcp (FastMCP server :8000)
    ├── SessionManager      ← anonymous Morrisons cookies
    ├── MorrisonClient      ← search + BOP endpoints
    │   └── ProductCache    ← SQLite TTL cache
    ├── IngredientParser    ← "500g chicken breast" → structured data
    ├── FuzzyMatcher        ← rapidfuzz token matching
    └── NutritionParser     ← BeautifulSoup HTML table parsing
```

---

## Caching

The server caches API responses in SQLite to reduce load on the Morrisons website:

| Cache type | TTL | Cache key |
|------------|-----|-----------|
| Search results | 1 hour (3600s) | `search:{normalised_query}` |
| Product BOP/nutrition | 24 hours (86400s) | `bop_v3:{retailerProductId}` (found products only) |

The SQLite database is stored at `/data/cache.db`, backed by a Docker named volume (`morrisons_data`) for persistence across container restarts.

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Optional live check against the real Morrisons API (3 requests)
MORRISONS_LIVE=1 pytest tests/test_live_morrisons.py
```

### Project Structure

```
src/morrisons_mcp/
├── server.py            # FastMCP app + 3 tool definitions
├── morrison_client.py   # Morrisons search + BOP API client
├── session_manager.py   # Cookie/session acquisition + refresh
├── cache.py             # SQLite async cache (aiosqlite)
├── ingredient_parser.py # "500g chicken breast" → ParsedIngredient
├── fuzzy_matcher.py     # Match ingredients to products (rapidfuzz)
├── mealie_client.py     # Optional Mealie pantry-staple check (cost_recipe)
├── nutrition_parser.py  # Per-100 g/ml label parsing + net quantity
└── models.py            # All Pydantic data models
```
