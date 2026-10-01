"""Per-item isolation for the daemon managers' ticks.

The trigger, one-shot and lane-run managers each serviced every item of a pass inside ONE
try/except, so a single item that raised on every pass — an unreadable spool entry, a
hand-edited lane record, a routine whose config no longer parses — ended the pass before the
items after it were reached, every five seconds, for as long as it stayed broken: one bad
routine silenced every routine that sorted after it. The detached manager had already learned
this (a per-task failure is logged and skipped); this is the same rule for the other three.

A failing item is logged on every pass (the daemon log is where the traceback lives) but
filed in the health stream ONCE until it next succeeds — as `scheduler_tick_error`, the event
that already means "a scheduler pass raised, whatever it owed is late", with the item named —
because a five-second tick would otherwise write seventeen thousand rows a day about one file.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..health_events import log_health_event

log = logging.getLogger("rsched.daemon")


class ItemGuard:
    """One manager's guard: `with guard.item(key): await …` contains that item's failure."""

    def __init__(self, routines_home: Path, manager: str) -> None:
        self.routines_home = routines_home
        self.manager = manager
        self._failing: set[str] = set()

    @contextmanager
    def item(self, key: str) -> Iterator[None]:
        try:
            yield
        except Exception:
            log.exception("%s: %s failed — the rest of the pass continues", self.manager, key)
            if key not in self._failing:
                self._failing.add(key)
                log_health_event(self.routines_home, "scheduler_tick_error", routine=key,
                                 run_id="", detail=f"{self.manager}: servicing {key} raised "
                                                   "on a tick; see the daemon log")
        else:
            self._failing.discard(key)
