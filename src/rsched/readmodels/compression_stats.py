"""Per-routine output-compression roll-up for the Stats tab — what the lossless compressor
actually bought — and what it cost to find out.

The per-observation `compression` metadata answers "what happened to THIS command's
output"; it is unreadable as a fleet question, because it lives one line at a time inside
transcripts. This read model answers the routine-level one: how many command outputs were
eligible, how many previews actually replaced an observation, how many estimated tokens
that saved, and how often the compressor's result was thrown away — a rejected result
costs the run the same wall clock as an accepted one.

Source: the DURABLE workflow-usage stream (`.control/workflow-usage.jsonl`), which
survives run retention, exactly like the per-util table and the monthly spend series. A
record carrying the `compression` key was counted at the engine; records written before
the counter existed carry no key and are simply not in the window — `since` names the
first record that is, so the table never implies a longer history than it has.

Records at every depth are summed: a child run's compression is its own (the parent folds
nothing in), the same contract per-util counts follow. They are read per RUN (`usage_runs`):
a continued run's legs are one run, each leg's own tally summed into it.
"""

from __future__ import annotations

from ..config import ServerConfig
from .incarnations import resolver
from .usage_stream import usage_runs

# the outcome vocabulary of engine.output_compression (metrics["status"])
STATUSES = ("applied", "unchanged", "fallback", "skipped")
# outcomes where the compressor actually RAN — the ones that cost the run its time
ATTEMPTED = ("applied", "unchanged", "fallback")


def _int(value: object) -> int:
    """A count from a stream record, forgiving of one malformed field (never a 500)."""
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _cell(slug: str) -> dict:
    return {"routine": slug, "runs": 0, "tokens_saved": 0, "seconds": 0.0,
            "first": "", "last": "", **dict.fromkeys(STATUSES, 0)}


def compression_stats(server: ServerConfig) -> dict:
    """`{"rows": [...], "totals": {...}, "since": iso|None, "records": n}` — one row per
    routine that has reported a counted run, ordered by estimated tokens saved; a routine
    archived and created again is two rows, the old one under its archive's name.

    Rows carry the raw outcome counts plus two derived readings: `candidates` (successful
    command outputs at all) and `attempts` (the compressor ran).
    `applied ÷ attempts` is the hit rate; `fallback` is the compressor producing a result
    the engine's own verification refused, which is a cost with no benefit at all.
    """
    rows: dict[str, dict] = {}
    since = ""
    records = 0
    name_of = resolver(server.routines_home)        # an archived routine is its own row
    for rec in usage_runs(server.routines_home):
        tally = rec.get("compression")
        if not isinstance(tally, dict):
            continue    # written before the counter existed: outside the window, not a zero
        records += 1
        ts = str(rec.get("ts") or "")
        if ts and (not since or ts < since):
            since = ts
        name = name_of(rec) or "?"
        cell = rows.setdefault(name, _cell(name))
        cell["runs"] += 1
        for status in STATUSES:
            cell[status] += _int(tally.get(status))
        cell["tokens_saved"] += _int(tally.get("tokens_saved"))
        cell["seconds"] = round(cell["seconds"] + _float(tally.get("ms")) / 1000, 1)
        if ts:
            cell["last"] = max(cell["last"], ts)
            cell["first"] = min(cell["first"] or ts, ts)

    totals = _cell("(all)")
    for cell in rows.values():
        cell["candidates"] = sum(cell[s] for s in STATUSES)
        cell["attempts"] = sum(cell[s] for s in ATTEMPTED)
        totals["runs"] += cell["runs"]
        totals["tokens_saved"] += cell["tokens_saved"]
        totals["seconds"] = round(totals["seconds"] + cell["seconds"], 1)
        for status in STATUSES:
            totals[status] += cell[status]
    totals["candidates"] = sum(totals[s] for s in STATUSES)
    totals["attempts"] = sum(totals[s] for s in ATTEMPTED)
    ordered = sorted(rows.values(), key=lambda c: (-c["tokens_saved"], c["routine"]))
    return {"rows": ordered, "totals": totals, "since": since or None, "records": records}
