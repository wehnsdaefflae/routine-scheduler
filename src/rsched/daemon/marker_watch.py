"""A migration deadline reaches a person before it reaches the gate. The one live reader of it.

The delete-after-convergence policy has had its enforcement for months (`tests/test_policy.py`)
and its 21-day lead time since F628 — and the lead time reached nobody. It was a pytest warning,
which a passing test folds into a summary xdist suppresses, plus a file beside the gate's own
evidence that no consumer reads. So the only EFFECTIVE signal a deadline ever gave was the
morning it turned every release gate on the host red, in front of whoever happened to ship that
day. Measured twice: `pending_edits.py`'s tombstone was found the day it expired and blocked
`main` itself (R2596), and the five settings-patterns markers of 2026-10-20 were found by hand at
an audit's orientation three days out, not by any signal (F702).

The warning needed a surface someone looks at without running a gate, and the instance already
has exactly one for "something due did not happen": the health stream, whose `BLOCKED_EVENTS` the
console renders without being recompiled (`readmodels/health_stream`). That is what this files —
the same remedy `backup_stale` is, for the same reason, and it reads the SAME declarations the
policy test does (`migration_markers`), so a surface can never claim a deadline the gate does
not enforce or miss one it does.

Two events, because they are two different facts and a reader acts differently on each:

- `migration_due_soon` — a declaration enters its window. Nothing is broken yet; this is the
  lead time, and it is deliberately filed while there is still room to act.
- `migration_expired` — the date has passed. Every release gate on this host is now red on files
  nobody touched, which is the condition that has twice been discovered by a blocked release.

Both carry `markers` (how many) and `soonest`/`expires` with the file, so the row says what to
delete rather than that something is wrong.

Both are also standing conditions, so the cadence is ONE row a day while the condition holds —
a deadline a month out would otherwise write a row every 5 s tick. It is latched HERE, on this
object, and deliberately not in `health_cooldown.REPEAT_COOLDOWN_HOURS`: nothing in
`BLOCKED_EVENTS` may be named in that table (a test pins it), because the console's blocked-fleet
fold COUNTS those rows and a suppressed one would make the fleet look less stuck than it is. The
day latch is `daemon/backup_watch`'s, for a condition of exactly the same shape.

That latch also decides the read cadence: the declarations are files in the installed tree, which
cannot change under a running daemon without a restart, and the window is measured in days — so
reading them once a day costs nothing and keeps the tick clean.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from ..config import ServerConfig
from ..health_events import log_health_event
from ..migration_markers import Declared, due_soon, expired

log = logging.getLogger("rsched.daemon")


def _names(found: list[Declared]) -> str:
    """The declarations as one readable list, newest deadline first — what to delete, by name."""
    return ", ".join(f"{d.path.name} ({d.expires})" for d in found)


def overdue_detail(found: list[Declared]) -> str:
    """What an expired declaration means, said once so the event and the log agree."""
    return (f"{len(found)} MIGRATION marker(s) are PAST their expiry: {_names(found)}. "
            "tests/test_policy.py fails on an expired marker, so every release gate on this "
            "host is red until the converged migration code is deleted — the "
            "delete-after-convergence policy's own prescription (CLAUDE.md)")


def soon_detail(soon: list[tuple[int, Declared]]) -> str:
    """What an approaching deadline means — with the days left, which is the actionable part."""
    listed = ", ".join(f"{d.path.name} in {left} day(s) ({d.expires})" for left, d in soon)
    return (f"{len(soon)} MIGRATION marker(s) expire soon: {listed}. On the day, every release "
            "gate on this host reds on these files — delete the converged migration code now, "
            "while it is still someone's choice rather than a blocked release")


class MarkerWatch:
    """The daemon's migration-deadline check: one health event per day while a deadline stands."""

    def __init__(self, server: ServerConfig) -> None:
        self.server = server
        self._checked: date | None = None

    async def tick(self) -> None:
        """Read the declared expiries once a day and file what is due. Never raises into the
        tick: a check about a deadline must not be the thing that stops the scheduler.
        """
        today = datetime.now(UTC).date()
        if self._checked == today:
            return
        self._checked = today
        try:
            overdue = expired(today)
            soon = due_soon(today)
        except Exception:
            log.exception("marker-watch: reading the declared migration expiries raised")
            return
        if overdue:
            detail = overdue_detail(overdue)
            log_health_event(self.server.routines_home, "migration_expired",
                             routine="", run_id="", detail=detail,
                             markers=len(overdue), expires=overdue[0].expires)
            log.warning("marker-watch: %s", detail)
        if soon:
            detail = soon_detail(soon)
            log_health_event(self.server.routines_home, "migration_due_soon",
                             routine="", run_id="", detail=detail,
                             markers=len(soon), soonest=soon[0][1].expires)
            log.info("marker-watch: %s", detail)
