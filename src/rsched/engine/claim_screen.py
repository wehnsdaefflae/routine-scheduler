"""Which routines' finish claims a DECISION model may screen, and at what probability (D302).

D287 asked for a cascade: a typed screen over each claim, with the prose judge called only on
the claims it flags. D302 is its narrowing — the operator's answer: *"Cascade per routine:
the six routines at 100% agreement get the screen, the three below 85% keep the prose judge for
every claim."* This module is that table and nothing else; `engine/verifier.py` consults it and
`engine/decision_shadow.py` keeps measuring every routine, screened or not, so the table has a
live evidence source to be revised against.

WHAT THE SCREEN MAY DO, and the bound that makes it safe: it may skip the prose call for ONE
finish, and only when EVERY claim of that finish clears `THRESHOLD`. A single claim below it
sends the whole finish to the prose judge, because the judge is one call over all the claims —
screening half of them saves nothing and loses the judge's view of the rest.

THE THRESHOLD IS NOT THE SHADOW'S 0.5. The stream recorded comparisons at 0.5 because that is
the only threshold that is not a tuning choice; using it HERE would pass 8 of the 11 claims the
prose judge refuted (probabilities 0.549 to 0.942, seven different routines). Measured over the
515 finish claims in `.control/decision-shadow.jsonl` (a seven-day window), zero refutations are
missed fleet-wide from 0.95 up, so 0.95 is the floor and the table sets it.

WHAT IT IS WORTH, measured the same day and deliberately recorded here rather than in a report:
the prose judge makes ONE call per finish (128 calls over those 515 claims), so a claim-level
saving is not a call-level saving. Over the six screened routines at 0.95 the cascade skips 6 of
128 finish calls — 4.7%, with zero missed refutations. The 27% figure the decision was argued
with counts CLAIMS, not calls. The honest reason to ship it anyway is that it is the safe shape
of a mechanism the operator wants in place; the honest thing to say is that it saves single
digits until the stream shows a routine deserving a lower bar.

A ROUTINE NOT IN THE TABLE KEEPS THE PROSE JUDGE. The answer names six to screen and three to
exclude; every other routine — the ones between 85% and 100%, the ones with too few pairs, and
every routine created after the measurement — falls through to the judge. That is the default
written down rather than left to a fallthrough, because the failure direction matters: an
unscreened routine costs a prose call, a wrongly screened one lets a false claim reach the
operator's summary.
"""

from __future__ import annotations

#: P(the claim is supported) at or above which the screen may answer for the prose judge.
#: Not the shadow's recording threshold — see the module docstring.
THRESHOLD = 0.95

#: The fewest agreeing pairs a routine needs before its agreement means anything. A routine at
#: 100% over three claims is not evidence, and without this rule the first three agreeing claims
#: of a brand-new routine would buy it a screen. The floor is 12 because that is the smallest
#: sample among the six the operator named (`fau`, 12/12): his answer is the authority on WHO,
#: so the rule is set to admit exactly his table and nothing weaker — a stricter floor would
#: have silently dropped two of his six, which is a rule overruling a decision.
MIN_PAIRS = 12

#: slug → (agreeing claims, claims compared) in the measurement window
#: (`.control/decision-shadow.jsonl`, a seven-day window, 515 finish claims, fleet
#: agreement 92.6%). Every entry is traceable to that stream and re-derivable from it; the
#: operator's answer names these six and no others.
MEASURED: dict[str, tuple[int, int]] = {
    "folder-reorg": (26, 26),
    "rules-review": (26, 26),
    "feeld-webui-maintainer": (21, 21),
    "moltbook-heartbeat": (20, 20),
    "config-optimizer": (13, 13),
    "fau": (12, 12),
}

#: Named by the answer as keeping the prose judge for every claim, with their measured
#: agreement. Listed rather than merely omitted so that a later edit cannot promote one by
#: forgetting why it was out.
EXCLUDED: dict[str, tuple[int, int]] = {
    "tv-show-tracker-seedbox-manager": (25, 33),
    "global-utils-review": (14, 18),
    "freelance-hunter": (31, 39),
}


def agreement(slug: str) -> float | None:
    """The measured agreement for `slug`, or None for a routine the window did not cover."""
    pairs = MEASURED.get(slug) or EXCLUDED.get(slug)
    if not pairs or not pairs[1]:
        return None
    return pairs[0] / pairs[1]


def screened(slug: str) -> bool:
    """May this routine's finish claims be screened at all?

    True only for a routine the operator's answer names AND whose measurement meets the
    minimum-pairs rule. Both halves are load-bearing: the answer is the authority, and the rule
    is what stops a future edit adding a slug on three agreeing claims.
    """
    pairs = MEASURED.get(slug)
    if not pairs:
        return False
    agree, total = pairs
    return total >= MIN_PAIRS and agree == total


def may_skip_judge(slug: str, probabilities: list[float | None]) -> bool:
    """Can the prose judge be skipped for THIS finish?

    Only when the routine is screened, the screen answered for every claim, and every one of
    those answers is at or above `THRESHOLD`. An unanswered claim (`None` — no decision model,
    a provider error, a malformed answer) is a claim the screen did not cover, so the judge
    runs: the cascade may be cheaper than the judge but never quieter than it.
    """
    if not probabilities or not screened(slug):
        return False
    return all(p is not None and p >= THRESHOLD for p in probabilities)
