"""Rosette-Sealed Checkout — demo web app (Python standard library only).

Pages:
  /                    listings with seal badges + the sandbox honesty line
  /checkout/<item_id>  order detail: create -> approve -> authorize states,
                       then seal #2 dry-run and the owner approval gate
  /receipts            the append-only receipt chain (the audit surface)
  /healthz             Render health check

Money honesty: every page states that all money is PayPal SANDBOX money
and which rail produced the state (stub until the credential pull lands,
then sandbox-live). Sandbox buyer approval is simulated by the demo buyer
step until the JS SDK + sandbox keys land — and the page says so.
"""
from __future__ import annotations

import html
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .. import config, state as _state
from ..checkout import checkout as run_checkout, seal_gate
from ..engine import verbs
from ..seal import LABELS

HONESTY = ("All money is PayPal <b>sandbox</b> money — no real funds move. "
           "Verdicts: Likely Genuine / Likely Counterfeit / Inconclusive.")

_sessions: dict[str, dict] = {}


def _snapshot() -> dict:
    return verbs.read({"source": "demo.listings"})


def _items() -> dict:
    return {it["item_id"]: it for it in _snapshot()["items"]}


def _page(title: str, body: str) -> bytes:
    rail = config.paypal_mode()
    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(title)} — Rosette-Sealed Checkout</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem auto;max-width:56rem;
padding:0 1rem}} .badge{{border:1px solid #888;border-radius:.5rem;
padding:.1rem .5rem}} .held{{background:#fff3cd}} .ok{{background:#d4edda}}
.bad{{background:#f8d7da}} .inc{{background:#e2e3e5}} pre{{background:#111;
color:#eee;padding:1rem;overflow:auto}} .rail{{color:#555}}</style></head>
<body>
<header><h1>Rosette-Sealed Checkout</h1>
<p>{HONESTY}</p>
<p class="rail">Payment rail: <b>{rail}</b>
{"(simulated buyer + PayPal-shaped stub responses — NOT real PayPal calls)"
 if rail == "stub" else "(real PayPal sandbox API calls)"}</p>
<nav><a href="/">Listings</a> · <a href="/receipts">Receipt chain</a></nav>
</header>
{body}
</body></html>"""
    return doc.encode()


def _seal_badge(seal: dict) -> str:
    cls = {"likely_genuine": "ok", "likely_counterfeit": "bad",
           "inconclusive": "inc"}[seal["verdict"]]
    return (f'<span class="badge {cls}">Seal: '
            f'{LABELS[seal["verdict"]]}</span> '
            f'<small>receipt {seal["receipt_ref"]}</small>')


def home() -> bytes:
    from ..seal import verify as seal_verify
    rows = []
    for it in _items().values():
        seal = seal_verify(it["identity"], it.get("fulfilment_evidence"))
        rows.append(
            f'<li><h3>{html.escape(it["title"])}</h3>'
            f'<p>${it["price"]["value"]} USD — {html.escape(it["scenario"])}'
            f'</p>{_seal_badge(seal)} '
            f'<a href="/checkout/{it["item_id"]}">Open checkout</a></li>')
    return _page("Listings", "<h2>Demo listings (sandbox copies)</h2><ul>"
                 + "".join(rows) + "</ul>")


def checkout_page(item_id: str, action: str | None,
                  form: dict) -> bytes:
    items = _items()
    item = items.get(item_id)
    if item is None:
        return _page("Not found", "<p>Unknown demo listing.</p>")
    session = _sessions.setdefault(item_id, {})
    parts = [f"<h2>{html.escape(item['title'])}</h2>",
             f"<p>Price: ${item['price']['value']} USD · "
             f"{html.escape(item['scenario'])}</p>"]

    if action == "buy" and "checkout" not in session:
        session["checkout"] = run_checkout(item, _snapshot())
    if "checkout" in session:
        co = session["checkout"]
        order = co["order"]
        parts.append(_seal_badge(co["seal_listing"]))
        panel = co["market_panel"]
        offers = panel.get("offers") or []
        panel_html = ("<p>Channel3 market panel: "
                      + (f"{len(offers)} comparable offer(s)"
                         if offers else
                         f'no offers shown — {html.escape(panel.get("reason", panel["status"]))}')
                      + " (advisory only; the seal never depends on it)</p>")
        parts.append(panel_html)
        parts.append(
            f'<div class="badge held"><h3>{co["state_caption"]}</h3>'
            f'<p>Order <code>{order["id"]}</code> — status '
            f'<b>{order["status"]}</b> (rail: {co["rail_mode"]}). '
            "The buyer's funds are held by the authorization; nothing "
            "has settled.</p></div>")
        auths = order["purchase_units"][0].get("payments", {}).get(
            "authorizations") or []
        if auths:
            parts.append(f'<p>Authorization <code>{auths[0]["id"]}</code> '
                         f'status <b>{auths[0]["status"]}</b>, expires '
                         f'{auths[0]["expiration_time"]}.</p>')
    else:
        parts.append(
            f'<form method="post" action="/checkout/{item_id}/buy">'
            '<button type="submit">Buy with PayPal (sandbox buyer)</button>'
            "</form>")

    if "checkout" in session and "gate" not in session:
        parts.append(
            f'<form method="post" action="/checkout/{item_id}/seal">'
            "<button type=\"submit\">Run fulfilment seal (seal #2) + "
            "dry-run</button></form>")
    if action == "seal" and "checkout" in session and "gate" not in session:
        session["gate"] = seal_gate(item, _snapshot(),
                                    session["checkout"]["order"])
    if "gate" in session:
        gate = session["gate"]
        change, preview = gate["change"], gate["preview"]
        parts.append(
            f'<div class="badge"><h3>Seal gate</h3>'
            f'<p>Proposed action: <b>{change["action"]}</b> — '
            f'{html.escape(change["reason"])}</p>'
            f'<p>Dry-run: {html.escape(preview["effects_preview"])}</p>'
            "</div>")
        decided = session.get("decided")
        if decided:
            parts.append(f"<p><b>Decision recorded:</b> "
                         f"{html.escape(str(decided))}</p>")
        else:
            pid = gate["proposal"]["proposal_id"]
            cid = change["change_id"]
            parts.append(
                f'<form method="post" action="/approve">'
                f'<input type="hidden" name="item_id" value="{item_id}">'
                f'<input type="hidden" name="proposal_id" value="{pid}">'
                f'<input type="hidden" name="change_id" value="{cid}">'
                '<button name="decision" value="approved" type="submit">'
                "Owner: APPROVE</button> "
                '<button name="decision" value="denied" type="submit">'
                "Owner: DENY</button></form>"
                "<p><small>Approval is the owner's tap — the agent can "
                "propose, never grant (engine contract §1.4).</small></p>")
    if action == "approve" and "gate" in session:
        pid = form.get("proposal_id", "")
        cid = form.get("change_id", "")
        record = verbs.approve(pid, "owner",
                               [{"change_id": cid,
                                 "decision": form.get("decision", "denied"),
                                 "note": "demo approval tap"}])
        if "error" not in record:
            result = verbs.apply(record)
            session["decided"] = result
            parts.append(f"<p><b>Applied:</b> "
                         f"{html.escape(str(result))}</p>")
    return _page("Checkout", "".join(parts))


def receipts_page() -> bytes:
    lines = []
    for r in _state.receipts():
        lines.append(f'{r["ts"]}  {r["receipt_id"]}  [{r["verb"]}] '
                     f'{r["summary"]}')
    body = ("<h2>Receipt chain (append-only)</h2><pre>"
            + html.escape("\n".join(lines) or "no receipts yet")
            + "</pre>")
    return _page("Receipts", body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            self._send(b"ok")
        elif path == "/":
            self._send(home())
        elif path == "/receipts":
            self._send(receipts_page())
        elif path.startswith("/checkout/"):
            self._send(checkout_page(path.rsplit("/", 1)[1], None, {}))
        else:
            self._send(_page("Not found", "<p>Not found.</p>"), 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        from urllib.parse import parse_qs
        form = {k: v[0] for k, v in
                parse_qs(self.rfile.read(length).decode()).items()}
        if path.startswith("/checkout/"):
            bits = path.strip("/").split("/")  # checkout/<id>/<action>
            self._send(checkout_page(bits[1], bits[2], {}))
        elif path == "/approve":
            self._send(checkout_page(form.get("item_id", ""), "approve",
                                     form))
        else:
            self._send(_page("Not found", "<p>Not found.</p>"), 404)


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
