"""One-shot boot migration: a reminder DEFINITION disagreement is settled by the store —
MIGRATION(expires=2027-02-28).

THE OPERATOR, 2026-11-14, answering this run's question:
*"The STORE is authoritative (self-audit owns it) — the holder's own record wins"*, over "the
stream wins", "delete the reminder" and "leave it drifting as a permanent exception".

WHERE THIS SITS. `hooks/reminderproj.drift` compares the definition the hook stream implies with
the one the routine's store holds, and D152 step 4 cannot serve the store FROM the stream until
it reads empty. Measured over all 31 live routines today:

  before 0.542.0   122 drifting ids in 21 routines — 95 store-only, 23 stream-only, 4 differs
  after  0.542.0    27 in  9 —  0 store-only, 23 stream-only, 4 differs
  after  0.548.0     4 in  2 —  0 store-only,  0 stream-only, 4 differs

This module takes the last class. Three of its four ids are mechanical — a missing `created_run`,
which the mirror era dropped (`hooks/store.record_reminder` admits it in its own docstring). The
fourth is why the operator was asked: `rem-20261026-233255-1` reads `owner:
vodafone-outage-claims` on the stream and `owner: self-audit` in the store, and **`owner` decides
who may REMOVE a hook** — a migration guessing it would hand one routine power over another
routine's reminder.

WHY HIS RULING IS ALSO THE RIGHT GENERAL RULE, which is what makes it safe for all four. The
store is where the routine that HOLDS the reminder writes it, through `remind_write.persist` —
the one path a routine's own act takes. The stream's row is a MIRROR of that act, and every
disagreement measured here is the mirror being POORER (a dropped field, an origin recorded as the
holder rather than the owner), never the store being poorer. So "the store wins" is not a
tie-break: it is a statement about which of the two is the record and which is the copy.

STILL AN APPEND, never an edit. The stream gains a later row carrying the store's own record; the
poorer row stays as history, and the fold — last row wins, by id — reads the agreement. That is
the same discipline `migrate_premirror_deletions` used for a deletion and the opposite of
`migrate_mirror_hooks`, which rewrote `reports.jsonl` in place because `reports.fold_rows` is
FIRST-wins for a filing. Reminders fold LAST-wins, so an append is both sufficient and honest.

WHAT IT DOES NOT TOUCH: a `store-only` id (0.542.0's backfill) or a `stream-only` one (0.548.0's
deletions). One class per migration, so the record says which one acted — and so a class nobody
has accounted for cannot hide behind another's repair.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .hooks import reminderproj
from .hooks import store as hookstore
from .ids import now_iso
from .paths import atomic_write_json

log = logging.getLogger("rsched.migrate")

RECORD = Path(".control") / "migrations" / "store-wins.json"


def disagreements(home: Path, routine_dir: Path) -> list[dict]:
    """The store's OWN record for every id where the two definitions disagree.

    Pure and read-only, so the live property is measurable with no write and no daemon boot —
    the shape `migrate_mirror_hooks.repaired_records` established. Only the `differs` class: an
    id missing from one side is another migration's business.
    """
    out = []
    for rid, (from_stream, in_store) in reminderproj.drift(home, routine_dir).items():
        if from_stream and in_store and from_stream != in_store:
            out.append({"id": rid, **in_store})
    return sorted(out, key=lambda r: str(r["id"]))


def run_migration(server) -> dict:
    """Append the store's record once for every disagreeing id. Returns the record (empty when
    it already ran).
    """
    home = Path(server.routines_home)
    record_path = home / RECORD
    if record_path.exists():
        return {}
    repaired: dict[str, list[str]] = {}
    left: dict[str, int] = {}
    for routine_dir in sorted(d for d in home.iterdir() if (d / "routine.yaml").is_file()):
        for row in disagreements(home, routine_dir):
            # The row keeps its HOLDER as addressee and takes the store's OWNER as origin: the
            # projection reads the holder off the addressee and the owner off the origin, and
            # this is exactly the id the operator ruled on — `owner` decides who may remove a
            # hook, so recording it as a self-hook would both hide it from its holder and give
            # the wrong routine that power.
            owner = str(row.get("owner") or "")
            foreign = bool(owner) and owner != routine_dir.name
            try:
                hookstore.record_reminder(
                    home, record=row, routine=owner or routine_dir.name,
                    run_id=str(row.get("created_run") or ""),
                    target=routine_dir.name if foreign else "")
            except (OSError, TypeError, ValueError) as exc:
                log.warning("store-wins: %s/%s: %s", routine_dir.name, row["id"], exc)
                continue
            repaired.setdefault(routine_dir.name, []).append(str(row["id"]))
        if remaining := len(reminderproj.drift(home, routine_dir)):
            left[routine_dir.name] = remaining
    record = {"at": now_iso(), "repaired": repaired,
              "repaired_total": sum(len(v) for v in repaired.values()),
              "still_drifting": left,
              "note": "The STORE is authoritative — the holder's own record wins (the "
                      "operator, 2026-11-14). The store is where the holding routine writes "
                      "through remind_write.persist; the stream's row is a mirror of that act, "
                      "and every measured disagreement was the mirror being poorer."}
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.info("store-wins: settled %d disagreements across %d routines",
             record["repaired_total"], len(repaired))
    return record
