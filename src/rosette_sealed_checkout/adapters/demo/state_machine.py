"""In-memory demo order/authorization store with PayPal-shaped objects."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ... import state as _state

# PayPal authorize ordering rule (INTEGRATIONS.md §1.2 / RISKS.md §4):
# an order must be APPROVED before it can be authorized.
ORDER_FLOW = ["CREATED", "APPROVED", "AUTHORIZED", "COMPLETED", "VOIDED"]


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(amount: dict) -> dict:
    return {"currency_code": amount["currency"], "value": amount["value"]}


class DemoPayPal:
    """One demo 'sandbox account' worth of orders, persisted to var/."""

    STORE = "demo_orders.json"

    def __init__(self) -> None:
        self._orders: dict[str, dict] = _state.load(self.STORE, {})

    def _persist(self) -> None:
        _state.save(self.STORE, self._orders)

    # -- Orders v2 shapes -------------------------------------------------
    def create_order(self, *, amount: dict, custom_id: str,
                     reference: str) -> dict:
        now = datetime.now(timezone.utc)
        order_id = "DEMO-" + _state.new_id("ord").split("_", 1)[1].upper()
        order = {
            "id": order_id,
            "mode": "stub",
            "status": "CREATED",
            "intent": "AUTHORIZE",
            "create_time": _ts(now),
            "purchase_units": [{
                "reference_id": reference,
                "custom_id": custom_id,
                "amount": _money(amount),
                "payments": {},
            }],
            "links": [{
                "href": f"/checkout/{order_id}",
                "rel": "approve",
                "method": "GET",
            }],
        }
        self._orders[order_id] = order
        self._persist()
        return order

    def get_order(self, order_id: str) -> dict | None:
        return self._orders.get(order_id)

    def approve_order(self, order_id: str) -> dict:
        """The demo buyer approves (the sandbox Personal persona's step)."""
        order = self._require(order_id, "CREATED")
        order["status"] = "APPROVED"
        order["payer"] = {"name": {"given_name": "Demo", "surname": "Buyer"},
                          "note": "sandbox Personal persona (simulated)"}
        self._persist()
        return order

    def authorize_order(self, order_id: str) -> dict:
        order = self._require(order_id, "APPROVED")
        now = datetime.now(timezone.utc)
        auth = {
            "id": "DEMO-" + _state.new_id("auth").split("_", 1)[1].upper(),
            "status": "CREATED",
            "amount": order["purchase_units"][0]["amount"],
            "create_time": _ts(now),
            # Real authorizations live 29 days; honor period is 3 days.
            "expiration_time": _ts(now + timedelta(days=29)),
        }
        order["purchase_units"][0]["payments"] = {"authorizations": [auth]}
        order["status"] = "AUTHORIZED"
        self._persist()
        return order

    # -- Payments v2 shapes ----------------------------------------------
    def capture_authorization(self, authorization_id: str) -> dict:
        order, auth = self._find_authorization(authorization_id)
        if auth["status"] != "CREATED":
            raise ValueError(
                f"authorization {authorization_id} is {auth['status']}, "
                "not capturable")
        capture = {
            "id": "DEMO-" + _state.new_id("cap").split("_", 1)[1].upper(),
            "status": "COMPLETED",
            "amount": auth["amount"],
            "final_capture": True,
            "create_time": _ts(datetime.now(timezone.utc)),
        }
        auth["status"] = "CAPTURED"
        order["purchase_units"][0]["payments"]["captures"] = [capture]
        order["status"] = "COMPLETED"
        self._persist()
        return capture

    def void_authorization(self, authorization_id: str) -> dict:
        order, auth = self._find_authorization(authorization_id)
        if auth["status"] != "CREATED":
            raise ValueError(
                f"authorization {authorization_id} is {auth['status']}, "
                "not voidable")
        auth["status"] = "VOIDED"
        order["status"] = "VOIDED"
        self._persist()
        return auth

    def refund_capture(self, capture_id: str) -> dict:
        for order in self._orders.values():
            payments = order["purchase_units"][0].get("payments", {})
            for cap in payments.get("captures", []):
                if cap["id"] == capture_id:
                    refund = {
                        "id": "DEMO-" + _state.new_id("ref").split("_", 1)[1].upper(),
                        "status": "COMPLETED",
                        "amount": cap["amount"],
                        "create_time": _ts(datetime.now(timezone.utc)),
                    }
                    payments["refunds"] = [refund]
                    self._persist()
                    return refund
        raise KeyError(f"unknown capture {capture_id}")

    # -- internals ---------------------------------------------------------
    def _require(self, order_id: str, status: str) -> dict:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"unknown order {order_id}")
        if order["status"] != status:
            raise ValueError(
                f"order {order_id} is {order['status']}, expected {status}")
        return order

    def _find_authorization(self, authorization_id: str) -> tuple[dict, dict]:
        for order in self._orders.values():
            payments = order["purchase_units"][0].get("payments", {})
            for auth in payments.get("authorizations", []):
                if auth["id"] == authorization_id:
                    return order, auth
        raise KeyError(f"unknown authorization {authorization_id}")
