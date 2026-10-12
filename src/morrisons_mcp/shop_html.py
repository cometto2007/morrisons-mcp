"""HTML for the shopping-list MCP App (``ui://morrisons/shop``).

The host renders it after ``shop_products`` returns and pushes the tool's structured
result in. The user ticks one product per line (Usual products come pre-ticked), sets
packs, and "Add to trolley" opens Morrisons with the picks in the URL fragment:

    https://groceries.morrisons.com/#add-to-trolley=<total>;<productId>:<packs>,...

The trolley userscript (``userscript/morrisons-trolley.user.js``) picks that
up first-party, so the trolley write carries the user's own login. Nothing about their
account passes through here.
"""

from .picker_html import EXT_APPS_CLIENT

SHOP_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="color-scheme" content="light dark">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Morrisons shopping list</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; padding: 12px; font: var(--font-text-sm-size, 14px)/1.4 var(--font-sans, system-ui, sans-serif);
         color: var(--color-text-primary, CanvasText); background: var(--color-background-primary, Canvas); }
  button, select, input { font: inherit; color: inherit; }
  .top { display: flex; gap: 8px; align-items: center; justify-content: space-between; }
  .top select { min-height: 44px; padding: 0 10px; border-radius: var(--border-radius-md, 8px);
                border: 1px solid var(--color-border-primary, #888); background: transparent; }
  h2 { font-size: var(--font-heading-sm-size, 14px); font-weight: var(--font-weight-semibold, 600); margin: 16px 0 6px; }
  .amt, .muted { color: var(--color-text-secondary, GrayText); font-weight: normal; font-size: var(--font-text-xs-size, 12px); }
  .amt { margin-left: 6px; }
  .muted { margin: 0 0 6px; }
  .row { display: flex; gap: 8px; overflow-x: auto; padding: 4px 0 8px; scroll-snap-type: x mandatory; }
  .card { position: relative; flex: 0 0 132px; scroll-snap-align: start; border: 2px solid var(--color-border-tertiary, #8884);
          border-radius: var(--border-radius-lg, 10px); padding: 8px; cursor: pointer; background: var(--color-background-secondary, transparent);
          text-align: left; min-height: 44px; }
  .card:hover, .card:focus-visible { border-color: var(--color-border-primary, #888); outline: none; }
  .card[aria-pressed="true"] { border-color: var(--color-border-success, #437426); background: var(--color-background-tertiary, transparent); }
  .card:disabled { opacity: .5; cursor: default; }
  .card img { width: 100%; aspect-ratio: 1; object-fit: contain; border-radius: var(--border-radius-sm, 6px); background: #fff; display: block; } /* product photos are shot on white */
  .card .n { margin-top: 6px; font-size: var(--font-text-xs-size, 12px); display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  .card .m { margin-top: 4px; font-size: var(--font-text-xs-size, 12px); font-weight: var(--font-weight-semibold, 600); }
  .card .u { font-size: var(--font-text-xs-size, 12px); color: var(--color-text-secondary, GrayText); }
  .card .o { font-size: var(--font-text-xs-size, 12px); color: var(--color-text-warning, #9a5b00); }
  .badge { position: absolute; top: 4px; left: 4px; padding: 1px 6px; border-radius: 999px; font-size: 11px; font-weight: 600;
           background: var(--color-background-inverse, CanvasText); color: var(--color-text-inverse, Canvas); }
  .card.skip { display: grid; place-items: center; text-align: center; color: var(--color-text-secondary, GrayText); }
  .qty { display: flex; gap: 8px; align-items: center; }
  .qty button, .bar button { min-height: 44px; min-width: 44px; padding: 0 14px; border-radius: var(--border-radius-md, 8px);
           border: 1px solid var(--color-border-primary, #888); background: transparent; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  .foot { position: sticky; bottom: 0; margin-top: 8px; border-top: 1px solid var(--color-border-tertiary, #8884); background: var(--color-background-primary, Canvas); }
  .bar { display: flex; gap: 8px; align-items: center; padding: 12px 0; }
  .bar button.primary { background: var(--color-background-inverse, CanvasText); color: var(--color-text-inverse, Canvas); border-color: transparent; }
  #status { margin-right: auto; }
  #after { padding-top: 8px; }
  #after input { width: 100%; box-sizing: border-box; min-height: 44px; padding: 0 8px; border-radius: var(--border-radius-md, 8px);
                 border: 1px solid var(--color-border-tertiary, #8884); background: transparent; }
</style></head>
<body>
<div class="top"><span class="muted">Your usual products are ticked. Tap another to swap.</span>
  <label>Sort <select id="sort"><option value="match">Best match</option><option value="price">Price</option><option value="unit">Unit price</option></select></label></div>
<div id="list">Loading products…</div>
<div class="foot"><div id="after" hidden><p id="note" class="muted"></p><input id="link" readonly aria-label="Trolley link"></div>
<div class="bar"><span id="status"></span><button id="expand" type="button">Expand</button>
  <button id="add" class="primary" type="button" disabled>Add to trolley</button></div></div>
<script type="module">
  import { App } from "__EXT_APPS_CLIENT__";
  const $ = (id) => document.getElementById(id);
  const app = new App({ name: "Morrisons shopping list", version: "1.0.0" }, { availableDisplayModes: ["inline", "fullscreen"] });
  const TROLLEY = "https://groceries.morrisons.com/#add-to-trolley=";
  let rows = [];        // shop_products rows
  let sel = [];         // per row: { p: chosen product or null (skip), q: packs }

  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const money = (n) => "£" + n.toFixed(2);
  const isUsual = (row, p) => row.usual_ids.includes(p.retailer_product_id);
  const perUnit = (p) => { const m = /^(£)?([\\d.]+)(p)?\\/(\\w+)$/.exec(p.unit_price || ""); return m ? { v: m[3] ? m[2] / 100 : +m[2], u: m[4] } : null; };

  // Usual products stay first whatever the sort; only the alternatives move.
  function sorted(row) {
    const rest = row.results.filter((p) => !isUsual(row, p));
    const how = $("sort").value;
    if (how === "price") rest.sort((a, b) => a.price - b.price);
    if (how === "unit") {
      // ponytail: Morrisons prices per kg, litre or each; a line mixing them (rare) puts its commonest unit first
      const seen = {}; rest.forEach((p) => { const x = perUnit(p); if (x) seen[x.u] = (seen[x.u] || 0) + 1; });
      const main = Object.keys(seen).sort((a, b) => seen[b] - seen[a])[0];
      const key = (p) => { const x = perUnit(p); return x ? [x.u === main ? 0 : 1, x.v] : [2, 0]; };
      rest.sort((a, b) => { const ka = key(a), kb = key(b); return ka[0] - kb[0] || ka[1] - kb[1]; });
    }
    return row.results.filter((p) => isUsual(row, p)).concat(rest);
  }

  function card(i, p) {
    const c = el("button", "card"); c.type = "button";
    c.setAttribute("aria-pressed", String(sel[i].p?.product_id === p.product_id));
    if (isUsual(rows[i], p)) c.append(el("span", "badge", "Usual"));
    if (p.image_url) { const img = el("img"); img.src = p.image_url; img.alt = ""; img.loading = "lazy"; c.append(img); }
    c.append(el("div", "n", p.name));
    c.append(el("div", "m", [money(p.price), p.pack_size].filter(Boolean).join(" · ")));
    if (p.unit_price) c.append(el("div", "u", p.unit_price));
    const offer = p.promotions?.[0]?.description; if (offer) c.append(el("div", "o", offer));
    if (p.available === false) { c.disabled = true; c.append(el("div", "u", "Unavailable")); }
    c.onclick = () => choose(i, p, c);
    return c;
  }

  function choose(i, p, c) {
    sel[i].p = p;
    c.parentElement.querySelectorAll(".card").forEach((x) => x.setAttribute("aria-pressed", String(x === c)));
    stepper(i); total();
  }

  function stepper(i) {
    const box = $("q" + i); box.replaceChildren();
    const s = sel[i]; if (!s.p) { box.append(el("span", "muted", "Not added. Tap a product to add it.")); return; }
    const less = el("button", null, "−"), more = el("button", null, "+"), n = el("span");
    less.type = more.type = "button"; less.setAttribute("aria-label", "One pack fewer"); more.setAttribute("aria-label", "One pack more");
    n.setAttribute("aria-live", "polite");
    const sync = () => { n.textContent = s.q + " × " + money(s.p.price) + " = " + money(s.q * s.p.price); less.disabled = s.q <= 1; more.disabled = s.q >= 99; total(); };
    less.onclick = () => { s.q--; sync(); }; more.onclick = () => { s.q++; sync(); };
    box.append(less, n, more); sync();
  }

  function render() {
    const list = $("list"); list.replaceChildren();
    if (!rows.length) { list.append(el("p", null, "Nothing to shop for: the list was empty.")); return; }
    rows.forEach((row, i) => {
      const h = el("h2", null, row.name); if (row.amount) h.append(el("span", "amt", "need " + row.amount)); list.append(h);
      if (row.gone_usual_ids.length) list.append(el("p", "muted", "Your usual product is no longer sold at Morrisons."));
      const strip = el("div", "row");
      sorted(row).forEach((p) => strip.append(card(i, p)));
      const skip = el("button", "card skip", row.results.length ? "Skip" : "No results"); skip.type = "button";
      skip.setAttribute("aria-pressed", String(!sel[i].p));
      skip.onclick = () => { sel[i].p = null; strip.querySelectorAll(".card").forEach((x) => x.setAttribute("aria-pressed", String(x === skip))); stepper(i); total(); };
      strip.append(skip); list.append(strip);
      const q = el("div", "qty"); q.id = "q" + i; list.append(q); stepper(i);
    });
  }

  function total() {
    const chosen = sel.filter((s) => s.p);
    const sum = chosen.reduce((t, s) => t + s.p.price * s.q, 0);
    $("status").textContent = chosen.length + " of " + rows.length + " · ≈ " + money(sum);
    $("add").disabled = !chosen.length;
    return sum;
  }

  $("sort").onchange = render;
  $("add").onclick = async () => {
    const picks = sel.filter((s) => s.p).map((s) => s.p.product_id + ":" + s.q).join(",");
    const url = TROLLEY + total().toFixed(2) + ";" + picks;
    $("link").value = url; $("after").hidden = false;
    $("note").textContent = "Opening Morrisons… If nothing gets added, open this link in Safari (the userscript only runs there):";
    const r = await app.openLink({ url }).catch(() => ({ isError: true }));
    if (r?.isError) $("note").textContent = "Couldn't open Morrisons from here. Copy this link into Safari:";
  };
  $("link").onfocus = (e) => e.target.select();

  app.ontoolresult = ({ structuredContent, content }) => {
    let d = structuredContent;
    if (!d) { try { d = JSON.parse(content?.find((c) => c.type === "text")?.text ?? ""); } catch { d = null; } }
    if (!d || !Array.isArray(d.items)) return;
    rows = d.items;
    // Pre-tick each line's first Usual product Morrisons can supply; a line without one starts skipped.
    sel = rows.map((row) => ({ p: row.results.find((p) => isUsual(row, p) && p.available !== false) || null, q: row.quantity || 1 }));
    $("after").hidden = true;
    render(); total();
    // Inline height is capped and clipped by the host; a long list needs its own scroll, so go fullscreen.
    const ctx = app.getHostContext();
    if (rows.length > 2 && ctx?.displayMode !== "fullscreen" && ctx?.availableDisplayModes?.includes("fullscreen")) {
      app.requestDisplayMode({ mode: "fullscreen" }).then((r) => { if (r.mode === "fullscreen") $("expand").textContent = "Expanded"; });
    }
  };
  const applyInsets = (ctx) => {
    const i = ctx?.safeAreaInsets; if (!i) return;
    document.body.style.padding = `${12 + (i.top || 0)}px ${12 + (i.right || 0)}px ${12 + (i.bottom || 0)}px ${12 + (i.left || 0)}px`;
    document.querySelector(".foot").style.bottom = (i.bottom || 0) + "px";
  };
  app.onhostcontextchanged = applyInsets;
  $("expand").onclick = async () => {
    const r = await app.requestDisplayMode({ mode: "fullscreen" });
    $("expand").textContent = r.mode === "fullscreen" ? "Expanded" : "Expand";
  };
  await app.connect();
  applyInsets(app.getHostContext());
</script>
</body></html>
""".replace("__EXT_APPS_CLIENT__", EXT_APPS_CLIENT)
