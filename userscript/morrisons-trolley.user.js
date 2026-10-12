// ==UserScript==
// @name         Morrisons trolley from a shopping list
// @namespace    https://github.com/cometto2007/morrisons-mcp
// @version      1.0.0
// @description  Adds the products picked in a shopping list (the Morrisons MCP's shop_products) to your Morrisons trolley, after you confirm.
// @match        https://groceries.morrisons.com/*
// @run-at       document-start
// @grant        none
// @inject-into  content
// @downloadURL  https://raw.githubusercontent.com/cometto2007/morrisons-mcp/main/userscript/morrisons-trolley.user.js
// @updateURL    https://raw.githubusercontent.com/cometto2007/morrisons-mcp/main/userscript/morrisons-trolley.user.js
// ==/UserScript==

// The shopping list opens https://groceries.morrisons.com/#add-to-trolley=<total>;<productId>:<packs>,...
// This runs first-party on Morrisons, so the trolley call carries your own login and nothing
// about your account leaves the browser. Morrisons' apply-quantity ADDS packs (their own
// remove button sends negative ones), so a link is remembered and a second use is flagged.
// It runs at document-start to take the fragment before Morrisons' app can rewrite the URL.

(async () => {
  const PENDING = "trolley-link:pending";
  const DONE = "trolley-link:done";
  const MAX_AGE = 30 * 60 * 1000; // a link not finished within 30 minutes (e.g. abandoned login) is dropped

  const hash = /^#add-to-trolley=(.+)$/.exec(location.hash);
  if (hash) {
    let code = "";
    try { code = decodeURIComponent(hash[1]); } catch { /* malformed %-escape: parse() reports it as damaged */ }
    save({ code, at: Date.now() });
    history.replaceState(null, "", location.pathname + location.search); // a reload must not add again
  }
  const pending = JSON.parse(localStorage.getItem(PENDING) || "null");
  if (!pending) return;
  if (Date.now() - pending.at > MAX_AGE) return localStorage.removeItem(PENDING);
  if (document.readyState === "loading") await new Promise((done) => addEventListener("DOMContentLoaded", done, { once: true }));

  const list = parse(pending.code);
  if (!list) {
    localStorage.removeItem(PENDING);
    return alert("Shopping list: this trolley link is damaged, so nothing was added.");
  }
  const session = await sessionState();
  if (!session?.csrf?.token) return alert("Shopping list: couldn't read your Morrisons session. Reload the page to try again.");
  if (!session.isLoggedIn) {
    // Keep the list: after logging in, any Morrisons page picks it up again. Say so once, not on every page of the login.
    if (!pending.told) alert(`Shopping list: log in to Morrisons and your ${count(list.items.length)} will be added.`);
    return save({ ...pending, told: true });
  }

  localStorage.removeItem(PENDING);
  const done = JSON.parse(localStorage.getItem(DONE) || "{}");
  const key = await digest(pending.code);
  const again = done[key] ? `\n\nYou already added this list on ${new Date(done[key]).toLocaleString()}.` : "";
  if (!confirm(`Add ${count(list.items.length)} (≈ £${list.total}) to your Morrisons trolley?${again}`)) return;

  const resp = await fetch("/api/cart/v1/carts/active/apply-quantity", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "content-type": "application/json",
      accept: "application/json; charset=utf-8",
      "x-csrf-token": session.csrf.token,
      "ecom-request-source": "web",
    },
    body: JSON.stringify(list.items),
  });
  if (!resp.ok) return alert(`Shopping list: Morrisons refused the trolley update (HTTP ${resp.status}), so nothing was added.`);
  done[key] = Date.now();
  localStorage.setItem(DONE, JSON.stringify(latest(done, 20)));
  const result = await resp.json().catch(() => ({}));
  const limited = (result.limitedItems || []).length; // over a quantity limit or out of stock
  alert(limited ? `Shopping list: added, except ${limited} product(s) Morrisons limited or couldn't supply.` : "Shopping list: added to your trolley.");
  location.assign("/basket");

  function count(n) {
    return n === 1 ? "1 product" : `${n} products`;
  }

  function save(value) {
    localStorage.setItem(PENDING, JSON.stringify(value));
  }

  /** "<total>;<productId>:<packs>,..." → { total, items: [{productId, quantity}] }, duplicates summed; null if malformed. */
  function parse(code) {
    const [total, picks, extra] = code.split(";");
    if (extra !== undefined || !/^\d{1,5}(\.\d{1,2})?$/.test(total) || !picks) return null;
    const packs = new Map();
    for (const part of picks.split(",")) {
      const m = /^([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}):(\d{1,2})$/i.exec(part);
      if (!m || +m[2] < 1) return null;
      packs.set(m[1], Math.min(99, (packs.get(m[1]) || 0) + +m[2]));
    }
    if (packs.size > 100) return null;
    return { total, items: [...packs].map(([productId, quantity]) => ({ productId, quantity })) };
  }

  /** The page's own state (window.__INITIAL_STATE__.session), read from a fresh home page so it reflects the current login. */
  async function sessionState() {
    try {
      const html = await (await fetch("/", { credentials: "same-origin" })).text();
      const script = new DOMParser().parseFromString(html, "text/html").querySelector('script[data-test="initial-state-script"]');
      const text = script?.textContent || "";
      return JSON.parse(text.slice(text.indexOf("{"))).session;
    } catch {
      return null;
    }
  }

  async function digest(text) {
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return [...new Uint8Array(bytes)].slice(0, 8).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  function latest(stamps, n) {
    return Object.fromEntries(Object.entries(stamps).sort((a, b) => b[1] - a[1]).slice(0, n));
  }
})();
