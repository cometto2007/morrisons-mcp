# Adding to the Morrisons trolley: authentication options

**Status:** decided 2026-10-12: **E (userscript)**, built as `shop_products` plus
`userscript/morrisons-trolley.user.js` (see the README). Analysis from 2026-10-11/12. No
live trolley writes have been made: everything below about Morrisons' own endpoints is
read off their public web bundle (`groceries.morrisons.com/static/index-*.js`) and the
initial-state JSON in the home page, i.e. their own client's behaviour, not a spec we
were given. The first real trolley write is Chris's first use (see **Live checks**).

Scope: one household, Chris's own Morrisons account, adding products Chris chose to Chris's
own trolley. It never pays — booking a slot and paying stays a manual step in the
Morrisons app/site. "Buy" in the original ask = "fill my trolley ready to check out".

---

## What the site actually does

Morrisons groceries is the Ocado Smart Platform ("OSP") web front end. The relevant
calls, pulled from the bundle:

| What | Call | Notes |
|---|---|---|
| Add N products to the live trolley | `POST /api/cart/v1/carts/active/apply-quantity` | Body is a list of `{productId, quantity}`. **One call does the whole shop.** Quantities are **added**, not set: the site's own remove sends `-quantity` to the same endpoint. |
| Add to a specific (draft) cart | `POST /api/cart/v1/carts/{cartId}/add-items` | Variant of the above. |
| "Instant shop" (re-buy my regulars) | `POST …/v2/instant-shop` body `{}` | Server picks the products, not us. Not a targeted add. |
| Merge a guest basket after login | `POST …/v1/carts/active/merge-items` `{visitorId}` | Only the web app's own post-login hook calls this; no public URL triggers it with an arbitrary visitorId. |
| Merge a basket from a share link | `POST …/v9/baskets/merge` (`application/jwt`), triggered by `?cartToken=<JWT>` | **The web app only *consumes* a cartToken — it never mints one.** So we can't produce one. |

Every mutating call needs:

- the **session cookies** (`global_sid`, `AWSALB*`, `VISITORID`, …), and
- an **`X-CSRF-TOKEN`** header whose value sits in the page's initial state
  (`session.csrf.token`), plus `ecom-request-source: web`.

If those cookies belong to a **logged-in customer**, items land in their **real
trolley**. If they're anonymous, items land in a throwaway **guest basket**.

### Two things this rules out immediately

1. **`cartToken` deep-link** ("picker gives a link, open it in a logged-in browser and
   it fills the trolley"). The token is a signed JWT the web app only reads; there's no
   endpoint in the web API that mints one for an arbitrary basket. That's an
   app-side/"share basket" feature we don't have. **Not available.**

2. **Picker reusing the browser's logged-in session directly.** The picker is an MCP App
   — a *sandboxed iframe served from the unpkg origin*, not from morrisons.com. A fetch
   from it to `/api/cart/*` is cross-origin, carries no Morrisons cookies (SameSite), and
   OSP won't CORS-allow a random origin. **So "same browser session, straight from the
   picker" cannot work through the sandbox.** (A *first-party* bookmarklet can — option C.)

So the only way to put Chris's picks in the **real** trolley is: **something that already
holds Chris's logged-in Morrisons session makes the `apply-quantity` call.** That "something"
is our server (options A/B), Chris's own browser via a bookmarklet (option C), or a
dedicated browser on macbrain that the server drives (option D).

---

## The workable options

### A. One-time cookie paste, server-held session (lowest build, some recurring friction)

Chris logs into groceries.morrisons.com once in a browser, copies the cookie header
(DevTools → Network → any request → `cookie`), and pastes it into a
`set_morrisons_session` MCP tool. A bookmarklet **cannot** do the copying: the session
cookies `global_sid` and `VISITORID` are HttpOnly, invisible to page JavaScript. Only
DevTools, a browser extension (`chrome.cookies` reads HttpOnly), or a browser driven over
its debugging protocol can read them. `global_sid` is also a *session* cookie (no expiry),
so how long a pasted one lives is down to Morrisons' server-side timeout. The server stores the cookies and uses them for
`apply-quantity`. The picker's "Add to trolley" then calls the server, which adds the
items to the real trolley.

- **One-time friction:** paste a cookie string once.
- **Recurring friction:** when the session finally expires, the server gets a 401/403,
  and the assistant pushes "tap to refresh your Morrisons login" → paste again.
- **Auto-refresh?** OSP rotates cookies on each response (`Set-Cookie`), and our
  `SessionManager` already follows them. A periodic keepalive GET (e.g. `/v2/sessions/active`)
  likely keeps a warm session alive for days–weeks. **Unknown exact lifetime — needs the
  read-only longevity check below.** This is the "is there a way to pull it automatically"
  answer: *partly* — we can keep an existing session alive automatically, but we can't
  *originate* one without a login.
- **Credentials stored:** session cookies only (no password). Medium sensitivity.
- **Verdict:** simplest to ship; good enough if the session proves long-lived.

### B. Headless login, fully automatic (lowest recurring friction, highest fragility + sensitivity)

Store Chris's Morrisons username+password as a server secret. The server drives the
external SSO login **headlessly with the preinstalled Chromium/Playwright**, harvests the
cookies, and repeats whenever they expire — so after one-time setup there's *zero*
recurring friction. This is the literal "pull it automatically" path.

- **One-time friction:** enter username+password once.
- **Recurring friction:** none, when it works.
- **Why it's fragile:** the login is an **external SSO redirect** and the site is fronted
  by an **AWS WAF captcha** (its SDK loads on the home page). Headless logins hit
  captcha/2FA/OTP and break; every SSO tweak can break it. It would need babysitting.
- **Credentials stored:** full password. Highest sensitivity.
- **Verdict:** possible, but brittle and credential-heavy. Only if A's re-paste cadence
  turns out to be genuinely annoying *and* the login proves automatable without captcha.

### C. First-party bookmarklet bridge (no credentials stored, real trolley, matches "logged-in browser") — **recommended**

The picker's "Add to trolley" doesn't call Morrisons from the server at all. Instead:

1. On Submit, the server stashes the chosen `{productId, quantity}` list under a short
   random **code** (TTL ~15 min, in the existing SQLite cache).
2. The picker shows: *"Open Morrisons (logged in) and tap your **PrepTrack → Morrisons**
   bookmarklet"* — plus the code.
3. The bookmarklet runs **first-party on groceries.morrisons.com**, where Chris is already
   logged in. It fetches the pending list from our MCP (one small CORS-enabled endpoint),
   reads the CSRF token out of the page state, and POSTs `apply-quantity` **with Chris's own
   cookies, same-origin**. Items land in the real trolley.

- **One-time friction:** drag the bookmarklet to the bookmarks bar once.
- **Recurring friction:** one tap on the bookmarklet, in a logged-in Morrisons tab, per
  shop. No logins, no expiry, no re-paste — the auth is whatever the browser already has.
- **Credentials stored:** **none.** The server only ever holds a transient product list.
  Lowest sensitivity by far.
- **Why it works where the sandbox idea didn't:** the bookmarklet is first-party code on
  morrisons.com, so it *does* get the cookies and CSRF the sandboxed picker can't.
- **Verdict:** closest to what you floated ("a logged-in browser"), lowest sensitivity,
  and the add-to-trolley is real. Superseded by D, which keeps the "your browser holds
  the login" property without the per-shop click.

### D. A dedicated logged-in browser on macbrain, driven by the MCP — **recommended (2026-10-12)**

macbrain already runs `brave-selkies`: a persistent Brave streamed at
`nexterm.chrislab.it/brave-browser/` behind Authentik, with a profile volume, a keyring
for saved passwords, a relaunch loop and a Kuma heartbeat. Clone that pattern as a
**dedicated Morrisons browser** (its own container and profile, holding only the Morrisons
login):

1. Chris logs into Morrisons once in the streamed window (the keyring can keep the password).
2. The browser runs with remote debugging on a non-default profile, reachable only from
   the morrisons-mcp container on a private Docker network.
3. "Add to trolley" makes the MCP open (or reuse) a groceries.morrisons.com tab over the
   DevTools Protocol and run the `apply-quantity` fetch **inside that tab**: the same
   first-party call as the bookmarklet in C, but nobody clicks anything. CSRF comes from
   the live page; cookie rotation and the AWS WAF challenge are the browser's own business.
4. When Morrisons logs the session out, the tool says so and Chris logs in again in the
   streamed window (works from the phone).

- **One-time friction:** log in once in the streamed window.
- **Recurring friction:** a normal re-login when Morrisons expires the session. No cookie
  copying, no extension, no second tab; the picker's Add to trolley is one tap.
- **Credentials stored:** the browser's own session (plus the password, if saved in its
  keyring), on macbrain, never copied out of the browser.
- **Why a separate container:** DevTools-protocol access is full control of that browser,
  so it must not be the everyday Brave with other logins in it.
- **Cost:** one more container (brave-selkies is capped at 4 GB RAM; this needs less).
- **Host:** its streamed window at `morrisons.chrislab.it` behind Authentik (Chris,
  2026-10-12), not under nexterm.
- **Finish line:** after adding, the picker's "Open trolley" link goes to
  `groceries.morrisons.com/basket`, which the Morrisons iPhone app claims (see E), so on
  the phone it opens the app on the trolley, ready for slot and payment.

### E. Userscript (Tampermonkey / Violentmonkey; "Userscripts" on iPhone Safari)

C without the click: a userscript runs automatically on groceries.morrisons.com, so the
picker can hand out a link carrying a short code; opening it in a logged-in browser makes
the script fetch the pick list from our server (`GM_xmlhttpRequest`, no CORS needed) and
run `apply-quantity` first-party with the user's own cookies. No container, no stored
credentials. This is the original "open a link in a logged-in browser" idea, made real.

The catch is the iPhone. Morrisons' `/.well-known/apple-app-site-association` (fetched
2026-10-12) has app `com.morrisons.grocery.atm.release` claim `/`, `/products/*`,
`/basket`, `/search`, `/regulars`, `/categories/*`, `/favorites`, `/orders`, `/promotions`,
`/recipes` and more, so with the app installed those links open the app, where no
userscript runs. A link would need an unclaimed path to stay in Safari, Safari needs the
Userscripts extension and its own Morrisons login, and an in-app browser (if Claude uses
one for links) doesn't run Safari extensions. On a laptop it's smooth; on the phone,
fiddly. D behaves the same on every device.

**Without the Morrisons app (Chris, 2026-10-12: may never install it), E beats D.**
Paying then happens in a browser anyway, and that browser has to be logged into
Morrisons. E reuses exactly that login; D adds a second logged-in browser on macbrain to
keep alive, plus a container and a debugging connection. Design for E:

- **The list rides in the link:** `groceries.morrisons.com/#add-to-trolley=<base64 of
  [{productId, quantity}]>`. The fragment never reaches Morrisons' server, and our server
  needs no new endpoint or gateway bypass. 30 items is about 2 KB.
- **The script asks first:** a `confirm("Add 14 items (£42.10) to your trolley?")` before
  writing, so a stray or crafted link can't fill the trolley silently.
- **Logged out:** the script keeps the list in `sessionStorage`, lets Chris log in on the
  page already open, and finishes after the login redirect comes back.
- **Then** it clears the fragment and opens `/basket`, ready for slot and payment.
- **Setup:** iPhone: the free Userscripts app (App Store), switched on in Settings → Safari
  → Extensions, plus a Morrisons login in Safari. Laptop: the same script in Tampermonkey.
  Safari only on the phone; iOS Chrome can't run extensions.
- **Unknown:** how the Claude iPhone app opens links. If it uses its own in-app browser,
  that view runs no extensions and doesn't share Safari's login, so each shop needs one
  "Open in Safari" tap.

---

## A snag that affects all three: stored link ID ≠ trolley ID

`apply-quantity` wants the **UUID `productId`** (the per-search `ProductResult.product_id`).
PrepTrack product links store the **numeric `retailerProductId`** (that's what the product
*URL* resolves on, and what `get_product_detail` takes). The two are different ids.

- **Pick-then-add (the picker flow):** fine as-is. The picker's live candidates already
  carry both ids, so Submit → add-to-trolley has the UUID in hand.
- **Add a *saved* Usual product straight to the trolley:** needs a resolve step first,
  `retailerProductId` → current `productId` (a search or product-page fetch), because the
  stored link doesn't hold the UUID. Worth confirming the UUID is even stable over time —
  if it rotates, only the pick-then-add path is reliable and "add my saved usuals in one
  tap" always goes through a fresh resolve. (The numeric id *is* stable — a 6-month-old
  one still resolves — which is why links store it.)

Implication for the picker you asked for: showing the saved Usual first/badged and
pre-selected is all driven by the numeric id we already store (match a candidate's
`retailer_product_id` to the link). Only the final add-to-trolley needs the UUID, which
the candidate row carries. So the pre-selection works without any resolve; the resolve is
only for a hypothetical "add saved usuals without opening the picker".

## Recommendation

**Chosen 2026-10-12: E** (Chris won't install the Morrisons app). E if the Morrisons app
stays uninstalled; otherwise D. E needs no
infrastructure and uses the one login Chris needs anyway to pay. D only wins when the app
is installed, because the app takes over Morrisons links on the phone. With D,
authentication becomes "log in to Morrisons like a normal person, now and then", the picker
keeps a one-tap Add to trolley, and nothing handles cookies. If another container is unwelcome, the fallback is A made automatic:
a ~40-line personal browser extension that pushes the Morrisons cookies to the server
whenever they change (desktop browsers only; needs a secret-protected endpoint outside the
OAuth gateway; the browser's and server's copies of the session can drift if Morrisons
rotates it). Plain DevTools paste is for testing only. B stays out (fragile, stores the
password).

---

## Live checks

With E, the session checks for A are moot. The userscript is tested in headless Chromium
against a stand-in Morrisons (`tests/test_shop_browser.py`). What only the real site can
confirm, on Chris's first use with one cheap item:

- `apply-quantity` accepts the bare `[{productId, quantity}]` body. The web app may send
  more fields (analytics `meta`); the bundle suggests they're optional.
- The CSRF token from a fresh `/` page is accepted for the POST.
- `limitedItems` is where refusals show up.

If the first use fails, the userscript's alert shows Morrisons' HTTP status, and nothing
is added.
