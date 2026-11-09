"""Engine runtime state: local JSON stores + the append-only receipt log.

State lives under repo/var/ (gitignored). Receipts are the audit surface
(ENGINE-CONTRACT.md §4): if it isn't in the receipt log, it didn't happen.
History is append-only — undo appends a compensating receipt, it never
rewrites.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

VAR_DIR = Path(__file__).resolve().parents[2] / "var"
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _path(name: str) -> Path:
    VAR_DIR.mkdir(parents=True, exist_ok=True)
    return VAR_DIR / name


def load(name: str, default):
    p = _path(name)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save(name: str, data) -> None:
    with _LOCK:
        _path(name).write_text(json.dumps(data, indent=2), encoding="utf-8")


def append_receipt(receipt: dict) -> dict:
    """Append one receipt to the append-only log; returns it with id + ts."""
    receipt = {"receipt_id": new_id("rcpt"), "ts": _now(), **receipt}
    with _LOCK:
        with _path("receipts.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt) + "\n")
    return receipt


def receipts() -> list[dict]:
    p = _path("receipts.jsonl")
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
