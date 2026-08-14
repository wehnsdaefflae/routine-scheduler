"""Reject an authored ok-finish that CLAIMS a high-signal action the run never took.

Motivated by self-audit finding F127 (2026-07-19): a routine wrote *"Filed report to
self-audit"* in its finish summary while taking no `report` action — narrated unperformed
work. The reviewer chose to REJECT such a finish (decision D31 = option B) so the run must
either actually take the action or drop the false claim before it may finish.

The check is deliberately NARROW — a false reject blocks a legitimate finish on the shared run
path, so precision beats recall:

  * Only the high-signal, side-effecting action kinds whose *engine token* essentially
    never appears in prose except as a deliberate claim: ``report``, ``ask_user``,
    ``schedule_run``, ``create_routine``. We match the LITERAL underscore token (so the
    common English word "created" never trips it), never natural-language paraphrases
    ("asked the user"), which are too ambiguous to reject on.
  * The token must be bound (within a short window) to an affirmative completion verb, with no
    negation just before ("did not file a report" is fine).
  * META routines (tag ``meta``: self-audit, routine-improver, config-optimizer, token-lab,
    clarification) are EXEMPT. Their whole job is to analyse and quote *other* runs' actions,
    so their summaries legitimately contain these tokens without taking the action — a universal
    check would false-reject the auditor's own finishes (self-audit's F127 summary literally
    quotes "Filed report").
"""
from __future__ import annotations

import re

# action kind -> regex of affirmative completion verb stems that assert it was performed
_CLAIM_ACTIONS: dict[str, str] = {
    # "sent/send/open" deliberately absent for report: they are DOCUMENT-shipping verbs, and
    # a routine whose deliverable is a PDF/LaTeX "report" writes them in every honest summary
    # (R360, frame-fill-lab:20260814-061034 — four consecutive false rejects).
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
    dashboard last-outcome and the next run's digest — a silent, recurring UI degradation any
    model can reintroduce (self-audit R82, 2026-08-01).

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
    """
    if is_meta or not summary:
        return []
    taken = set(taken_kinds)
    flagged: set[str] = set()
    for kind, verbs in _CLAIM_ACTIONS.items():
        if kind in taken or kind not in summary:
            continue
        # \b-bounded: "reports/", "reporting" or "schedule_runner" never contain the claim
        # token as a word, so document paths and derived nouns cannot trip the guard (R360).
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
