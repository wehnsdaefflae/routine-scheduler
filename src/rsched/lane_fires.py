"""Lane fire WATERMARKS — when each scheduled lane's chain was last ARMED, on disk.

The scheduler's lane fire table (`lane_next_fires`) is process memory: at boot it is
recomputed as the NEXT future fire, so a fire that came due while the daemon was down,
restarting or draining (a nightly self-audit release restarts it; a recreate or a host
reboot stops it for minutes) simply never happens — and, D71 having suppressed every
member's own cron, nothing else fires those routines either. A Tue/Thu lane lost both its
fires in one week that way, and the only trace was two operator messages sitting unread
in a member's inbox. This store is what boot catch-up (`daemon/lane_catchup.py`) compares
the last DUE fire against.

Daemon-owned DERIVED state, like the model-limits cache: written by every `lane_runs.arm`
(schedule, UI, `manage_lane run`, catch-up alike) and by the scheduler when the operator's
global pause skips a due fire on purpose — every path that HANDLES a fire, so what is left
unstamped is a fire nobody was there for. Never written by the web layer, and safe to
delete — a missing entry reads as "no evidence of a miss" and is stamped at the next boot.

    <routines_home>/.control/lane-fires.json      {"<lane_id>": "<iso of the last arm>"}
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, file_lock, read_json

FILE = "lane-fires.json"


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


def stamp(routines_home: Path, lane_id: str, when: str | None = None) -> None:
    """Record that `lane_id`'s chain was armed now (or at `when`, ISO)."""
    _set(routines_home, str(lane_id), when or now_iso())


def _set(routines_home: Path, key: str, value: str | None) -> None:
    """Write one entry (or drop it, for None) — the file's one writer.

    LOCKED, because one file holds every lane's watermark and is rewritten whole, and three
    contexts write it: the scheduler on the daemon's loop thread, the web layer's sync
    handlers on FastAPI's threadpool, and a root conversation's ENGINE PROCESS through
    `manage_lane`. Unlocked, two overlapping stamps each read the file and each write their
    own version, and one is simply lost — which is precisely the precondition for a spurious
    make-up chain at the next boot. flock is the one mechanism all three share.
    """
    with file_lock(lock_path(routines_home)):
        data = load(routines_home)
        if value is not None:
            data[key] = value
        elif data.pop(key, None) is None:
            return                                   # nothing to drop, nothing to rewrite
        atomic_write_json(path(routines_home), data)


def _instant(raw: str | None) -> datetime | None:
    """A stored stamp as an AWARE datetime, or None when it is absent, unreadable or naive.

    Every writer stamps an aware instant. A naive one — a hand edit of a file documented as
    safe to delete — came back naive, and boot catch-up's comparison with an aware due fire
    raised TypeError mid-loop, losing the make-up of every lane after it. It reads like a
    missing entry instead: no evidence of a miss, stamped afresh at the next boot.
    """
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


#: Key prefix for the SECOND thing this file remembers: the fire a global pause skipped and
#: therefore owes (D156). It rides the watermark file rather than a store of its own because the
#: two are written in the same breath by the same skip and must not be able to disagree — a
#: watermark that moved with no record of what moved it is exactly the state F573 measured.
_SKIP_PREFIX = "paused-skip:"


def stamp_paused_skip(routines_home: Path, lane_id: str, when: str | None = None) -> None:
    """Record that a GLOBAL PAUSE skipped `lane_id`'s due fire (and when it was due).

    The watermark says the fire was handled, which is what keeps the next boot from making it
    up. This says it was handled by being DROPPED, which is what lets `resume_catchup` make it
    up when the pause is lifted — the operator's D156 choice C. One entry per lane: a pause that
    swallows two fires of one lane owes the lane one chain, not two.
    """
    _set(routines_home, _SKIP_PREFIX + str(lane_id), when or now_iso())


def last_paused_skip(routines_home: Path, lane_id: str) -> datetime | None:
    """When a global pause last dropped this lane's due fire, or None when it owes nothing."""
    return _instant(load(routines_home).get(_SKIP_PREFIX + str(lane_id)))


def clear_paused_skip(routines_home: Path, lane_id: str) -> None:
    """Forget the owed fire — called once the resume has DECIDED about it, made up or not.

    Cleared on a decline as well as on an arm: an owed fire that survives the resume that
    declined it would be made up by the next resume, at a time nobody skipped anything.
    """
    _set(routines_home, _SKIP_PREFIX + str(lane_id), None)


def last_armed(routines_home: Path, lane_id: str) -> datetime | None:
    """The last recorded arm as an aware datetime, or None when nothing was recorded."""
    return _instant(load(routines_home).get(str(lane_id)))
