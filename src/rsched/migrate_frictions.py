"""One-shot boot migration: the past runs whose transcripts retention kept gain the FRICTIONS a
live run has recorded since this release — MIGRATION(expires=2026-12-31).

`runrecord.quality` carries `frictions` (engine/frictions.py), the signatures the moment test
reads (readmodels/change_frictions.py). Without a backfill that test could judge nothing until
two runs had landed on each side of a change; the transcripts of the last `keep_runs` runs of
every routine still hold exactly what the live record would have counted, so it is counted from
them the same way (`frictions.of_run`, the function the live record calls).

Only a depth-0 row that carries `quality` gains it, and only the LAST row of its run: the
stream's reader keeps the newest leg's quality, and an earlier leg's transcript was shorter than
the file on disk now is. A run whose dir is gone keeps no `frictions` key — UNKNOWN, which the
moment test leaves out on both sides, never a clean run.

Runs at daemon boot before the app starts, after the volume backfill, so nothing appends while
the stream is rewritten (atomically, after checking it did not grow); records what it did in
`.control/migrations/frictions.json`; while that exists it does nothing.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .engine import frictions
from .ids import now_iso
from .paths import atomic_write_json
from .readmodels.usage_stream import stream_path

log = logging.getLogger("rsched.migrate")

RECORD = Path(".control") / "migrations" / "frictions.json"


def _last_rows(parsed: list[dict | None]) -> dict[str, int]:
    """Per depth-0 run with a quality record, the index of its newest row."""
    last: dict[str, int] = {}
    for i, rec in enumerate(parsed):
        if (rec is not None and rec.get("depth") == 0 and isinstance(rec.get("quality"), dict)
                and "frictions" not in rec["quality"]):
            last[str(rec.get("run_id") or "")] = i
    return last


def run_migration(server) -> dict:
    """Backfill once. Returns the record (empty when it already ran)."""
    home = Path(server.routines_home)
    record_path = home / RECORD
    stream = stream_path(home)
    if record_path.exists() or not stream.is_file():
        return {}
    began = time.monotonic()
    before = stream.stat().st_size
    lines = stream.read_bytes().split(b"\n")
    parsed: list[dict | None] = []
    for raw in lines:
        try:
            rec = json.loads(raw) if raw.strip() else None
        except ValueError:
            rec = None
        parsed.append(rec if isinstance(rec, dict) else None)
    counted = 0
    last = _last_rows(parsed)
    for run_id, i in last.items():
        routine, _, ts = run_id.partition(":")
        found = frictions.of_run(home / routine / "runs" / ts) if routine and ts else None
        rec = parsed[i]
        if found is None or rec is None:
            continue
        rec["quality"] = {**rec["quality"], "frictions": found}
        lines[i] = json.dumps(rec, ensure_ascii=False).encode()
        counted += 1
    record = {"started": now_iso(), "runs": len(last), "counted": counted,
              "unknown": len(last) - counted}
    if stream.stat().st_size != before:          # something appended: never lose a record
        record["skipped"] = "the stream grew while it was rewritten — retried at the next boot"
        log.warning("frictions: %s", record["skipped"])
        return record
    tmp = stream.with_name(stream.name + ".frictions")
    tmp.write_bytes(b"\n".join(lines))
    tmp.replace(stream)
    record["seconds"] = round(time.monotonic() - began, 1)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.warning("frictions: %d of %d runs counted from their transcripts", counted, len(last))
    return record
