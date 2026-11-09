"""Demo sandbox state machine — the stub money rail (ENGINE-CONTRACT §8.5).

Stands in for the PayPal Orders v2 / Payments v2 sandbox until the owner's
credential pull lands real sandbox keys in .env. It models the sandbox's
state shapes faithfully (CREATED -> APPROVED -> AUTHORIZED -> COMPLETED /
VOIDED, authorization expiry, capture ids) so the whole checkout flow —
create order -> approve -> authorize -> capture | void, plus refund —
runs end to end locally with visible state at every step.

Every payload this module produces carries "mode": "stub" and the web UI
labels it on screen. Stub money is not even sandbox money and is never
presented as a real PayPal response (house honesty fence).
"""
