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
(schedule, UI, `manage_lane run`, catch-up alike), never by the web layer, and safe to
delete — a missing entry reads as "no evidence of a miss" and is stamped at the next boot.

    <routines_home>/.control/lane-fires.json      {"<lane_id>": "<iso of the last arm>"}
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, read_json

FILE = "lane-fires.json"


def path(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / FILE


def load(routines_home: Path) -> dict[str, str]:
    data = read_json(path(routines_home))
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def stamp(routines_home: Path, lane_id: str, when: str | None = None) -> None:
    """Record that `lane_id`'s chain was armed now (or at `when`, ISO)."""
    data = load(routines_home)
    data[str(lane_id)] = when or now_iso()
    atomic_write_json(path(routines_home), data)


def last_armed(routines_home: Path, lane_id: str) -> datetime | None:
    """The last recorded arm as an aware datetime, or None when nothing was recorded."""
    raw = load(routines_home).get(str(lane_id))
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None
