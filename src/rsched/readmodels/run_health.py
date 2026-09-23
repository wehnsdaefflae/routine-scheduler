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
to it. Both FLAG only: what consumes them is the routine page, the health payload and
routine-improver's target ORDER (its `orient` stage flags a candidate on the same two
window ratios, and `select-targets` puts the flagged ones first — a sweep spends its hour
where the numbers moved). Reverting stays the user's one click, never automatic.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..config import ServerConfig
from ..recipes import recipe_log
from . import health_stream, library_reads, memo

# --- regression heuristic constants (each with its reason) ---------------------------
# Runs compared on each side of the newest recipe change. 5 ≈ one week of a daily
# routine — long enough that a single flaky run is only 20% of the sample, short enough
# to catch a degradation before it burns a month of spend.
REGRESSION_WINDOW = 5
# Below 3 runs on either side a comparison is a coin flip, not evidence — don't judge.
MIN_RUNS = 3
# Fail-rate must jump by ≥ 0.4 (2 extra failures in a 5-run window) to flag: one extra
# bad run in 5 (+0.2) is within normal flake; two is a pattern.
FAIL_RATE_JUMP = 0.4
# Turn/token medians must grow ≥ 1.5× AND by an absolute floor to flag "ballooning":
# the ratio alone would flag a 2→3-turn routine (noise), the floor alone would miss a
# small routine tripling. Floors: 5 turns is a real extra work phase; 20k tokens is
# roughly a full extra context-load at typical per-turn sizes.
BALLOON_RATIO = 1.5
TURNS_FLOOR = 5
TOKENS_FLOOR = 20_000

# recipe_log depth for bucketing — far beyond any real routine's recipe-change count
_LOG_LIMIT = 500


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _parse_dt(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.now().astimezone().tzinfo)


def _stream_records(server: ServerConfig, slug: str) -> list[dict]:
    """This routine's depth-0 usage records, in append (chronological) order."""
    from .usage_stream import usage_records

    return [rec for rec in usage_records(server.routines_home)
            if not rec.get("depth") and rec.get("routine") == slug]


def _empty_bucket(version: dict, *, current: bool) -> dict:
    return {**version, "current": current, "runs": 0, "ok": 0, "partial": 0, "failed": 0,
            "aborted": 0, "fail_rate": None, "turns_median": 0, "tokens_median": 0,
            "asks_deferred": 0, "inferred_runs": 0, "first_ts": "", "last_ts": "",
            "_turns": [], "_tokens": []}


def _assign(rec: dict, versions: list[dict]) -> tuple[str | None, bool]:
    """(version commit, inferred?) for one record. Exact when the record carries the
    engine's recipe_commit stamp; date-mapped (newest version not after the run) for
    pre-stamp records; None = unattributable (no versions at all).
    """
    stamped = rec.get("recipe_commit")
    if stamped:
        return str(stamped), False
    if not versions:
        return None, False
    ts = _parse_dt(str(rec.get("ts") or ""))
    if ts is not None:
        for v in versions:  # newest first
            vd = _parse_dt(v["date"])
            if vd is not None and vd <= ts:
                return v["commit"], True
    return versions[-1]["commit"], True   # predates every known version → the oldest


def regression_flag(before: list[dict], after: list[dict], *,
                    window: int = REGRESSION_WINDOW) -> dict:
    """The deterministic heuristic: are the (≤window) runs after a recipe change clearly
    worse than the (≤window) runs before it? Pure over usage records; every threshold is
    a module constant with a stated reason. Returns {evaluated, flagged, reasons,
    before, after} — `reasons` name the numbers so the flag is auditable.
    """
    before, after = before[-window:], after[:window]

    def profile(recs: list[dict]) -> dict:
        ok = sum(1 for r in recs if r.get("status") == "ok")
        return {"runs": len(recs),
                "fail_rate": round(1 - ok / len(recs), 3) if recs else None,
                "turns_median": _median([float(r.get("turns") or 0) for r in recs]),
                "tokens_median": _median([float(r.get("tokens") or 0) for r in recs])}

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
    if (a["tokens_median"] >= BALLOON_RATIO * b["tokens_median"]
            and a["tokens_median"] - b["tokens_median"] >= TOKENS_FLOOR):
        reasons.append(f"median tokens ballooned {b['tokens_median']:,.0f} → "
                       f"{a['tokens_median']:,.0f} (≥{BALLOON_RATIO}× and "
                       f"+{TOKENS_FLOOR:,})")
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
    """
    return {**regression_flag(records[:-window], records[-window:], window=window),
            "window": window}


def routine_health(server: ServerConfig, routine_dir: Path, slug: str) -> dict:
    """The routine page's health payload: one bucket per recipe version that has runs (plus
    the current version even when unproven), newest first; `regression`, the evaluation of
    the newest recipe change; `trend`, the same evaluation keyed on time instead (what a
    library revision moves and a recipe commit does not); and `endings`, how this routine's
    partial finishes actually ended. Conversations and other unversioned dirs degrade to a
    single `untracked` bucket — and still get a `trend`, which needs no versions at all.
    """
    # One `git log` subprocess, and this payload is fetched on every routine-page open —
    # cached against the repo's reflog, which every commit appends to.
    versions = memo.memoized(f"recipe-log:{routine_dir}",
                             [routine_dir / ".git" / "logs" / "HEAD"],
                             lambda: recipe_log(routine_dir, limit=_LOG_LIMIT))
    records = _stream_records(server, slug)

    buckets: dict[str, dict] = {}
    for i, v in enumerate(versions):
        buckets[v["commit"]] = _empty_bucket(v, current=i == 0)
    untracked = _empty_bucket({"commit": None, "short": "", "date": "", "subject": ""},
                              current=False)

    ordered: list[tuple[dict, str | None]] = []   # (record, bucket key) in run order
    for rec in records:
        commit, inferred = _assign(rec, versions)
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
        b["_turns"].append(float(rec.get("turns") or 0))
        b["_tokens"].append(float(rec.get("tokens") or 0))
        b["asks_deferred"] += int(rec.get("asks_deferred") or 0)
        b["inferred_runs"] += 1 if inferred else 0
        ts = str(rec.get("ts") or "")
        b["first_ts"] = b["first_ts"] or ts
        b["last_ts"] = ts
        ordered.append((rec, commit))

    for b in [*buckets.values(), untracked]:
        b["fail_rate"] = round(1 - b["ok"] / b["runs"], 3) if b["runs"] else None
        b["turns_median"] = _median(b.pop("_turns"))
        b["tokens_median"] = _median(b.pop("_tokens"))

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

    shown = [b for b in buckets.values() if b["runs"] or b["current"]]
    return {"slug": slug,
            "versions": shown,
            "untracked": untracked if untracked["runs"] else None,
            "regression": regression,
            "trend": recent_trend(records),
            "endings": health_stream.budget_endings(server, slug),
            "cautions": cautions(server, routine_dir),
            "tracked": bool(versions)}


def cautions(server: ServerConfig, routine_dir: Path) -> dict:
    """What the between-turn feed has actually been doing here: every reminder in force with
    this routine's own tally, and every rule assist that has fired, with its count.

    These two stores are the feature's only evidence and they were WRITE-ONLY from the
    console — engine-written JSON under `state/`, read by nothing a person opens. A caution
    that fires on every run and is labelled `could_not` every time is a bad pattern, and
    reviewing precision is the whole reason the tallies are kept; without a surface, the
    review had to be done by reading files over ssh.

    Degrades to empty rather than raising: this rides the health payload, and a routine whose
    stores are missing or hand-broken is a routine with nothing to show, not a broken page.
    """
    from .. import assists as assists_mod
    from .. import reminders as reminders_mod
    from ..paths import read_json

    try:
        local, gstats = reminders_mod.load_local(routine_dir)
        shared = reminders_mod.load_global(server.reminders_home, gstats)
    except OSError:
        local, shared = [], []
    fired = read_json(assists_mod.state_path(routine_dir), {})
    counts = fired if isinstance(fired, dict) else {}
    known = {a.key: a for a in library_reads.assists(server.rules_home)}
    rows = []
    for key, n in sorted(counts.items(), key=lambda kv: (-int(kv[1] or 0), kv[0])):
        a = known.get(key)
        rows.append({"key": key, "fires": int(n or 0),
                     "rule": a.rule if a else key.split(":")[0],
                     "moment": a.moment if a else "",
                     "payload": a.payload if a else "",
                     "predicate": a.predicate if a else "",
                     # A key with no live assist behind it is the interesting row, not a
                     # corrupt one: the rule was revised or unbound and the tally outlived it.
                     "retired": a is None})
    return {"reminders": [r.as_record() for r in [*local, *shared]],
            "labels": list(reminders_mod.LABELS),
            "assists": rows}
