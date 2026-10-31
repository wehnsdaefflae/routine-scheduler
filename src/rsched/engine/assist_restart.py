"""The RESTART predicate — what a rule may ask as the DAEMON goes down under a live run.

Its own module for the reason `assist_runend.py` and `assist_child.py` are:
`assist_predicates.py` is the registry and is AT the size bar, so a predicate that needs more
than its registry entry lives beside it and is imported in.

This is the hook point `restart` (`hooks/points.py`) — the LAST point the engine passed through
with nothing firing at it. It is the only point fired from OUTSIDE a run: there is no turn loop
at the app lifespan's shutdown half, so the fire site hands each live run's routine dir to the
carrier rather than reaching a loop (`daemon/restart_hook.py`).

Its carrier is `prompt_section`, like `run-end`: the model is mid-turn and about to stop
existing, so nothing can reach THIS leg. What fires here reaches the routine's `state/notes.md`
— the channel the NEXT leg's state digest renders, whether that leg is an automatic resume or
the next scheduled run.

## Why it is not a duplicate of the resume note

`daemon/reap_resume.resume_after_restart` already files an inbox message, and it is the better
channel where it applies — but it only reaches a run the next boot actually RE-OPENS. A leg
whose resume is refused gets nothing: the resume cap is spent (`RESTART_RESUMES`), the routine
is paused, the run is gone from disk, or the mark never established the cause at all. Those are
exactly the cases in which a run's own record is the only place the cause could have been
written, and the daemon on its way out is the only party that still knows it.
"""

from __future__ import annotations

from typing import Any

#: Run states that were NOT mid-work when the daemon went down. `daemon/restart.PARKED` draws
#: the same line for the opposite question (whether a self-update restart may fire at all), and
#: for the same reason: a parked run is waiting on a person, so its leg is not being cut.
PARKED = ("waiting_user", "paused", "queued")


def work_cut_by_daemon_exit(s: Any) -> bool:
    """The daemon is exiting while this run was mid-work, so its turn is being cut.

    evidence-discipline's moment at the other end of the interruption. What the next leg reads
    without this is a transcript that simply stops: no finish, no summary, and — once the boot
    reap has had its say — an `orphaned_run` row whose cause is whatever could be inferred
    afterwards. Measured 2026-10-22 (F746/R2793/R2800): 79 orphans over 50 boots, 15 of the 20
    carrying a cause at all read `unknown`, and NOT ONE ever read `daemon_restart`. The cause
    is knowable here and nowhere later, because the exit is happening now.

    False for a PARKED run: it was waiting on a person, not working, and telling its next leg
    that work was cut would be a claim about something that did not happen. False when the
    situation carries no restart at all, so the same predicate declared at a wrong moment is a
    lint error rather than a mystery fire.
    """
    restart = getattr(s, "restart", None)
    if not isinstance(restart, dict):
        return False
    return str(restart.get("state") or "") not in PARKED
