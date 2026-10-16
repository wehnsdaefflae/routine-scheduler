"""The fires a GLOBAL PAUSE dropped for a ROUTINE and therefore owes — on disk (D183).

The lane analog is `lane_fires`, which keeps two things: when each lane's chain was last ARMED
(its watermark) and which fire a pause skipped (what it owes). A routine needs only the second
half: its own runs ARE its watermark — `registry.missed_fire` compares the last due fire against
the last run's start — so nothing here records an ordinary fire.

What it records is the one state no run directory can show: a fire that was DUE and deliberately
dropped, which is why `scheduler-builder` lost four consecutive fires without a trace a reader
could reach. The skip event (`fire_paused`) says it happened; this says it is still OWED, and
that is what lets the resume and the next boot make it up at most once.

    <routines_home>/.control/routine-fires.json   {"paused-skip:<slug>": "<iso of the due fire>"}

Daemon-owned derived state, like `lane_fires`: written by the scheduler when the operator's
pause skips a due fire, cleared by whichever catch-up DECIDES about it (made up or declined).
Safe to delete — a missing entry reads as "nothing owed", which is also the state of a fresh
instance.

One entry per routine, on purpose: a pause that swallows two of a routine's fires owes it ONE
run, not two. The key is the oldest unresolved skip, because the cost being measured is how
late the work is, and the first dropped fire is the earliest the work was owed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, file_lock, read_json

FILE = "routine-fires.json"

#: Key prefix, so this file can grow a second kind of stamp the way `lane_fires` did without
#: a slug ever being mistaken for one.
_SKIP_PREFIX = "paused-skip:"


def path(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / FILE


def lock_path(routines_home: Path) -> Path:
    """The flock file guarding the whole-file rewrite below."""
    return path(routines_home).with_suffix(".lock")


def load(routines_home: Path) -> dict[str, str]:
    data = read_json(path(routines_home))
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def _set(routines_home: Path, key: str, value: str | None) -> None:
    """Write one entry (or drop it, for None) — the file's one writer.

    LOCKED for the reason `lane_fires._set` is: one file holds every routine's owed fire and is
    rewritten whole, while the scheduler's loop thread and the web layer's resume handler both
    write it. Unlocked, two overlapping writes each read and each rewrite, one is lost, and a
    lost CLEAR is a make-up fired twice.
    """
    with file_lock(lock_path(routines_home)):
        data = load(routines_home)
        if value is not None:
            data[key] = value
        elif data.pop(key, None) is None:
            return                                   # nothing to drop, nothing to rewrite
        atomic_write_json(path(routines_home), data)


def _instant(raw: str | None) -> datetime | None:
    """A stored stamp as an AWARE datetime, or None when absent, unreadable or naive.

    Naive reads as absent for `lane_fires._instant`'s measured reason: a hand edit of a file
    documented as safe to delete came back naive and the comparison with an aware due fire
    raised mid-loop, losing the make-up of everything after it in the pass.
    """
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


def stamp_paused_skip(routines_home: Path, slug: str, when: str | None = None) -> None:
    """Record that a global pause skipped `slug`'s due fire (and when it was due).

    Keeps the OLDEST unresolved skip: a second dropped fire does not overwrite the first,
    because the make-up stands in for the work that has been waiting longest and the ratio
    that decides it is measured from there.
    """
    key = _SKIP_PREFIX + str(slug)
    if load(routines_home).get(key):
        return
    _set(routines_home, key, when or now_iso())


def last_paused_skip(routines_home: Path, slug: str) -> datetime | None:
    """The fire a global pause dropped for this routine, or None when it owes nothing."""
    return _instant(load(routines_home).get(_SKIP_PREFIX + str(slug)))


def clear_paused_skip(routines_home: Path, slug: str) -> None:
    """Forget the owed fire — once a catch-up has DECIDED about it, made up or declined.

    Cleared on a decline as well as on a make-up, for `lane_fires.clear_paused_skip`'s reason:
    an owed fire that survives the resume that declined it would be made up by the next resume,
    at a time nobody skipped anything.
    """
    _set(routines_home, _SKIP_PREFIX + str(slug), None)


def owed(routines_home: Path) -> dict[str, datetime]:
    """Every routine with an owed fire → the instant it was due. The catch-up's one read."""
    out: dict[str, datetime] = {}
    for key, raw in load(routines_home).items():
        if key.startswith(_SKIP_PREFIX) and (when := _instant(raw)) is not None:
            out[key[len(_SKIP_PREFIX):]] = when
    return out
