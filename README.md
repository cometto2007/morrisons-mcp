# Morrisons MCP Server

A self-hosted MCP (Model Context Protocol) server that scrapes Morrisons grocery data and exposes tools for product search, recipe costing, and nutrition analysis. It integrates with an existing [Mealie](https://mealie.io) instance for recipe ingredient sourcing, enabling Claude.ai to answer questions like "how much will this recipe cost at Morrisons?" or "what are the macros for this meal plan?"

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `search_products` | Search Morrisons products by keyword. Returns price, unit price, promotions, pack size, category. |
| `get_product_detail` | Get full product detail including nutrition per 100g (kcal, protein, fat, carbs, salt, etc.). |
| `cost_recipe` | Cost a recipe from a list of ingredient strings. Returns total cost + per-ingredient breakdown. |
| `cost_recipe_from_mealie` | Pull a recipe from Mealie by slug and cost it automatically via Morrisons. |
| `get_recipe_nutrition` | Match recipe ingredients to Morrisons products and estimate total/per-serving nutrition. |

---

## Setup

### Environment Variables

Copy `.env.example` to `.env` and fill in:

```env
MEALIE_URL=https://mealie.chrislab.it
MEALIE_API_KEY=your_mealie_api_key_here
CACHE_DB_PATH=/data/cache.db
```

| Variable | Description | Default |
|----------|-------------|---------|
| `MEALIE_URL` | Base URL of your Mealie instance | — |
| `MEALIE_API_KEY` | Mealie API key (Settings → API Tokens) | — |
| `CACHE_DB_PATH` | Path for the SQLite cache database | `/data/cache.db` |
| `LOG_LEVEL` | Logging level (DEBUG/INFO/WARNING) | `INFO` |

### Docker (recommended)

```bash
# Build the image
docker build -t morrisons-mcp .

# Run with docker-compose
cp .env.example .env
# Edit .env with your values
docker compose up -d
```

The server will be available at `http://localhost:8045/sse`.

### Running Locally

```bash
# Install dependencies (Python 3.12+)
pip install -e ".[dev]"

# Create cache directory
mkdir -p /data

# Start the server
python -m morrisons_mcp.server
```

---

## Connecting to Claude.ai

1. In Claude.ai, go to **Settings → Integrations → Add MCP Server**
2. Enter the SSE URL: `https://morrisons-mcp.chrislab.it/sse`
3. The 5 tools will appear automatically

When running locally: `http://localhost:8045/sse`

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

**Cost a Mealie recipe:**
```
cost_recipe_from_mealie("chicken-poke")
→ { recipe_name: "Chicken Poke", total_cost: 8.40, cost_per_serving: 2.10, ... }
```

---

## Architecture

```
Claude.ai
    │  SSE (MCP protocol)
    ▼
Traefik (TLS termination + Cloudflare Tunnel)
    │
    ▼
morrisons-mcp (FastMCP server :8000)
    ├── SessionManager      ← anonymous Morrisons cookies
    ├── MorrisonClient      ← search + BOP endpoints
    │   └── ProductCache    ← SQLite TTL cache
    ├── IngredientParser    ← "500g chicken breast" → structured data
    ├── FuzzyMatcher        ← rapidfuzz token matching
    ├── NutritionParser     ← BeautifulSoup HTML table parsing
    └── MealieClient        ← recipe ingredient fetching
```

---

## Caching

The server caches API responses in SQLite to reduce load on the Morrisons website:

| Cache type | TTL | Cache key |
|------------|-----|-----------|
| Search results | 1 hour (3600s) | `search:{normalised_query}` |
| Product BOP/nutrition | 24 hours (86400s) | `bop:{retailerProductId}` |

The SQLite database is stored at `CACHE_DB_PATH` (default `/data/cache.db`), backed by a Docker named volume (`morrisons_data`) for persistence across container restarts.

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/

# Run tests with verbose output
pytest tests/ -v

# Run a specific test file
pytest tests/test_ingredient_parser.py -v
```

### Project Structure

```
src/morrisons_mcp/
├── server.py            # FastMCP app + 5 tool definitions
├── morrison_client.py   # Morrisons search + BOP API client
├── session_manager.py   # Cookie/session acquisition + refresh
├── cache.py             # SQLite async cache (aiosqlite)
├── ingredient_parser.py # "500g chicken breast" → ParsedIngredient
├── fuzzy_matcher.py     # Match ingredients to products (rapidfuzz)
├── nutrition_parser.py  # Parse BOP HTML nutrition tables
├── mealie_client.py     # Mealie recipe API client
└── models.py            # All Pydantic data models
```
