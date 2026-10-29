"""The RUN-TREE predicates — what a rule may ask about a child run this run started.

Its own module for the reason `assist_outcome.py` is: `assist_predicates.py` is the registry
and is AT the size bar, so a predicate that needs more than its registry entry lives beside it
and is imported in. The registry stays the one table of names.

Both ENDS of a child's life are here, because they are one subject and share one Situation
field: the hook points `spawn` and `child-exit` (`hooks/points.py`), fired at the turn boundary
by `engine/control.py` — `announce_started_subruns` for the first, `announce_finished_subruns`
for the second. They were two of the four points the registry carried with an empty `fires`
since 0.458.0, and the operator's brief names exactly this traffic: *"Messages between runs,
their children, and the user should implement hooks"*.

What a predicate here may look at is the child's own ROW as the parent is shown it — at the
start, the number, label, workflow and the brief it was given; at the exit, the mode, status,
turn count and summary — and never the child's transcript: the parent's prompt carries those
rows, so a check that read more than the parent can see would fire on evidence the run cannot
act on.
"""

from __future__ import annotations

from typing import Any

#: The one status that means a child did what it was asked. `partial` names a blocker it could
#: not clear and `failed` that the job could not be done — both are hand-backs whose summary a
#: parent must read before folding it into anything, which is the line the rule carries.
OK = "ok"

#: Below this many characters a brief cannot carry what a child needs. A child shares NONE of
#: the parent's context, so its instruction has to name the task, the paths and the hand-back
#: by itself. A floor, not a target — a long brief is not thereby a good one, which is why the
#: rule's line asks about its CONTENT.
THIN_BRIEF = 400

#: What a brief must name to be actionable at all: somewhere to read or something to produce.
#: A brief naming neither describes work whose subject exists only in the parent's context.
_PATH_MARKS = ("/", "artifacts", "http")


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


def spawned_a_thin_brief(s: Any) -> bool:
    """A child was just started on a brief too thin to stand on its own.

    evidence-discipline's OTHER moment on the run tree, and the one still fixable: a child
    cannot be re-briefed, only killed and started again, so the moment to read a brief back is
    the boundary right after it was sent. A child has its own directory, its own budget and a
    FRESH context — it sees nothing of this conversation but the prompt it was given, so a
    brief gesturing at "the file we were looking at" or "the approach above" describes work the
    child cannot find. The parent then reads a confident hand-back about something else: the
    same claim-without-evidence failure the rule exists for, one level down, and the most
    expensive kind, because a whole child's budget is spent before anyone can see it.

    Two ways to be thin, both measured on the brief ALONE: too short to carry a self-contained
    task, or long enough while naming nothing the child can open. False when no start row is
    present, so the predicate is inert at every other moment.
    """
    row = s.child
    if not isinstance(row, dict) or row.get("status") is not None:
        return False       # an EXIT row, not a start row — that is the other predicate's
    brief = str(row.get("brief") or "")
    if not brief.strip():
        return False       # nothing to judge; a start with no brief of its own is not this
    return len(brief) < THIN_BRIEF or not any(m in brief for m in _PATH_MARKS)
