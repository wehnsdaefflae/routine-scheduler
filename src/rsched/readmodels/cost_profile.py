"""What one RUN of a routine costs — the per-run half of the cost picture, and the only
surface that says what `cost_trend_degraded` is actually comparing when it fires.

Three cost readings already exist and none of them answer "what does a run of this cost":
`stats.monthly_spend` rolls spend up per CALENDAR MONTH (a month of a daily routine is 30
runs averaged into one cell), `stats.aggregate` slices the fleet for the Stats tab, and
`run_health.recent_trend` reduces two 5-run windows to a BOOLEAN flag plus medians buried in
a `reasons` string. So the routine page could tell the operator a routine had ballooned
without ever telling him what it costs per run, which is the figure a budget is set in.

This module is the per-run reading, and it is deliberately a READER ONLY: it introduces no
cost quantity of its own. `volume` (fresh + output + cache reads + cache writes — the whole
context the model processed) and `notional_cost` are the two figures every ranking on this
instance already uses (`usage_log.spend`), the runs are the legs folded once by
`usage_stream.usage_runs`, the incarnation filter is `incarnations.live_runs`, and the trend
verdict is `run_health.recent_trend` called and reported VERBATIM — not re-derived, so a
reader can never see this module and the routine page disagree about whether a routine is
flagged.

## Why it also reports accounting integrity (R3112)

A per-run cost figure is only honest if every run was counted the same way. Until 0.494.0 two
defects understated a run that spawned children: `history.prior_usage` rebuilt a resumed leg's
cumulative base without summing `subrun_end`, so every child collected in an earlier leg
vanished at the resume boundary, and an orphaned child's spend was written as `usage: {}` with
the real figure left in `sub/<n>/transcript.jsonl`. One measured run of this instance's own
audit routine under-reported its tree by 40%. Both causes are closed, so runs from 0.494.0 on
are sound — but **the stream on disk still carries the older understated rows**, and a window
of five runs that straddles the fix compares two different quantities. Reported as one median
that reads as a 1.4× improvement in cost that nobody made.

Rather than hard-code a release date or a cutoff timestamp — which would be wrong for every
other instance and unfalsifiable on this one — understatement is DETECTED from the stream
itself. A parent's usage folds its children in (`stats.monthly_spend`: "a parent's usage
already folds its children in"), and a child reports its own row under `<parent>#sub<n>` at
depth > 0. So a depth-0 run whose own child rows sum to MORE volume than the parent recorded
is understated by arithmetic, whatever release wrote it, and `UNDERSTATEMENT_SLACK` keeps
ordinary rounding from reading as a defect. The profile carries the count per window and
`mixed_accounting`, which names the side that is contaminated. A consumer that renders a
median without reading that key is rendering a mixture.

Pure over a routines home — fully unit-testable, no server object, no running daemon.
"""

from __future__ import annotations

from pathlib import Path

from .incarnations import live_runs
from .run_health import REGRESSION_WINDOW, recent_trend
from .usage_stream import usage_runs

#: A child's row and its parent's fold are written from the same usage dicts, so the parent
#: should be ≥ the sum of its children. Only a shortfall beyond this fraction counts as
#: understatement: a parent carries its OWN turns on top of every child, so the ordinary case
#: is a comfortable surplus, and this slack exists for rounding and for a child whose last
#: leg landed after its parent's fold — not to excuse a missing subtree. The measured R3112
#: case was a 40% shortfall, two orders above this.
UNDERSTATEMENT_SLACK = 0.02


def _median(vals: list[float]) -> float:
    """Deliberately the SAME definition `run_health._median` uses — the median this module
    reports and the median the flag fires on must never be two statistics.
    tests/test_cost_profile.py::test_median_agrees_with_the_flags_median pins them together.
    """
    if not vals:
        return 0.0
    s = sorted(vals)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _child_volume(records: list[dict]) -> dict[str, float]:
    """Volume reported by the CHILD rows of each parent run, keyed by the parent's run_id.

    A subrun's run_id is `<parent>#sub<n>` (`engine/subruns.py`, `engine/childrun.py`), at
    depth > 0. Nested children (`<parent>#sub<n>#sub<m>`) fold onto their nearest parent AND
    onto the root, which is what a tree total means; partitioning on the FIRST separator is
    what makes that true.
    """
    totals: dict[str, float] = {}
    for rec in records:
        if not rec.get("depth"):
            continue
        run_id = str(rec.get("run_id") or "")
        root, sep, _ = run_id.partition("#sub")
        if not sep or not root:
            continue
        volume = rec.get("volume")
        if volume is None:
            continue
        try:
            totals[root] = totals.get(root, 0.0) + float(volume)
        except (TypeError, ValueError):
            continue
    return totals


def _understated(volume: float | None, children: float) -> bool:
    """True when a parent reports LESS than its own children add up to — the R3112 symptom,
    read off the arithmetic rather than off a release date. A run with no children, or no
    volume recorded at all, is no reading either way: never understated by default.
    """
    if volume is None or not children:
        return False
    return float(volume) < children * (1.0 - UNDERSTATEMENT_SLACK)


def _run_row(rec: dict, children: dict[str, float]) -> dict:
    """One run of the profile: what it cost, and whether that figure can be trusted."""
    run_id = str(rec.get("run_id") or "")
    try:
        volume = None if rec.get("volume") is None else float(rec["volume"])
    except (TypeError, ValueError):       # one malformed row is skipped as a READING, never
        volume = None                     # counted as zero — a 0 would drag every median
    try:
        notional = None if rec.get("notional_cost") is None else float(rec["notional_cost"])
    except (TypeError, ValueError):
        notional = None
    child_volume = children.get(run_id, 0.0)
    return {"run_id": run_id, "ts": str(rec.get("ts") or ""),
            "status": str(rec.get("status") or ""),
            "turns": int(rec.get("turns") or 0),
            "volume": volume, "notional_cost": notional,
            "child_volume": child_volume,
            "understated": _understated(volume, child_volume)}


def _sanitized(records: list[dict], rows: list[dict]) -> list[dict]:
    """Copies of `records` whose unusable `volume` fields are None, for handing to
    `run_health.recent_trend`.

    Not defensiveness for its own sake: `regression_flag` does `float(r["volume"])` behind an
    `is not None` guard, so ONE malformed row (a volume written as a dict or a stray string)
    raises TypeError out of the read model — the exact failure `stats.py` documents itself as
    refusing ("one malformed record is skipped whole … never a 500 on the dashboard"). That
    fragility is reachable today from the routine page's own development line
    (`recipe_regression`), not only from here, and it is NOT fixed in this module: it is
    reported against `readmodels/run_health.py` to be fixed where the four readers of that
    stream share it. Until then this reader refuses to crash on data it has already parsed
    once, and the records are COPIED because the stream's lists are shared and immutable
    by contract (`usage_stream`).
    """
    usable = {r["run_id"]: r["volume"] for r in rows}
    out = []
    for rec in records:
        volume = usable.get(str(rec.get("run_id") or ""))
        out.append(rec if rec.get("volume") == volume else {**rec, "volume": volume})
    return out


def _window(rows: list[dict]) -> dict:
    """The cost reading of one window of runs: what a run of it typically cost, and how much
    of that reading rests on figures the accounting defect understated.
    """
    volumes = [r["volume"] for r in rows if r["volume"] is not None]
    notionals = [r["notional_cost"] for r in rows if r["notional_cost"] is not None]
    understated = [r for r in rows if r["understated"]]
    return {"runs": len(rows),
            "measured": len(volumes),
            "volume_median": _median(volumes),
            "volume_max": max(volumes) if volumes else 0.0,
            "notional_median": _median(notionals),
            "notional_priced": len(notionals),
            "turns_median": _median([float(r["turns"]) for r in rows]),
            "understated_runs": len(understated),
            "trustworthy": not understated}


def cost_profile(routines_home: Path, slug: str, *,
                 window: int = REGRESSION_WINDOW) -> dict:
    """What a run of `slug` costs: the typical run, the recent window against the one before
    it, and the trend verdict with what it compared.

    The two windows are the SAME pair `recent_trend` judges — the last `window` runs against
    the `window` before them, legs folded first — so `trend.flagged` and the medians beside it
    describe one comparison, not two that happen to be nearby. `mixed_accounting` is the key
    a consumer must read before it renders any of these medians: it names the side whose rows
    the R3112 accounting defect understated, and a comparison across that boundary measures
    the fix as much as the routine.

    Shape: {slug, runs, typical, baseline, recent, trend, mixed_accounting, integrity}.
    """
    records = usage_runs(Path(routines_home))
    depth0 = [rec for rec in records if not rec.get("depth")]
    mine = live_runs(Path(routines_home), depth0, slug)
    children = _child_volume(records)
    rows = [_run_row(rec, children) for rec in mine]

    recent_rows = rows[-window:]
    baseline_rows = rows[:-window][-window:] if len(rows) > window else []
    recent, baseline = _window(recent_rows), _window(baseline_rows)

    sides = [name for name, side in (("recent", recent), ("baseline", baseline))
             if side["understated_runs"]]
    understated_total = sum(1 for r in rows if r["understated"])
    return {
        "slug": slug,
        "runs": len(rows),
        # every run the routine has, not just the compared windows: the figure a budget is
        # set in, and the one a single flagged window should never be mistaken for
        "typical": _window(rows),
        "baseline": {**baseline, "runs_detail": baseline_rows},
        "recent": {**recent, "runs_detail": recent_rows},
        # verbatim from the flag's own read model — never re-derived here
        "trend": recent_trend(_sanitized(mine, rows), window=window),
        "mixed_accounting": sides,
        "integrity": {
            "understated_runs": understated_total,
            "checked_runs": len(rows),
            "with_children": sum(1 for r in rows if r["child_volume"]),
            "detail": (
                f"{understated_total} of {len(rows)} runs report less volume than their own "
                f"child rows sum to (>{UNDERSTATEMENT_SLACK:.0%} short) — the accounting "
                "defect closed in 0.494.0 (R3112). Their cost is a floor, not a figure; a "
                "median spanning them and later runs measures the fix too."
            ) if understated_total else (
                f"all {len(rows)} runs account for their own child rows; no figure here is "
                "understated by R3112."
            ),
        },
    }
