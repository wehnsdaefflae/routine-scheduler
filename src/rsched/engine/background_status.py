"""What is RUNNING in the background, as `subruns` rows (R3600).

The operator caught this mid-run in `tv-show-tracker-seedbox-manager`: a `background: true`
script was in flight as `bg1`, the run asked `subruns` to see what it was waiting for, and the
answer was *"no record of a child in THIS leg — and none on disk either, so none was started"*.
Work the run had started was running, and the one action whose job is to say what is running
denied it existed.

**Why the two belong in one table.** `spawn`/`subtask` and a backgrounded call are different
mechanisms — a child run is a process with its own budget, a background call is a thread inside
this interpreter — but they are the same THING to a run reading its own state: work it started,
not yet in hand, whose result arrives at a later turn boundary tagged with an identifier. `kill`
already took both namespaces (`n` for a child, `handle` for a background call) for exactly that
reason; the status table was the only surface that knew one and not the other.

**Two namespaces stay two namespaces**, so these rows are a separate `background` key rather
than more `rows`: a row with an `n` is a sub-workflow and a row with a `handle` is a thread, and
`subruns`' own count has always meant children. Folding them into one list would make `count`
mean two things and invite `kill n=1` at a `bg1`.

`state` distinguishes `running` from `landed` because a call that FINISHED mid-turn is still in
the live list — `collect` drops a Pending only at the next turn boundary, when it delivers it.
Reporting that as `running` would replace one false claim with another.
"""

from __future__ import annotations

from typing import Any


def status_rows(loop) -> list[dict[str, Any]]:
    """One row per background call still in the live list, oldest handle first."""
    from .background import _pendings

    return [{"handle": p.handle, "kind": p.kind, "brief": p.brief, "started_turn": p.turn,
             "state": "landed" if p.done else "running", "stoppable": p.stoppable}
            for p in _pendings(loop)]


def lines(rows: list[dict[str, Any]]) -> list[str]:
    """The rows as the run reads them, under the children (prompt surface, like its sibling)."""
    return [f"- {r['handle']} [{r['kind']}] {r['state']}"
            + (f" · started turn {r['started_turn']}" if r.get("started_turn") else "")
            + (f" · {r['brief']}" if r.get("brief") else "")
            + ("" if r.get("stoppable") else " · kill stops only its delivery")
            for r in rows]
