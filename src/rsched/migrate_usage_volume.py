"""One-shot boot migration: every past usage record gains the spend fields a live run has written
since this release — MIGRATION(expires=2026-12-15).

`usage_log.spend` gives each row `cached_in`, `cache_write`, `volume` (the whole context volume)
and `notional_cost`. Every cost ranking now reads `volume`, so a row without it would drop out
of every ranking. What each past row's prompt-cache traffic WAS is recovered from what the run
itself recorded, per LEG (a row is one leg; the stream's reader sums a run's legs):

- a depth-0 row carrying `quality.cache_read` / `quality.cache_write` (since 2026-07-12): those
  are the run's CUMULATIVE figures at that leg's end, so a leg's own share is its figure minus
  the previous leg's of the same run;
- a depth-0 row without them, the only leg of its run: the run's `status.json` usage, where
  retention kept the run dir;
- a child-run row (`<run>#sub<n>`): its `subrun_end` event's usage in the parent's transcript,
  where retention kept it.

A row none of these covers gets `null` for all three — UNMEASURED, never zero and never
estimated. `notional_cost` is `null` on every past row: what those tokens cost at the prices of
the day was never recorded, and today's prices are not those. Runs at daemon boot before the app
starts, so nothing appends while the stream is rewritten (atomically, after checking it did not
grow); records what it did in `.control/migrations/usage-volume.json`; while that exists it does
nothing.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .engine.transcript import read_events
from .ids import now_iso
from .paths import atomic_write_json, read_json
from .readmodels.usage_stream import stream_path

log = logging.getLogger("rsched.migrate")

RECORD = Path(".control") / "migrations" / "usage-volume.json"


def _run_dir(server, routine: str, ts: str) -> Path | None:
    for home in (server.routines_home, server.conversations_home, server.background_home):
        d = Path(home) / routine / "runs" / ts
        if d.is_dir():
            return d
    return None


def _cache(usage: object) -> tuple[int, int] | None:
    if not isinstance(usage, dict):
        return None
    return int(usage.get("cached_in") or 0), int(usage.get("cache_write") or 0)


def _subrun_usage(run_dir: Path | None, n: str) -> tuple[int, int] | None:
    if run_dir is None or not (run_dir / "transcript.jsonl").is_file():
        return None
    for ev in read_events(run_dir / "transcript.jsonl", 0)[0]:
        payload = ev.get("payload") or {}
        if ev.get("type") == "subrun_end" and str(payload.get("n")) == n:
            return _cache(payload.get("usage"))
    return None


def leg_cache(server, records: list[dict]) -> list[tuple[int, int] | None]:
    """Per record, its leg's own (cache reads, cache writes), or None when unrecorded."""
    legs: dict[str, int] = {}
    for rec in records:
        legs[str(rec.get("run_id"))] = legs.get(str(rec.get("run_id")), 0) + 1
    previous: dict[str, tuple[int, int]] = {}
    out: list[tuple[int, int] | None] = []
    for rec in records:
        run_id = str(rec.get("run_id") or "")
        base, _, sub = run_id.partition("#sub")
        routine, _, ts = base.partition(":")
        raw_quality = rec.get("quality")
        quality: dict = raw_quality if isinstance(raw_quality, dict) else {}
        found: tuple[int, int] | None = None
        if sub:
            found = _subrun_usage(_run_dir(server, routine, ts), sub)
        elif "cache_read" in quality or "cache_write" in quality:
            cum = (int(quality.get("cache_read") or 0), int(quality.get("cache_write") or 0))
            before = previous.get(run_id, (0, 0))
            previous[run_id] = cum
            found = (max(0, cum[0] - before[0]), max(0, cum[1] - before[1]))
        elif legs[run_id] == 1 and (d := _run_dir(server, routine, ts)) is not None:
            status = read_json(d / "status.json")
            found = _cache(status.get("usage")) if isinstance(status, dict) else None
        out.append(found)
    return out


def migrated(rec: dict, cache: tuple[int, int] | None) -> dict:
    tokens = int(rec.get("tokens") or 0)
    fields: dict = {"cached_in": None, "cache_write": None, "volume": None,
                    "notional_cost": None}
    if cache is not None:
        fields.update(cached_in=cache[0], cache_write=cache[1],
                      volume=tokens + cache[0] + cache[1])
    out: dict = {}
    for key, value in rec.items():        # keep the writer's key order: spend after `cost`
        out[key] = value
        if key == "cost":
            out.update(fields)
    return out


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
        parsed.append(rec if isinstance(rec, dict) and "volume" not in rec else None)
    todo = [r for r in parsed if r is not None]
    caches = iter(leg_cache(server, todo))
    out_lines, measured = [], 0
    for raw, rec in zip(lines, parsed, strict=True):
        if rec is None:
            out_lines.append(raw)
            continue
        cache = next(caches)
        measured += cache is not None
        out_lines.append(json.dumps(migrated(rec, cache), ensure_ascii=False).encode())
    record = {"started": now_iso(), "rows": len(todo), "measured": measured,
              "unmeasured": len(todo) - measured}
    if stream.stat().st_size != before:          # something appended: never lose a record
        record["skipped"] = "the stream grew while it was rewritten — retried at the next boot"
        log.warning("usage volume: %s", record["skipped"])
        return record
    tmp = stream.with_name(stream.name + ".volume")
    tmp.write_bytes(b"\n".join(out_lines))
    tmp.replace(stream)
    record["seconds"] = round(time.monotonic() - began, 1)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.warning("usage volume: %d rows backfilled, %d measured, %d unmeasured", len(todo),
                measured, len(todo) - measured)
    return record
