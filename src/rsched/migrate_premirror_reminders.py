"""One-shot boot migration: PRE-MIRROR reminders gain the hook they never got —
MIGRATION(expires=2027-02-28).

D152 step 3 — making the reminder store a DERIVED VIEW of the hook stream — rests on the same
property the report half rested on: folding the stream gives the store, id for id. Measured on
the live instance 2026-11-14, over all 31 routines with `hooks/reminderproj.drift`, it did not,
and the gap is **15× the reports problem**: 122 drifting ids across 21 of 31 routines, in three
classes that need three different answers.

- **95 `store-only`** — a definition in a routine's `state/reminders.json` with no hook at all.
  **Every one predates the mirror**: the newest is `rem-20261018-…` and `hookmirror` landed on
  2026-10-19 (41 from September, 54 from October, none from November). So this class is
  bounded, closed, and exactly what a one-shot backfill is for — which is what this module does.
- **23 `stream-only`** — a hook whose definition is no longer in the store. Five are from
  November, because a routine deleting its OWN reminder wrote the store and no row; that is a
  WRITE-PATH defect, fixed in `remind_write.remove` with `hookmirror.reminder_removed`, and it
  is deliberately not repaired here: an append-only stream's older `remove`-less deletions are
  correct history, and resurrecting them is precisely what must not happen.
- **4 `differs`** — three missing `created_run` (the mirror era dropped it, as
  `hooks/store.record_reminder`'s own docstring admits) and one disagreeing on `owner`
  (`rem-20261026-233255-1`: `vodafone-outage-claims` on the stream, `self-audit` in the store).
  `owner` decides who may REMOVE a hook, so which side is authoritative is a question this
  migration may not answer by guessing; all four are left alone and named in the record.

**Why a backfill and not a repair-in-place.** The reports half rewrote records, because a
mirror-era row was the same fact recorded POORLY. Here the row is ABSENT, so there is nothing to
rewrite: the hook is appended, and the stream's order gains it at the end rather than at the date
it was written. That is honest — the hook's `origin` carries the reminder's own `created_run`, so
when the definition entered is still answerable from the row itself, and nothing pretends the
append happened in October.

**What is deliberately NOT mirrored.** The TALLIES (`fires` and the four labels). They are
per-routine evidence with their own writer (`reminders.record`, its own read-modify-write), and
`hooks/store.record_reminder` leaves them out on purpose: mirroring a counter would give one
number two writers, which is the defect this campaign removes rather than spreads. A backfilled
hook therefore carries the DEFINITION, which is what `reminderproj.definitions` compares.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .hooks import reminderproj
from .hooks import store as hookstore
from .ids import now_iso
from .paths import atomic_write_json

log = logging.getLogger("rsched.migrate")

RECORD = Path(".control") / "migrations" / "premirror-reminders.json"


def routine_dirs(home: Path) -> list[Path]:
    """Every installed routine, by the one marker that makes a directory one.

    `enabled` is deliberately not consulted: a disabled routine's store is still compared by
    the comparator this exists to empty, and a backfill that skipped it would leave the
    property permanently false for reasons nobody could see.
    """
    return sorted(d for d in home.iterdir() if (d / "routine.yaml").is_file())


def missing_hooks(home: Path, routine_dir: Path) -> list[dict]:
    """The DEFINITIONS this routine's store holds that the stream has no hook for.

    Pure and read-only, so the live-instance property is measurable in a test with no write and
    no daemon boot — the shape `migrate_mirror_hooks.repaired_records` established.

    Only the `store-only` class: an id the stream knows, in any state, is left alone. A
    `stream-only` id is a deletion the stream recorded correctly (or, before the write-path fix,
    one it recorded by omission) and must never be resurrected; a `differs` id is a
    disagreement about content, which an append cannot settle.
    """
    out = []
    for rid, (from_stream, in_store) in reminderproj.drift(home, routine_dir).items():
        if from_stream is None and in_store is not None:
            out.append({"id": rid, **in_store})
    return sorted(out, key=lambda r: str(r["id"]))


def run_migration(server) -> dict:
    """Append the missing reminder hooks once, for every installed routine. Returns the record
    (empty when it already ran, or when there is nothing to backfill).
    """
    home = Path(server.routines_home)
    record_path = home / RECORD
    if record_path.exists():
        return {}
    filled: dict[str, list[str]] = {}
    left: dict[str, int] = {}
    for routine_dir in routine_dirs(home):
        rows = missing_hooks(home, routine_dir)
        for row in rows:
            # WHO the hook comes from and WHO holds it are two different routines when a sibling
            # set it (`reminders.set_for`): the projection reads the holder off the ADDRESSEE and
            # the owner off the ORIGIN, so a foreign hook must be recorded origin=owner,
            # addressee=holder. Passing the holder as both would have written `owner: <holder>`
            # where the store says `owner: <sibling>` — turning a store-only id into a `differs`
            # one, i.e. making the comparator worse while appearing to fix it.
            owner = str(row.get("owner") or "")
            try:
                hookstore.record_reminder(
                    home, record=row, routine=owner or routine_dir.name,
                    run_id=str(row.get("created_run") or ""),
                    target=routine_dir.name if owner else "")
            except (OSError, TypeError, ValueError) as exc:
                log.warning("premirror-reminders: %s/%s: %s", routine_dir.name, row["id"], exc)
                continue
            filled.setdefault(routine_dir.name, []).append(str(row["id"]))
        if remaining := len(reminderproj.drift(home, routine_dir)):
            left[routine_dir.name] = remaining
    record = {"at": now_iso(), "filled": filled,
              "filled_total": sum(len(v) for v in filled.values()),
              "still_drifting": left,
              "note": "store-only ids only; stream-only deletions and the four `differs` ids "
                      "are left alone (see this module's docstring)"}
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.info("premirror-reminders: backfilled %d hooks across %d routines",
             record["filled_total"], len(filled))
    return record
