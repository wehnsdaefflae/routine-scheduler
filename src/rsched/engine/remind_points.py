"""The reminder layer's TRIGGER KINDS, asked through the hook-point registry (D152 step 4).

`hooks/points.py` has said since 0.458.0 that the `reminder` mechanism fires at eight points,
and `points.TRIGGER_POINTS` maps every one of `reminders.KINDS` to the point it is tested at.
What was missing is the only thing that makes such a table true: a reader. The reminder layer
matched with `store.matching(...)` directly at four sites in `engine/remind.py`, so the
registry described it without governing it — the same shape the rule layer had before
`assist._matching` was routed through `hooks/dispatch.at`, and the half of the operator's
question ("are hooks fully implemented? … So should reminders") that the point table alone
could not answer.

WHAT CHANGES, AND WHAT DELIBERATELY DOES NOT. The matching is now asked at a POINT:

    matching_at(loop, "result", target)   # -> the reminders that fire, in order

which resolves the kind to its point (`points.for_trigger`), drops a kind this build does not
map (lenient, like every reader in `hooks/`), and evaluates each candidate through
`hooks/dispatch.at` — inheriting the two invariants the seam owns, for free and identically
with the rule layer:

1. a candidate whose payload the point cannot carry never fires (`points.allows`);
2. a predicate that RAISES means "did not fire", never a failed turn.

(2) is new to this layer and it is the substance of the step rather than a tidy-up: a reminder
pattern is a user-authored regex compiled at load, and `store.matching` called it inside the
turn loop with nothing between a pathological pattern and the run. Now a raising match is one
reminder that did not fire.

What stays in `engine/remind.py`: the once-per-action hold budget, the `fires` tally, and every
rendering. `hooks/dispatch.py` says why — it decides which candidates fire and nothing else, so
a shared guard cannot let one layer spend another's, and the wording is prompt surface that
belongs to the mechanism.

WHY A SEPARATE MODULE and not six lines in `remind.py`: that file is 605 lines against a
ceiling of 605 (`tests/size_ratchet.json`) — over the house bar with ZERO slack, so it may not
gain a line. The payload mapping lives here beside the one function that reads it.
"""

from __future__ import annotations

from ..hooks import dispatch, points
from ..reminders import HOLDING_KINDS, Reminder


# Trigger kind -> the PAYLOAD a reminder of that kind delivers, in `points.PAYLOADS`' own
# vocabulary. Two values only, and the split is not this layer's choice: a reminder whose kind
# can stop an action delivers a `hold` (the action is already emitted, so reaching the model
# means not running it), and every other kind rides the observation it was getting anyway,
# which is a `remind`. `reminders.HOLDING_KINDS` is the authority on which is which, so this
# mapping is derived from it rather than being a second list that can disagree.
#
# Why the payload is declared at all: it is what lets the POINT refuse a mismatch. A reminder
# kind pointed at `pre-action` with a non-holding payload would be a silent no-op — the shape
# `hooks/dispatch` exists to turn into a dropped candidate with a test.
def payload_of(kind: str) -> str:
    """The payload a reminder of this trigger kind delivers at its point."""
    return "hold" if str(kind or "") in HOLDING_KINDS else "remind"


def matching_at(loop, kind: str, target: str) -> list[Reminder]:
    """The reminders of `kind` that FIRE on `target`, decided by the one hook-point evaluator.

    `loop.reminders` is this run's live set; `target` is the text the kind matches against (the
    canonical action string, the observation, the turn's `say`, a `turn:<moment>` block). The
    caller renders, counts and delivers — see this module's docstring for why those stay there.

    A kind with no point in this build fires nothing, rather than falling back to a direct
    match: a fallback would make the registry advisory again, which is the defect being fixed.
    """
    point = points.for_trigger(kind)
    if not point:
        return []
    candidates = [r for r in (loop.reminders or []) if r.kind == kind]
    return dispatch.at(point, candidates, lambda r: r.matches(target),
                       payload=lambda r: payload_of(r.kind))
