"""Environment config — the ONLY module that reads .env / os.environ.

Values are loaded lazily and never logged. Presence checks return booleans
so callers (and the build status tooling) can verify a credential slot is
filled WITHOUT ever printing the value.

.env loading is a tiny built-in parser (stdlib only): KEY=VALUE lines,
# comments, optional surrounding quotes. A real .env is created by the
owner step at build time; it is gitignored and never committed.
"""
from __future__ import annotations

import os
from pathlib import Path

# repo root = three parents up from src/rosette_sealed_checkout/config.py
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"

_loaded = False


def _load_env() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def get(name: str, default: str = "") -> str:
    _load_env()
    return os.environ.get(name, default)


def present(name: str) -> bool:
    """True iff the variable is set to a non-empty value. Never returns it."""
    return bool(get(name).strip())


# --- the slots the demo app knows about (names mirror .env.example) --------
PAYPAL_CLIENT_ID = "PAYPAL_CLIENT_ID"
PAYPAL_CLIENT_SECRET = "PAYPAL_CLIENT_SECRET"
PAYPAL_BASE_URL = "PAYPAL_BASE_URL"
CHANNEL3_API_KEY = "CHANNEL3_API_KEY"
KERNEL_API_KEY = "KERNEL_API_KEY"
EBAY_READ_ENABLED = "EBAY_READ_ENABLED"

# Sandbox ONLY. There is deliberately no production base URL anywhere in
# this project (ENGINE-CONTRACT.md §7); pointing the config anywhere else
# is refused at client construction time.
SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"


def paypal_base_url() -> str:
    return get(PAYPAL_BASE_URL, SANDBOX_BASE_URL)


def paypal_mode() -> str:
    """"sandbox-live" when both credential slots are filled, else "stub".

    The demo app shows this mode on screen: stub responses must never be
    mistaken for real sandbox calls (house honesty fence).
    """
    if present(PAYPAL_CLIENT_ID) and present(PAYPAL_CLIENT_SECRET):
        return "sandbox-live"
    return "stub"


def channel3_mode() -> str:
    return "live" if present(CHANNEL3_API_KEY) else "unavailable"


def kernel_mode() -> str:
    return "live" if present(KERNEL_API_KEY) else "unavailable"
