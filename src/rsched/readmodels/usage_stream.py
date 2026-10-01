"""The ONE parser of the durable usage stream (`.control/workflow-usage.jsonl`), and its one
fold from LEGS to RUNS.

Four read-models fold this stream (stats' monthly spend, run_health's recipe buckets,
util_stats' reliability table, compression_stats' roll-up); each used to re-read and re-parse
the whole file per request. This module parses it ONCE per change (stat-fingerprint memo) and
hands every consumer the same parsed records.

A record is one LEG of a run, not one run. A run the operator continues, or one that resumes
after a restart, appends a further record under the same `run_id`, and the fields of a leg
mean two different things: `tokens`, `cost` and the `compression` tally are the leg's OWN,
while `turns`, `utils`, `referrals` and `asks_deferred` are CUMULATIVE — a resumed leg's boot
reseeds them from the prior leg's status (`engine/history.prior_counters`, F131/F132). Summed
record by record, a cumulative field counts every earlier leg again: a conversation answering
ten replies with one util call each read as 55 calls. Everything that counts per run reads
`usage_runs`, the stream folded once per change; `usage_records` is the raw legs.

The returned lists and their records are SHARED — treat them as immutable (fold, filter,
never mutate). A consumer that must annotate copies first. Unparseable lines are skipped,
exactly as every hand parser did.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..health_events import WORKFLOW_USAGE_FILE
from . import memo

#: A leg's own spend — summed across a run's legs. Every other field is the newest leg's.
_LEG_NUMBERS = ("tokens", "cost")
#: A leg's own count maps — summed key by key across a run's legs.
_LEG_TALLIES = ("compression",)


def stream_path(routines_home: Path) -> Path:
    return routines_home / ".control" / WORKFLOW_USAGE_FILE


def usage_records(routines_home: Path) -> list[dict]:
    """All records — one per LEG — oldest first. Missing file → []."""
    path = stream_path(routines_home)

    def parse() -> list[dict]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        out: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out

    return memo.memoized_shared(f"usage-stream:{path}", [path], parse)


def usage_runs(routines_home: Path) -> list[dict]:
    """One record per RUN — `fold_legs` over `usage_records`, memoized with it. A subrun's
    `<run>#sub<n>` is a run of its own, exactly as its records were.
    """
    path = stream_path(routines_home)
    return memo.memoized_shared(f"usage-runs:{path}", [path],
                                lambda: fold_legs(usage_records(routines_home)))


def _number(value: object) -> float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _add_tallies(older: object, newer: object) -> dict:
    out: dict = {}
    for tally in (older, newer):
        for key, value in (tally.items() if isinstance(tally, dict) else ()):
            out[key] = _number(out.get(key)) + _number(value)
    return out


def fold_legs(records: list[dict]) -> list[dict]:
    """One record per RUN, not per leg — the unit every per-run count assumes it has.

    Keep the NEWEST leg (the cumulative fields are right there, and its `status` and `ts` are
    how the run ended) and SUM the per-leg ones. Measured on this instance 2026-09-23: 50.5% of
    depth-0 records were extra legs (2,445 records over 1,211 runs), so a five-record window
    was often two runs plus their bookkeeping — and one live specimen, a 148-turn leg carrying
    2,080 tokens, dragged a median hard enough to flag a routine that had not changed.

    Order is preserved by first appearance, because every caller slices these by recency. The
    fold never raises: it sits behind a memo every reader shares, so a malformed number counts
    as nothing here and a run with one leg keeps its raw values for the reader to judge.
    """
    folded: dict[str, dict] = {}
    for rec in records:
        key = str(rec.get("run_id") or id(rec))
        prev = folded.get(key)
        if prev is None:
            folded[key] = dict(rec)
            continue
        merged = dict(rec)
        for field in _LEG_NUMBERS:
            merged[field] = _number(prev.get(field)) + _number(rec.get(field))
        for field in _LEG_TALLIES:
            if isinstance(prev.get(field), dict) or isinstance(rec.get(field), dict):
                merged[field] = _add_tallies(prev.get(field), rec.get(field))
        folded[key] = merged
    return list(folded.values())
