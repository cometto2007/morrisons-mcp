"""get_product_detail against a mocked Morrisons HTTP layer."""
from unittest.mock import AsyncMock

import httpx
import pytest

from morrisons_mcp.cache import ProductCache
from morrisons_mcp.morrison_client import MorrisonClient, ProductNotFoundError

from .test_nutrition_parser import PER_100ML_LABEL


def _resp(status: int, payload) -> httpx.Response:
    return httpx.Response(
        status, json=payload, request=httpx.Request("GET", "https://example.test/bop")
    )


BOP_OK = {
    "product": {
        "retailerProductId": "100162517",
        "name": "Coca-Cola Cherry 500ml",
        "brand": "Coca-Cola",
        "packSizeDescription": "500ml",
        "price": {"amount": "1.85", "currency": "GBP"},
    },
    "bopData": {
        "fields": [
            {"title": "brand", "content": "Coca-Cola"},
            # A non-nutrition field with a table must not be mistaken for the label
            {"title": "otherInformation", "content": "<table><tr><td>x</td><td>1g</td></tr></table>"},
            {"title": "nutritionalData", "content": PER_100ML_LABEL},
        ]
    },
    "bopPromotions": [],
}

BOP_404 = {
    "httpStatus": 404,
    "message": "No productId exists for retailerProductId 999999999999",
    "code": "product-page-ws-40",
}


@pytest.fixture
async def client(tmp_path):
    cache = ProductCache(db_path=str(tmp_path / "cache.db"))
    c = MorrisonClient(cache=cache)
    yield c
    await c.close()
    await cache.close()


async def _cached_keys(cache: ProductCache) -> list[str]:
    db = await cache._ensure_db()
    async with db.execute("SELECT key FROM cache") as cur:
        return [r[0] for r in await cur.fetchall()]


async def test_product_detail_parses_label_and_net_quantity(client):
    client.session.request = AsyncMock(return_value=_resp(200, BOP_OK))
    d = await client.get_product_detail("100162517")
    assert d.found is True
    assert d.name == "Coca-Cola Cherry 500ml"
    assert d.pack_size == "500ml"
    assert d.net_quantity.value == 500 and d.net_quantity.unit == "ml"
    n = d.nutrition_per_100g
    assert n.basis == "100ml"
    assert n.energy_kcal == 46 and n.sugars_g == 11.4 and n.salt_g == 0.01
    assert await _cached_keys(client.cache) == ["bop_v2:100162517"]


@pytest.mark.parametrize(
    "resp",
    [
        _resp(404, BOP_404),
        _resp(200, {}),
        _resp(200, {"product": {}, "bopData": {"fields": []}}),
        # A different product must never be returned for the requested ID
        _resp(200, {**BOP_OK, "product": {**BOP_OK["product"], "retailerProductId": "123"}}),
    ],
    ids=["http-404", "empty-body", "empty-product", "different-product"],
)
async def test_dead_product_is_not_found_and_not_cached(client, resp):
    client.session.request = AsyncMock(return_value=resp)
    with pytest.raises(ProductNotFoundError):
        await client.get_product_detail("999999999999")
    assert await _cached_keys(client.cache) == []

    # Asking again goes back to Morrisons rather than a cached "Unknown"
    with pytest.raises(ProductNotFoundError):
        await client.get_product_detail("999999999999")
    assert client.session.request.await_count == 2


async def test_server_error_is_not_not_found(client):
    client.session.request = AsyncMock(return_value=_resp(503, {}))
    with pytest.raises(RuntimeError, match="HTTP 503"):
        await client.get_product_detail("100162517")
    assert await _cached_keys(client.cache) == []


async def test_tool_returns_found_false_for_dead_product(client):
    from morrisons_mcp import server

    client.session.request = AsyncMock(return_value=_resp(404, BOP_404))
    ctx = type("Ctx", (), {"lifespan_context": {"morrison": client}})()
    fn = getattr(server.get_product_detail, "fn", server.get_product_detail)
    result = await fn("999999999999", ctx)
    assert result.found is False
    assert result.retailer_product_id == "999999999999"
    assert result.name is None and result.nutrition_per_100g is None
