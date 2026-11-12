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


def test_caption_reflects_actual_state():
    """The checkout caption is derived from the order's live state —
    a settled or voided order must never still claim 'held'."""
    from rosette_sealed_checkout.checkout import state_caption

    def order(status, payments=None):
        return {"status": status,
                "purchase_units": [{"payments": payments or {}}]}

    held = state_caption(order("AUTHORIZED",
                               {"authorizations": [{"id": "A",
                                                    "status": "CREATED"}]}))
    assert held == "AUTHORIZED — held, not settled"
    settled = state_caption(order(
        "COMPLETED",
        {"authorizations": [{"id": "A", "status": "CAPTURED"}],
         "captures": [{"id": "C", "status": "COMPLETED"}]}))
    assert "held" not in settled and "CAPTURED" in settled
    # A refunded capture must never still claim "settled" — sandbox
    # shape (PayPal flips the capture status, refunds listed):
    refunded = state_caption(order(
        "COMPLETED",
        {"authorizations": [{"id": "A", "status": "CAPTURED"}],
         "captures": [{"id": "C", "status": "REFUNDED"}],
         "refunds": [{"id": "R", "status": "COMPLETED"}]}))
    assert refunded.startswith("REFUNDED") and "settled" not in refunded
    # Stub-rail shape (capture stays COMPLETED, refund recorded):
    refunded_stub = state_caption(order(
        "COMPLETED",
        {"authorizations": [{"id": "A", "status": "CAPTURED"}],
         "captures": [{"id": "C", "status": "COMPLETED"}],
         "refunds": [{"id": "R", "status": "COMPLETED"}]}))
    assert refunded_stub.startswith("REFUNDED")
    # A partial refund is its own honest state, not a full refund:
    partial = state_caption(order(
        "COMPLETED",
        {"authorizations": [{"id": "A", "status": "CAPTURED"}],
         "captures": [{"id": "C", "status": "PARTIALLY_REFUNDED"}],
         "refunds": [{"id": "R", "status": "COMPLETED"}]}))
    assert partial.startswith("PARTIALLY REFUNDED")
    voided = state_caption(order("VOIDED",
                                 {"authorizations": [{"id": "A",
                                                      "status": "VOIDED"}]}))
    assert voided.startswith("VOIDED")
    waiting = state_caption(order("CREATED"))
    assert "waiting for the buyer" in waiting


def test_adapter_rail_is_stub_without_credentials():
    """With no sandbox credentials in .env the adapter must serve the
    stub rail (tests run credential-free) — and say so."""
    assert paypal.rail() == "stub"
    order, fields = paypal.create_and_authorize(
        change_id="test-rail", amount={"currency": "USD", "value": "1.00"},
        seal_receipt_id="rcpt_test")
    assert order["id"].startswith("DEMO-")
    assert fields["approve_url"] is None


def test_capture_request_shape_is_conforming():
    """The Payments v2 capture call must send an explicit amount +
    final_capture body AND Prefer: return=representation — without
    the header PayPal answers with a minimal representation (no
    amount field), which is what broke the Nov 10 live apply."""
    from rosette_sealed_checkout.adapters.paypal.client import \
        SandboxPayPalClient

    client = SandboxPayPalClient()  # base-URL check only; no creds read
    seen = {}

    def fake_call(method, path, body=None, request_id=None,
                  extra_headers=None):
        seen.update(method=method, path=path, body=body,
                    request_id=request_id, extra_headers=extra_headers)
        return {"id": "CAP1", "status": "COMPLETED",
                "amount": {"currency_code": "USD", "value": "25.00"}}

    client._call = fake_call
    out = client.capture_authorization(
        "AUTH1", amount={"currency": "USD", "value": "25.00"},
        request_id="chg_test")
    assert out["id"] == "CAP1"
    assert seen["method"] == "POST"
    assert seen["path"] == "/v2/payments/authorizations/AUTH1/capture"
    assert seen["body"] == {"amount": {"currency_code": "USD",
                                       "value": "25.00"},
                            "final_capture": True}
    assert seen["extra_headers"] == {"Prefer": "return=representation"}
    assert seen["request_id"] == "chg_test"  # contract §1.5 idempotency


def test_sandbox_apply_tolerates_minimal_capture_body():
    """Regression for the Nov 10 live failure: PayPal's capture
    response was the minimal representation {"id", "status", "links"}
    with NO amount field, and the adapter died on KeyError('amount')
    after the money had already moved. The adapter must take the
    amount from the approved change params instead."""
    class FakeClient:
        def __init__(self):
            self.got = {}

        def capture_authorization(self, authorization_id, *, amount,
                                  request_id):
            self.got = {"authorization_id": authorization_id,
                        "amount": amount, "request_id": request_id}
            return {"id": "0DT92802L3371522U", "status": "COMPLETED",
                    "links": []}  # the exact live minimal body shape

    fake = FakeClient()
    orig_rail, orig_live = paypal.rail, paypal._live_client
    paypal.rail = lambda: "sandbox"
    paypal._live_client = lambda: fake
    try:
        change = {"change_id": "chg_regression", "action": "capture"}
        params = {"authorization_id": "7M4577450N801910A",
                  "amount": {"currency": "USD", "value": "25.00"},
                  "item_id": "demo-listing-001"}
        out = paypal.apply_change(change, params)
    finally:
        paypal.rail, paypal._live_client = orig_rail, orig_live
    assert out["paypal_capture_id"] == "0DT92802L3371522U"
    assert "$25.00" in out["summary"] and "real sandbox rail" in \
        out["summary"]
    assert fake.got["amount"] == {"currency": "USD", "value": "25.00"}
    assert fake.got["request_id"] == "chg_regression"


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
