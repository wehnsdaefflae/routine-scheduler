"""One-shot boot migration: the MIRROR-ERA report hooks gain what the mirror dropped —
MIGRATION(expires=2027-01-31).

D152 campaign step 9 — the step that makes the hook stream the thing every reader READS — rests
on one property: folding the stream gives the report ledger, row for row. Measured on the live
instance the day this shipped, it did not, and by a narrow and dated margin: **8 of 5,334 rows**,
every one filed on **2026-10-19**, between step 2 (the mirror) and step 4 (the hook as the
source). Step 2's mirror carried only `title`, `detail` and `target`, so those eight hooks are
missing the DISPOSAL their ledger row records — `answers`, `closes`, `settles`, `supersedes`.

What that costs is not cosmetic, and it is why this runs before the read is repointed: the
disposal is exactly what `report_threads._is_open` reads. Eight reports that the ledger says are
closed read as OPEN on the stream — `R2709`, `R2712`, `R2715`, `R2716`, `R2719`, `R2720` (all
`answers` + `closes`, one of them with `settles`) and `R2718`, `R2721` (`supersedes`). A read
served from the stream would have re-opened six settled exchanges and lost two folds, and done
it silently, because a missing key produces no error — only a wrong answer.

The repair is the declared inverse, not a patch: `hooks/reportinv.hook_of_row` is total over
this ledger (step 7 measured it at 5,016 rows, zero mismatches), so the hook a row IS can be
recomputed from the row itself. A record is rewritten only when the ledger row is UNAMBIGUOUS —
one row for that `(id, event)` key — and only when the projection of the stored hook differs
from it. Two live ids (`R999`, `R1000`) carry a duplicated `delivered` key from a double
delivery in August; they are left exactly as they are, because which of two rows a record
belongs to is not a question this migration may answer by guessing.

**Why rewrite rather than append.** The stream is append-only and a later row about an id is the
later FACT about it — but `reports.fold_rows` is first-wins for a filing, so an appended richer
filing would be ignored by the very fold this exists to make correct. A mirror-era row is not a
later fact; it is the same fact recorded poorly. So it is corrected in place, atomically, after
checking the stream did not grow meanwhile — the shape `migrate_report_hooks` already uses.

Runs at daemon boot before the app starts, after the pre-stream backfill (whose rows come
through the same inverse and are therefore already exact); records what it did in
`.control/migrations/mirror-hooks.json`; while that record exists it does nothing.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .hooks import from_record
from .hooks import store as hookstore
from .hooks.reportinv import hook_of_row
from .hooks.reportproj import rows_for
from .ids import now_iso
from .paths import atomic_write_json, read_jsonl
from .reports import reports_path

log = logging.getLogger("rsched.migrate")

RECORD = Path(".control") / "migrations" / "mirror-hooks.json"


def _key(row: dict) -> tuple[str, str]:
    """What identifies one ledger row: its report id, and its event (`""` for the filing).

    The same key `migrate_report_hooks` uses, for the same reason — a report that was filed,
    delivered and superseded is three rows and three hooks, so the id alone cannot name one.
    """
    return str(row.get("id") or ""), str(row.get("event") or "")


def unambiguous(ledger_rows: list[dict]) -> dict[tuple[str, str], dict]:
    """The ledger rows whose `(id, event)` key names exactly ONE row.

    A duplicated key is excluded rather than resolved: two rows under one key are two facts
    the stream cannot tell apart either, so repairing a record from "one of them" would be
    this migration inventing history. On the live ledger that is `R999`/`R1000`'s doubled
    `delivered` — 2 keys of 5,332.
    """
    seen: dict[tuple[str, str], dict] = {}
    dupes: set[tuple[str, str]] = set()
    for row in ledger_rows:
        if not isinstance(row, dict) or not str(row.get("id") or ""):
            continue
        key = _key(row)
        if key in seen:
            dupes.add(key)
        else:
            seen[key] = row
    return {k: v for k, v in seen.items() if k not in dupes}


def repaired_records(ledger_rows: list[dict], stream_records: list[dict]) -> list[dict]:
    """The stream's records with every POORER report record recomputed from its ledger row.

    Pure, and that is deliberate: the property this migration exists to establish — that
    folding the stream gives the ledger — is then measurable against the LIVE stores in a test,
    with no write and no daemon boot (`tests/test_hooks_read.py`). Order is preserved exactly;
    a record is replaced, never moved, so nothing about the stream's sequence changes.

    A record is replaced only when all three hold: it is a `report` hook, its ledger row is
    unambiguous, and the row the stored hook PROJECTS TO differs from that ledger row. Anything
    else — a reminder, an injection, a run-tree hook, a report whose projection already matches
    — is passed through untouched.
    """
    rows = unambiguous(ledger_rows)
    out: list[dict] = []
    for record in stream_records:
        hook = from_record(record) if isinstance(record, dict) else None
        if hook is None or hook.source != "report":
            out.append(record)
            continue
        row = rows.get((hook.id, str(hook.payload.get("event") or "")))
        if row is None or rows_for(hook) == row:
            out.append(record)
            continue
        out.append(hook_of_row(row).as_record())
    return out


def run_migration(server) -> dict:
    """Repair the mirror-era report hooks once. Returns the record (empty when it already ran,
    or when there is nothing to repair).
    """
    home = Path(server.routines_home)
    record_path = home / RECORD
    ledger = reports_path(home)
    stream = hookstore.hooks_path(home)
    if record_path.exists() or not ledger.is_file() or not stream.is_file():
        return {}
    began = time.monotonic()
    before = stream.stat().st_size
    kept = list(read_jsonl(stream))
    repaired = repaired_records(list(read_jsonl(ledger)), kept)
    changed = sum(1 for a, b in zip(kept, repaired, strict=True) if a != b)
    record = {"started": now_iso(), "repaired": changed, "rows": len(repaired)}
    if changed and stream.stat().st_size != before:
        record["skipped"] = "the stream grew while it was repaired — retried at the next boot"
        log.warning("mirror-hooks: %s", record["skipped"])
        return record
    if changed:
        tmp = stream.with_name(stream.name + ".mirror-hooks")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in repaired),
                       encoding="utf-8")
        tmp.replace(stream)
        log.warning("mirror-hooks: %d mirror-era report hooks regained the disposal the "
                    "mirror dropped", changed)
    record["seconds"] = round(time.monotonic() - began, 1)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    return record
