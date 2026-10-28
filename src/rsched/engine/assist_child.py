"""The CHILD-EXIT predicates — what a rule may ask about a child run that just finished.

Its own module for the reason `assist_outcome.py` is: `assist_predicates.py` is the registry
and was four lines under the size bar, so a predicate that needs more than its registry entry
lives beside it and is imported in. The registry stays the one table of names.

The moment this serves is the hook point `child-exit` (`hooks/points.py`), fired by
`engine/control.announce_finished_subruns` — the turn boundary at which a parent is told a
child it started has exited and where its hand-back landed. It is one of the four points the
registry carried with an empty `fires` since 0.458.0, and the operator's brief names exactly
this traffic: *"Messages between runs, their children, and the user should implement hooks"*.

What a predicate here may look at is the child's own hand-back ROW — its mode, label, status,
turn count and summary — and never the child's transcript: the parent's prompt carries the
hand-back, so a check that read more than the parent can see would fire on evidence the run
cannot act on.
"""

from __future__ import annotations

from typing import Any

#: The one status that means a child did what it was asked. `partial` names a blocker it could
#: not clear and `failed` that the job could not be done — both are hand-backs whose summary a
#: parent must read before folding it into anything, which is the line the rule carries.
OK = "ok"


def child_fell_short(s: Any) -> bool:
    """A child run this parent started has exited WITHOUT finishing `ok`.

    evidence-discipline's moment on the run tree. A child hands back a summary and nothing
    else: the parent reads that text, not the child's work, so a `partial` or `failed` child
    is the one case where folding the summary onward would carry a claim the child itself
    declined to make. The engine already distinguishes the three statuses at the child's own
    finish; this only asks whether the one that just landed is the clean one.

    False when no child row is present, so the predicate is inert at every other moment —
    a rule that declares it at the wrong moment is a lint error, not a mystery fire.
    """
    child = s.child
    if not isinstance(child, dict):
        return False
    status = str(child.get("status") or "").strip().lower()
    return bool(status) and status != OK
