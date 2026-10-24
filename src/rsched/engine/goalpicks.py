"""A numbered PICK is the person's own words — the choices one resolves against (F731).

The engine renders an options question as NUMBERED PICKS: `answerform.js` labels every option
button "<n> · <option>" and digit keys 1-9 prefill one, and a run that lays its choices out as
a numbered list in the question's own prose is told to do exactly that. So the person's whole
reply is often the keystroke he was shown — `1.`, `2`, `3)`. The goal validator then refused
the run for the ambiguity the engine had built: "the words '1.' are not in anything the person
wrote to this run" (vodafone-outage-claims, run 20261019-031230, turn 29 onward), so an
approval a routine had correctly recorded as a goal could not be recorded at all.

What the console actually sends was measured, not assumed (2026-10-24): the live answered
records `vodafone-outage-claims/questions/answered/q-20261019-031230-29.json` (`"answer": "1."`)
and `tv-show-tracker-seedbox-manager/.../q-20260923-222843-75.json` (`"answer": "3"`) BOTH carry
`"options": []` — their choices were numbered in the question's PROSE. A real options list
submits the option TEXT (`input.value = o`), which needs no resolving. Both shapes therefore
have to be read: `options` when the question carried them, the question's own numbered list
when it did not.

The grounding rule does not weaken. A number resolves ONLY to a choice of that number which
the person actually picked this run; a quote matching neither his words nor such a choice is
refused exactly as before, and the refusal still names the words the model wrote.
"""

from __future__ import annotations

import re

#: A fenced block's delimiter. The measured question contains ```js whose first code line is
#: `1 < r.length && …` — code is not a choice list, and a question that shows the run numbers
#: in a snippet must not have them read as picks.
_FENCE = re.compile(r"^\s*(?:```|~~~)")
#: One numbered choice in a question's prose, as Markdown defines a list item: the number at the
#: line's start (≤3 spaces of indent), then `.` or `)`, then the words.
_CHOICE = re.compile(r"^ {0,3}(\d{1,2})[.)]\s+(\S.*)$")
#: A BARE pick: the whole text is the number of a choice and nothing else. Anything more is
#: prose, and prose carries the person's own words already.
_PICK = re.compile(r"^(\d{1,2})\s*[.)]?$")


def pick(text: str) -> int | None:
    """The choice number this text is, or None when it is not a bare pick."""
    m = _PICK.match(str(text).strip())
    return int(m[1]) if m else None


def choices(question: str, options: list[str]) -> dict[int, str]:
    """What each number offered, by number: the `options` the question carried, else the
    numbered list in its prose.

    A number the prose uses TWICE with different words (two lists in one question) is dropped
    rather than guessed — which of them he picked is unknowable, and grounding a goal in the
    wrong sentence is the one thing the quote rule exists to prevent.
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
    # asking happened in an earlier run, so this run has no `question` event to match on. That
    # is the path the measured defect took.
    q = questions.get(str(payload.get("qid") or "")) or {}
    question = str(q.get("question") or payload.get("question") or "")
    options = [str(o) for o in (q.get("options") or payload.get("options") or [])]
    return choices(question, options).get(n, "")


def with_choice(ev: dict, text: str, questions: dict[str, dict]) -> str:
    """`text` with the words behind its pick appended — unchanged when it is not one.

    Appended to the answer's OWN text rather than added as a text of its own, so every index
    still means one thing the person wrote and `heard_at(after=…)` keeps its meaning.
    """
    return f"{text}\n{chose}" if (chose := chosen(ev, questions)) else text


def quoted_choice(quote: str, events: list[dict]) -> str:
    """The words a bare numbered QUOTE means: the choice the person picked by that number this
    run, or "" — for a quote that is not a bare pick, or names a number he never picked.

    The LATEST such pick, when a run asked two options questions and he answered both with the
    same number: the approval a run is recording is the one that just arrived. A run that needs
    to be unambiguous quotes the choice's own words, which is always possible (`person_texts`
    carries them) and is what the refusal tells it to do.
    """
    n = pick(quote)
    if n is None:
        return ""
    questions = asked(events)
    for ev in reversed(events):
        if pick((ev.get("payload") or {}).get("text") or "") == n and (c := chosen(ev, questions)):
            return c
    return ""
