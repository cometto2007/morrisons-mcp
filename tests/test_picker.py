"""pick_products: an MCP App (tool + ui:// resource) the gateway can pass through."""
import json

import pytest
from starlette.testclient import TestClient

from morrisons_mcp.models import ProductResult
from morrisons_mcp.server import PICKER_URI, _picker_query, create_http_app, pick_products

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(client: TestClient, method: str, params: dict, id_: int) -> dict:
    resp = client.post("/mcp", headers=HEADERS,
                       json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
    assert resp.status_code == 200, resp.text
    if resp.headers["content-type"].startswith("text/event-stream"):
        data = [l[5:].strip() for l in resp.text.splitlines() if l.startswith("data:")]
        return json.loads(data[-1])
    return resp.json()


def test_product_url_is_the_short_morrisons_link():
    p = ProductResult(product_id="x", retailer_product_id="105415501", name="t", price=0.45)
    assert p.url == "https://groceries.morrisons.com/products/105415501"
    # survives the cache round trip (dump -> validate)
    assert ProductResult.model_validate(p.model_dump()).url == p.url


def test_picker_query_keeps_prep_words_and_applies_rewrites():
    assert _picker_query("  chopped   tomatoes ") == "chopped tomatoes"
    assert _picker_query("green peas") == "garden peas"


def test_tool_advertises_ui_and_resource_serves_mcp_app_html(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DB_PATH", str(tmp_path / "cache.db"))
    with TestClient(create_http_app()) as client:
        tools = _rpc(client, "tools/list", {}, 1)["result"]["tools"]
        picker = next(t for t in tools if t["name"] == "pick_products")
        assert picker["_meta"]["ui"]["resourceUri"] == PICKER_URI

        res = _rpc(client, "resources/read", {"uri": PICKER_URI}, 2)["result"]["contents"][0]
        assert res["mimeType"] == "text/html;profile=mcp-app"
        assert "sendMessage" in res["text"] and "ontoolresult" in res["text"]

        listed = _rpc(client, "resources/list", {}, 3)["result"]["resources"]
        meta = next(r for r in listed if r["uri"] == PICKER_URI)["_meta"]["ui"]
        assert "https://groceries.morrisons.com" in meta["csp"]["resourceDomains"]


class _FakeMorrison:
    def __init__(self):
        self.queries = []

    async def search(self, query, max_results=20):
        self.queries.append((query, max_results))
        if query == "boom":
            raise RuntimeError("Morrisons down")
        return [ProductResult(product_id=f"{query}-{i}", retailer_product_id=str(100000 + i),
                              name=f"{query} {i}", price=1.0 + i) for i in range(max_results)]


class _Ctx:
    def __init__(self, morrison):
        self.lifespan_context = {"morrison": morrison}


@pytest.mark.asyncio
async def test_pick_products_groups_candidates_per_ingredient():
    fake = _FakeMorrison()
    result = await pick_products(["chopped tomatoes", "boom", "green peas"], _Ctx(fake), max_results=3)
    rows = result.ingredients
    assert [r.ingredient for r in rows] == ["chopped tomatoes", "boom", "green peas"]
    assert rows[2].query == "garden peas"
    assert len(rows[0].results) == 3 and rows[0].results[0].url.endswith("/products/100000")
    assert rows[1].results == []  # a failed search is an empty row, not an error
    assert fake.queries[0] == ("chopped tomatoes", 3)


@pytest.mark.asyncio
async def test_pick_products_caps_ingredients_and_results():
    fake = _FakeMorrison()
    result = await pick_products([f"food {i}" for i in range(20)], _Ctx(fake), max_results=50)
    assert len(result.ingredients) == 15
    assert fake.queries[0][1] == 12
