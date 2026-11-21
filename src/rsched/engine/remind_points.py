"""The reminder layer's TRIGGER KINDS, asked through the hook-point registry (D152).

`points.TRIGGER_POINTS` maps every one of `reminders.KINDS` to the point it is tested at; this
module is that table's reader, so the registry governs the layer rather than describing it:

    matching_at(loop, "result", target)   # -> the reminders that fire, in order

resolves the kind to its point (`points.for_trigger`), drops a kind this build does not map,
and evaluates each candidate through `hooks/dispatch.at`, inheriting the seam's two invariants
identically with the rule layer:

1. a candidate whose payload the point cannot carry never fires (`points.allows`);
2. a predicate that RAISES (e.g. a pathological user-authored regex) means "did not fire",
   never a failed turn.

The once-per-action hold budget, the `fires` tally and every rendering stay in
`engine/remind.py`: dispatch decides which candidates fire and nothing else. A separate module
because `remind.py` is at its size-ratchet ceiling.
"""

from __future__ import annotations

from ..hooks import dispatch, points
from ..reminders import HOLDING_KINDS, Reminder


# Trigger kind -> the PAYLOAD it delivers, in `points.PAYLOADS`' vocabulary: `hold` for a kind
# that can stop an action, `remind` for every other (derived from `reminders.HOLDING_KINDS`, not
# a second list). Declared so the POINT can refuse a mismatch instead of a silent no-op.
def payload_of(kind: str) -> str:
    """The payload a reminder of this trigger kind delivers at its point."""
    return "hold" if str(kind or "") in HOLDING_KINDS else "remind"


def matching_at(loop, kind: str, target: str) -> list[Reminder]:
    """The reminders of `kind` that FIRE on `target`, decided by the one hook-point evaluator.

    `loop.reminders` is this run's live set; `target` is the text the kind matches against (the
    canonical action string, the observation, the turn's `say`, a `turn:<moment>` block). The
    caller renders, counts and delivers. A kind with no point fires nothing — a direct-match
    fallback would make the registry advisory again.
    """
    point = points.for_trigger(kind)
    if not point:
        return []
    candidates = [r for r in (loop.reminders or []) if r.kind == kind]
    return dispatch.at(point, candidates, lambda r: r.matches(target),
                       payload=lambda r: payload_of(r.kind))
