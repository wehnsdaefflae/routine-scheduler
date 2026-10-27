r"""A question's PROSE, repaired at intake — the double-escaped newline (D85-A).

Moved out of `engine/interact.py`, which is the ask protocol (a run's need for a human into a
decision record and back into an answer) and sits exactly on the repo's ~350-line bar. Repairing
what a model emitted is a different responsibility, and the repo already keeps the twin of this
one apart in `engine/loopend.py` (a finish summary whose newlines came back double-escaped).

Nothing about the rule changed in the move.
"""

from __future__ import annotations


def unescape_newlines(text: str) -> str:
    r"""Literal backslash-n sequences ("\n") become real newlines — see `normalize_plain`."""
    if "\\n" not in text:
        return text
    return text.replace("\\r\\n", "\n").replace("\\n", "\n")


def normalize_plain(qtype: str, question: str, default: str) -> tuple[str, str]:
    r"""D85-A (F291/R242): some models double-escape newlines in PLAIN question text, so a
    literal backslash-n reached the UI as "\n" (the renderer and store are correct —
    mdInline handles real newlines). Normalize question + default at intake, but ONLY for
    plain questions: util-approvals embed util SOURCE and access requests carry typed ids,
    where the two-character sequence is intended verbatim.
    """
    if qtype != "question":
        return question, default
    return unescape_newlines(question), unescape_newlines(default)
