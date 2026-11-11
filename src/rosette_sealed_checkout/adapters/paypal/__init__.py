"""PayPal sandbox adapter (INTEGRATIONS.md §1).

Registers:
- reader:   order/authorization state  (GET /v2/checkout/orders/{id})
- effector: paypal.checkout — create order, authorize, capture, void
- effector: paypal.refund    — refund a capture (undo of capture)

Sandbox base URL only. There is deliberately no production configuration.

TWO TRANSPORTS, chosen per call by credential presence (config.paypal_mode):

- "sandbox" rail — when PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are in
  .env, every money call goes through client.SandboxPayPalClient to the
  real PayPal sandbox API. Receipts say "(real sandbox rail)" and every
  id is a genuine PayPal sandbox id.
- "stub" rail — with no credentials, the engine runs against the demo
  stub (adapters/demo) and receipts say "(stub rail)" with DEMO- ids
  (ENGINE-CONTRACT §8.5). A stub response can never be mistaken for a
  PayPal response.

The one step the merchant API cannot perform is the BUYER's approval:
PayPal requires the payer to approve the order in PayPal's own checkout
(INTEGRATIONS.md §1.2; a live authorize attempt on an unapproved order
returns HTTP 422 ORDER_NOT_APPROVED). On the sandbox rail this adapter
therefore creates the order for real and exposes the HATEOAS "approve"
link; once PayPal reports the order APPROVED, refresh_and_authorize()
authorizes for real. The designed automation for that click is the
Kernel browser persona typing the sandbox Personal account's credentials
from local config (INTEGRATIONS.md §1.1, RISKS.md §4 — see
adapters/kernel); until that driver lands, the demo's buyer approves
through the link, exactly as a real buyer would.
"""
from __future__ import annotations

from ... import config, state as _state
from ..demo.state_machine import DemoPayPal
from .client import PayPalError, SandboxPayPalClient

_demo = DemoPayPal()
_client: SandboxPayPalClient | None = None


def rail() -> str:
    """Which transport actually serves the money calls right now."""
    return "sandbox" if config.paypal_mode() == "sandbox-live" else "stub"


def _live_client() -> SandboxPayPalClient:
    global _client
    if _client is None:
        # Constructor refuses any non-sandbox base URL — loudly, never a
        # silent fall back to the stub (the banner would be lying).
        _client = SandboxPayPalClient()
    return _client


def _authorization_id(order: dict) -> str | None:
    payments = order["purchase_units"][0].get("payments", {})
    auths = payments.get("authorizations") or []
    return auths[0]["id"] if auths else None


def approve_url(order: dict) -> str | None:
    """The HATEOAS buyer-approval link of a live order (None on stub)."""
    for link in order.get("links", []):
        if link.get("rel") == "approve":
            return link.get("href")
    return None


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


def create_order(*, change_id: str, amount: dict,
                 seal_receipt_id: str) -> tuple[dict, dict]:
    """Create an order on the active rail. Returns (order, meta).

    Meta carries the rail that served the call and, on the sandbox rail,
    the buyer-approval URL (the order is CREATED, not yet authorized).
    """
    if rail() == "sandbox":
        # Unique per attempt: PayPal-Request-Id reuse with a different
        # body is rejected, and a re-clicked Buy must mint a fresh order.
        request_id = f"{change_id}-{_state.new_id('rq')}"
        order = _live_client().create_order(
            amount=amount, custom_id=seal_receipt_id,
            request_id=request_id)
        return order, {"rail": "sandbox",
                       "approve_url": approve_url(order),
                       "paypal_order_id": order["id"]}
    order = _demo.create_order(amount=amount, custom_id=seal_receipt_id,
                               reference=change_id)
    return order, {"rail": "stub", "approve_url": None,
                   "paypal_order_id": order["id"]}


def refresh_and_authorize(order_id: str) -> dict | None:
    """Read the order fresh; authorize it if the buyer has approved.

    Stub rail: the demo order was authorized at buy time — return it as
    stored. Sandbox rail: GET the real order; when PayPal reports
    APPROVED and no authorization exists yet, POST the authorize call
    for real (idempotent per order) and return the authorized order.
    """
    if rail() == "stub":
        return _demo.get_order(order_id)
    client = _live_client()
    order = client.get_order(order_id)
    if order.get("status") == "APPROVED" and not _authorization_id(order):
        order = client.authorize_order(
            order_id, request_id=f"authorize-{order_id}")
    return order


def create_and_authorize(*, change_id: str, amount: dict,
                         seal_receipt_id: str) -> tuple[dict, dict]:
    """Create -> buyer approves -> authorize, as far as the rail allows.

    Stub rail: the simulated sandbox Personal persona approves in-app,
    so the returned order is AUTHORIZED (the demo's original behaviour).
    Sandbox rail: the order is created for real; it comes back AUTHORIZED
    only if the buyer has already approved — otherwise it is CREATED and
    the caller surfaces meta["approve_url"] for the buyer's click.
    Returns (order, receipt-fields) for the engine to log.
    """
    order, meta = create_order(change_id=change_id, amount=amount,
                               seal_receipt_id=seal_receipt_id)
    if meta["rail"] == "stub":
        _demo.approve_order(order["id"])  # sandbox Personal persona (sim.)
        order = _demo.authorize_order(order["id"])
    else:
        refreshed = refresh_and_authorize(order["id"])
        if refreshed is not None:
            order = refreshed
    return order, {
        "paypal_order_id": order["id"],
        "paypal_authorization_id": _authorization_id(order),
        "approve_url": meta.get("approve_url"),
    }


def apply_change(change: dict, params: dict) -> dict:
    """Execute one approved change (capture | void) on the active rail."""
    action = change["action"]
    if rail() == "sandbox":
        client = _live_client()
        request_id = change["change_id"]  # contract §1.5 idempotency key
        if action == "capture":
            capture = client.capture_authorization(
                params["authorization_id"], amount=params["amount"],
                request_id=request_id)
            # The summary's amount comes from the approved change
            # params (what the owner approved), never from the response
            # body: a minimal-representation response carries no amount
            # field, and reading it there is what killed the Nov 10
            # apply AFTER PayPal had already captured (KeyError
            # 'amount' on a capture that had in fact succeeded).
            return {"paypal_capture_id": capture["id"],
                    "summary": f"Captured ${params['amount']['value']} "
                               f"via PayPal sandbox API (real sandbox "
                               f"rail), capture {capture['id']}"}
        if action == "void":
            # Real void answers 204 No Content — the summary is built
            # from the request, the effect is read back via the reader.
            client.void_authorization(params["authorization_id"],
                                      request_id=request_id)
            return {"summary": f"Voided authorization "
                               f"{params['authorization_id']} via PayPal "
                               "sandbox API (real sandbox rail) — hold "
                               "released, buyer keeps their money"}
        raise ValueError(f"paypal.checkout cannot apply action {action!r}")
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
    if rail() == "sandbox":
        refund = _live_client().refund_capture(
            capture_id, request_id=f"undo-{receipt['receipt_id']}")
        return {"paypal_refund_id": refund["id"],
                "summary": f"Refunded capture {capture_id} via PayPal "
                           f"sandbox API (real sandbox rail), "
                           f"refund {refund['id']}"}
    refund = _demo.refund_capture(capture_id)
    return {"paypal_refund_id": refund["id"],
            "summary": f"Refunded capture {capture_id} (stub rail), "
                       f"refund {refund['id']}"}


def read_order(order_id: str) -> dict | None:
    """The PayPal reader: order/authorization state from the active rail."""
    if rail() == "sandbox":
        try:
            return _live_client().get_order(order_id)
        except PayPalError:
            return None
    return _demo.get_order(order_id)
