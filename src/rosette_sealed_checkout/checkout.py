"""Checkout orchestration — the demo's happy path, in one place.

seller checkout(item) = seal #1 (listing seal, receipt pinned to the
listing) -> Channel3 market panel attached as advisory evidence ->
create order -> buyer approves -> authorize. Funds are now HELD, not
captured. The seal gate (seal #2 -> dry-run -> approve -> apply) runs in
engine.verbs; the web layer calls it, it does not reimplement it.

Every payload carries the rail mode: "stub" until the owner's sandbox
credential pull, then "sandbox-live". The UI prints the mode on screen —
stub state is never dressed up as a real PayPal response.

On the sandbox rail the buyer approval is PayPal's own step: the order
comes back CREATED with an approve link, and authorization happens once
PayPal reports APPROVED (adapters/paypal.refresh_and_authorize). The
state caption is therefore DERIVED from the order's actual state at
render time — it can never claim "held" for an order that has settled,
been voided, or is still waiting on its buyer.
"""
from __future__ import annotations

from . import config
from .adapters import channel3, paypal
from .engine import verbs
from .seal import verify as seal_verify


def state_caption(order: dict) -> str:
    """One-line state caption computed from the order's real state."""
    payments = order["purchase_units"][0].get("payments", {})
    if payments.get("captures"):
        return "CAPTURED — settled (sandbox money, seller paid)"
    auths = payments.get("authorizations") or []
    if order.get("status") == "VOIDED" or \
            (auths and auths[0].get("status") == "VOIDED"):
        return "VOIDED — hold released, buyer keeps their money"
    if auths:
        return "AUTHORIZED — held, not settled"
    if order.get("status") == "APPROVED":
        return "APPROVED — buyer approved; authorizing"
    if order.get("status") == "CREATED":
        return "CREATED — waiting for the buyer to approve in PayPal sandbox"
    return str(order.get("status", ""))


def checkout(item: dict, snapshot: dict) -> dict:
    """Run seal #1 + create order (+ authorize when the rail allows)."""
    seal1 = seal_verify(item["identity"], item.get("fulfilment_evidence"))
    panel = channel3.market_panel(item["identity"])
    order, fields = paypal.create_and_authorize(
        change_id=f"buy-{item['item_id']}",
        amount=item["price"],
        seal_receipt_id=seal1["receipt_ref"])
    return {
        "item_id": item["item_id"],
        "rail_mode": config.paypal_mode(),
        "seal_listing": seal1,
        "market_panel": panel,
        "order": order,
        "approve_url": fields.get("approve_url"),
        "state_caption": state_caption(order),
    }


def seal_gate(item: dict, snapshot: dict, order: dict) -> dict:
    """Seal #2 + dry-run for one authorized listing (approval comes next)."""
    proposal = verbs.check(snapshot, {"order": {item["item_id"]: order}})
    change = next(c for c in proposal["changes"]
                  if c["params"]["item_id"] == item["item_id"])
    report = verbs.dry_run(proposal)
    preview = next(r for r in report["results"]
                   if r["change_id"] == change["change_id"])
    return {"proposal": proposal, "change": change, "preview": preview}
