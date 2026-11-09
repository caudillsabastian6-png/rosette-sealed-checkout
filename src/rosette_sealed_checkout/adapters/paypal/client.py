"""PayPal sandbox client — Orders v2 + Payments v2 (INTEGRATIONS.md §1).

Two transports behind one interface:

- SandboxPayPalClient: real calls to https://api-m.sandbox.paypal.com via
  urllib (stdlib). Used only when PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET
  are present in .env (the owner's credential-pull step). The base URL is
  pinned to the sandbox host — any other configured host is refused.
- Stub mode is NOT here: engine stub receipts are produced by
  adapters/demo + the stub effector, so a stub response can never be
  mistaken for a PayPal response by construction (honesty fence).

Every mutating call carries PayPal-Request-Id = the engine change_id
(idempotency, ENGINE-CONTRACT.md §1.5). Secrets are read via config at
call time and never logged.
"""
from __future__ import annotations

import base64
import json
import urllib.request

from ... import config


class PayPalError(RuntimeError):
    def __init__(self, kind: str, detail: str, retryable: bool = False):
        super().__init__(detail)
        self.kind = kind          # contract error taxonomy (§3)
        self.detail = detail
        self.retryable = retryable


class SandboxPayPalClient:
    def __init__(self) -> None:
        base = config.paypal_base_url().rstrip("/")
        if base != config.SANDBOX_BASE_URL:
            raise PayPalError(
                "source_unavailable",
                f"refusing non-sandbox PayPal base URL: {base!r} "
                "(this project has no production configuration)")
        self._base = base
        self._token: str | None = None

    # -- transport ---------------------------------------------------------
    def _auth_token(self) -> str:
        if self._token:
            return self._token
        client_id = config.get(config.PAYPAL_CLIENT_ID)
        client_secret = config.get(config.PAYPAL_CLIENT_SECRET)
        if not client_id or not client_secret:
            raise PayPalError(
                "source_unavailable",
                "PayPal sandbox credentials are not in .env yet "
                "(owner credential-pull step)")
        basic = base64.b64encode(
            f"{client_id}:{client_secret}".encode()).decode()
        req = urllib.request.Request(
            f"{self._base}/v1/oauth2/token",
            data=b"grant_type=client_credentials",
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            self._token = json.loads(resp.read())["access_token"]
        return self._token

    def _call(self, method: str, path: str, body: dict | None = None,
              request_id: str | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self._auth_token()}",
                   "Content-Type": "application/json"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        req = urllib.request.Request(f"{self._base}{path}", data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:  # typed, never stringly (§3)
            raise PayPalError("effector_failed",
                              f"PayPal sandbox {method} {path} -> "
                              f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise PayPalError("source_unavailable",
                              f"PayPal sandbox unreachable: {exc.reason}",
                              retryable=True) from exc
        return json.loads(raw) if raw else {}

    # -- Orders v2 / Payments v2 (INTEGRATIONS.md §1.2) --------------------
    def create_order(self, *, amount: dict, custom_id: str,
                     request_id: str) -> dict:
        return self._call("POST", "/v2/checkout/orders", {
            "intent": "AUTHORIZE",
            "purchase_units": [{
                "amount": {"currency_code": amount["currency"],
                           "value": amount["value"]},
                "custom_id": custom_id,
            }],
        }, request_id=request_id)

    def get_order(self, order_id: str) -> dict:
        return self._call("GET", f"/v2/checkout/orders/{order_id}")

    def authorize_order(self, order_id: str, request_id: str) -> dict:
        return self._call("POST", f"/v2/checkout/orders/{order_id}/authorize",
                          {}, request_id=request_id)

    def capture_authorization(self, authorization_id: str,
                              request_id: str) -> dict:
        return self._call("POST",
                          f"/v2/payments/authorizations/"
                          f"{authorization_id}/capture",
                          {}, request_id=request_id)

    def void_authorization(self, authorization_id: str,
                           request_id: str) -> dict:
        return self._call("POST",
                          f"/v2/payments/authorizations/"
                          f"{authorization_id}/void",
                          {}, request_id=request_id)

    def refund_capture(self, capture_id: str, request_id: str) -> dict:
        return self._call("POST", f"/v2/payments/captures/{capture_id}/refund",
                          {}, request_id=request_id)
