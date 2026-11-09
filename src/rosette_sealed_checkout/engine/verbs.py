"""Contract verbs (ENGINE-CONTRACT.md v1, §1) — reference implementation.

Six verbs over local state, implementing the contract shapes exactly:
- read:     immutable, content-hashed snapshots; demo source = the
            sanitized sandbox copies in data/demo/ (listings.json)
- check:    pure; runs the Rosette seal evidence function and proposes
            capture when the seal passes, void when it does not.
            Inconclusive is never capture-proposable (the seal gate).
- dry_run:  previews effects; unpreviewable changes are fenced out.
- approve:  HUMAN actor only ("owner"); per-change decisions.
- apply:    executes approved changes through the registered effector;
            money movements are never auto-retried; change_id is the
            idempotency key.
- undo:     compensating action via the effector; appends a new receipt.

Effector registration is this build's own (paypal.checkout, stub rail
until the sandbox credential pull). The receipt log is the audit surface.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .. import state as _state
from ..seal import LABELS, verify as seal_verify

DEMO_DIR = Path(__file__).resolve().parents[3] / "data" / "demo"
FRESHNESS_MINUTES = 15  # contract §2 default for money-adjacent sources

_snapshots: dict[str, dict] = {}


# ---------------------------------------------------------------- read
def read(scope: dict) -> dict:
    source = scope.get("source", "demo.listings")
    if source != "demo.listings":
        return {"error": {"kind": "source_unavailable",
                          "detail": f"reader {source!r} is not wired in "
                                    "the local demo (demo.listings only)",
                          "retryable": True}}
    items = json.loads((DEMO_DIR / "listings.json").read_text("utf-8"))
    only = (scope.get("filter") or {}).get("item_id")
    if only:
        items = [it for it in items if it["item_id"] == only]
    content = json.dumps(items, sort_keys=True).encode()
    snapshot = {
        "snapshot_id": _state.new_id("snap"),
        "source": source,
        "fetched_at": _state._now(),
        "content_hash": "sha256:" + hashlib.sha256(content).hexdigest(),
        "items": items,
    }
    _snapshots[snapshot["snapshot_id"]] = snapshot
    _state.append_receipt({
        "verb": "read", "actor": "engine",
        "summary": f"Read {len(items)} item(s) from {source} "
                   f"-> {snapshot['snapshot_id']} "
                   f"({snapshot['content_hash'][:23]}…)",
        "external_ids": {}, "undoable": False, "undoes": None})
    return snapshot


# --------------------------------------------------------------- check
def check(snapshot: dict, rules: dict | None = None) -> dict:
    """Proposal rules for the seal gate (PayPal layer config, contract §7):

    - seal verdict Likely Genuine      -> propose capture
    - seal verdict Likely Counterfeit  -> propose void
    - seal verdict Inconclusive        -> propose void (funds are never
      captured on an abstention) AND the listing carries the mandatory
      Inconclusive case into the demo
    """
    order_ctx = (rules or {}).get("order") or {}
    changes = []
    for item in snapshot.get("items", []):
        seal = seal_verify(item["identity"], item.get("fulfilment_evidence"))
        verdict = seal["verdict"]
        order = order_ctx.get(item["item_id"])
        auth_id = None
        if order:
            payments = order["purchase_units"][0].get("payments", {})
            auths = payments.get("authorizations") or []
            auth_id = auths[0]["id"] if auths else None
        if verdict == "likely_genuine":
            action, risk = "capture", "money-movement"
            reason = (f"Fulfilment seal passed ({LABELS[verdict]}, "
                      f"receipt {seal['receipt_ref']})")
        elif verdict == "likely_counterfeit":
            action, risk = "void", "money-movement"
            reason = (f"Fulfilment seal FAILED ({LABELS[verdict]}, "
                      f"receipt {seal['receipt_ref']}) — release the hold")
        else:
            action, risk = "void", "money-movement"
            reason = (f"Seal INCONCLUSIVE ({seal['summary']}) — an "
                      "abstention never captures; release the hold")
        changes.append({
            "change_id": _state.new_id("chg"),
            "target": f"paypal.authorization:{auth_id or 'pending'}",
            "action": action,
            "params": {"authorization_id": auth_id,
                       "amount": item["price"],
                       "item_id": item["item_id"]},
            "reason": reason,
            "evidence": [{"kind": "seal_verdict",
                          "ref": seal["receipt_ref"],
                          "summary": seal["summary"]}],
            "risk": risk,
        })
    proposal = {"proposal_id": _state.new_id("prop"),
                "snapshot_id": snapshot.get("snapshot_id"),
                "changes": changes}
    _state.append_receipt({
        "verb": "check", "actor": "engine",
        "summary": f"Proposal {proposal['proposal_id']}: " +
                   ", ".join(f"{c['action']} for "
                             f"{c['params']['item_id']}"
                             for c in changes),
        "external_ids": {}, "undoable": False, "undoes": None})
    _state.save("proposals.json",
                {**_state.load("proposals.json", {}),
                 proposal["proposal_id"]: proposal})
    return proposal


# ------------------------------------------------------------- dry_run
def dry_run(proposal: dict) -> dict:
    results = []
    for change in proposal.get("changes", []):
        auth_id = change["params"].get("authorization_id")
        if not auth_id:
            results.append({
                "change_id": change["change_id"], "previewable": False,
                "effects_preview": "No authorization exists yet — run the "
                                   "buyer checkout first.",
                "warnings": ["not_authorized"]})
            continue
        amount = change["params"]["amount"]
        if change["action"] == "capture":
            preview = (f"Capture ${amount['value']} from authorization "
                       f"{auth_id} -> seller sandbox balance")
        else:
            preview = (f"Void authorization {auth_id} — the "
                       f"${amount['value']} hold is released and the "
                       "buyer keeps their money")
        results.append({"change_id": change["change_id"],
                        "previewable": True,
                        "effects_preview": preview, "warnings": []})
    report = {"proposal_id": proposal.get("proposal_id"),
              "results": results}
    _state.append_receipt({
        "verb": "dry_run", "actor": "engine",
        "summary": f"Dry-run {report['proposal_id']}: " +
                   "; ".join(r["effects_preview"] for r in results),
        "external_ids": {}, "undoable": False, "undoes": None})
    return report


# ------------------------------------------------------------- approve
def approve(proposal_id: str, actor: str, decisions: list[dict]) -> dict:
    """The human gate. An agent identity can request, never grant (§1.4)."""
    if actor != "owner":
        return {"error": {"kind": "approval_required",
                          "detail": f"actor {actor!r} cannot approve — "
                                    "the approval gate is the owner's tap",
                          "retryable": False}}
    record = {"proposal_id": proposal_id, "actor": actor,
              "decided_at": _state._now(), "decisions": decisions}
    for d in decisions:
        _state.append_receipt({
            "verb": "approve", "actor": "owner",
            "summary": f"Change {d['change_id']}: {d['decision']}"
                       + (f" — {d['note']}" if d.get("note") else ""),
            "external_ids": {}, "undoable": False, "undoes": None})
    approvals = _state.load("approvals.json", {})
    approvals[proposal_id] = record
    _state.save("approvals.json", approvals)
    return record


# --------------------------------------------------------------- apply
def apply(approval: dict) -> dict:
    from ..adapters import paypal  # registered effector: paypal.checkout

    proposals = _state.load("proposals.json", {})
    proposal = proposals.get(approval["proposal_id"])
    if proposal is None:
        return {"error": {"kind": "effector_failed",
                          "detail": "proposal not found for approval",
                          "retryable": False}}
    by_id = {c["change_id"]: c for c in proposal["changes"]}
    results = []
    for decision in approval["decisions"]:
        change = by_id.get(decision["change_id"])
        if change is None:
            continue
        if decision["decision"] != "approved":
            results.append({"change_id": change["change_id"],
                            "status": "denied"})
            continue
        try:
            out = paypal.apply_change(change, change["params"])
        except Exception as exc:  # failed apply is terminal (§1.5)
            _state.append_receipt({
                "verb": "apply", "actor": "engine",
                "summary": f"FAILED {change['action']} "
                           f"{change['change_id']}: {exc}",
                "external_ids": {}, "undoable": False, "undoes": None})
            results.append({"change_id": change["change_id"],
                            "status": "failed", "error": str(exc)})
            continue
        receipt = _state.append_receipt({
            "verb": "apply", "actor": "engine",
            "summary": out["summary"],
            "external_ids": {k: v for k, v in out.items()
                             if k != "summary"},
            "undoable": change["action"] == "capture",
            "undoes": None})
        results.append({"change_id": change["change_id"],
                        "status": "applied",
                        "receipt_id": receipt["receipt_id"]})
    return {"proposal_id": approval["proposal_id"], "results": results}


# ---------------------------------------------------------------- undo
def undo(receipt_id: str) -> dict:
    from ..adapters import paypal

    original = next((r for r in _state.receipts()
                     if r["receipt_id"] == receipt_id), None)
    if original is None:
        return {"error": {"kind": "undo_unavailable",
                          "detail": f"no receipt {receipt_id}",
                          "retryable": False}}
    if not original.get("undoable"):
        return {"error": {"kind": "undo_unavailable",
                          "detail": "this receipt declares undoable: false",
                          "retryable": False}}
    out = paypal.compensate(original)
    receipt = _state.append_receipt({
        "verb": "undo", "actor": "engine",
        "summary": out["summary"],
        "external_ids": {k: v for k, v in out.items() if k != "summary"},
        "undoable": False, "undoes": receipt_id})
    return {"receipt_id": receipt["receipt_id"], "undoes": receipt_id,
            "status": "undone"}
