"""Which `.memory/` notes the routine's runs actually READ — counted by the engine, per note.

A note is written once and then sits in every run's digest as an INDEX line whether anyone
consults it or not. D268 gave a note an expiry the WRITER foresaw (`stale_after`); nothing said
which notes no run has needed in months, so a notebook only grew. Hermes Agent's skill curator
reads the same signal (a skill unused for 30 days goes stale, then is archived); this module
counts it and lets the post-run review leg (engine/reviewleg.py) act on it.

THE RECORD is `.memory/.reads.json`, engine-owned like `INDEX.md` beside it (`.memory/` is sealed
from every generic write and the jail): the number of routine runs counted, and per note the run
number it was first seen at, the last run that read it and how many runs read it. A run counts
a note READ when its `memory_read` returned that note from the routine's own notebook — not a
job workspace's (`engine/jobops.py` lends a run its own), and not the INDEX. Updated once per
depth-0 routine run at the close-out, deterministically, before the autocommit commits it.

THE READING is `unread`: a note no run has read in the last `UNREAD_RUNS` counted runs, its age
counted from when it was first seen, so a note written last week is never "unread". It decides
nothing by itself. The review leg names those notes and may fold one into the note that covers
it; nothing here deletes, archives or demotes a note (`.memory/` is versioned by the routine's
repo, and absence of a read is not proof of no value — Hermes' own curator never prunes on use
counts alone).
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..paths import atomic_write_json, read_json

log = logging.getLogger(__name__)

#: The sidecar's file name inside `.memory/` (not a `*.md`, so never a note or an INDEX line).
SIDECAR = ".reads.json"

#: Counted runs without a read before a note reads as unread.
UNREAD_RUNS = 20


def noted(ctx, mem_dir: Path, name: str) -> None:
    """`memory_read` returned `name` from `mem_dir`: count it when that is the routine's own."""
    try:
        own = Path(mem_dir).resolve() == (Path(ctx.routine.dir) / ".memory").resolve()
    except OSError:
        return
    if own:
        ctx.notes_read.add(name)


def _load(mem_dir: Path) -> dict:
    data = read_json(mem_dir / SIDECAR, {})
    if not isinstance(data, dict) or not isinstance(data.get("notes"), dict):
        return {"runs": 0, "notes": {}}
    return {"runs": int(data.get("runs") or 0), "notes": dict(data["notes"])}


def _topics(mem_dir: Path) -> list[str]:
    return sorted(p.stem for p in mem_dir.glob("*.md") if p.name != "INDEX.md")


def record(ctx) -> None:
    """At the close-out of a depth-0 routine run: count this run and the notes it read."""
    mem_dir = Path(ctx.routine.dir) / ".memory"
    if not mem_dir.is_dir():
        return
    try:
        data = _load(mem_dir)
        run = data["runs"] + 1
        notes: dict = {}
        for name in _topics(mem_dir):
            row = data["notes"].get(name)
            row = dict(row) if isinstance(row, dict) else {"first_run": run, "reads": 0}
            if name in ctx.notes_read:
                row["last_read_run"] = run
                row["reads"] = int(row.get("reads") or 0) + 1
            notes[name] = row
        atomic_write_json(mem_dir / SIDECAR, {"runs": run, "notes": notes})
    except (OSError, ValueError, TypeError) as exc:   # a count must never cost the close-out
        log.warning("notereads: could not record %s: %s", mem_dir, exc)


def unread(mem_dir: Path, *, runs: int = UNREAD_RUNS) -> list[str]:
    """The notes no counted run has read in the last `runs` runs, oldest-read first."""
    data = _load(Path(mem_dir))
    now = data["runs"]
    stale: list[tuple[int, str]] = []
    for name, row in data["notes"].items():
        if not isinstance(row, dict) or not (Path(mem_dir) / f"{name}.md").is_file():
            continue
        since = int(row.get("last_read_run") or row.get("first_run") or now)
        if now - since >= runs:
            stale.append((since, name))
    return [name for _, name in sorted(stale)]
