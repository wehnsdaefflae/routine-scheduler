"""Reject an authored ok-finish that CLAIMS a high-signal action the run never took.

Narrated unperformed work (D31): the run must take the action or drop the claim before it may
finish. The check is deliberately NARROW — a false reject blocks a legitimate finish, so
precision beats recall:

  * Only the high-signal, side-effecting action kinds whose *engine token* essentially
    never appears in prose except as a deliberate claim: ``report``, ``ask_user``,
    ``schedule_run``, ``create_routine``. We match the LITERAL underscore token (so the
    common English word "created" never trips it), never natural-language paraphrases
    ("asked the user"), which are too ambiguous to reject on.
  * The token must be bound (within a short window) to an affirmative completion verb, with no
    negation just before ("did not file a report" is fine).
  * META routines (tag ``meta``) are EXEMPT: they analyse and quote *other* runs' actions.
"""
from __future__ import annotations

import re

# action kind -> regex of affirmative completion verb stems that assert it was performed
_CLAIM_ACTIONS: dict[str, str] = {
    # "sent/send/open" deliberately absent for report: document-shipping verbs that a routine
    # whose deliverable is a PDF "report" writes in every honest summary (R360)
    "report": r"fil|submit|post|logg|escalat|rais",
    "ask_user": r"ask|question|escalat|surfac|prompt",
    "schedule_run": r"schedul|arm|queu",
    "create_routine": r"creat|materializ|scaffold|set up|built|generat",
}
_NEGATION = r"\b(?:not|no|never|without|didn'?t|couldn'?t|cannot|can'?t|skip)\b"
_WINDOW = 24  # max chars between the verb and the literal token, and the negation look-back
# A "report" beside document vocabulary is a DELIVERABLE, not the action (R360): "compiled
# the LaTeX report", "the report PDF", "reports/analysis.pdf" claim no engine action.
_DOC_CONTEXT = re.compile(r"(?i)pdf|latex|\breports?/|\.(?:md|tex|html?)\b")


def normalize_escaped_newlines(text: str) -> str:
    r"""Repair a summary / say / report-detail that DOUBLE-ESCAPED its newlines.

    A finish or report field is authored as JSON, where a real line break is written ``\\n``.
    A model that instead emits ``\\\\n`` yields a Python string carrying the literal two
    characters backslash-n, which render verbatim as ``\\n`` in the Markdown console, the
    dashboard last-outcome and the next run's digest.

    The tell of a WHOLESALE double-escape is unambiguous: the text carries literal ``\\n``
    (or ``\\t``) escapes and NOT ONE real newline. Then the author plainly meant line breaks,
    so the literal escapes are normalized to the real characters. If any real newline is
    already present, every literal ``\\n`` is left untouched — it is intentional (e.g. a code
    snippet or a quoted path), never a wholesale double-escape.
    """
    if not text or "\n" in text:
        return text
    if "\\n" not in text and "\\t" not in text:
        return text
    return text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")


def unbacked_action_claims(summary: str, taken_kinds, is_meta: bool) -> list[str]:
    """Return the sorted action kinds the ``summary`` claims were performed but that are NOT in
    ``taken_kinds``. Empty for meta routines, an empty summary, or when every claimed action was
    actually taken this run.

    ``taken_kinds`` is ``loop.executed_kinds`` — kinds that EXECUTED, per the one predicate
    ``donothing.grounds_a_finish`` (D243) — never the kinds the model EMITTED, which include
    held and refused actions. A kind that cannot ground a finish cannot back a claim either.
    """
    if is_meta or not summary:
        return []
    taken = set(taken_kinds)
    flagged: set[str] = set()
    for kind, verbs in _CLAIM_ACTIONS.items():
        if kind in taken or kind not in summary:
            continue
        # \b-bounded, so document paths and derived nouns ("reports/", "reporting") never trip
        token = rf"\b{re.escape(kind)}\b"
        pat = re.compile(
            rf"(?:{verbs})\w*.{{0,{_WINDOW}}}?{token}|{token}.{{0,{_WINDOW}}}?(?:{verbs})\w*",
            re.IGNORECASE,
        )
        for m in pat.finditer(summary):
            pre = summary[max(0, m.start() - _WINDOW):m.start()]
            if re.search(_NEGATION, pre, re.IGNORECASE):
                continue
            if kind == "report":
                seg = summary[max(0, m.start() - _WINDOW):m.end() + _WINDOW]
                if _DOC_CONTEXT.search(seg):
                    continue
            flagged.add(kind)
            break
    return sorted(flagged)
