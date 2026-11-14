"""One-shot boot migration: pre-mirror reminder DELETIONS gain the row that records them —
MIGRATION(expires=2027-02-28).

D152 step 4 (goal b4) ends with the reminder store SERVED from the hook stream. The property
that has to hold first is the one `hooks/reminderproj.drift` measures: fold the stream, read the
store, and every definition agrees. Where it stands, measured over all 31 live routines:

  before 0.542.0   122 drifting ids in 21 routines — 95 store-only, 23 stream-only, 4 differs
  after  0.542.0    27 drifting ids in  9 routines —  0 store-only, 23 stream-only, 4 differs

The `store-only` class is EMPTY: that was the dangerous one (a stream-served read would have
lost 95 live patterns) and 0.542.0's backfill closed it on the real instance. This module takes
the next class.

WHAT THE 23 ARE. Reminders a routine DELETED before `remind_write.remove` learned to mirror the
deletion (also 0.542.0). The store dropped the definition; the stream never heard, so it still
implies one. `migrate_premirror_reminders` called them "correct history" and left them — right
about the history, wrong about the consequence: the campaign's end state is a stream-served
store, and a fold that cannot see those deletions would **re-raise 23 patterns their own
routines removed**. A reminder decides whether an action is HELD, so that is a change to what
binds a run, not a stale row.

WHY THIS IS NOT A DELETION FROM THE STREAM. An append-only stream must never lose a row. The fix
is the row the write path would have written: one `disposition: remove` hook per id, which
`reminderproj.REMOVING` already subtracts. Both facts end up in the stream in the order they
happened — the definition was written, then it was removed.

WHAT IS LEFT ALONE, and named in the record rather than silently skipped: the 4 `differs` ids.
Three are missing `created_run` (the mirror era dropped it, as `hooks/store.record_reminder`'s
docstring admits); one disagrees on `owner` — `rem-20261026-233255-1`, `vodafone-outage-claims`
on the stream against `self-audit` in the store. `owner` decides who may REMOVE a hook, so a
migration may not settle it by guessing: it is the operator's call, and the record carries it.

WHY A ROUTINE'S OWN DELETION AND NOT A CURATOR'S. The row is addressed exactly as the write it
undoes: `self` for a hook the routine holds for its own runs, the holder for one a sibling set
there — `record_reminder`'s `target` argument. Recording a foreign hook as a self-hook would
make the holder unable to see it and the owner able to remove what it never set.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .hooks import reminderproj
from .hooks import store as hookstore
from .ids import now_iso
from .paths import atomic_write_json

log = logging.getLogger("rsched.migrate")

RECORD = Path(".control") / "migrations" / "premirror-deletions.json"


def routine_dirs(home: Path) -> list[Path]:
    """Every installed routine, by the one marker that makes a directory one. `enabled` is
    deliberately not consulted: a disabled routine's store is still compared by the comparator
    this exists to empty.
    """
    return sorted(d for d in home.iterdir() if (d / "routine.yaml").is_file())


def missing_removals(home: Path, routine_dir: Path) -> list[dict]:
    """The DEFINITIONS the stream still implies for this routine that its store has dropped.

    Pure and read-only, so the live-instance property is measurable in a test with no write and
    no daemon boot — the shape `migrate_mirror_hooks.repaired_records` established and
    `migrate_premirror_reminders.missing_hooks` followed.

    Only the `stream-only` class. A `store-only` id is 0.542.0's backfill (and is empty now); a
    `differs` id is a disagreement about CONTENT, which a disposition row cannot settle.
    """
    out = []
    for rid, (from_stream, in_store) in reminderproj.drift(home, routine_dir).items():
        if from_stream and not in_store:
            out.append({"id": rid, **from_stream})
    return sorted(out, key=lambda r: str(r["id"]))


def run_migration(server) -> dict:
    """Record the missing deletion once, for every installed routine. Returns the record (empty
    when it already ran).
    """
    home = Path(server.routines_home)
    record_path = home / RECORD
    if record_path.exists():
        return {}
    removed: dict[str, list[str]] = {}
    left: dict[str, int] = {}
    for routine_dir in routine_dirs(home):
        for row in missing_removals(home, routine_dir):
            # The hook is addressed as the write it undoes was: `self` when the routine holds it
            # for its own runs, the HOLDER when a sibling set it there. The projection reads the
            # holder off the addressee and the owner off the origin, so a foreign hook must be
            # recorded origin=owner, addressee=holder — the same correction
            # `migrate_premirror_reminders` needed, where passing the holder as both turned a
            # store-only id into a `differs` one.
            owner = str(row.get("owner") or "")
            foreign = bool(owner) and owner != routine_dir.name
            try:
                hookstore.record_reminder(
                    home, record=row, routine=owner or routine_dir.name,
                    run_id=str(row.get("created_run") or ""),
                    target=routine_dir.name if foreign else "",
                    disposition="remove")
            except (OSError, TypeError, ValueError) as exc:
                log.warning("premirror-deletions: %s/%s: %s", routine_dir.name, row["id"], exc)
                continue
            removed.setdefault(routine_dir.name, []).append(str(row["id"]))
        if remaining := len(reminderproj.drift(home, routine_dir)):
            left[routine_dir.name] = remaining
    record = {"at": now_iso(), "removed": removed,
              "removed_total": sum(len(v) for v in removed.values()),
              "still_drifting": left,
              "note": "stream-only ids only. The `differs` class is left alone: three lack "
                      "created_run (the mirror era dropped it) and rem-20261026-233255-1 "
                      "disagrees on owner, which decides who may remove a hook — the "
                      "operator's call, not a migration's."}
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.info("premirror-deletions: recorded %d deletions across %d routines",
             record["removed_total"], len(removed))
    return record
