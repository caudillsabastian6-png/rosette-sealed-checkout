# Rosette-Sealed Checkout

**A PayPal checkout where the money waits for proof: a collectible card's payment is authorized and held, and only captured after Rosette's verification seal passes on the shipped card — if the seal fails, the authorization is voided and the buyer keeps their money.**

Built for the PayPal AI Hackathon (Devpost, submission due Nov 12, 2026). All money in this project is **PayPal sandbox** money — no real funds ever move.

## How it works

1. A seller's agent drafts a listing from a **verified card identity** (real store state read through the eBay APIs).
2. **Rosette** — a print-forensics verification method — attaches its seal (verdict + receipt) to the listing; a market panel from Channel3 sits beside it.
3. The buyer checks out with PayPal. The payment is **authorized and held, not captured**.
4. Before capture, the engine re-checks the fulfilment evidence (shipped-card scans vs the sealed identity) and a human approves.
5. Seal passes → **capture**. Seal fails or approval denied → **void**. Every step is written to an append-only receipt log, and a capture can be undone by refund.

The state machine underneath — *read → check → dry-run → approve → apply / log / undo* — is the **Blast Radius engine**, first built for the Vultr: Agent Rush hackathon and reused here under a published interface contract (vendored in `docs/ENGINE-CONTRACT.md`).

## Tools used

- **PayPal sandbox** — Orders v2 + Payments v2 (create order, authorize, capture, void, refund); JavaScript SDK checkout with `intent=authorize`.
- **Kernel** — cloud Chromium browsers the agent drives for the buyer-side checkout (live view used in the demo video).
- **Channel3** — product-data API for the comparable-offers market panel.
- **eBay APIs** — Sell (Account / Fulfillment / Inventory) + Trading (GetMyeBaySelling), read-only, for real store state.
- **Blast Radius engine** — the shared read/check/dry-run/approve/apply/log/undo state machine (see above).
- **Rosette** — the house card-verification method that issues the seal.

## Setup

The demo core is **Python standard library only** — no installs needed.

```bash
# 1. Python 3.11+. Nothing to install for the local demo.
# 2. (Optional, for live sandbox calls) copy .env.example to .env and fill
#    in SANDBOX-ONLY credentials: PayPal sandbox app client id/secret,
#    Channel3 API key, Kernel API key. Without them the app runs on its
#    stub rail and labels itself "stub" on every screen — PayPal-shaped
#    simulated responses, clearly marked, never presented as real calls.
#    eBay readers are optional: without them the demo seeds from data/demo/.
# 3. Run:
PYTHONPATH=src python -m rosette_sealed_checkout.web   # http://localhost:8080
# 4. Tests (standalone or under pytest):
python3 tests/test_flow.py
```

Walk the demo: open a listing → **Buy** (order is created, the demo buyer
approves, funds are AUTHORIZED — held, not settled) → **Run fulfilment
seal** (seal #2 + dry-run) → the owner approval tap captures, or the seal
gate voids. The third listing is the mandatory Inconclusive case: blurred
fulfilment scans, the seal abstains, and an abstention never captures.
Every step lands on the receipt chain at `/receipts`.

Demo data in `data/demo/` is sanitized sandbox copies — no real listings, buyers, or credentials.

## Licence

MIT — see `LICENSE`. Submissions remain the intellectual property of their creators (PayPal AI Hackathon Rules §7).
