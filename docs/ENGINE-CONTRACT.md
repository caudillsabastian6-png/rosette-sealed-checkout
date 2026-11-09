# ENGINE-CONTRACT.md — v1

**What this is:** the interface contract between the **Blast Radius engine** (built for the Vultr: Agent Rush hackathon, Nov 5–8) and every layer that sits on it — starting with the **PayPal layer** (Rosette-Sealed Checkout, build Nov 9–12). The engine does not exist yet. This contract is written first so the Vultr build can implement against it and the PayPal build can be designed now **without forking the engine later**.

**Reuse law (Latch):** the engine legally feeds Vultr → PayPal → Codegeist → DeveloperWeek. Each event adds only the layer it asks for. Anything that would require a core change for a downstream event is a contract bug — raise it here, don't fork.

**Conventions:** JSON-shaped payloads (language-agnostic). All timestamps UTC ISO-8601. All money as `{ "currency": "USD", "value": "25.00" }` strings, PayPal-style — never floats. Every identifier is opaque and prefixed (`snap_`, `prop_`, `chg_`, `rcpt_`).

---

## 1. The six verbs

The engine is one state machine with six operations. Jobs (repricing, reconciliation, checkout-sealing, …) are configurations of these verbs, never new machinery.

### 1.1 `read(scope) → Snapshot`

Reads real-world state through registered **readers** (eBay listings, tracker records, PayPal orders, …). Read-only by construction: a reader has no write path.

```jsonc
// in
{ "scope": { "source": "ebay.trading", "filter": { "list": "active" } },
  "idempotency_key": "…" }
// out — Snapshot
{ "snapshot_id": "snap_…", "source": "ebay.trading", "fetched_at": "…",
  "content_hash": "sha256:…",
  "items": [ { "item_id": "…", "kind": "listing", "fields": { /* source-shaped */ } } ] }
```

Guarantees: a snapshot is immutable; two reads of unchanged state produce the same `content_hash`; the snapshot — never the live source — is what later verbs reason about.

### 1.2 `check(snapshot, rules) → Proposal`

Runs **evidence functions** (see §6) over a snapshot and produces proposed changes, each carrying its reasons and evidence. Pure: no side effects, safe to re-run.

```jsonc
// out — Proposal
{ "proposal_id": "prop_…", "snapshot_id": "snap_…",
  "changes": [ {
      "change_id": "chg_…", "target": "paypal.authorization:…",
      "action": "capture",                       // effector action name
      "params": { "amount": { "currency": "USD", "value": "25.00" } },
      "reason": "Fulfilment seal passed (receipt rcpt_…)",
      "evidence": [ { "kind": "seal_verdict", "ref": "rcpt_…", "summary": "…" } ],
      "risk": "money-movement"                   // low | medium | money-movement
  } ] }
```

### 1.3 `dry_run(proposal) → DryRunReport`

Simulates every change against current state (re-reading the minimum needed) and reports the exact effects that *would* occur, plus warnings (stale snapshot, authorization inside its honor-period edge, amount drift). A change whose effect cannot be previewed is marked `previewable: false` and **cannot be approved** — that is a fence, not a warning.

```jsonc
{ "proposal_id": "prop_…",
  "results": [ { "change_id": "chg_…", "previewable": true,
                 "effects_preview": "Capture $25.00 from authorization 8AH… → seller sandbox balance",
                 "warnings": [] } ] }
```

### 1.4 `approve(proposal_id, actor, decision) → ApprovalRecord`

The human gate. `actor` must be a human identity (in this house: the owner). An agent identity can *request* approval, never grant it. Decision is per-change: `approved | denied`, with an optional note. Denied changes are terminal — they are not re-proposable from the same snapshot.

```jsonc
{ "proposal_id": "prop_…", "actor": "owner", "decided_at": "…",
  "decisions": [ { "change_id": "chg_…", "decision": "approved", "note": "" } ] }
```

### 1.5 `apply(approval) → ApplyResult`

Executes approved changes through registered **effectors** (§5), one receipt per change. Partial failure is a normal outcome: each change lands in `applied | failed` with its error; the engine never silently retries a money movement — a failed apply is re-proposed from a fresh snapshot instead.

Idempotency: every effector call carries the `change_id` as its idempotency key (for PayPal, the `PayPal-Request-Id` header), so a crashed-and-restarted apply cannot double-capture.

### 1.6 `log` and `undo(receipt_id) → UndoResult`

`log` is not a verb callers invoke; it is the engine's append-only receipt stream (§4) that every other verb writes to.

`undo` executes the **compensating action** registered by the effector that produced the receipt (capture → refund; authorization → void; reprice → restore prior price) and appends a *new* receipt pointing at the original. History is never rewritten. If no compensating action exists, `undo` fails loudly with `undoable: false` on the original receipt — effectors must declare this at registration time, not at undo time.

## 2. Change lifecycle

```
PROPOSED ──dry_run──► DRY_RUN_OK ──approve──► APPROVED ──apply──► APPLIED ──undo──► UNDONE
    │                     │                      │                    │
    └──► (stale)          └──► REJECTED          └──► DENIED          └──► FAILED (terminal;
         snapshot              (unpreviewable)                          re-propose fresh)
         expired
```

A snapshot older than its source's freshness window (default 15 min for money-adjacent sources) makes its proposals `stale`: dry-run refuses until re-read. This is what stops the classic agent bug of approving yesterday's state.

## 3. Errors

Typed, machine-readable, never stringly:

```jsonc
{ "error": { "kind": "stale_snapshot | unpreviewable | approval_required |
                      effector_failed | undo_unavailable | source_unavailable",
             "change_id": "chg_…", "detail": "…", "retryable": false } }
```

`retryable: true` is only ever set for `source_unavailable` on a *read*. Money-movement failures are never auto-retried (§1.5).

## 4. Receipts

Every verb appends receipts to one append-only log (JSONL in the reference implementation):

```jsonc
{ "receipt_id": "rcpt_…", "ts": "…", "verb": "apply",
  "change_id": "chg_…", "actor": "engine|owner",
  "summary": "Captured $25.00, authorization 8AH…, capture id 9XY…",
  "external_ids": { "paypal_order_id": "…", "paypal_capture_id": "…" },
  "undoable": true, "undoes": null }
```

Receipts are the audit surface every event reuses: the Vultr morning list reads them, the PayPal demo's receipt screen renders them, Codegeist's agent files them. If it isn't in the receipt log, it didn't happen.

## 5. Effectors — the only write path

An **effector** is a registered adapter that turns an approved change into one external side effect. Registration declares: `action` name, target source, the apply call, the compensating (undo) call or `undoable: false`, and the idempotency-key mapping. Core ships with none; each build registers its own.

| Effector | Build | Actions (apply → compensate) |
|---|---|---|
| `ebay.price` | Vultr | revise listing price → restore prior price |
| `ebay.listing` | Vultr | end/revise listing → relist-same (where legal) |
| `paypal.checkout` | **PayPal** | `capture` authorization → `refund` capture; `void` authorization → *(none — terminal, `undoable: false`)*; `authorize` order → `void` |
| `paypal.refund` | **PayPal** | refund capture → *(none — terminal)* |

## 6. Evidence functions — the pluggable "check"

`check` runs named evidence functions: pure `(snapshot, params) → evidence[]` units. The Rosette seal is one: `seal.verify(identity, scans) → { verdict, receipt_ref }`. Model/LLM calls live **inside** evidence functions and nowhere else, so the model provider (Vultr Serverless Inference in the Vultr build; whatever this build wires) swaps without touching the engine. An evidence function must be able to abstain (`verdict: "inconclusive"`) — a checker that cannot say "I don't know" does not pass this contract.

## 7. The boundary — shared vs PayPal-specific

**Shared (engine core, built once for Vultr, reused unchanged):** the six verbs and lifecycle; snapshot store + hashing; receipt log; approval UI/API (the owner's tap is the same surface in every build); dry-run differ; evidence-function registry; effector registry; error taxonomy. Readers for eBay (Trading + Sell) are shared — the PayPal build consumes the same snapshots the Vultr jobs use.

**PayPal-specific (this build only, all behind the effector/reader boundary):**
- `paypal.checkout` + `paypal.refund` effectors (INTEGRATIONS.md §1 for the exact calls).
- PayPal **reader**: order/authorization state (`GET /v2/checkout/orders/{id}`) so `check` can reason about held funds.
- **Sandbox personas**: seller = his sandbox Business account (merchant of record / payee), buyer = his sandbox Personal account. Persona config is data (which sandbox emails/accounts), never code.
- The **seal gate** composition: the PayPal layer's job definitions — `check` rules that say "a capture change may only be proposed when `seal.verify` returned a passing verdict on the fulfilment evidence, and must be denied-proposable when it returned Inconclusive." The *rule* is PayPal-layer configuration; the *mechanism* (evidence functions + approval) is core.
- Checkout UI (JS SDK buttons, `intent=authorize`) and the demo's listing/receipt screens.

**Explicitly NOT in the PayPal layer:** repricing/reconciliation logic (Vultr jobs), any marketplace/platform-fee machinery (needs PayPal partner onboarding we don't have and don't need — see INTEGRATIONS.md §1.4), and any live-money path (sandbox base URL is a config constant with no production value anywhere in the repo).

## 8. What the Vultr build must deliver for this contract to hold

1. The six verbs with the payload shapes above (field names may be adjusted **before Nov 8** by amending this file; after that, changes are breaking and need a v2).
2. The receipt log as the single audit surface (JSONL or equivalent append-only store).
3. Effector + evidence-function registries (the PayPal build registers into them; it does not subclass the engine).
4. The approval surface usable headlessly (API) as well as in a UI — the PayPal demo's approval tap calls the same endpoint the Vultr morning list uses.
5. A **stub mode**: with no effectors registered, `apply` records intent-only receipts. The PayPal build's Nov 9 start uses an in-repo stub implementing exactly this contract if the Vultr engine is still in flight — the stub and the real engine must be interchangeable behind these verbs.
