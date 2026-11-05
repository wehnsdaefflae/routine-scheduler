"""One-shot boot migration: every pre-stream report row gains the HOOK it always was —
MIGRATION(expires=2026-12-31).

D152 step 4 made the hook the source of every report filed from 2026-10-19 on. The ledger
starts 2026-07-19. Measured on the live instance the day this shipped: 3,952 of 5,016 ledger
rows, covering 2,209 report ids, have no hook at all. Until they do, a decision moved onto the
stream (`hooks/reportinv.open_threads_now` is the first) answers from the last three weeks of a
four-month history — the open-thread cap would stop counting a thread opened in September, and
would do it silently, because a miss here produces no error, only a smaller number.

So the history is carried over through the declared inverse (`hooks/reportinv.hook_of_row`),
which is exact rather than approximate: `reportproj`'s two row builders are total over this
ledger — no filing or event row carries a key outside what they emit.

Order is the stream's contract: append-only, and a later row about an id is the later fact
about it. The backfill therefore MERGES by timestamp rather than appending — every pre-stream
row sorts ahead of the first hook ever written (2026-10-19T05:11), and the 7 live rows that
interleave are lifecycle events whose id is absent from the stream entirely, so no id's own
history is reordered. Rows already in the stream are left untouched, identified the way the
stream identifies anything: by id, and by the one fact that distinguishes two rows of one id
(its event, or the filing).

Runs at daemon boot before the app starts, so nothing appends while the stream is rewritten
(atomically, after checking it did not grow); records what it did in
`.control/migrations/report-hooks.json`; while that record exists it does nothing.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .hooks import store as hookstore
from .hooks.reportinv import hook_of_row
from .ids import now_iso
from .paths import atomic_write_json, read_jsonl
from .reports import reports_path

log = logging.getLogger("rsched.migrate")

RECORD = Path(".control") / "migrations" / "report-hooks.json"


def _key(row: dict) -> tuple[str, str]:
    """What identifies one ledger row: its report id, and its event (`""` for the filing).

    Two rows of one id are two facts about it, so the id alone cannot be the key — a report
    that was filed, delivered and superseded is three rows and must become three hooks.
    """
    return str(row.get("id") or ""), str(row.get("event") or "")


def run_migration(server) -> dict:
    """Carry the pre-stream report history into the hook stream once. Returns the record
    (empty when it already ran, or when there is nothing to carry).
    """
    home = Path(server.routines_home)
    record_path = home / RECORD
    ledger = reports_path(home)
    if record_path.exists() or not ledger.is_file():
        return {}
    began = time.monotonic()
    stream = hookstore.hooks_path(home)
    before = stream.stat().st_size if stream.is_file() else 0
    existing = {(h.id, str(h.payload.get("event") or ""))
                for h in hookstore.read_hooks(home) if h.source == "report"}
    carried: list[dict] = []
    skipped = 0
    for row in read_jsonl(ledger):
        if not isinstance(row, dict) or _key(row) in existing:
            continue
        hook = hook_of_row(row)
        if not hook.id:
            skipped += 1
            continue
        carried.append(hook.as_record())
    record = {"started": now_iso(), "carried": len(carried), "unnamed_rows": skipped,
              "already_present": len(existing)}
    if not carried:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(record_path, record)
        return record
    kept = list(read_jsonl(stream)) if stream.is_file() else []
    merged = sorted([*kept, *carried], key=lambda r: str(r.get("ts") or ""))
    if (stream.stat().st_size if stream.is_file() else 0) != before:
        record["skipped"] = "the stream grew while it was rewritten — retried at the next boot"
        log.warning("report-hooks: %s", record["skipped"])
        return record
    stream.parent.mkdir(parents=True, exist_ok=True)
    tmp = stream.with_name(stream.name + ".report-hooks")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in merged),
                   encoding="utf-8")
    tmp.replace(stream)
    record["rows_after"] = len(merged)
    record["seconds"] = round(time.monotonic() - began, 1)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.warning("report-hooks: %d pre-stream report rows carried into the hook stream "
                "(%d rows now)", len(carried), len(merged))
    return record
