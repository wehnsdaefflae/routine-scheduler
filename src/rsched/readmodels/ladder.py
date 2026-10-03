"""The escalation ladder's state for ONE run — the rail's ladder strip.

A rung's whole record is in the run's own transcript (`engine/ladder.py` writes four event
types), so this is a read over that file and writes nothing: the single-writer contract under
`runs/` is the engine's. It answers the three questions a reader of a supervised run has:

- **which rung is current** — how deep the oversight went, and whether one is running now;
- **how many turns until the next escalation** — the interval the ladder is counting down;
- **what the last rung ruled** — its verdict and disposition, or that it ruled nothing.

A skip is kept, not dropped: `oversight_skipped` is the one record that explains a run with the
ladder ON and no supervision in it, and the reason (no supervisor pattern, the tree's ceiling, a
spent budget) is the only thing that distinguishes "healthy, nothing to say" from "the mechanism
never ran". Returning None for a run with no ladder events is what lets the rail HIDE the card
instead of showing an empty one.
"""

from __future__ import annotations

from pathlib import Path

from ..engine.transcript import read_events

#: The events this read-model folds — `engine/ladder.py` is the only writer of all four.
DISPATCH = "oversight_dispatch"
DIRECTIVE = "oversight_directive"
SKIPPED = "oversight_skipped"
NO_DIRECTIVE = "oversight_no_directive"


def _int(value: object, default: int = 0) -> int:
    """A payload field as an int, or `default` — a transcript is data on disk, not a typed API,
    and a record written by an older version may carry anything or nothing.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def ladder_state(run_dir: Path, *, turn: int = 0) -> dict | None:
    """The ladder strip's facts for this run, or None if no rung ever fired or was skipped.

    `turn` is the run's current turn, used only to count the interval down; 0 leaves
    `turns_to_next` None, which is honest for a finished run — there is no next escalation.

    Memoized on the run's own transcript fingerprint, like the task tree: the rail polls this.
    """
    from . import memo

    path = run_dir / "transcript.jsonl"
    return memo.memoized(f"ladder:{run_dir}:{turn}", memo.transcript_paths(run_dir),
                         lambda: _ladder_state(path, turn))


def _ladder_state(path: Path, turn: int) -> dict | None:
    events, _ = read_events(path, 0)
    rungs = [ev for ev in events
             if ev.get("type") in (DISPATCH, DIRECTIVE, SKIPPED, NO_DIRECTIVE)]
    if not rungs:
        return None
    state: dict = {"rung": 0, "dispatched": 0, "skipped": 0, "verdict": "", "disposition": "",
                   "next_look": "", "turns_to_next": None, "last": "", "last_reason": ""}
    last_dispatch_turn = 0
    next_rung_in = 0
    for ev in rungs:
        p = ev.get("payload") or {}
        etype = ev.get("type")
        rung = _int(p.get("rung"))
        if rung:
            state["rung"] = max(state["rung"], rung)
        if etype == DISPATCH:
            state["dispatched"] += 1
            last_dispatch_turn = _int(p.get("turn"))
            state["last"] = "dispatched"
        elif etype == DIRECTIVE:
            state["verdict"] = str(p.get("verdict") or "")
            state["disposition"] = str(p.get("disposition") or "")
            state["next_look"] = str(p.get("next_look") or "")
            next_rung_in = _int(p.get("next_rung_in"))
            state["last"] = "ruled"
        elif etype == SKIPPED:
            state["skipped"] += 1
            state["last"] = "skipped"
            state["last_reason"] = str(p.get("reason") or "")
        else:                                    # NO_DIRECTIVE
            state["last"] = "silent"
            state["last_reason"] = str(p.get("status") or "")
    # The countdown is only meaningful while the run is live AND a rung has set an interval.
    # A directive's `next_rung_in` is counted from the dispatch that produced it, which is how
    # the engine counts it (`ctx.last_rung_turn`), so the strip and the trigger cannot disagree.
    if turn and next_rung_in and last_dispatch_turn:
        state["turns_to_next"] = max(0, last_dispatch_turn + next_rung_in - turn)
    return state


__all__ = ["DIRECTIVE", "DISPATCH", "NO_DIRECTIVE", "SKIPPED", "ladder_state"]
