"""Reject an authored ok-finish that CLAIMS a high-signal action the run never took.

Motivated by self-audit finding F127 (2026-07-19): a routine wrote *"Filed report_bug to
self-audit"* in its finish summary while taking no `report_bug` action — narrated unperformed
work. The reviewer chose to REJECT such a finish (decision D31 = option B) so the run must
either actually take the action or drop the false claim before it may finish.

The check is deliberately NARROW — a false reject blocks a legitimate finish on the shared run
path, so precision beats recall:

  * Only the three high-signal, side-effecting action kinds whose *engine token* essentially
    never appears in prose except as a deliberate claim: ``report_bug``, ``ask_user``,
    ``schedule_run``. We match the LITERAL underscore token, never natural-language paraphrases
    ("asked the user"), which are too ambiguous to reject on.
  * The token must be bound (within a short window) to an affirmative completion verb, with no
    negation just before ("did not file a report_bug" is fine).
  * META routines (tag ``meta``: self-audit, routine-improver, config-optimizer, token-lab,
    clarification) are EXEMPT. Their whole job is to analyse and quote *other* runs' actions,
    so their summaries legitimately contain these tokens without taking the action — a universal
    check would false-reject the auditor's own finishes (self-audit's F127 summary literally
    quotes "Filed report_bug").
"""
from __future__ import annotations

import re

# action kind -> regex of affirmative completion verb stems that assert it was performed
_CLAIM_ACTIONS: dict[str, str] = {
    "report_bug": r"fil|submit|post|logg|sent|send|open|escalat|rais",
    "ask_user": r"ask|question|escalat|surfac|prompt",
    "schedule_run": r"schedul|arm|queu",
}
_NEGATION = r"\b(?:not|no|never|without|didn'?t|couldn'?t|cannot|can'?t|skip)\b"
_WINDOW = 24  # max chars between the verb and the literal token, and the negation look-back


def unbacked_action_claims(summary: str, taken_kinds, is_meta: bool) -> list[str]:
    """Return the sorted action kinds the ``summary`` claims were performed but that are NOT in
    ``taken_kinds``. Empty for meta routines, an empty summary, or when every claimed action was
    actually taken this run.
    """
    if is_meta or not summary:
        return []
    taken = set(taken_kinds)
    flagged: set[str] = set()
    for kind, verbs in _CLAIM_ACTIONS.items():
        if kind in taken or kind not in summary:
            continue
        token = re.escape(kind)
        pat = re.compile(
            rf"(?:{verbs})\w*.{{0,{_WINDOW}}}?{token}|{token}.{{0,{_WINDOW}}}?(?:{verbs})\w*",
            re.IGNORECASE,
        )
        for m in pat.finditer(summary):
            pre = summary[max(0, m.start() - _WINDOW):m.start()]
            if re.search(_NEGATION, pre, re.IGNORECASE):
                continue
            flagged.add(kind)
            break
    return sorted(flagged)
