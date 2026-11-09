"""The Rosette seal — demo evidence function (PREP.md §3; ENGINE-CONTRACT §6).

seal.verify(identity, evidence) -> {"verdict": ..., "receipt_ref": ...}

DEMO implementation over sanitized sandbox-copy data in data/demo/. It
compares the fulfilment scans' recorded identity (printed number / name /
set) against the sealed listing identity and applies house verdict law:

  Likely Genuine       identity matches on every recorded field
  Likely Counterfeit   printed number matches but the name/set recorded on
                       the shipped card contradicts the sealed identity
                       (the classic swap: right number, wrong card)
  Inconclusive         evidence missing, unreadable, or the printed number
                       itself does not match / cannot be confirmed —
                       the seal abstains rather than guessing

Vocabulary is exactly Likely Genuine / Likely Counterfeit / Inconclusive.
The real house pipeline replaces this function behind the same signature;
nothing downstream depends on how the verdict was reached.
"""
from __future__ import annotations

VERDICTS = ("likely_genuine", "likely_counterfeit", "inconclusive")

LABELS = {
    "likely_genuine": "Likely Genuine",
    "likely_counterfeit": "Likely Counterfeit",
    "inconclusive": "Inconclusive",
}


def verify(identity: dict, evidence: dict | None) -> dict:
    """identity: the sealed listing identity {name, set, number}.
    evidence: the fulfilment scan record {name, set, number, legible} or
    None when no fulfilment evidence exists."""
    if not evidence:
        return {"verdict": "inconclusive",
                "summary": "No fulfilment evidence on file — the seal abstains."}
    if not evidence.get("legible", False):
        return {"verdict": "inconclusive",
                "summary": "Fulfilment scans are not legible enough to read "
                           "the printed number — the seal abstains."}
    if not evidence.get("number") or evidence["number"] != identity.get("number"):
        return {"verdict": "inconclusive",
                "summary": "The shipped card's printed number does not match "
                           "the sealed identity and cannot be confirmed — "
                           "the seal abstains."}
    # Printed number matches (the decisive field). Name/set decide the rest.
    name_ok = (evidence.get("name") or "").strip().lower() == \
              (identity.get("name") or "").strip().lower()
    set_ok = (evidence.get("set") or "").strip().lower() == \
             (identity.get("set") or "").strip().lower()
    if name_ok and set_ok:
        return {"verdict": "likely_genuine",
                "summary": "Shipped card matches the sealed identity on "
                           "printed number, name and set."}
    return {"verdict": "likely_counterfeit",
            "summary": "Printed number matches but the shipped card's "
                       "name/set contradict the sealed identity — "
                       "consistent with a swapped card."}
