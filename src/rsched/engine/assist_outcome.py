"""The `outcome-questioned` assist predicate — a person is asking about RESULTS, not process.

The 2026-10-17 investigation found the operator's complaints about outcomes turned into more
self-scrutiny every time: "do you even send out applications?", "work on your skills… you need
to get better", "spend considerably more time on self-healing" each produced another check, a
longer recipe, a closer audit of the routine's own process — and no more interviews. The run
read the words as a correction of HOW it worked (`fix-the-cause`'s moment) when they were a
statement about WHAT it produced.

This predicate notices the moment so the run does not have to: a person's words reached the run
— at boot (a message, an answer given between runs) or mid-run — and they question the results.
It fires the `outcome-first` rule's line, which sends the run to its outcome series and to the
routine-improver's outcome review instead of to another self-check.

The match is a deliberately plain vocabulary (English and German, the operator's two) over the
person's own words only — `goals.person_texts`, never a report, a machine channel or the recipe.
A false positive costs one appended line; a miss costs what it cost before. The transcript is
read only when the person has spoken since the last look (`ctx.user_replies` moved).
"""

from __future__ import annotations

import re
from typing import Any

_RESULTS = (r"interviews?|repl(?:y|ies)|responses?|answers?|wins?|won|clients?|contracts?|"
            r"applications?|programm?es?|money|leads?|bookings?|results?|outcomes?|sales?|"
            r"antworten|ergebnisse?|zusagen|auftr[aä]ge?|kunden|bewerbungen?|geld|erfolge?")
_SIGNS = re.compile(
    rf"\b(?:do|did|are|have) you even\b"
    rf"|\b(?:no|zero|0|not a single|not one|none of the|kein(?:e|en)?|null)\s+"
    rf"(?:\w+\s+)?(?:{_RESULTS})\b"
    rf"|\b(?:get(?:ting)? better|need to improve|have to improve|not working|isn'?t working|"
    rf"doesn'?t work|does not work|useless|a waste of|nothing (?:came|comes|happened|to show)|"
    rf"what (?:have|are) you (?:even )?(?:done|doing)|where are the|why (?:is|are) there no)\b"
    rf"|\b(?:bringt nichts|funktioniert nicht|wo sind die|warum gibt es keine|"
    rf"nichts gebracht|besser werden)\b",
    re.IGNORECASE)


def questions_results(text: str) -> bool:
    """Do these words question what the routine PRODUCES (rather than how it works)?"""
    return bool(_SIGNS.search(text or ""))


def outcome_questioned(s: Any) -> bool:
    """New words of the person since the last look question the results (module docstring).
    Watermarked on the loop by the person's utterance count, so each message is read once —
    including the ones the run's boot delivered, which are exactly where a complaint between
    runs lands.
    """
    from . import goals

    ctx = s.ctx
    replies = int(getattr(ctx, "user_replies", 0) or 0)
    seen = getattr(s.loop, "assist_outcome_seen", None)
    if seen is not None and seen[0] == replies:
        return False
    texts = goals.person_texts(goals.transcript_events(ctx.run_dir), brief=ctx.brief or "")
    start = seen[1] if seen is not None else 0
    s.loop.assist_outcome_seen = (replies, len(texts))
    return any(questions_results(t) for t in texts[start:])
