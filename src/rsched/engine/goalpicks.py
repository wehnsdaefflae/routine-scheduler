"""A numbered PICK is the person's own words — the choices one resolves against (F731).

The console renders an options question as numbered picks, so a person's whole reply is often
`1.`, `2` or `3)` — which the goal validator would refuse as not his words. Two shapes are
read: the question's `options` when it carried them (the console then submits the option text,
needing no resolving), else the numbered list in the question's own prose.

The grounding rule does not weaken: a number resolves ONLY to a choice of that number the person
actually picked this run; anything else is refused as before.
"""

from __future__ import annotations

import re

#: A fenced block's delimiter — numbers inside a code snippet are not picks.
_FENCE = re.compile(r"^\s*(?:```|~~~)")
#: One numbered choice in a question's prose, as Markdown defines a list item: the number at the
#: line's start (≤3 spaces of indent), then `.` or `)`, then the words.
_CHOICE = re.compile(r"^ {0,3}(\d{1,2})[.)]\s+(\S.*)$")
#: A BARE pick: the whole text is a choice number and nothing else (prose carries its own words).
_PICK = re.compile(r"^(\d{1,2})\s*[.)]?$")


def pick(text: str) -> int | None:
    """The choice number this text is, or None when it is not a bare pick."""
    m = _PICK.match(str(text).strip())
    return int(m[1]) if m else None


def choices(question: str, options: list[str]) -> dict[int, str]:
    """What each number offered, by number: the `options` the question carried, else the
    numbered list in its prose.

    A number the prose uses TWICE with different words is dropped rather than guessed.
    """
    if options:
        return {n: text for n, o in enumerate(options, 1) if (text := str(o).strip())}
    found: dict[int, str] = {}
    ambiguous: set[int] = set()
    fenced = False
    for line in str(question).splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced or (m := _CHOICE.match(line)) is None:
            continue
        n, body = int(m[1]), m[2].strip()
        if n in found and found[n] != body:
            ambiguous.add(n)
        found.setdefault(n, body)
    for n in ambiguous:
        del found[n]
    return found


def asked(events: list[dict]) -> dict[str, dict]:
    """Every question this run asked, by qid — where a BLOCKING ask's `options` live
    (engine/interact.py writes them onto the `question` event).
    """
    return {str(p.get("qid") or ""): p
            for ev in events if ev.get("type") == "question" and (p := ev.get("payload") or {})}


def chosen(ev: dict, questions: dict[str, dict]) -> str:
    """The words of the choice this ANSWER event picked by number — "" when its text is not a
    bare pick, or no choice of that number was offered.
    """
    if ev.get("type") != "answer":
        return ""
    payload = ev.get("payload") or {}
    n = pick(payload.get("text") or "")
    if n is None:
        return ""
    # A DEFERRED answer carries its question on the answer event itself (engine/boot.py): the
    # asking happened in an earlier run, so there is no `question` event to match on.
    q = questions.get(str(payload.get("qid") or "")) or {}
    question = str(q.get("question") or payload.get("question") or "")
    options = [str(o) for o in (q.get("options") or payload.get("options") or [])]
    return choices(question, options).get(n, "")


def with_choice(ev: dict, text: str, questions: dict[str, dict]) -> str:
    """`text` with the words behind its pick appended — unchanged when it is not one.

    Appended to the answer's OWN text so every index still means one thing the person wrote.
    """
    return f"{text}\n{chose}" if (chose := chosen(ev, questions)) else text


def quoted_choice(quote: str, events: list[dict]) -> str:
    """The words a bare numbered QUOTE means: the choice the person picked by that number this
    run, or "" — for a quote that is not a bare pick, or names a number he never picked.

    The LATEST such pick wins when two questions got the same number; a run needing to be
    unambiguous quotes the choice's own words.
    """
    n = pick(quote)
    if n is None:
        return ""
    questions = asked(events)
    for ev in reversed(events):
        if pick((ev.get("payload") or {}).get("text") or "") == n and (c := chosen(ev, questions)):
            return c
    return ""
