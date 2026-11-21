r"""A question's PROSE, repaired at intake — the double-escaped newline (D85-A).

Kept apart from the ask protocol (`engine/interact.py`): repairing what a model emitted is a
different responsibility (its twin for finish summaries is in `engine/loopend.py`).
"""

from __future__ import annotations


def unescape_newlines(text: str) -> str:
    r"""Literal backslash-n sequences ("\n") become real newlines — see `normalize_plain`."""
    if "\\n" not in text:
        return text
    return text.replace("\\r\\n", "\n").replace("\\n", "\n")


def normalize_plain(qtype: str, question: str, default: str) -> tuple[str, str]:
    r"""Some models double-escape newlines in PLAIN question text (D85-A). Normalize question +
    default at intake, but ONLY for plain questions: util-approvals embed util SOURCE and access
    requests carry typed ids, where the two-character sequence is intended verbatim.
    """
    if qtype != "question":
        return question, default
    return unescape_newlines(question), unescape_newlines(default)
