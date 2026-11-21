"""The RESTART predicate — what a rule may ask as the DAEMON goes down under a live run.

The hook point `restart` (`hooks/points.py`) is the only point fired from OUTSIDE a run: the
fire site hands each live run's routine dir to the carrier (`daemon/restart_hook.py`). Its
carrier is `prompt_section`, like `run-end`: nothing can reach THIS leg, so what fires reaches
`state/notes.md`, which the NEXT leg's state digest renders.

Not a duplicate of `daemon/reap_resume.resume_after_restart`'s inbox note: that reaches only a
run the next boot RE-OPENS, and a refused resume (cap spent, routine paused, run gone) gets
nothing — while the daemon on its way out is the only party that still knows the cause.
"""

from __future__ import annotations

from typing import Any

#: Run states that were NOT mid-work when the daemon went down (cf. `daemon/restart.PARKED`):
#: a parked run is waiting on a person, so its leg is not being cut.
PARKED = ("waiting_user", "paused", "queued")


def work_cut_by_daemon_exit(s: Any) -> bool:
    """The daemon is exiting while this run was mid-work, so its turn is being cut.

    Without this the next leg reads a transcript that simply stops; the cause is knowable here
    and nowhere later (F746).

    False for a PARKED run (it was not working), and False when the situation carries no
    restart, so the predicate declared at a wrong moment is a lint error, not a mystery fire.
    """
    restart = getattr(s, "restart", None)
    if not isinstance(restart, dict):
        return False
    return str(restart.get("state") or "") not in PARKED
