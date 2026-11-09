"""End-to-end flow tests — engine verbs + stub rail + seal gate.

Runs under pytest OR standalone:  python3 tests/test_flow.py
(var/ state is isolated to a temp dir so tests never touch demo state.)
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rosette_sealed_checkout import state  # noqa: E402

state.VAR_DIR = Path(tempfile.mkdtemp(prefix="rsc-test-"))

from rosette_sealed_checkout.adapters import paypal  # noqa: E402
from rosette_sealed_checkout.engine import verbs  # noqa: E402
from rosette_sealed_checkout.seal import verify  # noqa: E402


def _item(snapshot, item_id):
    return next(i for i in snapshot["items"] if i["item_id"] == item_id)


def _checkout_and_gate(snapshot, item):
    order, _ = paypal.create_and_authorize(
        change_id=f"test-{item['item_id']}", amount=item["price"],
        seal_receipt_id="rcpt_test")
    assert order["status"] == "AUTHORIZED"
    proposal = verbs.check(snapshot, {"order": {item["item_id"]: order}})
    change = next(c for c in proposal["changes"]
                  if c["params"]["item_id"] == item["item_id"])
    report = verbs.dry_run(proposal)
    preview = next(r for r in report["results"]
                   if r["change_id"] == change["change_id"])
    return order, proposal, change, preview


def test_seal_verdicts():
    ident = {"name": "A", "set": "S", "number": "001/100"}
    assert verify(ident, {**ident, "legible": True})["verdict"] == \
        "likely_genuine"
    assert verify(ident, {"name": "B", "set": "S", "number": "001/100",
                          "legible": True})["verdict"] == \
        "likely_counterfeit"
    assert verify(ident, {**ident, "legible": False})["verdict"] == \
        "inconclusive"
    assert verify(ident, None)["verdict"] == "inconclusive"
    assert verify(ident, {"name": "A", "set": "S", "number": "999/100",
                          "legible": True})["verdict"] == "inconclusive"


def test_read_snapshot_is_stable():
    a = verbs.read({"source": "demo.listings"})
    b = verbs.read({"source": "demo.listings"})
    assert a["content_hash"] == b["content_hash"]  # unchanged state, same hash
    assert len(a["items"]) == 3


def test_happy_path_capture_and_undo():
    snap = verbs.read({"source": "demo.listings"})
    order, proposal, change, preview = _checkout_and_gate(
        snap, _item(snap, "demo-listing-001"))
    assert change["action"] == "capture" and preview["previewable"]
    record = verbs.approve(proposal["proposal_id"], "owner",
                           [{"change_id": change["change_id"],
                             "decision": "approved", "note": ""}])
    result = verbs.apply(record)
    assert result["results"][0]["status"] == "applied"
    assert paypal.read_order(order["id"])["status"] == "COMPLETED"
    undone = verbs.undo(result["results"][0]["receipt_id"])
    assert undone["status"] == "undone"  # capture compensated by refund


def test_counterfeit_path_voids():
    snap = verbs.read({"source": "demo.listings"})
    order, proposal, change, _ = _checkout_and_gate(
        snap, _item(snap, "demo-listing-002"))
    assert change["action"] == "void"
    record = verbs.approve(proposal["proposal_id"], "owner",
                           [{"change_id": change["change_id"],
                             "decision": "approved", "note": ""}])
    verbs.apply(record)
    assert paypal.read_order(order["id"])["status"] == "VOIDED"


def test_inconclusive_never_captures():
    snap = verbs.read({"source": "demo.listings"})
    _, _, change, _ = _checkout_and_gate(snap, _item(snap, "demo-listing-003"))
    assert change["action"] == "void"  # abstention releases the hold


def test_agent_cannot_approve():
    snap = verbs.read({"source": "demo.listings"})
    proposal = verbs.check(snap, {"order": {}})
    out = verbs.approve(proposal["proposal_id"], "agent",
                        [{"change_id": proposal["changes"][0]["change_id"],
                          "decision": "approved", "note": ""}])
    assert out["error"]["kind"] == "approval_required"


def test_denial_is_terminal_for_that_change():
    snap = verbs.read({"source": "demo.listings"})
    order, proposal, change, _ = _checkout_and_gate(
        snap, _item(snap, "demo-listing-001"))
    record = verbs.approve(proposal["proposal_id"], "owner",
                           [{"change_id": change["change_id"],
                             "decision": "denied", "note": "no"}])
    result = verbs.apply(record)
    assert result["results"][0]["status"] == "denied"
    assert paypal.read_order(order["id"])["status"] == "AUTHORIZED"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"{len(fns)} tests passed")
