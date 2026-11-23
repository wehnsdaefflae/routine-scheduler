"""What a backup ATTEMPT cost, read from the record `deploy/backup.sh` appends (D299).

The stamps `daemon/backup_watch` judges say a night started and did not finish. They cannot say
whether it missed by a minute or by three hours, nor how much it moved — and the remedy the
stale event used to print, the backup unit's journal, is unreachable from inside a run's sandbox
(no journalctl, no systemctl, no docker there). So `backup.sh` appends one JSON line per attempt
per tier under the routines home's `.control/`, which every run can already read, and this module
is the only reader of it.

Separate from `backup_watch` on purpose, and not because that file was full: the stamp
arithmetic answers *is the backup current?* from two timestamps on the share, while this answers
*what happened on the last attempt?* from a log on local disk. They fail independently — the
share can be unreadable while the log is fine, and vice versa — so each is read, tested and
changed on its own.

A row looks like:

    {"at": "2026-11-23T01:06:48+01:00", "tier": "offbox", "outcome": "failed",
     "elapsed_s": 14400, "note": "rsync exit 30"}

`transferred` carries rsync's own figure when the attempt completed. Nothing here raises: a
backup diagnosis must never be the thing that stops the scheduler's tick.
"""

from __future__ import annotations

import json
from pathlib import Path

#: The file `deploy/backup.sh` appends to, under the routines home's `.control/`.
ATTEMPTS_FILE = "backup-attempts.jsonl"


def attempts_path(routines_home: Path | str | None = None) -> Path:
    """Where the record lives. The seam a test replaces, like `backup_roots` next door."""
    home = Path(routines_home) if routines_home is not None else Path.home() / "routines"
    return home / ".control" / ATTEMPTS_FILE


def newest_attempt(tier: str, routines_home: Path | str | None = None) -> dict | None:
    """The last recorded attempt of `tier` ("local" / "offbox"), or None when there is none.

    Reads the tail backwards and keeps the first matching row. One night adds at most two lines,
    but a reader must not depend on that: a corrupt or half-written line is SKIPPED rather than
    raised, and an unreadable file is simply no record.
    """
    try:
        lines = attempts_path(routines_home).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw in reversed(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("tier") == tier:
            return row
    return None


def attempt_detail(tier_label: str, routines_home: Path | str | None = None) -> str:
    """One clause naming what the last attempt of this tier COST, for a stale verdict's detail.

    Empty when nothing is recorded, so a verdict composed before `backup.sh` has ever written a
    row reads exactly as it did before: the clause is an addition to the sentence, never a
    condition for producing one.
    """
    row = newest_attempt("offbox" if tier_label.startswith("off") else "local", routines_home)
    if row is None:
        return ""
    parts = [(f"the last recorded attempt was {row.get('outcome', 'unknown')} after "
              f"{row.get('elapsed_s', '?')}s")]
    moved = row.get("transferred")
    if moved:
        parts.append(f"moving {moved}")
    note = row.get("note")
    if note:
        parts.append(str(note))
    return ". " + ", ".join(parts) + f" ({ATTEMPTS_FILE})"
