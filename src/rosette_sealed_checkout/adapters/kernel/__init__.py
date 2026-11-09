"""Kernel buyer-browser adapter (INTEGRATIONS.md §3).

Creates an on-demand cloud browser (headed, stealth), drives the sandbox
buyer checkout over CDP with Playwright, exposes the live-view URL for the
demo, and deletes the session explicitly when done. Sandbox credentials are
typed by the driver from local config — never logged, never in receipts.

STATUS: integration point only. The live driver lands Nov 10–11 (Kernel
trial ends Nov 14 — RISKS.md §1) once KERNEL_API_KEY is in .env. Until
then the demo's buyer step is the stub persona inside adapters/paypal
(PayPal-shaped approve step), and this module reports its own state
honestly instead of pretending to drive a browser.
"""
from __future__ import annotations

from ... import config


def status() -> dict:
    if not config.present(config.KERNEL_API_KEY):
        return {"status": "unavailable",
                "reason": "KERNEL_API_KEY not in .env yet "
                          "(owner credential-pull step)"}
    return {"status": "key_present",
            "reason": "driver wiring scheduled Nov 10–11 "
                      "(trial ends Nov 14)"}
