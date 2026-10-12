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
        "iconAttributes": [{"label": "Vegetarian", "file": "vegetarian"}, {"label": "Vegan", "file": "vegan"}],
    },
    "bopData": {
        "fields": [
            {"title": "brand", "content": "Coca-Cola"},
            {"title": "ingredients", "content": "Carbonated Water, Sugar, Colour (Caramel E150d),<br />Flavourings including <strong>Caffeine</strong>"},
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
    assert await _cached_keys(client.cache) == ["bop_v4:100162517"]
    assert d.label_icons == ["Vegetarian", "Vegan"]


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


def _with_fields(ingredients=None, icon_attributes=()):
    fields = [f for f in BOP_OK["bopData"]["fields"] if f["title"] != "ingredients"]
    if ingredients is not None:
        fields.append({"title": "ingredients", "content": ingredients})
    return {**BOP_OK, "product": {**BOP_OK["product"], "iconAttributes": icon_attributes},
            "bopData": {"fields": fields}}


async def test_ingredients_keep_label_spacing_and_list_bold_allergens(client):
    # Real Morrisons markup: allergens in <b>, line breaks as <br />, entities
    html = ("Water, Yogurt Powder (<b>Milk</b>), Honey,<br />"
            "<b>Cashew Nuts</b>, Butter (<b>Milk</b>) &amp; <strong>Wheat</strong> Flour")
    client.session.request = AsyncMock(return_value=_resp(200, _with_fields(html)))
    d = await client.get_product_detail("100162517")
    assert d.ingredients == "Water, Yogurt Powder (Milk), Honey, Cashew Nuts, Butter (Milk) & Wheat Flour"
    assert d.allergens == ["Milk", "Cashew Nuts", "Wheat"]


async def test_unlabelled_product_has_no_ingredients(client):
    client.session.request = AsyncMock(return_value=_resp(200, _with_fields(None, [])))
    d = await client.get_product_detail("100162517")
    assert d.ingredients is None
    assert d.allergens == []
    assert d.label_icons == []


@pytest.mark.parametrize("icons", [5, "Vegan", None, [None, 3, {"label": 5}, {"label": {"x": 1}}, {"label": " Vegan "}]],
                         ids=["int", "string", "null", "mixed-list"])
async def test_label_icons_survive_untrusted_shapes(client, icons):
    client.session.request = AsyncMock(return_value=_resp(200, _with_fields("Tomato", icons)))
    d = await client.get_product_detail("100162517")
    assert d.label_icons == (["Vegan"] if isinstance(icons, list) else [])


# The BOP payload's `product` has the search result's shape (field names from a live
# response, 2026-10-12), so get_product returns what shop_products needs for the trolley.
BOP_CHICKEN = {
    "product": {
        "productId": "7ae952e7-ab87-41fb-a77a-92776a9fef3a",
        "retailerProductId": "108444543",
        "name": "Morrisons British Whole Chicken Medium 1.45kg",
        "brand": "Morrisons",
        "packSizeDescription": "1.45kg",
        "price": {"amount": "5.50", "currency": "GBP"},
        "unitPrice": {"price": {"amount": "3.79", "currency": "GBP"}, "unit": "fop.price.per.kg", "unitName": "PER_1KG"},
        "image": {"src": "https://groceries.morrisons.com/images-v3/a/b/300x300.jpg"},
        "available": True,
        "promotions": None,
    },
    "bopData": {"fields": []},
    "bopPromotions": [{"longDescription": "Now £4.25, Was £5.50"}],
}


async def test_get_product_reads_the_bop_product_like_a_search_result(client):
    client.session.request = AsyncMock(return_value=_resp(200, BOP_CHICKEN))
    p = await client.get_product("108444543")
    assert p.product_id == "7ae952e7-ab87-41fb-a77a-92776a9fef3a"
    assert (p.price, p.unit_price, p.pack_size) == (5.5, "£3.79/kg", "1.45kg")
    assert p.image_url.endswith("300x300.jpg") and p.available is True
    assert [x.description for x in p.promotions] == ["Now £4.25, Was £5.50"]  # null list, offers beside it
    assert await _cached_keys(client.cache) == ["product:108444543"]
    assert (await client.get_product("108444543")) == p
    assert client.session.request.await_count == 1  # second read came from the cache


async def test_get_product_gone_is_not_found_and_not_cached(client):
    client.session.request = AsyncMock(return_value=_resp(404, BOP_404))
    with pytest.raises(ProductNotFoundError):
        await client.get_product("999999999999")
    assert await _cached_keys(client.cache) == []


def test_unit_price_in_pence_reads_as_pence():
    from morrisons_mcp.morrison_client import _parse_unit_price

    each = {"price": {"amount": "45.8", "currency": "GBX"}, "unit": "fop.price.per.each", "unitName": "EACH"}
    kg = {"price": {"amount": "7.00", "currency": "GBP"}, "unit": "fop.price.per.kg", "unitName": "PER_1KG"}
    assert _parse_unit_price(each) == "45.8p/each"  # eggs at £2.75 for 6, not £45.80 each
    assert _parse_unit_price(kg) == "£7.00/kg"
