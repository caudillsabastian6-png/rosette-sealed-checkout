"""Channel3 adapter (INTEGRATIONS.md §2).

One evidence function: market_panel(identity) -> top comparable offers,
stored on the seal receipt. Advisory only — the seal never depends on it,
and an empty result is a valid result.

INTEGRATION POINT (crew step, key-gated): the live call goes behind
market_panel_live() once CHANNEL3_API_KEY is present in .env — header
"x-api-key", POST /v1/search per INTEGRATIONS.md §2.2, top 5 offers kept
as {title, price, merchant, url, fetched_at}. Until the key lands,
market_panel() returns the designed graceful-empty state and says why.
"""
from __future__ import annotations

from ... import config

PROVIDER = "channel3"


def market_panel(identity: dict) -> dict:
    """Advisory comparable-offers panel for a card identity."""
    if not config.present(config.CHANNEL3_API_KEY):
        return {"provider": PROVIDER, "status": "unavailable",
                "reason": "CHANNEL3_API_KEY not in .env yet "
                          "(owner credential-pull step)",
                "offers": []}
    return market_panel_live(identity)


def market_panel_live(identity: dict) -> dict:
    """Live Channel3 search — wired when the key lands (see module docstring).

    Kept dependency-free (urllib) so the demo core stays stdlib-only; the
    Fern SDK in [integrations] extras is an alternative behind this same
    function signature.
    """
    import json
    import urllib.request

    query = " ".join(str(identity.get(k, "")) for k in ("name", "set",
                                                        "number")).strip()
    req = urllib.request.Request(
        "https://api.trychannel3.com/v1/search",
        data=json.dumps({"query": query, "limit": 5}).encode(),
        headers={"x-api-key": config.get(config.CHANNEL3_API_KEY),
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read())
    except Exception as exc:  # advisory only: failure is an empty panel
        return {"provider": PROVIDER, "status": "error",
                "reason": f"Channel3 search failed: {exc}", "offers": []}
    offers = []
    for product in payload.get("products", [])[:5]:
        offer = (product.get("offers") or [{}])[0]
        price = offer.get("price") or {}
        offers.append({
            "title": product.get("title", ""),
            "price": price.get("value") if isinstance(price, dict) else price,
            "merchant": offer.get("merchant") or offer.get("retailer") or "",
            "url": offer.get("url", ""),
        })
    return {"provider": PROVIDER,
            "status": "ok" if offers else "no_matches",
            "offers": offers}
