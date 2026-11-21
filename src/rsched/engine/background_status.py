"""What is RUNNING in the background, as `subruns` rows (R3600).

A child run and a backgrounded call are different mechanisms (a process with its own budget vs.
a thread here) but the same THING to a run reading its own state: work it started whose result
arrives at a later turn boundary. So `subruns` reports both — `kill` already takes both.

The two namespaces stay separate: these rows go under their own `background` key, so `count`
keeps meaning children and nobody aims `kill n=1` at a `bg1`.

`state` distinguishes `running` from `landed`: a call that finished mid-turn stays in the live
list until `collect` delivers it at the next turn boundary.
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
