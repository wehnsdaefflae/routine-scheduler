"""The `outcome-questioned` assist predicate — a person is asking about RESULTS, not process.

A complaint about outcomes is easily misread as a correction of HOW the routine works
(`fix-the-cause`'s moment), producing more self-scrutiny instead of better results. This
predicate notices when a person's words — at boot or mid-run — question the results, and fires
the `outcome-first` rule's line, sending the run to its outcome series instead of another
self-check.

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
    including those delivered at boot.
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
