"""The `restart` hook point's FIRE SITE — the only one outside a run (D152 step 9).

`hooks/points.py` has carried `restart` with an empty `fires` since the registry shipped in
0.458.0, and after steps 6-8 wired `child-exit`, `spawn` and `run-end` it was the last point
the engine passed through with nothing firing at it. This is that point.

## Why it cannot be an in-loop rung like the other seven

Every other point is asked by the turn loop, which holds the run's context, its assist set and
its fire guards (`engine/assist.py`). This one happens when the DAEMON goes down under a live
run: the engine is a separate process about to be killed, and the party that knows a restart is
happening is the one exiting. So the site hands each live run's own directory to the carrier
instead of reaching a loop — the same carrier `run-end` uses (`state/notes.md`, whose tail the
next state digest renders), because a line written here is for whichever leg reads that file
next: the automatic resume if the boot grants one, or the next scheduled run if it does not.

## Why the resume note does not already do this

`daemon/reap_resume.resume_after_restart` files an inbox message, and where it applies it is
the better channel — it reaches the resumed leg as a message rather than as a note. But it only
reaches a run the next boot actually RE-OPENS, and four things stop that: the resume cap is
spent, the routine is paused, the mark never established the cause, or the run is simply gone.
Those are exactly the cases where nothing records what happened, and they are the ones F746
measured: 79 orphans over 50 boots, 15 of the 20 that carried a cause at all reading `unknown`.

Best-effort throughout, like `restart.mark_graceful_exit` beside it: a diagnostic written on
the way out may never delay or fail the shutdown it is describing.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import assists as lib
from ..config.routine import load_routine
from ..engine import notes
from ..engine.assist import rendered_line
from ..engine.assist_predicates import PREDICATES, Situation
from ..hooks import dispatch
from ..paths import read_json

log = logging.getLogger("rsched.daemon.restart_hook")

#: The source the stamp names, as `engine/assist.at_run_end` names its own. A reader of
#: `state/notes.md` must be able to tell a hook's line from the run's own, and WHICH hook.
SOURCE = "hook restart"


@dataclass(frozen=True)
class _Dir:
    """`notes.append_line` resolves the file under `ctx.routine.dir` (engine/run_context)."""

    dir: Path


@dataclass(frozen=True)
class _Target:
    """The four fields the note carrier reads, and nothing else.

    Declared rather than improvised so this site cannot drift from `notes.append_line`'s
    contract: it stamps `run_ts`, `turn` and `phase`, and resolves the path through
    `work_dir`, which takes an open task's workspace when there is one. A run the daemon is
    killing has no task state to read from outside, so `job_dir` is None by construction and
    the line lands in the routine's own `state/notes.md`.
    """

    routine: _Dir
    run_ts: str
    turn: int
    phase: str = "restart"
    job_dir: None = None


def _fires(assist: lib.Assist, situation: Situation) -> bool:
    """This assist's predicate against the situation. Unknown predicate = does not fire.

    `engine/assist._fire` is the in-loop twin and keeps two things this cannot: the
    once-per-run guard (there is no run left to guard — this fires once, at the exit) and the
    membership set a resumed leg rebuilds. What both share is routed through `hooks/dispatch`,
    which is also what swallows a raising predicate.
    """
    predicate = PREDICATES.get(assist.predicate)
    return bool(predicate and predicate.check(situation))


def _for_run(server: Any, run: Any, reason: str) -> int:
    """Fire the point for ONE live run; the number of lines written."""
    run_dir = Path(run.run_dir)
    status = read_json(run_dir / "status.json")
    state = status.get("state", "unknown") if isinstance(status, dict) else "unknown"
    turn = int(status.get("turn") or 0) if isinstance(status, dict) else 0
    cfg, _ = load_routine(run_dir.parent.parent)
    if cfg is None:
        return 0
    candidates = [a for a in lib.for_rules(server.rules_home, list(cfg.rules or []))
                  if a.moment == "restart"]
    if not candidates:
        return 0
    situation = Situation(loop=None, restart={"state": state, "reason": reason,
                                              "run_id": getattr(run, "run_id", "")})
    target = _Target(routine=_Dir(dir=cfg.dir), run_ts=str(run.run_ts), turn=turn)
    fired = dispatch.at("restart", candidates, lambda a: _fires(a, situation),
                        payload=lambda a: a.payload)
    for assist in fired:
        notes.append_line(target, rendered_line(assist), source=SOURCE)
        lib.record_fire(cfg.dir, assist)
    return len(fired)


def at_daemon_exit(server: Any, runs: Iterable[Any], reason: str) -> int:
    """Fire the `restart` point for every run still live at the exit; lines written.

    `runs` are the daemon's own active runs (`runner.active.values()`) rather than a scan of
    disk: the runner is what knows which engines this process is about to take down with it,
    and a status file can lag by a turn. A run parked on a question is among them and the
    predicate declines it — nothing is being cut there.
    """
    written = 0
    for run in list(runs):
        try:
            written += _for_run(server, run, reason)
        except Exception as exc:        # see the module docstring: a diagnostic written on the
            log.warning("restart hook skipped %s: %s",      # way out may never fail or delay
                        getattr(run, "run_id", "?"), exc)   # the shutdown it describes

    if written:
        log.warning("restart hook: %d note line(s) written for the next leg", written)
    return written
