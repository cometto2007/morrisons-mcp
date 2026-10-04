"""HTML for the product picker MCP App (``ui://morrisons/picker``).

The host renders this in a sandboxed frame after ``pick_products`` returns,
pushes the tool's structured result in, and relays the Submit button's
message into the chat as the user's own message.
"""

EXT_APPS_ORIGIN = "https://unpkg.com"
EXT_APPS_CLIENT = EXT_APPS_ORIGIN + "/@modelcontextprotocol/ext-apps@1.7.5/dist/src/app-with-deps.js"

PICKER_HTML = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="color-scheme" content="light dark">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Morrisons picker</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin: 0; padding: 12px; font: var(--font-text-sm-size, 14px)/1.4 var(--font-sans, system-ui, sans-serif);
         color: var(--color-text-primary, CanvasText); background: var(--color-background-primary, Canvas); }}
  h2 {{ font-size: var(--font-heading-sm-size, 14px); font-weight: var(--font-weight-semibold, 600); margin: 14px 0 6px; }}
  .row {{ display: flex; gap: 8px; overflow-x: auto; padding: 4px 0 8px; scroll-snap-type: x mandatory; }}
  .card {{ flex: 0 0 128px; scroll-snap-align: start; border: 2px solid var(--color-border-tertiary, #8884); border-radius: var(--border-radius-lg, 10px);
          padding: 8px; cursor: pointer; background: var(--color-background-secondary, transparent); color: inherit; font: inherit; text-align: left; min-height: 44px; }}
  .card:hover, .card:focus-visible {{ border-color: var(--color-border-primary, #888); outline: none; }}
  .card[aria-pressed="true"] {{ border-color: var(--color-border-success, #437426); background: var(--color-background-tertiary, transparent); }}
  .card img {{ width: 100%; aspect-ratio: 1; object-fit: contain; border-radius: var(--border-radius-sm, 6px); background: #fff; display: block; }} /* product photos are shot on white */
  .card .n {{ margin-top: 6px; font-size: var(--font-text-xs-size, 12px); display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
  .card .m {{ font-size: var(--font-text-xs-size, 12px); color: var(--color-text-secondary, GrayText); margin-top: 4px; }}
  .card.none {{ display: grid; place-items: center; text-align: center; color: var(--color-text-secondary, GrayText); }}
  .bar {{ padding: 12px 0 0; display: flex; gap: 8px; justify-content: flex-end; border-top: 1px solid var(--color-border-tertiary, #8884); margin-top: 8px; }}
  .bar button {{ min-height: 44px; padding: 0 18px; border-radius: var(--border-radius-md, 8px); border: 1px solid var(--color-border-primary, #888);
           background: transparent; color: inherit; font: inherit; cursor: pointer; }}
  .bar button.primary {{ background: var(--color-background-inverse, #141413); color: var(--color-text-inverse, #fff); border-color: transparent; }}
  .bar button:disabled {{ opacity: .5; cursor: default; }}
  #status {{ align-self: center; margin-right: auto; color: var(--color-text-secondary, GrayText); }}
</style></head>
<body>
<div id="list">Loading products…</div>
<div class="bar"><span id="status"></span><button id="expand">Expand</button><button id="submit" class="primary" disabled>Submit picks</button></div>
<script type="module">
  import {{ App }} from "{EXT_APPS_CLIENT}";
  const $ = (id) => document.getElementById(id);
  const app = new App({{ name: "Morrisons product picker", version: "1.0.0" }}, {{ availableDisplayModes: ["inline", "fullscreen"] }});
  let data = {{ ingredients: [] }};
  const picks = new Map(); // ingredient index -> product or null ("none of these")

  const el = (tag, cls, text) => {{ const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }};

  function card(i, p) {{
    const c = el("button", "card"); c.type = "button"; c.setAttribute("aria-pressed", "false");
    if (p.image_url) {{ const img = el("img"); img.src = p.image_url; img.alt = ""; img.loading = "lazy"; c.append(img); }}
    c.append(el("div", "n", p.name));
    c.append(el("div", "m", [p.pack_size, p.price != null ? "£" + p.price.toFixed(2) : null].filter(Boolean).join(" · ")));
    c.onclick = () => choose(i, p, c);
    return c;
  }}

  function choose(i, p, c) {{
    picks.set(i, p);
    c.parentElement.querySelectorAll(".card").forEach((x) => x.setAttribute("aria-pressed", "false"));
    c.setAttribute("aria-pressed", "true");
    $("submit").disabled = picks.size === 0;
    $("status").textContent = picks.size + " of " + data.ingredients.length + " chosen";
  }}

  function render() {{
    const list = $("list"); list.replaceChildren();
    if (!data.ingredients.length) {{ list.append(el("p", null, "Nothing to pick: no ingredients were given.")); return; }}
    data.ingredients.forEach((ing, i) => {{
      list.append(el("h2", null, ing.ingredient));
      const row = el("div", "row");
      ing.results.forEach((p) => row.append(card(i, p)));
      const none = el("button", "card none", ing.results.length ? "None of these" : "No results");
      none.type = "button"; none.setAttribute("aria-pressed", "false");
      none.onclick = () => choose(i, null, none);
      row.append(none);
      list.append(row);
    }});
  }}

  const oneLine = (s) => String(s ?? "").replace(/\\s+/g, " ").trim(); // product data is third-party text
  function message() {{
    const lines = data.ingredients.map((ing, i) => {{
      const name = oneLine(ing.ingredient);
      if (!picks.has(i)) return `- ${{name}}: not chosen`;
      const p = picks.get(i);
      if (!p) return `- ${{name}}: none of these`;
      const id = /^\\d+$/.test(p.retailer_product_id) ? p.retailer_product_id : "?";
      return `- ${{name}} → ${{oneLine(p.name)}} (id ${{id}}, https://groceries.morrisons.com/products/${{id}})`;
    }});
    return "Morrisons picks:\\n" + lines.join("\\n");
  }}

  app.ontoolresult = ({{ structuredContent, content }}) => {{
    let d = structuredContent;
    if (!d) {{ try {{ d = JSON.parse(content?.find((c) => c.type === "text")?.text ?? ""); }} catch {{ d = null; }} }}
    if (!d || !Array.isArray(d.ingredients)) return;
    data = d; picks.clear(); $("submit").disabled = true; $("status").textContent = "";
    render();
    // Inline height is capped and clipped by the host; a long picker needs its own scroll, so go fullscreen.
    const ctx = app.getHostContext();
    if (data.ingredients.length > 2 && ctx?.displayMode !== "fullscreen" && ctx?.availableDisplayModes?.includes("fullscreen")) {{
      app.requestDisplayMode({{ mode: "fullscreen" }}).then((r) => {{ if (r.mode === "fullscreen") $("expand").textContent = "Expanded"; }});
    }}
  }};
  const applyInsets = (ctx) => {{ const i = ctx?.safeAreaInsets; if (i) document.body.style.padding = `${{12 + (i.top || 0)}}px ${{12 + (i.right || 0)}}px ${{12 + (i.bottom || 0)}}px ${{12 + (i.left || 0)}}px`; }};
  app.onhostcontextchanged = applyInsets;
  $("expand").onclick = async () => {{
    const r = await app.requestDisplayMode({{ mode: "fullscreen" }});
    $("expand").textContent = r.mode === "fullscreen" ? "Expanded" : "Expand";
  }};
  $("submit").onclick = async () => {{
    $("submit").disabled = true;
    await app.sendMessage({{ role: "user", content: [{{ type: "text", text: message() }}] }});
    $("status").textContent = "Sent to the chat";
  }};
  await app.connect();
  applyInsets(app.getHostContext());
</script>
</body></html>
"""
