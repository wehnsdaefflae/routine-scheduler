"""The durable USAGE stream's writer — one line per finished (sub)run leg in
`<routines_home>/.control/workflow-usage.jsonl`.

It is the feedback stream the routine-improver routine mines (its outcome review ranks routines
by what they cost against what they produce, and its `library-pass` stage maintains the shared
library from it) and the DURABLE spend series: run dirs fall to retention, this stream survives,
so every monthly figure and every cost ranking reads it (`readmodels/usage_stream.py` is its one
parser). Subruns report like any other run (depth > 0).

## What a row says a run cost

`spend` derives every spend field from the run's usage dict, in one place:

- `tokens` — fresh input + output. What a token BUDGET meters, and nothing else: it leaves the
  prompt cache out, so it is NOT a cost — a run re-reading a 400k prefix every turn reads small.
- `cached_in`, `cache_write` — the prompt-cache reads and writes of this leg.
- `volume` — all four together: the whole context the model processed. Every RANKING of what a
  routine costs reads this (or `notional_cost`), never `tokens`.
- `notional_cost` — the leg at list price (`endpoints/prices.py`), whatever transport carried
  it; `null` when no completion was priced. `cost` stays the provider's own reported figure,
  which is 0.0 on a subscription transport.
- `outcome` (depth 0, when the routine declares one) — what the run's results script reported
  (`engine/outcome.py`), so outcomes and cost form one time series.

Payload extensions are never a new shape: `recipe_commit`, `library_commit` (the library HEAD
at the run's END, depth 0 only — a rule revision reaches every holder without moving a recipe
version), `utils` (per-util outcome counts, ALWAYS present — its presence marks the record as
util-counted), `asks_deferred`, `compression`, and `fingerprint` / `quality` (what made the run
and how it went, engine/runrecord.py; depth 0 only). Best-effort like the health log: an I/O
error is swallowed, so telemetry never fails a finished run.
"""

from __future__ import annotations

from pathlib import Path

from .ids import now_iso
from .paths import append_jsonl

WORKFLOW_USAGE_FILE = "workflow-usage.jsonl"


def spend(usage: dict) -> dict:
    """The spend fields of one row, from a usage dict (`endpoints/base.Completion` keys)."""
    fresh, out = int(usage.get("in") or 0), int(usage.get("out") or 0)
    cached, written = int(usage.get("cached_in") or 0), int(usage.get("cache_write") or 0)
    notional = usage.get("notional")
    return {"tokens": fresh + out, "cost": round(float(usage.get("cost") or 0.0), 6),
            "cached_in": cached, "cache_write": written,
            "volume": fresh + out + cached + written,
            "notional_cost": round(float(notional), 6) if notional is not None else None}


def log_workflow_usage(routines_home: Path, *, routine: str, run_id: str,  # noqa: PLR0913 — a flat record writer: one keyword per stream field keeps the vocabulary explicit
                       workflow: str, depth: int, status: str, turns: int, usage: dict,
                       referrals: int = 0,
                       recipe_commit: str | None = None, library_commit: str | None = None,
                       utils: dict | None = None,
                       asks_deferred: int = 0, compression: dict | None = None,
                       fingerprint: dict | None = None, quality: dict | None = None,
                       outcome: dict | None = None) -> None:
    """Append one leg's row. `usage` is the leg's OWN usage (not the run's cumulative total):
    the stream's reader sums a run's legs (`usage_stream.fold_legs`).
    """
    try:
        append_jsonl(Path(routines_home) / ".control" / WORKFLOW_USAGE_FILE, {
            "ts": now_iso(),
            "routine": routine,
            "run_id": run_id,
            "workflow": workflow or "(unknown)",
            "depth": depth,
            "status": status,
            "turns": turns,
            **spend(usage),
            "referrals": referrals,
            "recipe_commit": recipe_commit,
            "library_commit": library_commit,
            "utils": utils or {},
            "asks_deferred": asks_deferred,
            "compression": compression or {},
            **({"fingerprint": fingerprint} if fingerprint else {}),
            **({"quality": quality} if quality else {}),
            **({"outcome": outcome} if outcome else {}),
        })
    except OSError:
        pass
