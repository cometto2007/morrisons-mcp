# Morrisons MCP Server

A self-hosted MCP (Model Context Protocol) server that scrapes Morrisons grocery data and exposes tools for product search, recipe costing, and nutrition analysis. Enables Claude.ai to answer questions like "how much will this recipe cost at Morrisons?" or "what are the macros for this meal plan?"

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `search_products` | Search Morrisons products by keyword. Returns price, unit price, promotions, pack size, category. |
| `get_product_detail` | Get full product detail including nutrition per 100g (kcal, protein, fat, carbs, salt, etc.). |
| `cost_recipe` | Cost a recipe from a list of ingredient strings. Returns total cost + per-ingredient breakdown. |
| `get_recipe_nutrition` | Match recipe ingredients to Morrisons products and estimate total/per-serving nutrition. |

---

## Setup

### Environment Variables

Optionally copy `.env.example` to `.env`. The only configurable variable is:

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | Logging level (DEBUG/INFO/WARNING) | `INFO` |

### Docker (recommended)

```bash
docker compose up -d
```

The server listens on port 8000. With Traefik it will be available at your configured hostname (e.g. `https://morrisons-mcp.chrislab.it/sse`).

### Running Locally

```bash
# Install dependencies (Python 3.12+)
pip install -e ".[dev]"

# Start the server
python -m morrisons_mcp.server
```

---

## Connecting to Claude.ai

1. In Claude.ai, go to **Settings → Integrations → Add MCP Server**
2. Enter the SSE URL: `https://morrisons-mcp.chrislab.it/sse`
3. The 4 tools will appear automatically

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
→ { name: "...", nutrition_per_100g: { energy_kcal: 165, protein_g: 31.0, fat_g: 3.6, ... }, ... }
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

**Get recipe nutrition:**
```
get_recipe_nutrition(
    ingredients=["500g chicken breast", "200g rice"],
    servings=4
)
→ { total_kcal: 1020.0, per_serving_kcal: 255.0, total_protein_g: 148.0, ... }
```

---

## Architecture

```
Claude.ai
    │  SSE (MCP protocol)
    ▼
Traefik (TLS termination)
    │
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
| Product BOP/nutrition | 24 hours (86400s) | `bop:{retailerProductId}` |

The SQLite database is stored at `/data/cache.db`, backed by a Docker named volume (`morrisons_data`) for persistence across container restarts.

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v
```

### Project Structure

```
src/morrisons_mcp/
├── server.py            # FastMCP app + 4 tool definitions
├── morrison_client.py   # Morrisons search + BOP API client
├── session_manager.py   # Cookie/session acquisition + refresh
├── cache.py             # SQLite async cache (aiosqlite)
├── ingredient_parser.py # "500g chicken breast" → ParsedIngredient
├── fuzzy_matcher.py     # Match ingredients to products (rapidfuzz)
├── nutrition_parser.py  # Parse BOP HTML nutrition tables
└── models.py            # All Pydantic data models
```
