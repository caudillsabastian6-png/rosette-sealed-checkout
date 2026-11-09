"""Blast Radius engine — client/stub side.

The engine core is built for Vultr (Nov 5–8) against ENGINE-CONTRACT.md v1.
This package will either (a) vendor/import that engine, or (b) fall back to
the in-repo stub implementing the same six verbs:
read / check / dry_run / approve / apply(+log) / undo.
Nothing in the PayPal layer may call an adapter except through these verbs.
"""
