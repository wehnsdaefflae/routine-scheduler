"""A routine's OUTCOME against its COST, run by run — the evidence an outcome review starts from.

Every run of a routine that declares an outcome (config/outcome.py) records what its
`scripts/outcome.py` measured (`outcome_measures`) beside what the run cost (`volume`, the whole
context processed, and `notional_cost`) in the durable usage stream. This module reads that
stream back as one series per routine and says, over a window of weeks, what moved and what it
cost to move it.

It deliberately reads nothing the routine says about itself — no status, no Done-when verdict, no
self-check: those are what told the instance freelance-radar was healthy through 89 applications
and zero interviews. A run whose outcome could not be measured is shown as such (`error`), never
as a zero; a measure with fewer than two readings in the window has no change, not a change of 0.

Per measure, over the window: a `count` (a running total) moved by `last - first`; a `level` (a
current reading) went from `first` to `last`; `better` says which direction is progress. Cost over
the same window: the runs, their summed volume, and their notional dollars where measured
(`notional_runs` says how many carried one). `per_unit` is volume per unit of a count's progress —
`null` when it made none, which is the reading an outcome review exists to surface.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from .incarnations import live_runs
from .usage_stream import usage_runs

#: The review window when none is asked for: four weeks is two dozen runs of a daily routine,
#: and a routine that moved nothing in a month at its cost has told you what it will do next.
DEFAULT_WEEKS = 4


def _when(ts: object) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def series(routines_home: Path, slug: str) -> list[dict]:
    """Every depth-0 run of the routine, oldest first: its outcome reading and its cost."""
    runs = live_runs(routines_home,
                     [r for r in usage_runs(routines_home) if not r.get("depth")], slug)
    out = []
    for r in runs:
        raw = r.get("outcome_measures")
        measured: dict = raw if isinstance(raw, dict) else {}
        out.append({"ts": r.get("ts"), "run_id": r.get("run_id"), "status": r.get("status"),
                    "turns": r.get("turns"), "volume": r.get("volume"),
                    "notional_cost": r.get("notional_cost"),
                    "values": measured.get("values") or {}, "error": measured.get("error")})
    return out


def _measure_change(rows: list[dict], measure: dict) -> dict:
    readings = [(row["ts"], row["values"][measure["id"]]) for row in rows
                if measure["id"] in row["values"]]
    out: dict = {**measure, "readings": len(readings), "first": None, "last": None,
                 "change": None}
    if readings:
        out["first"], out["last"] = readings[0][1], readings[-1][1]
    if len(readings) >= 2:
        out["change"] = round(readings[-1][1] - readings[0][1], 6)
    return out


def review(routines_home: Path, cfg, *, weeks: int = DEFAULT_WEEKS,
           now: datetime | None = None) -> dict:
    """The routine's outcome and cost over the last `weeks`: per measure what moved, and what the
    window's runs cost. `declared: false` when the routine declares no outcome contract.
    """
    contract = getattr(cfg, "outcome", None)
    rows = series(routines_home, cfg.slug)
    since = (now or datetime.now(UTC)) - timedelta(weeks=weeks)
    window = [r for r in rows if (w := _when(r["ts"])) is not None and w >= since]
    volume = sum(int(r["volume"]) for r in window if r["volume"] is not None)
    priced = [r["notional_cost"] for r in window if r["notional_cost"] is not None]
    measures = [_measure_change(window, m.model_dump()) for m in contract.measures] \
        if contract else []
    for m in measures:
        progress = m["change"] if m["better"] == "higher" else \
            (-m["change"] if m["change"] is not None else None)
        m["per_unit"] = (round(volume / progress) if m["kind"] == "count" and progress
                         and progress > 0 else None)
    return {"slug": cfg.slug, "declared": contract is not None,
            "principal": contract.principal if contract else "", "weeks": weeks,
            "measures": measures,
            "cost": {"runs": len(window), "volume": volume,
                     "unmeasured_runs": sum(1 for r in window if r["volume"] is None),
                     "notional": round(sum(priced), 2), "notional_runs": len(priced)},
            "unreadable_runs": sum(1 for r in window if r["error"]),
            "series": rows[-60:]}
