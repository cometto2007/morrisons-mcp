"""The shopping-list view and the trolley userscript, in headless Chromium.

Nothing reaches Morrisons or unpkg: the tests' routes answer every request, so the real
view HTML and the real userscript run against a stand-in Morrisons that records the
trolley call. Needs the [browser] extra and `playwright install --only-shell chromium`.
"""
import json
import pathlib

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from morrisons_mcp.picker_html import EXT_APPS_CLIENT  # noqa: E402
from morrisons_mcp.shop_html import SHOP_HTML  # noqa: E402

USERSCRIPT = (pathlib.Path(__file__).parents[1] / "userscript" / "morrisons-trolley.user.js").read_text()
MORRISONS = "https://groceries.morrisons.com"
VIEW = "https://view.test/"
A, B, C = (f"{d * 8}-{d * 4}-4{d * 3}-8{d * 3}-{d * 12}" for d in "abc")
D, E = (f"{d * 8}-{d * 4}-4{d * 3}-8{d * 3}-{d * 12}" for d in "de")

# Stands in for the ext-apps client: exposes the app and records openLink.
FAKE_EXT_APPS = """export class App {
  constructor() { window.__app = this; }
  async connect() {}
  getHostContext() { return {}; }
  async requestDisplayMode() { return { mode: "inline" }; }
  async openLink({ url }) { window.__opened = url; return {}; }
}"""


def _product(uuid, rid, name, price, unit_price):
    return {"product_id": uuid, "retailer_product_id": rid, "name": name, "price": price, "unit_price": unit_price,
            "pack_size": "400g", "promotions": [], "available": True, "image_url": None}


LIST = {"items": [
    {"name": "chopped tomatoes", "query": "chopped tomatoes", "quantity": 2, "amount": "2 tins",
     "usual_ids": ["100000001"], "gone_usual_ids": [],
     "results": [_product(A, "100000001", "Usual Tomatoes", 0.55, "£1.38/kg"),
                 _product(B, "100000002", "Dear Tomatoes", 0.40, "£1.00/kg"),
                 _product(C, "100000003", "Cheap Tomatoes", 0.30, "£0.75/kg")]},
    {"name": "milk", "query": "milk", "quantity": 1, "amount": None, "usual_ids": [], "gone_usual_ids": [],
     "results": [_product(D, "200000001", "Big Milk", 1.45, "£0.64/litre"),
                 _product(E, "200000002", "Small Milk", 1.10, "£0.97/litre")]},
]}


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"no Chromium for Playwright: {e}")
        yield b
        b.close()


def _wait(page, condition, ms=5000):
    for _ in range(ms // 50):
        if condition():
            return
        page.wait_for_timeout(50)  # also lets Playwright deliver dialog events
    raise AssertionError("timed out waiting")


def _open_view(browser, data=LIST):
    page = browser.new_page()

    def answer(route):
        url = route.request.url
        if url == EXT_APPS_CLIENT:
            return route.fulfill(content_type="text/javascript", body=FAKE_EXT_APPS)
        if url == VIEW:
            return route.fulfill(content_type="text/html", body=SHOP_HTML)
        route.abort()

    page.route("**/*", answer)
    page.goto(VIEW)
    page.wait_for_function("!!(window.__app && window.__app.ontoolresult)")
    page.evaluate("list => window.__app.ontoolresult({ structuredContent: list })", data)
    return page


class _Morrisons:
    """A stand-in groceries.morrisons.com running the userscript the way a manager would."""

    def __init__(self, browser, logged_in=True, status=200):
        self.page = browser.new_page()  # a fresh context: its own localStorage
        self.state = {"session": {"csrf": {"token": "csrf-123"}, "isLoggedIn": logged_in}}
        self.status, self.calls, self.dialogs, self.confirm = status, [], [], True
        self.page.on("dialog", self._dialog)
        self.page.route("**/*", lambda route: route.abort())
        self.page.route(f"{MORRISONS}/**", self._answer)
        self.page.add_init_script(USERSCRIPT)  # @run-at document-start, like a userscript manager

    def _dialog(self, dialog):
        self.dialogs.append(dialog.message)
        dialog.accept() if dialog.type != "confirm" or self.confirm else dialog.dismiss()

    def _answer(self, route):
        req = route.request
        if req.method == "POST" and req.url == f"{MORRISONS}/api/cart/v1/carts/active/apply-quantity":
            self.calls.append({"body": json.loads(req.post_data), "csrf": req.headers.get("x-csrf-token"),
                               "source": req.headers.get("ecom-request-source")})
            return route.fulfill(status=self.status, json={"limitedItems": []})
        state = json.dumps(self.state)
        route.fulfill(content_type="text/html",
                      body=f'<html><body><script data-test="initial-state-script">window.__INITIAL_STATE__={state}</script></body></html>')

    def open(self, path):
        self.page.goto(MORRISONS + path, wait_until="commit")


def test_view_preticks_the_usual_sorts_alternatives_and_its_link_fills_the_trolley(browser):
    page = _open_view(browser)
    tomatoes, milk = page.locator(".row").nth(0), page.locator(".row").nth(1)
    first = tomatoes.locator(".card").first
    assert "Usual" in first.inner_text() and first.get_attribute("aria-pressed") == "true"
    assert "need 2 tins" in page.locator("h2").first.inner_text()
    assert milk.locator(".card.skip").get_attribute("aria-pressed") == "true"  # no Usual: starts skipped
    assert page.locator("#q1").inner_text() == "Not added. Tap a product to add it."
    assert page.locator("#status").inner_text() == "1 of 2 · ≈ £1.10"

    page.select_option("#sort", "price")  # the Usual stays first; alternatives cheapest first
    assert tomatoes.locator(".card .n").all_inner_texts() == ["Usual Tomatoes", "Cheap Tomatoes", "Dear Tomatoes"]
    page.select_option("#sort", "unit")
    assert milk.locator(".card .n").all_inner_texts() == ["Big Milk", "Small Milk"]  # £0.64/litre first

    milk.get_by_text("Small Milk").click()
    page.get_by_label("One pack more").nth(1).click()
    assert page.locator("#status").inner_text() == "2 of 2 · ≈ £3.30"
    page.get_by_role("button", name="Add to trolley").click()
    url = page.wait_for_function("window.__opened").json_value()
    assert url == f"{MORRISONS}/#add-to-trolley=3.30;{A}:2,{E}:2"
    assert page.locator("#link").input_value() == url

    # The same link, opened where the userscript runs, is exactly what reaches the trolley.
    shop = _Morrisons(browser)
    shop.page.goto(url, wait_until="commit")
    shop.page.wait_for_url(f"{MORRISONS}/basket")
    assert shop.calls == [{"body": [{"productId": A, "quantity": 2}, {"productId": E, "quantity": 2}],
                           "csrf": "csrf-123", "source": "web"}]
    assert shop.dialogs == ["Add 2 products (≈ £3.30) to your Morrisons trolley?", "Shopping list: added to your trolley."]


def test_unit_sort_reads_pence_per_each(browser):
    eggs = {"items": [{"name": "eggs", "query": "eggs", "quantity": 1, "amount": None, "usual_ids": [], "gone_usual_ids": [],
                       "results": [_product(A, "300000001", "Golden Eggs", 2.75, "45.8p/each"),
                                   _product(B, "300000002", "Big Box Eggs", 4.10, "£0.41/each"),
                                   _product(C, "300000003", "Value Eggs", 1.30, "21.7p/each")]}]}
    page = _open_view(browser, eggs)
    page.select_option("#sort", "unit")
    assert page.locator(".card .n").all_inner_texts() == ["Value Eggs", "Big Box Eggs", "Golden Eggs"]


def test_repeated_products_are_summed(browser):
    shop = _Morrisons(browser)
    shop.open(f"/#add-to-trolley=1.65;{A}:2,{B}:1,{A}:1")
    shop.page.wait_for_url(f"{MORRISONS}/basket")
    assert shop.calls[0]["body"] == [{"productId": A, "quantity": 3}, {"productId": B, "quantity": 1}]


def test_second_use_of_a_link_is_flagged_and_declining_adds_nothing(browser):
    shop = _Morrisons(browser)
    link = f"/#add-to-trolley=0.55;{A}:1"
    shop.open(link)
    shop.page.wait_for_url(f"{MORRISONS}/basket")
    shop.confirm = False
    shop.open(link)
    _wait(shop.page, lambda: len(shop.dialogs) == 3)
    assert "You already added this list on" in shop.dialogs[2]
    shop.page.wait_for_timeout(300)
    assert len(shop.calls) == 1 and shop.page.url == f"{MORRISONS}/"  # fragment cleared, no second add


def test_logged_out_keeps_the_list_until_login_then_adds_it(browser):
    shop = _Morrisons(browser, logged_in=False)
    shop.open(f"/#add-to-trolley=1.10;{E}:1")
    _wait(shop.page, lambda: shop.dialogs)
    assert shop.dialogs == ["Shopping list: log in to Morrisons and your 1 product will be added."]

    with shop.page.expect_response(f"{MORRISONS}/"):  # the script re-reads the session on the next page...
        shop.open("/login")
    shop.page.wait_for_timeout(300)
    assert len(shop.dialogs) == 1 and shop.calls == []  # ...but says so only once

    shop.state["session"]["isLoggedIn"] = True
    shop.open("/")
    shop.page.wait_for_url(f"{MORRISONS}/basket")
    assert shop.calls[0]["body"] == [{"productId": E, "quantity": 1}]


@pytest.mark.parametrize("code", ["1.00;not-a-product:2", f"1.00;{A}:0", f"x;{A}:1", f"1.00;{A}:1;extra", "%E0%A4%A"])
def test_damaged_link_adds_nothing(browser, code):
    shop = _Morrisons(browser)
    shop.open(f"/#add-to-trolley={code}")
    _wait(shop.page, lambda: shop.dialogs)
    assert shop.dialogs == ["Shopping list: this trolley link is damaged, so nothing was added."] and shop.calls == []


def test_refused_update_is_reported_and_not_remembered(browser):
    shop = _Morrisons(browser, status=403)
    shop.open(f"/#add-to-trolley=0.55;{A}:1")
    _wait(shop.page, lambda: len(shop.dialogs) == 2)
    assert shop.dialogs[1] == "Shopping list: Morrisons refused the trolley update (HTTP 403), so nothing was added."
    assert shop.page.url == f"{MORRISONS}/"
    assert shop.page.evaluate("localStorage.getItem('trolley-link:done')") is None
