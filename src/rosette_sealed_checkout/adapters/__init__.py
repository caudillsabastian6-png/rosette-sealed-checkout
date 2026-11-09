"""Adapters: the only code allowed to touch an external service.

One subpackage per service. Adapters register with the engine as readers
(read-only) or effectors (the only write path) — see ENGINE-CONTRACT.md §5.
No adapter value (key, token, secret) is ever hardcoded; all come from .env.
"""
