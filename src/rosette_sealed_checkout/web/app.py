"""Rosette-Sealed Checkout — demo web app (Python standard library only).

Pages:
  /                    listings with seal badges + the sandbox honesty line
  /checkout/<item_id>  order detail: create -> (buyer approves) ->
                       authorize states, then seal #2 dry-run, the owner
                       approval gate, and undo (refund) of a capture
  /receipts            the append-only receipt chain (the audit surface)
  /healthz             Render health check

Money honesty: every page states that all money is PayPal SANDBOX money
and which rail produced the state. On the stub rail the buyer approval
is simulated by the demo buyer step and every id is DEMO- prefixed. On
the sandbox-live rail the money calls are real PayPal sandbox API calls;
the one step PayPal reserves for the buyer — approving the order —
happens in PayPal's own sandbox checkout via the approve link the page
shows, and the app authorizes for real once PayPal reports APPROVED.
"""
from __future__ import annotations

import html
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .. import config, state as _state
from ..adapters import paypal
from ..checkout import checkout as run_checkout, seal_gate, state_caption
from ..engine import verbs
from ..seal import LABELS

HONESTY = ("All money is PayPal <b>sandbox</b> money — no real funds move. "
           "Verdicts: Likely Genuine / Likely Counterfeit / Inconclusive.")

_sessions: dict[str, dict] = {}


def _snapshot() -> dict:
    return verbs.read({"source": "demo.listings"})


def _items() -> dict:
    return {it["item_id"]: it for it in _snapshot()["items"]}


_CSS = """
:root{
--plum:#2a1450;--plum2:#3d2070;--gold:#d9a441;--goldd:#a97a24;
--ink:#26193d;--bg:#f3effa;--line:#ddd2f2;
--okbg:#def3e2;--ok:#176b32;--okline:#4c9a5f;
--badbg:#fbe4e4;--bad:#8c2323;--badline:#c2504c;
--incbg:#e9e9ee;--inc:#43434e;--incline:#9a9aa6;
--ambbg:#fdf0d0;--amb:#6b4e00;--ambline:#d9a441;
--voidbg:#eae5f6;--void:#3d3358;--voidline:#8d7fb8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
font-size:1.05rem;line-height:1.55}
.wrap{max-width:56rem;margin:0 auto;padding:0 1rem}
header.site{background:linear-gradient(140deg,#1c0b38,var(--plum2));
color:#f5efff;border-bottom:4px solid var(--gold);padding:1.5rem 0 1.25rem}
header.site h1{margin:.1rem 0 .7rem;font-size:1.75rem;line-height:1.2}
header.site p{margin:.4rem 0;color:#e7ddff;max-width:44rem}
header.site .rail{color:#cdbcf5;font-size:.96rem}
header.site b{color:#ffd98a}
nav{margin-top:.9rem;display:flex;gap:.6rem;flex-wrap:wrap}
nav a{display:inline-block;padding:.6rem 1.15rem;border:1.5px solid var(--gold);
border-radius:999px;color:#ffd98a;text-decoration:none;font-weight:700;
background:rgba(255,255,255,.07)}
main{display:block;padding-bottom:2.5rem}
h2{color:var(--plum);font-size:1.4rem;margin:1.5rem 0 .5rem}
h3{margin:.15rem 0 .55rem}
a{color:#5b2ea6}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
font-size:.88em;background:rgba(42,20,80,.07);padding:.05rem .35rem;
border-radius:.35rem}
.card{background:#fff;border:1px solid var(--line);border-radius:1rem;
box-shadow:0 2px 10px rgba(42,20,80,.09);padding:1.1rem 1.2rem;margin:1rem 0}
ul.cards{list-style:none;margin:0;padding:0}
ul.cards li{background:#fff;border:1px solid var(--line);border-radius:1rem;
box-shadow:0 2px 10px rgba(42,20,80,.09);padding:1.15rem 1.2rem;margin:1rem 0}
ul.cards li h3{margin-top:0;font-size:1.2rem}
ul.cards li a{display:inline-block;margin-top:.55rem;background:var(--plum);
color:#fff;font-weight:700;text-decoration:none;padding:.8rem 1.4rem;
border-radius:.85rem;border:2px solid var(--plum)}
.pill{display:inline-block;border-radius:999px;padding:.3rem .9rem;
font-weight:700;border:1.5px solid;font-size:.98rem}
.pill.ok,.note.ok{background:var(--okbg);color:var(--ok);border-color:var(--okline)}
.pill.bad,.note.bad{background:var(--badbg);color:var(--bad);border-color:var(--badline)}
.pill.inc,.note.inc{background:var(--incbg);color:var(--inc);border-color:var(--incline)}
.note{border:1.5px solid;border-radius:.9rem;padding:.9rem 1.1rem;margin:.85rem 0}
.state{border:2px solid;border-radius:1rem;padding:1.1rem 1.2rem;margin:1rem 0}
.state h3{margin:0 0 .45rem;font-size:1.3rem}
.state p{margin:.2rem 0}
.state.held{background:var(--ambbg);border-color:var(--ambline);color:var(--amb)}
.state.captured{background:var(--okbg);border-color:var(--okline);color:#0f4d24}
.state.voided{background:var(--voidbg);border-color:var(--voidline);color:var(--void)}
.state.waiting{background:#ececf1;border-color:#b9b9c6;color:#3c3c46}
.card.gate{border:2.5px dashed var(--goldd);background:#fffaf0}
.card.buyer{border-color:var(--gold)}
.card.buyer a{display:inline-block;background:var(--gold);color:#2a1a00;
font-weight:700;text-decoration:none;padding:.95rem 1.5rem;border-radius:.9rem;
border:2px solid var(--goldd);margin-top:.4rem}
.panel{background:#fff;border:1px solid var(--line);border-radius:.9rem;
padding:.8rem 1.05rem}
button{font:inherit;font-weight:700;min-height:3.4rem;padding:.85rem 1.6rem;
border-radius:.9rem;border:2px solid var(--plum);background:var(--plum);
color:#fff;cursor:pointer;margin:.45rem .45rem .45rem 0}
button[value="approved"]{background:var(--gold);border-color:var(--goldd);
color:#2a1a00}
button[value="denied"]{background:#fff;color:#6b5a8f;border-color:#b7a8dd}
form[action="/undo"] button{background:#fff;color:#8c2323;border-color:#c2504c}
.chainwrap{overflow-x:auto;background:#fff;border:1px solid var(--line);
border-radius:1rem;box-shadow:0 2px 10px rgba(42,20,80,.09)}
table.chain{border-collapse:collapse;width:100%;min-width:34rem}
.chain th{background:var(--plum);color:#f5efff;text-align:left;
padding:.65rem .85rem;font-size:.92rem}
.chain td{padding:.6rem .85rem;border-top:1px solid #eee7fa;vertical-align:top}
.chain .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
font-size:.85em;white-space:nowrap}
.chain .verb{display:inline-block;background:#efe8fd;border:1px solid #c9b6f5;
color:#3d2070;border-radius:.5rem;padding:.05rem .55rem;font-weight:700;
font-size:.86em;white-space:nowrap}
.chain td.sum{min-width:16rem}
@media (max-width:40rem){
.chainwrap{overflow:visible}
table.chain{min-width:0}
.chain tr.head{display:none}
.chain tr{display:block;padding:.7rem .9rem}
.chain td{display:block;border-top:none;padding:.12rem 0}
.chain .mono{white-space:normal;word-break:break-all}
.chain td.sum{min-width:0}
}
"""


def _page(title: str, body: str) -> bytes:
    rail = config.paypal_mode()
    rail_note = ("(simulated buyer + PayPal-shaped stub responses — "
                 "NOT real PayPal calls)"
                 if rail == "stub" else
                 "(real PayPal sandbox API calls — the buyer approves in "
                 "PayPal's own sandbox checkout, then this app authorizes, "
                 "captures and refunds through the API)")
    doc = ("<!DOCTYPE html>\n"
           '<html lang="en"><head><meta charset="utf-8">\n'
           '<meta name="viewport" content="width=device-width, '
           'initial-scale=1">\n'
           f"<title>{html.escape(title)} — Rosette-Sealed Checkout</title>\n"
           f"<style>{_CSS}</style></head>\n"
           "<body>\n"
           '<header class="site"><div class="wrap">\n'
           "<h1>Rosette-Sealed Checkout</h1>\n"
           f"<p>{HONESTY}</p>\n"
           f'<p class="rail">Payment rail: <b>{rail}</b> {rail_note}</p>\n'
           '<nav><a href="/">Listings</a>'
           '<a href="/receipts">Receipt chain</a></nav>\n'
           "</div></header>\n"
           f'<main class="wrap">{body}</main>\n'
           "</body></html>")
    return doc.encode()


def _seal_badge(seal: dict) -> str:
    cls = {"likely_genuine": "ok", "likely_counterfeit": "bad",
           "inconclusive": "inc"}[seal["verdict"]]
    return (f'<span class="pill {cls}">Seal: '
            f'{LABELS[seal["verdict"]]}</span> '
            f'<small>receipt {seal["receipt_ref"]}</small>')


def _state_class(caption: str) -> str:
    """CSS class for the order-state banner, derived from the caption
    state_caption() already computed — styling only, the caption text
    and the logic behind it are untouched."""
    if caption.startswith("CAPTURED"):
        return "captured"
    if caption.startswith("VOIDED"):
        return "voided"
    if caption.startswith(("AUTHORIZED", "APPROVED")):
        return "held"
    return "waiting"


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
    return _page("Listings",
                 "<h2>Demo listings (sandbox copies)</h2><ul class=\"cards\">"
                 + "".join(rows) + "</ul>")


def _authorizations(order: dict) -> list:
    return order["purchase_units"][0].get("payments", {}).get(
        "authorizations") or []


def _receipt_summary(receipt_id: str) -> str | None:
    for r in _state.receipts():
        if r["receipt_id"] == receipt_id:
            return r["summary"]
    return None


def _apply_result_html(result: dict) -> str:
    """Render an apply outcome in plain language (judge-facing pages
    never dump raw dicts; the full technical detail lives in the
    receipt chain)."""
    if "error" in result:
        err = result["error"]
        return (f'<p class="note bad"><b>Not applied:</b> '
                f'{html.escape(err.get("detail", err.get("kind", "")))}'
                "</p>")
    lines = []
    for res in result.get("results", []):
        status = res.get("status")
        if status == "applied":
            summary = (_receipt_summary(res.get("receipt_id", ""))
                       or "Change applied.")
            lines.append(
                f'<p class="note ok"><b>Applied.</b> '
                f'{html.escape(summary)} '
                f'<small>(receipt {html.escape(res.get("receipt_id", ""))}'
                " — full detail in the receipt chain)</small></p>")
        elif status == "denied":
            lines.append(
                '<p class="note inc"><b>Denied by the owner.</b> '
                "Nothing was applied — the buyer's money stays held "
                "exactly as the order shows above.</p>")
        else:  # failed
            lines.append(
                f'<p class="note bad"><b>This step failed in the '
                f'app:</b> {html.escape(str(res.get("error", "")))}<br>'
                "Nothing moved <i>because of this error</i>. The order "
                "box above is re-read fresh from PayPal on every page "
                "load and shows the money's true state — trust it, "
                "not this message.</p>")
    return "".join(lines) or "<p>No changes were applied.</p>"


def _undo_result_html(undone: dict) -> str:
    if "error" in undone:
        err = undone["error"]
        return (f'<p class="note bad"><b>Undo failed:</b> '
                f'{html.escape(err.get("detail", err.get("kind", "")))}'
                "</p>")
    summary = (_receipt_summary(undone.get("receipt_id", ""))
               or "Capture undone.")
    return (f'<p class="note ok"><b>Undone.</b> '
            f'{html.escape(summary)}</p>')


def _applied_capture_receipt(session: dict) -> str | None:
    """The receipt id of this session's applied capture, if any."""
    gate = session.get("gate") or {}
    if (gate.get("change") or {}).get("action") != "capture":
        return None
    for res in (session.get("decided") or {}).get("results", []):
        if res.get("status") == "applied" and res.get("receipt_id"):
            return res["receipt_id"]
    return None


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
        # Read the order fresh from the rail on every render: on the
        # sandbox rail this is what notices the buyer's approval and
        # authorizes for real; it also keeps the caption truthful
        # after capture / void / refund.
        try:
            fresh = paypal.refresh_and_authorize(co["order"]["id"])
        except Exception as exc:  # rail read failed — show last known state
            fresh = None
            parts.append(f"<p><small>Rail read failed: "
                         f"{html.escape(str(exc))}</small></p>")
        if fresh is not None:
            co["order"] = fresh
        order = co["order"]
        parts.append(_seal_badge(co["seal_listing"]))
        panel = co["market_panel"]
        offers = panel.get("offers") or []
        panel_html = ("<p class=\"panel\">Channel3 market panel: "
                      + (f"{len(offers)} comparable offer(s)"
                         if offers else
                         f'no offers shown — {html.escape(panel.get("reason", panel["status"]))}')
                      + " (advisory only; the seal never depends on it)</p>")
        parts.append(panel_html)
        caption = state_caption(order)
        parts.append(
            f'<div class="state {_state_class(caption)}">'
            f'<h3>{caption}</h3>'
            f'<p>Order <code>{order["id"]}</code> — status '
            f'<b>{order["status"]}</b> (rail: {co["rail_mode"]}).</p></div>')
        auths = _authorizations(order)
        if auths:
            parts.append(f'<p>Authorization <code>{auths[0]["id"]}</code> '
                         f'status <b>{auths[0]["status"]}</b>, expires '
                         f'{auths[0].get("expiration_time", "—")}.</p>')
        elif co["rail_mode"] != "stub":
            url = co.get("approve_url") or paypal.approve_url(order)
            if url:
                parts.append(
                    '<div class="card buyer"><h3>Buyer step — approval at '
                    'PayPal</h3><p>PayPal holds the pen on this step: the '
                    'buyer (the sandbox <b>Personal</b> account) approves '
                    'the order in PayPal\'s own sandbox checkout. Then '
                    'this page authorizes for real — reload after '
                    'approving.</p>'
                    f'<p><a href="{html.escape(url)}" '
                    'target="_blank" rel="noopener">Open the PayPal '
                    'sandbox approval page</a></p></div>')
    else:
        label = ("Buy with PayPal (sandbox buyer)"
                 if config.paypal_mode() == "stub"
                 else "Create the PayPal sandbox order")
        parts.append(
            f'<form method="post" action="/checkout/{item_id}/buy">'
            f'<button type="submit">{label}</button>'
            "</form>")

    authorized = "checkout" in session and bool(
        _authorizations(session["checkout"]["order"]))
    if authorized and "gate" not in session:
        parts.append(
            f'<form method="post" action="/checkout/{item_id}/seal">'
            "<button type=\"submit\">Run fulfilment seal (seal #2) + "
            "dry-run</button></form>")
    elif "checkout" in session and not authorized:
        parts.append("<p><small>The fulfilment seal gate unlocks once "
                     "the authorization exists — the buyer's approval "
                     "comes first.</small></p>")
    if action == "seal" and authorized and "gate" not in session:
        session["gate"] = seal_gate(item, _snapshot(),
                                    session["checkout"]["order"])
    if "gate" in session:
        gate = session["gate"]
        change, preview = gate["change"], gate["preview"]
        parts.append(
            f'<div class="card gate"><h3>Seal gate</h3>'
            f'<p>Proposed action: <b>{change["action"]}</b> — '
            f'{html.escape(change["reason"])}</p>'
            f'<p>Dry-run: {html.escape(preview["effects_preview"])}</p>'
            "</div>")
        decided = session.get("decided")
        if decided:
            parts.append("<h3>Decision recorded</h3>"
                         + _apply_result_html(decided))
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
            parts.append(_apply_result_html(result))

    # Undo (engine contract §1.6): a captured order can be undone — the
    # effector's compensation is a refund, appended to the chain.
    if action == "undo" and "undone" not in session:
        receipt_id = form.get("receipt_id", "")
        if receipt_id:
            try:
                session["undone"] = verbs.undo(receipt_id)
            except Exception as exc:
                session["undone"] = {"error": {
                    "kind": "effector_failed", "detail": str(exc)}}
    capture_receipt = _applied_capture_receipt(session)
    if capture_receipt and "undone" not in session:
        parts.append(
            '<div class="card undo"><h3>Undo this capture</h3>'
            "<p>Undo runs the capture's compensating action — a full "
            "refund through the rail — and appends a new receipt to "
            "the chain. History is never rewritten.</p>"
            f'<form method="post" action="/undo">'
            f'<input type="hidden" name="item_id" value="{item_id}">'
            f'<input type="hidden" name="receipt_id" '
            f'value="{capture_receipt}">'
            '<button type="submit">Owner: UNDO capture (refund)</button>'
            "</form></div>")
    if "undone" in session:
        parts.append(_undo_result_html(session["undone"]))
    return _page("Checkout", "".join(parts))


def receipts_page() -> bytes:
    rows = []
    for r in _state.receipts():
        rows.append(
            "<tr>"
            f'<td class="mono">{html.escape(r["ts"])}</td>'
            f'<td class="mono">{html.escape(r["receipt_id"])}</td>'
            f'<td><span class="verb">{html.escape(r["verb"])}</span></td>'
            f'<td class="sum">{html.escape(r["summary"])}</td>'
            "</tr>")
    if rows:
        body = ("<h2>Receipt chain (append-only)</h2>"
                '<div class="chainwrap"><table class="chain">'
                '<tr class="head"><th>Time (UTC)</th><th>Receipt</th>'
                "<th>Verb</th><th>Summary</th></tr>"
                + "".join(rows) + "</table></div>")
    else:
        body = ("<h2>Receipt chain (append-only)</h2>"
                "<p>no receipts yet</p>")
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
        elif path == "/undo":
            self._send(checkout_page(form.get("item_id", ""), "undo",
                                     form))
        else:
            self._send(_page("Not found", "<p>Not found.</p>"), 404)


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
