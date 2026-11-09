# INTEGRATIONS.md — per-service notes from public docs

Researched Nov 1, 2026, from public documentation only. **No live calls were made and no credits were consumed.** Key/secret *values* are never recorded here — only where they are obtained at build time.

---

## 1. PayPal — sandbox Orders v2 + Payments v2

**Sources:** developer.paypal.com — *PayPal Checkout* (standard use cases), *Authorize a payment and capture funds later*, *Delay disbursement* (Commerce Platform), Orders/Payments API references.

### 1.1 Bases and auth

- REST base (sandbox): `https://api-m.sandbox.paypal.com`
- OAuth: `POST /v1/oauth2/token` with `grant_type=client_credentials`, HTTP Basic over `client_id:secret` → `access_token` (Bearer, hours-long expiry; fetch per server run, cache in memory only).
- JS SDK: `https://www.paypal.com/sdk/js?client-id=<SANDBOX_CLIENT_ID>&intent=authorize&currency=USD` — the `intent=authorize` parameter matters: the SDK's default is authorize+capture in one step.
- Every mutating REST call sends a **`PayPal-Request-Id`** header — PayPal's idempotency key; the engine passes its `change_id` here (ENGINE-CONTRACT.md §1.5).

**Where the keys are obtained at build time:** Developer Dashboard → **Apps & Credentials** → **Sandbox** tab → app **"Card Cauldron Rosette"** (already created) → Client ID + Secret are shown on the app page. At build time the owner/Potion copies them into the local `.env` (names in `repo/.env.example`); they are never committed, never pasted into chat or docs. His PayPal *login* is in the Secure Vault; the app secret is only ever read from the dashboard page itself.

**Sandbox personas:** Dashboard → **Sandbox → Accounts**: the **Business** account = seller/payee; the **Personal** account = buyer. Their sign-in emails/passwords live in that Accounts UI (sandbox-only credentials — never his real PayPal password; the buyer approval step in the demo uses the sandbox Personal account, including when the Kernel browser types it).

### 1.2 The calls this flow uses

| Step | Call | Notes |
|---|---|---|
| Create order | `POST /v2/checkout/orders` | Body: `intent: "AUTHORIZE"`, `purchase_units[0].amount`, `custom_id` = seal receipt id, `invoice_id` = our change-set id. Returns order `id`, status `CREATED`, `links[]` incl. `rel: "approve"`. |
| Buyer approval | JS SDK buttons (`createOrder` → buyer approves → `onApprove`) or redirect to the approve link | Buyer signs in with the sandbox Personal account. Order status becomes `APPROVED` — **an order cannot be authorized before it is APPROVED**. |
| Authorize | `POST /v2/checkout/orders/{order_id}/authorize` | Returns the authorization under `purchase_units[0].payments.authorizations[0]` (id, status `CREATED`, amount, expiry). Funds are now **held**. |
| Capture | `POST /v2/payments/authorizations/{authorization_id}/capture` | Body may carry a partial `amount` and `final_capture: true`. Returns capture id, status `COMPLETED`. This is the engine's `apply` for an approved capture change. |
| Void | `POST /v2/payments/authorizations/{authorization_id}/void` | Releases the hold. Terminal — the engine marks the receipt `undoable: false`. |
| Refund (post-capture undo) | `POST /v2/payments/captures/{capture_id}/refund` | The compensating action for a capture. |
| Read state | `GET /v2/checkout/orders/{order_id}` | The engine's PayPal reader. **Poll this as the demo's source of truth** — sandbox webhook delivery can lag. |
| Webhooks (optional) | `CHECKOUT.ORDER.APPROVED`, `PAYMENT.AUTHORIZATION.CREATED`, `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.REFUNDED`, `PAYMENT.CAPTURE.DENIED` | Testable in the dashboard's webhook simulator. Nice-to-have for the demo; never the state source of truth (see above). |

### 1.3 Authorize/capture mechanics that shape the demo

- An authorization is valid **29 days**, but PayPal recommends capturing within the **3-day honor period**; capture success after that is subject to funds availability. The demo captures within minutes, so this is a non-issue — but the dry-run surfaces "authorization age" as a warning field because the engine is built for real shop work later.
- Capture can be **less than, equal to, or more than** the authorized amount — over-capture capped at **115% of the original authorization or +$75, whichever is less**. Multiple partial captures are allowed; re-authorization restarts the honor period.
- Fees: authorization and capture can incur merchant fees in some markets (live behaviour; sandbox charges nothing real).

### 1.4 ⚠ Doc finding that shapes the design — partner features

The *Delay disbursement* / Commerce Platform docs describe `disbursement_mode: "DELAYED"`, `platform_fees`, and the `PayPal-Auth-Assertion` header — and the platforms authorize/capture guide states **"You must be an approved partner to use this integration"** with seller onboarding required. That is the marketplace variant. **We do not use it.** Rosette-Sealed Checkout makes his sandbox Business account the merchant of record (the app is that account's own app, payee defaults to it), so the standard Orders v2 AUTHORIZE → capture path — documented for ordinary merchants, e.g. "verifying that you have the item in stock" — covers the seal gate with no partner approval, no third-party onboarding, no platform fees. If a future version wants true third-party escrow, that is a partner-application project, not a config change.

### 1.5 Sandbox quirks (from docs + platform behaviour)

- Sandbox money is fake end-to-end; sandbox accounts carry fake balances and fake cards. Nothing in sandbox can touch a real account — but the **repo must never carry a production base URL or live credentials** (ENGINE-CONTRACT.md §7), because the same code with live keys would be a real money path.
- Sandbox can be slow and occasionally flaky; the demo polls `GET order` with backoff rather than trusting a single response.
- A v2-API scope gotcha from the Commerce Platform upgrade notes: older apps could get `401` on Orders v2 "even in sandbox" until scopes were updated via an account manager. Our app was created fresh in 2026 for this dashboard — if a `401` appears on the first create-order call on Nov 9, check the app's scopes/features on its dashboard page before debugging code.
- `PayPal-Request-Id` reuse with a *different* body is rejected — the engine's change ids are unique per change, so this only bites on manual curl testing.

---

## 2. Channel3 — product-data API

**Sources:** channel3.docs.buildwithfern.com (official docs), PyPI `channel3` + `channel3_sdk` package pages, trychannel3.com/developers + Channel3's own blog posts, the hackathon sponsor page (paypalaihackathon.devpost.com/details/channel3).

### 2.1 Auth and where the key is obtained

- Auth: API key in the **`x-api-key`** header (SDKs read env var `CHANNEL3_API_KEY`).
- **Obtained at build time:** sign in at trychannel3.com/developers (developer account already claimed Nov 1) → dashboard → create/copy the API key → into local `.env`. Then apply promo code **`PAYPAL-HACKATHON-2026`** in the dashboard to attach the **20,000 one-time credits** (+ 1,000/month free tier). If the key runs dry mid-build, Channel3's hackathon Discord channel tops up free (their LinkedIn post says so verbatim).

### 2.2 Call shapes (enough to design the catalogue side)

REST lives under `/v1` (their own docs/blog: a search function "POSTs to Channel3's /v1/search endpoint"); the official SDKs wrap it. Design against these shapes:

```python
# Python (Fern SDK, package name "channel3")
from channel3 import Channel3Client
client = Channel3Client(api_key=os.environ["CHANNEL3_API_KEY"])

resp = client.products.search(query="Charizard ex 199/165", limit=10)
for p in resp.products:
    p.title; p.brand; p.images
    p.offers[0].price; p.offers[0].url        # merchant offer + attributed buy link
resp.next_page_token                           # cursor pagination

client.products.image_search(image_url=<listing photo URL>)   # visual search
client.products.similar(product_id=…)                          # lookalikes
client.products.lookup(url=<any retailer product URL>)         # → structured metadata
client.products.retrieve(product_id=…); client.products.enrich(…)
client.brands.list() / .search() / .get()
```

A search returns **deduplicated products, not web pages**: `title, brand, description, images, categories, attributes, variants` (each attribute value stored three ways — store spelling, brand spelling, and a normalized enum agents can filter on) and **`offers[]`** across 25,000+ retailers with `price, availability, url`. Response time is advertised at <1s. There is also an **MCP server** (`https://mcp.trychannel3.com/`, tools `search_products` / `get_products`) — a possible agent surface, but the build designs against REST/SDK so the engine's evidence functions stay plain functions.

**What a lookup returns for our design:** for the comps panel we need, per candidate: title, one image, best offer price + merchant + URL, availability. All present in the shape above. The panel stores the top 5 as evidence on the seal receipt (title, price, merchant, URL, fetched-at).

### 2.3 Credit mechanics (vs the claim state)

- Free tier: 1,000 searches/month, no card. Hackathon promo: 20,000 credits. Paid rate after free: **$7 per 1,000 queries** (their published comparison) — i.e. one credit ≈ one search. **No contradiction with the claim state**: the claimed 20,000 credits + 1,000/month is what the dashboard showed Nov 1, and the docs' pricing is consistent with credits being prepaid searches.
- **Design budget:** the demo's comps panel costs 1 text search (+1 image search per listing, optional). A full build week of rehearsals is a few hundred searches — the 20k is effectively bottomless for this event; no rationing logic needed, but the engine logs searches as receipts anyway (house standard).

### 2.4 ⚠ Flags

- **Two Python SDK generations exist** (`channel3` — Fern, `Channel3Client.products.search(...)`; and `channel3_sdk` — Stainless, `Channel3(...).search.perform(...)`), plus a TS SDK. Pin **one** at build time (scaffold assumes `channel3`), and isolate it behind our `adapters/channel3/` module so a swap touches one file.
- **TC
...[truncated 6702 chars]