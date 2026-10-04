"""Smoke test: the served app speaks Streamable HTTP at POST /mcp, statelessly."""
import json

from starlette.testclient import TestClient

from morrisons_mcp.server import create_http_app

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(client: TestClient, method: str, params: dict, id_: int) -> dict:
    resp = client.post(
        "/mcp", headers=HEADERS,
        json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params},
    )
    assert resp.status_code == 200, resp.text
    if resp.headers["content-type"].startswith("text/event-stream"):
        data = [l[5:].strip() for l in resp.text.splitlines() if l.startswith("data:")]
        return json.loads(data[-1])
    return resp.json()


def test_initialize_and_list_tools_over_post_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DB_PATH", str(tmp_path / "cache.db"))
    with TestClient(create_http_app()) as client:
        init = _rpc(client, "initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0"},
        }, 1)
        assert init["result"]["serverInfo"]["name"] == "Morrisons Grocery MCP"

        # Stateless: no session id needs to be carried between requests
        tools = _rpc(client, "tools/list", {}, 2)
        names = {t["name"] for t in tools["result"]["tools"]}
        assert names == {"search_products", "get_product_detail", "cost_recipe"}

        # The SSE endpoint is gone
        assert client.get("/sse").status_code == 404
