"""The Rosette seal (PREP.md §3, steps 3 and 5).

seal.verify(identity, evidence) -> {verdict, summary, receipt_ref}
Verdicts follow house law: "likely_genuine" | "likely_counterfeit" |
"inconclusive" (labels: Likely Genuine / Likely Counterfeit /
Inconclusive). The seal MUST be able to abstain — an evidence function
that cannot say "inconclusive" does not pass the engine contract (§6).

Implementation: seal/verify.py (demo evidence function over sandbox-copy
data; the house pipeline swaps in behind the same signature).
"""
from __future__ import annotations

from .. import state as _state
from .verify import LABELS, VERDICTS, verify as _verify

__all__ = ["verify", "LABELS", "VERDICTS"]


def verify(identity: dict, evidence: dict | None) -> dict:
    """Run the seal check and pin a receipt reference to the result."""
    result = _verify(identity, evidence)
    receipt = _state.append_receipt({
        "verb": "check",
        "actor": "engine",
        "summary": f"Seal verdict {LABELS[result['verdict']]}: "
                   f"{result['summary']}",
        "external_ids": {},
        "undoable": False,
        "undoes": None,
    })
    return {**result, "receipt_ref": receipt["receipt_id"]}
