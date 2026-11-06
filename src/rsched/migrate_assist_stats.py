"""One-shot boot migration: every routine's assist tally grows from an INT per key to the stat
fields a reminder's tally keeps — MIGRATION(expires=2026-12-31).

`state/assists.json` counted fires as a bare int per assist key. D239 wires the four-way
outcome label to the `hold` rung, and the label needs somewhere to go: the tally now carries
`fires` plus `could_not` / `would_have` / `did` / `didnt` (`rsched/assist_stats.py`). The int
IS the fire count, so it is kept as `fires` and nothing is lost — the live files hold real
evidence (seven keys and 114 fires in one routine alone), which is exactly the history a
precision review reads.

Without the conversion nothing breaks — `assist_stats.normalize` reads a legacy int as
`{"fires": n}` — but every routine's file would stay in the old shape until its next fire, so
the console's four new columns would read as zeros for keys that have fired hundreds of times
and never been labelled. One pass at boot makes the whole fleet answer in one vocabulary.

Runs at daemon boot before the app starts; records what it did in
`.control/migrations/assist-stats.json`; while that exists it does nothing. Every server
attribute is read with `getattr`: `cmd_daemon` is handed a four-attribute stand-in by the
fresh-container boot test, and a hard attribute read in any migration kills the WHOLE boot
(R3132).
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import assist_stats
from .assists import state_path
from .ids import now_iso
from .paths import atomic_write_json, read_json

log = logging.getLogger("rsched.migrate")

RECORD = Path(".control") / "migrations" / "assist-stats.json"


def _is_legacy(raw: object) -> bool:
    """Does this file still hold the old shape? One value that is not a stat mapping is
    enough — a file half-converted by an interrupted run is finished rather than skipped.
    """
    return isinstance(raw, dict) and any(not isinstance(v, dict) for v in raw.values())


def run_migration(server) -> dict:
    """Convert every routine's tally once. Returns the record (empty when it already ran)."""
    home = Path(str(getattr(server, "routines_home", "") or "."))
    if not home.is_dir():
        return {}
    record_path = home / RECORD
    if record_path.exists():
        return {}
    converted, keys = 0, 0
    for routine_dir in sorted(p for p in home.iterdir() if p.is_dir()):
        raw = read_json(state_path(routine_dir), None)
        if not _is_legacy(raw):
            continue
        tallies = assist_stats.read(routine_dir)
        assist_stats.write(routine_dir, tallies)
        converted += 1
        keys += len(tallies)
    record = {"started": now_iso(), "converted": converted, "keys": keys}
    try:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(record_path, record)
    except (OSError, ValueError):
        return record        # unwritable control dir: retried at the next boot, never fatal
    if converted:
        log.warning("assist-stats: %d tally file(s), %d key(s) grown to the label fields",
                    converted, keys)
    return record
