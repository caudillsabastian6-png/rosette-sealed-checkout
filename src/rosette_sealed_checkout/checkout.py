"""Checkout orchestration — the demo's happy path, in one place.

seller checkout(item) = seal #1 (listing seal, receipt pinned to the
listing) -> Channel3 market panel attached as advisory evidence ->
create order -> buyer approves -> authorize. Funds are now HELD, not
captured. The seal gate (seal #2 -> dry-run -> approve -> apply) runs in
engine.verbs; the web layer calls it, it does not reimplement it.

Every payload carries the rail mode: "stub" until the owner's sandbox
credential pull, then "sandbox-live". The UI prints the mode on screen —
stub state is never dressed up as a real PayPal response.
"""
from __future__ import annotations

from . import config
from .adapters import channel3, paypal
from .engine import verbs
from .seal import verify as seal_verify


def checkout(item: dict, snapshot: dict) -> dict:
    """Run seal #1 + order/approve/authorize for one listing."""
    seal1 = seal_verify(item["identity"], item.get("fulfilment_evidence"))
    panel = channel3.market_panel(item["identity"])
    order, external = paypal.create_and_authorize(
        change_id=f"buy-{item['item_id']}",
        amount=item["price"],
        seal_receipt_id=seal1["receipt_ref"])
    return {
        "item_id": item["item_id"],
        "rail_mode": config.paypal_mode(),
        "seal_listing": seal1,
        "market_panel": panel,
        "order": order,
        "state_caption": "AUTHORIZED — held, not settled",
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
