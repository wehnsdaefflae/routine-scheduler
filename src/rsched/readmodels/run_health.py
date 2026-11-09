"""Per-routine health bucketed by RECIPE VERSION, plus two deterministic regression flags.

The product claims routines improve through use (the routine-improver edits recipes
directly) — this read-model closes the loop: every run is attributed to the recipe
version that produced it (recipes.current_recipe_commit, stamped by the engine into
status.json and the workflow-usage record), so a recipe change that quietly degrades a
routine shows up as numbers, not as transcript archaeology.

Source: the durable workflow-usage stream (depth-0 records — run dirs fall to retention,
the stream survives) joined with the routine dir's recipe-commit series (recipes.
recipe_log). Records that predate the recipe_commit field are attributed BY DATE — the
newest recipe commit not after the run — and reported as `inferred` (pre-stamp history
was committed at the NEXT run's end, so date attribution can be off by one run around
old recipe changes; honest, not exact).

The regression heuristic is deliberately simple thresholds — no statistics libraries:
window medians and rate deltas over the runs just before/after the newest recipe change.
Every constant carries its reason. The SAME thresholds are applied a second way, keyed on
time rather than on version (`recent_trend`), because a library rule revision reaches every
holder at once and moves no recipe commit, so the version-keyed flag is structurally blind
to it. Both FLAG only: what consumes them is a routine's development view (Changes → the
routine, which carries the version table and the banner), the one development line on the
production routine page (`recipe_regression`), the health payload and routine-improver's
target ORDER (its `orient` stage flags a candidate on the same two window ratios, and
`select-targets` puts the flagged ones first — a sweep spends its hour where the numbers
moved). Reverting stays the user's one click, never automatic.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..config import ServerConfig
from ..recipes import recipe_log
from . import health_reads, library_reads, memo
from .incarnations import live_runs
from .stamps import instant
from .usage_stream import fold_legs, reading, usage_runs

# --- regression heuristic constants (each with its MEASURED number) -----------------
# All six measured against this instance's own stream 2026-11-07 (R3245): 4,877 records →
# 1,997 folded depth-0 runs over 227 actors, as adjacent before/after window pairs of ONE
# actor stepped by one run — NOTHING changed between the halves, so every figure here is
# ordinary variation and every flag it reports a FALSE one. None of the six moved; five of
# them stopped being plausible prose. Re-measure: scheduler-builder scripts/regression_constants.
# Runs on each side of the newest recipe change; 5 ≈ a week of a daily routine. MEASURED: the
# noise band hardly moves with the window (volume-ratio p95 2.48/2.51/2.59 at 3/5/7, turns
# 1.82/1.79/1.83; false flags 7.5%/9.3%/8.4%) — a wider window buys no quiet.
REGRESSION_WINDOW = 5
# Below 3 runs a comparison is a coin flip. MEASURED, and load-bearing in a way nobody had
# written down: at a window of 2 ONE failure is a fail-rate delta of 0.5, which clears
# FAIL_RATE_JUMP by itself, and the ordinary tails widen (turns max 5.0→6.98×, volume
# 8.29→10.14×). This floor is what stops a single flaky run being a regression.
MIN_RUNS = 3
# MEASURED: ordinary 5-vs-5 deltas run median 0.0, p90 0.2, p95 0.2, p99 0.4, max 0.6 — +0.4
# sits at the p99 and flags 40 of 1,412 unchanged windows (2.8%). It is also the only value
# distinct from 0.2 (a 5-run fail rate moves in 0.2 steps): 0.3 flags the same 40, 0.2 flags 201.
FAIL_RATE_JUMP = 0.4
# Turn/volume medians must grow ≥ BALLOON_RATIO× AND by an absolute floor to flag
# "ballooning": the ratio alone would flag a 2→3-turn routine (noise), the floor alone would
# miss a small routine tripling. VOLUME is the whole context the model processed (usage_log:
# fresh + output + cache reads + cache writes) — `tokens` leaves the cache out, so a routine
# re-reading a 400k prefix every turn never ballooned by it.
# 2.7 is MEASURED (D181, operator 2026-10-15): the p95 of adjacent-window variation. 1063
# token windows (2026-10-13) gave median 1.04, p95 2.73, max 7.16 — the former 1.5 sat INSIDE
# that spread, 20.4% of unchanged windows clearing it. Re-measured on VOLUME 2026-10-17 (740
# windows) p95 2.51, and CONFIRMED 2026-11-07 (1,110 windows): median 1.09, p75 1.40, p90
# 1.99, p95 2.51, max 5.88 — unmoved in three weeks. The regression it exists for is 6.8×
# (49,409 → 338,024 tokens, self-audit 2026-09). Lowering it under 2.7 re-enters the measured
# band; tests/test_balloon_ratio.py fails if it is.
BALLOON_RATIO = 2.7
# MEASURED and INERT: every candidate from 3 to 13 flags the same 4 windows of 1,412 — no pair
# clears 2.7× turns with a delta under 13 (ordinary |delta| median 13). The ratio decides turns
# alone today; this floor costs nothing and still guards the 1→3-turn case it cannot see.
TURNS_FLOOR = 5
# MEASURED and load-bearing, unlike TURNS_FLOOR: 1M flags 98 unchanged windows, 2M 94, 3M 87,
# 5M 71, 10M 43. 2M is still the median ABSOLUTE adjacent change — 2,217,034 over 1,110 windows
# (2026-11-07) against D181's 2,179,949 over 740. It is also the WHOLE decision for the 66 of
# 1,412 windows whose BEFORE half has no volume reading: growth from nothing satisfies any
# ratio, so only this floor stands there.
VOLUME_FLOOR = 2_000_000

# recipe_log depth for bucketing — far beyond any real routine's recipe-change count
_LOG_LIMIT = 500


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _readings(recs: list[dict], field: str) -> list[float]:
    """The usable values of one stream field — an unmeasured OR malformed row is NO reading.

    Both were `float(rec[field])` behind an `is not None` guard, which raises TypeError straight
    out of this read model on a row whose number was written as a dict or a stray string (R3138).
    `fold_legs` sanitizes only runs with SEVERAL legs ("a run with one leg keeps its raw values"),
    so a single-leg row reaches here exactly as it was written. Four readers fold this stream —
    the production routine page's development line, `GET /routines/{slug}/health`, the
    routine-improver's target order and run-end telemetry (`_log_cost_trend`) — so the skip
    belongs here rather than in each caller, and a malformed row may not become a 0 either: 0 is
    a reading, and one of them drags a median hard enough to fire a flag by itself.
    """
    return [v for v in (reading(rec.get(field)) for rec in recs) if v is not None]


def _stream_runs(server: ServerConfig, slug: str) -> list[dict]:
    """This routine's depth-0 RUNS (`usage_runs` — the legs already folded), in chronological
    order of their first leg — the routine's OWN: an archived routine that held the slug before
    it is another routine (readmodels/incarnations.py), and its runs would read as this one's
    past in every bucket, the regression and the trend.
    """
    return live_runs(server.routines_home,
                     [rec for rec in usage_runs(server.routines_home) if not rec.get("depth")],
                     slug)


def _empty_bucket(version: dict, *, current: bool) -> dict:
    return {**version, "current": current, "runs": 0, "ok": 0, "partial": 0, "failed": 0,
            "aborted": 0, "fail_rate": None, "turns_median": 0, "volume_median": 0,
            "asks_deferred": 0, "inferred_runs": 0, "first_ts": "", "last_ts": "",
            "_turns": [], "_volume": []}


def _assign(rec: dict, dated: list[tuple[datetime | None, str]]) -> tuple[str | None, bool]:
    """(version commit, inferred?) for one record. Exact when the record carries the
    engine's recipe_commit stamp; date-mapped (newest version not after the run) for
    pre-stamp records; None = unattributable (no versions at all). `dated` is the version
    series newest first, each commit beside its date read once as an instant.
    """
    stamped = rec.get("recipe_commit")
    if stamped:
        return str(stamped), False
    if not dated:
        return None, False
    ts = instant(rec.get("ts"))
    if ts is not None:
        for when, commit in dated:
            if when is not None and when <= ts:
                return commit, True
    return dated[-1][1], True   # predates every known version → the oldest


def regression_flag(before: list[dict], after: list[dict], *,
                    window: int = REGRESSION_WINDOW) -> dict:
    """The deterministic heuristic: are the (≤window) runs after a recipe change clearly
    worse than the (≤window) runs before it? Pure over usage records; every threshold is
    a module constant with a stated reason. Returns {evaluated, flagged, reasons,
    before, after} — `reasons` name the numbers so the flag is auditable.
    """
    before, after = fold_legs(before)[-window:], fold_legs(after)[:window]

    def profile(recs: list[dict]) -> dict:
        ok = sum(1 for r in recs if r.get("status") == "ok")
        return {"runs": len(recs),
                "fail_rate": round(1 - ok / len(recs), 3) if recs else None,
                "turns_median": _median([reading(r.get("turns")) or 0 for r in recs]),
                "volume_median": _median(_readings(recs, "volume"))}

    b, a = profile(before), profile(after)
    if len(before) < MIN_RUNS or len(after) < MIN_RUNS:
        return {"evaluated": False, "flagged": False, "reasons": [], "before": b, "after": a}
    reasons = []
    if (a["fail_rate"] or 0) - (b["fail_rate"] or 0) >= FAIL_RATE_JUMP:
        reasons.append(f"fail rate jumped {b['fail_rate']:.0%} → {a['fail_rate']:.0%} "
                       f"(threshold: +{FAIL_RATE_JUMP:.0%})")
    if (a["turns_median"] >= BALLOON_RATIO * b["turns_median"]
            and a["turns_median"] - b["turns_median"] >= TURNS_FLOOR):
        reasons.append(f"median turns ballooned {b['turns_median']:g} → "
                       f"{a['turns_median']:g} (≥{BALLOON_RATIO}× and +{TURNS_FLOOR})")
    if (a["volume_median"] >= BALLOON_RATIO * b["volume_median"]
            and a["volume_median"] - b["volume_median"] >= VOLUME_FLOOR):
        reasons.append(f"median volume ballooned {b['volume_median']:,.0f} → "
                       f"{a['volume_median']:,.0f} (≥{BALLOON_RATIO}× and "
                       f"+{VOLUME_FLOOR:,})")
    return {"evaluated": True, "flagged": bool(reasons), "reasons": reasons,
            "before": b, "after": a}


def recent_trend(records: list[dict], *, window: int = REGRESSION_WINDOW) -> dict:
    """The same heuristic keyed on TIME instead of on recipe version: the last `window`
    depth-0 runs against the `window` before them, whatever recipe produced either side.

    The version-keyed flag above cannot see the most expensive kind of regression there is.
    Its buckets key on `recipe_commit`, so it moves only when THIS routine's recipe moves —
    and a library RULE revision reaches every holder at once without touching a single
    recipe (error-recovery hit 30 routines on 2026-09-16, web-research 136 on 09-18). The
    measured case: self-audit's weekly medians went 49,409 tokens / 87% ok to 338,024 / 43%
    and back to 95,782, with no recipe change of any kind under them, so `versions` held one
    bucket, `regression` never evaluated, and nothing on the instance said a word.

    Same thresholds as `regression_flag` deliberately — one heuristic, two keys — so a reader
    comparing the two reads a difference in WHAT changed, never in how it was judged.

    The legs are folded BEFORE the tail is sliced, because `window` is a count of RUNS — that is
    what every constant above states its reason in ("a single flaky run is only 20% of the
    sample", "2 extra failures in a 5-run window"). Slicing first and letting `regression_flag`
    fold inside the cut window silently halves the sample: 2,543 depth-0 records on this
    instance covered 1,256 runs, and the error compounds with cost, because a heavy run finishes
    more often and so eats more of its own window. Measured 2026-09-25, before this was fixed:
    46 of 53 actors' "5-run" windows were not 5 runs, `weightloss` (173 legs / 67 runs) was
    compared 2-against-1 and therefore never judged at all, while three routines were flagged on
    samples smaller than MIN_RUNS.
    """
    folded = fold_legs(records)
    return {**regression_flag(folded[:-window], folded[-window:], window=window),
            "window": window}


def routine_health(server: ServerConfig, routine_dir: Path, slug: str) -> dict:
    """The health payload of a routine's development view (Changes → the routine): one bucket
    per recipe version that has runs (plus the current version even when unproven), newest
    first; `regression`, the evaluation of the newest recipe change; `trend`, the same
    evaluation keyed on time instead (what a library revision moves and a recipe commit does
    not); and `endings`, how this routine's partial finishes actually ended — including
    `corrected`, the legs whose run was resumed and finished ok (F637/F703). Conversations and
    other unversioned dirs degrade to a single `untracked` bucket — and still get a `trend`,
    which needs no versions at all.
    """
    by_version = _by_version(server, routine_dir, slug)
    return {"slug": slug,
            "versions": by_version["versions"],
            "untracked": by_version["untracked"],
            "regression": by_version["regression"],
            "trend": recent_trend(by_version["records"]),
            "endings": health_reads.budget_endings(server, slug),
            "cautions": cautions(server, routine_dir),
            "tracked": by_version["tracked"]}


def recipe_regression(server: ServerConfig, routine_dir: Path, slug: str) -> dict:
    """The version-keyed evaluation of the newest recipe change, alone — what the production
    routine page's one development line reads (api_changes.routine_summary) without paying for
    the cautions and the health stream the full payload carries.
    """
    return _by_version(server, routine_dir, slug)["regression"]


def _by_version(server: ServerConfig, routine_dir: Path, slug: str) -> dict:
    """The runs bucketed by the recipe version that produced them, and the regression of the
    newest version against the runs just before it.
    """
    # One `git log` subprocess, and this is read on every development-view open and by the
    # routine page's development line — cached against the repo's reflog, which every commit
    # appends to.
    versions = memo.memoized(f"recipe-log:{routine_dir}",
                             [routine_dir / ".git" / "logs" / "HEAD"],
                             lambda: recipe_log(routine_dir, limit=_LOG_LIMIT))
    # RUNS, not legs, because every consumer below counts runs: the per-version buckets
    # (`runs`, the status tallies, both medians), the version-keyed regression, and the
    # time-keyed trend. A continued run appends a further record under the same run_id, so
    # tallying raw legs inflated every bucket's `runs` by the leg ratio — 2.02 legs per run
    # on this instance, measured 2026-09-25 — and a run that finished `partial` and was then
    # continued to `ok` was tallied into BOTH columns of its own bucket.
    records = _stream_runs(server, slug)

    buckets: dict[str, dict] = {}
    for i, v in enumerate(versions):
        buckets[v["commit"]] = _empty_bucket(v, current=i == 0)
    untracked = _empty_bucket({"commit": None, "short": "", "date": "", "subject": ""},
                              current=False)

    ordered: list[tuple[dict, str | None]] = []   # (record, bucket key) in run order
    dated = [(instant(v["date"]), v["commit"]) for v in versions]
    for rec in records:
        commit, inferred = _assign(rec, dated)
        if commit is not None and commit not in buckets:
            # stamped with a commit outside the log window (or rewritten history):
            # still a real version — give it its own bucket so nothing is silently lost
            buckets[commit] = _empty_bucket(
                {"commit": commit, "short": commit[:9], "date": "",
                 "subject": "(not in the recipe log)"}, current=False)
        b = buckets[commit] if commit is not None else untracked
        b["runs"] += 1
        status = str(rec.get("status") or "")
        b[status if status in ("ok", "partial", "failed", "aborted") else "failed"] += 1
        b["_turns"].append(reading(rec.get("turns")) or 0)
        b["_volume"].extend(_readings([rec], "volume"))   # unmeasured OR malformed: no reading
        b["asks_deferred"] += int(rec.get("asks_deferred") or 0)
        b["inferred_runs"] += 1 if inferred else 0
        ts = str(rec.get("ts") or "")
        b["first_ts"] = b["first_ts"] or ts
        b["last_ts"] = ts
        ordered.append((rec, commit))

    for b in [*buckets.values(), untracked]:
        b["fail_rate"] = round(1 - b["ok"] / b["runs"], 3) if b["runs"] else None
        b["turns_median"] = _median(b.pop("_turns"))
        b["volume_median"] = _median(b.pop("_volume"))

    # regression: runs under the newest version vs the runs immediately before them
    regression: dict = {"evaluated": False, "flagged": False, "reasons": []}
    if versions:
        newest = versions[0]["commit"]
        idx = [i for i, (_, c) in enumerate(ordered) if c == newest]
        if idx:
            after = [ordered[i][0] for i in idx]
            before = [r for r, c in ordered[:idx[0]] if c != newest]
            regression = {**regression_flag(before, after),
                          "commit": newest, "short": versions[0]["short"],
                          "subject": versions[0]["subject"]}

    return {"versions": [b for b in buckets.values() if b["runs"] or b["current"]],
            "untracked": untracked if untracked["runs"] else None,
            "regression": regression, "records": records, "tracked": bool(versions)}


def cautions(server: ServerConfig, routine_dir: Path) -> dict:
    """What the between-turn feed has actually been doing here: every reminder in force with
    this routine's own tally, and every rule assist that has fired, with its own tally too.

    These two stores are the feature's only evidence and they were WRITE-ONLY from the
    console — engine-written JSON under `state/`, read by nothing a person opens. A caution
    that fires on every run and is labelled `could_not` every time is a bad pattern, and
    reviewing precision is the whole reason the tallies are kept; without a surface, the
    review had to be done by reading files over ssh.

    Degrades to empty rather than raising: this rides the health payload, and a routine whose
    stores are missing or hand-broken is a routine with nothing to show, not a broken page.
    """
    from .. import assist_stats, remind_dead
    from .. import reminders as reminders_mod

    try:
        local, gstats = reminders_mod.load_local(routine_dir)
        shared = reminders_mod.load_global(server.reminders_home, gstats)
    except OSError:
        local, shared = [], []
    # the four outcome labels beside `fires`, in the field names a reminder's tally keeps
    # (D239): at the turn-costing `hold` rung a bare count cannot say what the turn bought
    tallies = assist_stats.read(routine_dir)
    known = {a.key: a for a in library_reads.assists(server.rules_home)}
    rows = []
    for key, stats in sorted(tallies.items(), key=lambda kv: (-kv[1]["fires"], kv[0])):
        a, n = known.get(key), stats["fires"]
        rows.append({"key": key, "fires": n, "stats": stats,
                     "rule": a.rule if a else key.split(":")[0],
                     "moment": a.moment if a else "",
                     "payload": a.payload if a else "",
                     "predicate": a.predicate if a else "",
                     # A key with no live assist behind it is the interesting row, not a
                     # corrupt one: the rule was revised or unbound and the tally outlived it.
                     "retired": a is None})
    return {"reminders": remind_dead.decorate(routine_dir, [*local, *shared]),  # D231 annotation
            "labels": list(reminders_mod.LABELS),
            "assists": rows}
