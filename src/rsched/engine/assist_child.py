"""The RUN-TREE predicates — what a rule may ask about a child run this run started.

Beside the registry (`assist_predicates.py`, at the size bar), which stays the one table of names.

Both ENDS of a child's life are here, sharing one Situation field: the hook points `spawn` and
`child-exit` (`hooks/points.py`), fired at the turn boundary by `engine/control.py`
(`announce_started_subruns` / `announce_finished_subruns`).

A predicate here may look only at the child's ROW as the parent is shown it (start: number,
label, workflow, brief; exit: mode, status, turns, summary) — never the child's transcript, or it
would fire on evidence the run cannot see or act on.
"""

from __future__ import annotations

from typing import Any

#: The one status that means a child did what it was asked; a `partial` or `failed` summary must
#: be read before the parent folds it into anything.
OK = "ok"

#: Below this many characters a brief cannot carry what a child needs: it shares NONE of the
#: parent's context. A floor, not a target — the rule's line asks about CONTENT.
THIN_BRIEF = 400

#: What a brief must name to be actionable at all: somewhere to read or something to produce.
#: A brief naming neither describes work whose subject exists only in the parent's context.
_PATH_MARKS = ("/", "artifacts", "http")


def child_fell_short(s: Any) -> bool:
    """A child run this parent started has exited WITHOUT finishing `ok`.

    evidence-discipline's moment on the run tree: the parent reads only the summary, so folding
    a not-`ok` child's summary onward would carry a claim the child itself declined to make.
    False when no child row is present, so the predicate is inert at every other moment.
    """
    child = s.child
    if not isinstance(child, dict):
        return False
    status = str(child.get("status") or "").strip().lower()
    return bool(status) and status != OK


def spawned_a_thin_brief(s: Any) -> bool:
    """A child was just started on a brief too thin to stand on its own.

    evidence-discipline's OTHER moment on the run tree, and the one still fixable: a child
    cannot be re-briefed, only killed and restarted, and it sees nothing but its brief — so a
    brief pointing at "the file above" describes work the child cannot find.

    Two ways to be thin, both measured on the brief ALONE: too short to be self-contained, or
    naming nothing the child can open. False when no start row is present.
    """
    row = s.child
    if not isinstance(row, dict) or row.get("status") is not None:
        return False       # an EXIT row, not a start row — that is the other predicate's
    brief = str(row.get("brief") or "")
    if not brief.strip():
        return False       # nothing to judge; a start with no brief of its own is not this
    return len(brief) < THIN_BRIEF or not any(m in brief for m in _PATH_MARKS)
