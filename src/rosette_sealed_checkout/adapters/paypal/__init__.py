"""PayPal sandbox adapter (INTEGRATIONS.md §1).

Registers:
- reader:   order/authorization state  (GET /v2/checkout/orders/{id})
- effector: paypal.checkout — create order, authorize, capture, void
- effector: paypal.refund    — refund a capture (undo of capture)

Sandbox base URL only. There is deliberately no production configuration.
Live calls live in client.py and activate only when the sandbox
credentials are present in .env (owner credential-pull step). Until then
the engine runs against the demo stub (adapters/demo) and the effector
below records intent-only receipts with mode "stub" (ENGINE-CONTRACT §8.5).
"""
from __future__ import annotations

from ... import state as _state
from ..demo.state_machine import DemoPayPal

_demo = DemoPayPal()


def _authorization_id(order: dict) -> str | None:
    payments = order["purchase_units"][0].get("payments", {})
    auths = payments.get("authorizations") or []
    return auths[0]["id"] if auths else None


def effector_spec() -> dict:
    """Registration declaration (ENGINE-CONTRACT §5)."""
    return {
        "effector": "paypal.checkout",
        "actions": {
            "authorize": {"compensate": "void"},
            "capture": {"compensate": "refund"},
            "void": {"compensate": None, "undoable": False},
        },
        "idempotency": "change_id -> PayPal-Request-Id",
    }


def create_and_authorize(*, change_id: str, amount: dict,
                         seal_receipt_id: str) -> tuple[dict, dict]:
    """Demo buyer path: create order -> buyer approves -> authorize.

    Stub transport (adapters/demo) until sandbox credentials land; the
    returned objects are PayPal-shaped and carry mode "stub". Returns
    (order, receipt-fields) for the engine to log.
    """
    order = _demo.create_order(amount=amount, custom_id=seal_receipt_id,
                               reference=change_id)
    _demo.approve_order(order["id"])  # sandbox Personal persona (simulated)
    order = _demo.authorize_order(order["id"])
    return order, {
        "paypal_order_id": order["id"],
        "paypal_authorization_id": _authorization_id(order),
    }


def apply_change(change: dict, params: dict) -> dict:
    """Execute one approved change (capture | void) on the stub rail."""
    action = change["action"]
    if action == "capture":
        capture = _demo.capture_authorization(params["authorization_id"])
        return {"paypal_capture_id": capture["id"],
                "summary": f"Captured ${capture['amount']['value']} "
                           f"(stub rail), capture {capture['id']}"}
    if action == "void":
        auth = _demo.void_authorization(params["authorization_id"])
        return {"summary": f"Voided authorization {auth['id']} (stub rail) "
                           "— hold released, buyer keeps their money"}
    raise ValueError(f"paypal.checkout cannot apply action {action!r}")


def compensate(receipt: dict) -> dict:
    """Undo a capture by refund (the effector's registered compensation)."""
    capture_id = receipt.get("external_ids", {}).get("paypal_capture_id")
    if not capture_id:
        raise ValueError("receipt carries no capture id to refund")
    refund = _demo.refund_capture(capture_id)
    return {"paypal_refund_id": refund["id"],
            "summary": f"Refunded capture {capture_id} (stub rail), "
                       f"refund {refund['id']}"}


def read_order(order_id: str) -> dict | None:
    """The PayPal reader: order/authorization state (stub rail locally)."""
    return _demo.get_order(order_id)
