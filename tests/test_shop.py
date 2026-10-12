"""shop_products: a shopping list MCP App. The trolley write happens in the user's browser
(userscript), so the server side is search + Usual-first ordering, tested with fakes."""
import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from fastmcp.exceptions import ToolError

from morrisons_mcp.models import ProductResult, ShopItem
from morrisons_mcp.morrison_client import MorrisonClient, ProductNotFoundError
from morrisons_mcp.server import SHOP_URI, create_http_app, morrisons_product_id, shop_products

from .test_picker import _rpc

TOMATO = "https://groceries.morrisons.com/products/morrisons-chopped-tomatoes-400g/"


@pytest.mark.parametrize("link,expected", [
    (TOMATO + "105415501", "105415501"),
    ("https://groceries.morrisons.com/products/morrisons-chopped-tomatoes-400g-105415501", "105415501"),
    ("https://groceries.morrisons.com/products/105415501/", "105415501"),
    (" 105415501 ", "105415501"),
    ("https://www.tesco.com/groceries/en-GB/products/254656543", None),
    ("https://evil-morrisons.com/products/105415501", None),
    ("javascript:alert(1)//groceries.morrisons.com/products/105415501", None),
    ("https://groceries.morrisons.com/search?q=105415501", None),
    ("12345", None),
])
def test_morrisons_product_id(link, expected):
    assert morrisons_product_id(link) == expected


def _p(rid: str, name: str = "", price: float = 1.0) -> ProductResult:
    return ProductResult(product_id=f"uuid-{rid}", retailer_product_id=rid, name=name or f"p{rid}", price=price)


class _FakeMorrison:
    def __init__(self, search=None, products=None, gone=(), failing=()):
        self.results, self.products = search or {}, products or {}
        self.gone, self.failing = set(gone), set(failing)
        self.searches, self.lookups = [], []

    async def search(self, query, max_results=20):
        self.searches.append(query)
        if query in self.failing:
            raise RuntimeError("Morrisons down")
        return self.results.get(query, [])[:max_results]

    async def get_product(self, rid):
        self.lookups.append(rid)
        if rid in self.gone:
            raise ProductNotFoundError(rid)
        if rid in self.failing:
            raise RuntimeError("Morrisons down")
        return self.products[rid]


class _Ctx:
    def __init__(self, morrison):
        self.lifespan_context = {"morrison": morrison}


def _ids(row):
    return [p.retailer_product_id for p in row.results]


async def test_usual_found_in_search_goes_first_without_a_lookup_and_rows_keep_order():
    fake = _FakeMorrison(search={
        "chopped tomatoes": [_p("100001"), _p("100002"), _p("100003")],
        "milk": [_p("200001")],
    })
    out = await shop_products([
        ShopItem(name="chopped tomatoes", links=[TOMATO + "100002"], quantity=2, amount="2 tins"),
        ShopItem(name="  milk "),
    ], _Ctx(fake))
    tomatoes, milk = out.items
    assert _ids(tomatoes) == ["100002", "100001", "100003"]
    assert tomatoes.usual_ids == ["100002"] and tomatoes.quantity == 2 and tomatoes.amount == "2 tins"
    assert milk.name == "milk" and milk.usual_ids == [] and _ids(milk) == ["200001"]
    assert fake.lookups == []


async def test_usual_missing_from_search_is_looked_up_and_main_stays_first():
    fake = _FakeMorrison(
        search={"chopped tomatoes": [_p("100001"), _p("100003")]},
        products={"300001": _p("300001", "Napolina Chopped Tomatoes")},
    )
    row = (await shop_products([ShopItem(name="chopped tomatoes", links=["300001", TOMATO + "100003"])], _Ctx(fake))).items[0]
    assert _ids(row) == ["300001", "100003", "100001"]
    assert row.usual_ids == ["300001", "100003"]
    assert fake.lookups == ["300001"]


async def test_gone_usual_is_reported_not_shown():
    fake = _FakeMorrison(search={"oats": [_p("100001")]}, gone={"400001"})
    row = (await shop_products([ShopItem(name="oats", links=["400001"])], _Ctx(fake))).items[0]
    assert row.gone_usual_ids == ["400001"] and row.usual_ids == [] and _ids(row) == ["100001"]


async def test_links_only_line_searches_by_its_main_products_name():
    fake = _FakeMorrison(
        products={"300001": _p("300001", "Napolina Chopped Tomatoes 400g")},
        search={"Napolina Chopped Tomatoes 400g": [_p("300001"), _p("100009")]},
    )
    row = (await shop_products([ShopItem(links=["300001"])], _Ctx(fake))).items[0]
    assert row.name == row.query == "Napolina Chopped Tomatoes 400g"
    assert _ids(row) == ["300001", "100009"] and row.usual_ids == ["300001"]
    assert fake.lookups == ["300001"]  # looked up once, not again after the search


async def test_failed_search_or_lookup_thins_the_row_instead_of_failing():
    fake = _FakeMorrison(products={"300001": _p("300001")}, failing={"rice", "500001"})
    row = (await shop_products([ShopItem(name="rice", links=["300001", "500001"])], _Ctx(fake))).items[0]
    assert _ids(row) == ["300001"] and row.usual_ids == ["300001"]
    assert row.gone_usual_ids == []  # an error is not "no longer sold"


async def test_other_shops_and_repeated_links_are_ignored():
    fake = _FakeMorrison(search={"beans": [_p("100001")]})
    links = ["https://www.tesco.com/groceries/en-GB/products/254656543", "100001",
             "https://groceries.morrisons.com/products/beans/100001"]
    row = (await shop_products([ShopItem(name="beans", links=links)], _Ctx(fake))).items[0]
    assert row.usual_ids == ["100001"] and _ids(row) == ["100001"]


async def test_line_with_only_other_shops_links_is_an_empty_row_not_a_failure():
    fake = _FakeMorrison(search={"milk": [_p("200001")]})
    out = await shop_products([ShopItem(links=["https://www.tesco.com/groceries/en-GB/products/254656543"]),
                               ShopItem(name="milk")], _Ctx(fake))
    tesco_only, milk = out.items
    assert tesco_only.results == [] and tesco_only.query == "" and tesco_only.name == "Item with no Morrisons link"
    assert _ids(milk) == ["200001"]  # the other lines still come back
    assert fake.searches == ["milk"] and fake.lookups == []


async def test_max_results_caps_alternatives_not_usuals():
    fake = _FakeMorrison(search={"tea": [_p(str(100000 + i)) for i in range(10)]})
    row = (await shop_products([ShopItem(name="tea", links=["100005"])], _Ctx(fake), max_results=2)).items[0]
    assert _ids(row) == ["100005", "100000", "100001"]
    row = (await shop_products([ShopItem(name="tea")], _Ctx(fake), max_results=50)).items[0]
    assert len(row.results) == 10  # capped at 12, and the search only had 10
    assert fake.searches[-1] == "tea"


async def test_refuses_too_many_items_before_searching_and_needs_name_or_link():
    fake = _FakeMorrison()
    with pytest.raises(ToolError, match="At most 40"):
        await shop_products([ShopItem(name=f"food {i}") for i in range(41)], _Ctx(fake))
    assert fake.searches == []
    with pytest.raises(ValidationError, match="name or at least one link"):
        ShopItem(name="  ")
    with pytest.raises(ValidationError):
        ShopItem(name="milk", quantity=0)


def test_tool_and_view_are_served_over_http(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DB_PATH", str(tmp_path / "cache.db"))
    fake = _FakeMorrison(search={"milk": [_p("200001"), _p("200002")]})
    monkeypatch.setattr(MorrisonClient, "search", fake.search)
    with TestClient(create_http_app()) as client:
        tools = _rpc(client, "tools/list", {}, 1)["result"]["tools"]
        shop = next(t for t in tools if t["name"] == "shop_products")
        assert shop["_meta"]["ui"]["resourceUri"] == SHOP_URI

        res = _rpc(client, "resources/read", {"uri": SHOP_URI}, 2)["result"]["contents"][0]
        assert res["mimeType"] == "text/html;profile=mcp-app"
        assert "#add-to-trolley=" in res["text"] and "openLink" in res["text"] and "__EXT_APPS_CLIENT__" not in res["text"]
        assert res["_meta"]["ui"]["csp"]["resourceDomains"] == ["https://unpkg.com", "https://groceries.morrisons.com"]

        out = _rpc(client, "tools/call", {"name": "shop_products", "arguments": {
            "items": [{"name": "milk", "links": ["200002"], "quantity": 2}]}}, 3)["result"]
        row = out["structuredContent"]["items"][0]
        assert row["usual_ids"] == ["200002"] and row["results"][0]["product_id"] == "uuid-200002"
        assert not out.get("isError")
