"""Health-by-recipe-version (rsched.run_health): the deterministic regression heuristic —
every threshold constant exercised on both sides — and the bucketing read-model over the
durable usage stream + a real git recipe history.
"""

import json
from datetime import UTC, datetime

from conftest import git_in
from rsched.config import ServerConfig
from rsched.readmodels.run_health import (
    BALLOON_RATIO,
    FAIL_RATE_JUMP,
    MIN_RUNS,
    REGRESSION_WINDOW,
    TOKENS_FLOOR,
    TURNS_FLOOR,
    recent_trend,
    regression_flag,
    routine_health,
)


def _rec(status="ok", turns=10, tokens=5000, **extra):
    return {"status": status, "turns": turns, "tokens": tokens, **extra}


# ---- the heuristic, constant by constant ---------------------------------------------


def test_not_evaluated_below_min_runs():
    """Fewer than MIN_RUNS on either side is a coin flip, not evidence — never judged."""
    ok = [_rec() for _ in range(5)]
    assert not regression_flag(ok[: MIN_RUNS - 1], ok)["evaluated"]
    assert not regression_flag(ok, ok[: MIN_RUNS - 1])["evaluated"]
    assert regression_flag(ok[:MIN_RUNS], ok[:MIN_RUNS])["evaluated"]


def test_fail_rate_jump_threshold():
    """One extra failure in 5 (+0.2) is flake; two (+0.4, the constant) is a pattern."""
    before = [_rec() for _ in range(5)]
    one_bad = [_rec("failed"), *[_rec() for _ in range(4)]]
    two_bad = [_rec("failed"), _rec("aborted"), *[_rec() for _ in range(3)]]
    assert not regression_flag(before, one_bad)["flagged"]
    verdict = regression_flag(before, two_bad)
    assert verdict["flagged"] and "fail rate" in verdict["reasons"][0]
    assert verdict["after"]["fail_rate"] - verdict["before"]["fail_rate"] >= FAIL_RATE_JUMP


def test_partial_counts_as_not_ok():
    """A budget-stopped (partial) run is a degradation — only status ok counts as ok."""
    before = [_rec() for _ in range(5)]
    after = [_rec("partial"), _rec("partial"), _rec(), _rec(), _rec()]
    assert regression_flag(before, after)["flagged"]


def test_turns_balloon_needs_ratio_and_floor():
    before_small = [_rec(turns=2) for _ in range(5)]
    after_small = [_rec(turns=4) for _ in range(5)]      # 2× but +2 < TURNS_FLOOR: noise
    assert not regression_flag(before_small, after_small)["flagged"]
    before = [_rec(turns=10) for _ in range(5)]
    after = [_rec(turns=10 * BALLOON_RATIO + TURNS_FLOOR) for _ in range(5)]
    verdict = regression_flag(before, after)
    assert verdict["flagged"] and "turns ballooned" in verdict["reasons"][0]
    # absolute growth over the floor but ratio under BALLOON_RATIO: gradual, not a flag
    before20 = [_rec(turns=20) for _ in range(5)]
    grown_but_under_ratio = [_rec(turns=20 * BALLOON_RATIO - 1) for _ in range(5)]
    assert not regression_flag(before20, grown_but_under_ratio)["flagged"]


def test_tokens_balloon_needs_ratio_and_floor():
    before = [_rec(tokens=50_000) for _ in range(5)]
    flagged = regression_flag(before, [_rec(tokens=50_000 * BALLOON_RATIO) for _ in range(5)])
    assert flagged["flagged"] and "tokens ballooned" in flagged["reasons"][0]
    # ratio met on a tiny base but the absolute growth is under the floor: noise
    small = [_rec(tokens=1_000) for _ in range(5)]
    assert not regression_flag(small, [_rec(tokens=1_000 + TOKENS_FLOOR - 1)
                                       for _ in range(5)])["flagged"]


def test_windows_slice_last_before_and_first_after():
    """Only the REGRESSION_WINDOW runs adjacent to the change are compared: ancient
    failures before, and drift long after, don't blur the verdict."""
    before = [_rec("failed") for _ in range(10)] + [_rec() for _ in range(REGRESSION_WINDOW)]
    after = [_rec() for _ in range(REGRESSION_WINDOW)] + [_rec("failed") for _ in range(10)]
    verdict = regression_flag(before, after)
    assert verdict["evaluated"] and not verdict["flagged"]
    assert verdict["before"]["runs"] == verdict["after"]["runs"] == REGRESSION_WINDOW


def test_recent_trend_compares_the_last_two_windows():
    """The time-keyed flag is version-blind: it slices the tail into two windows and
    applies the SAME thresholds, which is the only way a library-rule revision — one that
    moves no recipe commit anywhere — can register at all."""
    records = [_rec(tokens=10_000) for _ in range(REGRESSION_WINDOW)] + \
              [_rec(tokens=200_000) for _ in range(REGRESSION_WINDOW)]
    verdict = recent_trend(records)
    assert verdict["evaluated"] and verdict["flagged"]
    assert verdict["window"] == REGRESSION_WINDOW
    assert any("tokens ballooned" in r for r in verdict["reasons"])
    assert verdict["before"]["runs"] == verdict["after"]["runs"] == REGRESSION_WINDOW


def test_recent_trend_needs_two_windows_of_runs():
    """A routine with only one window of history is not judged — MIN_RUNS on both sides."""
    assert not recent_trend([_rec() for _ in range(REGRESSION_WINDOW)])["evaluated"]


def test_recent_trend_window_counts_runs_not_legs():
    """The window is REGRESSION_WINDOW *runs*, and a run that finishes several times must
    not spend the window on its own bookkeeping.

    Every constant in this module states its reason in RUNS — the window is "5 ≈ one week of
    a daily routine … a single flaky run is only 20% of the sample", the fail-rate jump is
    "2 extra failures in a 5-run window", MIN_RUNS is "below 3 runs on either side a
    comparison is a coin flip". A run the operator continues, or one that resumes after a
    restart, appends a further usage record under the SAME run_id, and on this instance
    2,543 depth-0 records covered 1,256 runs — 2.02 legs per run. So slicing the tail before
    folding those legs hands the heuristic a sample half the size it reports, and the error
    runs the wrong way: the more a run costs, the more often it finishes, and the more of the
    window it eats. Measured 2026-09-25, `weightloss` (173 legs / 67 runs) got a 2-vs-1
    comparison and was never judged at all, while three routines were flagged on samples
    below MIN_RUNS.

    Ten runs of two legs each: a leg-sliced window sees 5 legs = 2 runs and cannot judge;
    a run-sliced window sees 5 runs on each side and judges the token balloon.
    """
    records = []
    for i in range(10):
        tokens = 10_000 if i < 5 else 200_000
        # two legs under ONE run_id: `turns` is cumulative, `tokens` per-leg (fold_legs)
        records.append(_rec(run_id=f"r{i}", turns=40, tokens=tokens // 2))
        records.append(_rec(run_id=f"r{i}", turns=80, tokens=tokens // 2))

    verdict = recent_trend(records)
    assert verdict["before"]["runs"] == REGRESSION_WINDOW, (
        f"the 'before' side holds {verdict['before']['runs']} runs, not "
        f"{REGRESSION_WINDOW} — the window was sliced in legs")
    assert verdict["after"]["runs"] == REGRESSION_WINDOW, (
        f"the 'after' side holds {verdict['after']['runs']} runs, not "
        f"{REGRESSION_WINDOW} — the window was sliced in legs")
    assert verdict["evaluated"], "a routine with 10 real runs of history must be judged"
    assert verdict["flagged"] and any("tokens ballooned" in r for r in verdict["reasons"])
    # the fold summed the per-leg tokens back into whole runs
    assert verdict["before"]["tokens_median"] == 10_000
    assert verdict["after"]["tokens_median"] == 200_000


# ---- the read-model over stream + git -------------------------------------------------


def _git(d, *args, date="2026-07-01T10:00:00+00:00"):
    git_in(d, *args, date=date)


def _setup(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    d = server.routines_home / "gitr"
    d.mkdir(parents=True)
    (d / "main.md").write_text("# v1\n", encoding="utf-8")
    _git(d, "init", "-q")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "scaffold", date="2026-07-01T10:00:00+00:00")
    return server, d


def _stream(server, records):
    control = server.routines_home / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "workflow-usage.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def test_buckets_by_stamp_and_by_date(tmp_path):
    """Stamped records bucket exactly; pre-stamp records are date-attributed (inferred);
    the newest change is regression-evaluated against the runs before it."""
    server, d = _setup(tmp_path)
    from rsched.recipes import recipe_log
    v1 = recipe_log(d)[0]["commit"]
    (d / "main.md").write_text("# v2\n", encoding="utf-8")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "recipe: v2", date="2026-07-10T10:00:00+00:00")
    v2 = recipe_log(d)[0]["commit"]

    records = [  # 3 legacy ok runs under v1 (date-mapped), then 3 stamped failures on v2
        {"routine": "gitr", "run_id": f"gitr:2026070{i}-070000", "depth": 0, "status": "ok",
         "turns": 8, "tokens": 4000, "ts": f"2026-07-0{i}T07:10:00+00:00"}
        for i in (2, 3, 4)
    ] + [
        {"routine": "gitr", "run_id": f"gitr:2026071{i}-070000", "depth": 0,
         "status": "failed", "turns": 9, "tokens": 4100, "recipe_commit": v2,
         "asks_deferred": 2, "ts": f"2026-07-1{i}T07:10:00+00:00"}
        for i in (1, 2, 3)
    ] + [
        # depth>0 and other-routine records must not leak into the buckets
        {"routine": "gitr", "run_id": "gitr:x#sub1", "depth": 1, "status": "ok",
         "turns": 2, "tokens": 100, "ts": "2026-07-13T08:00:00+00:00"},
        {"routine": "other", "run_id": "other:x", "depth": 0, "status": "ok",
         "turns": 2, "tokens": 100, "ts": "2026-07-13T08:00:00+00:00"},
    ]
    _stream(server, records)

    h = routine_health(server, d, "gitr")
    assert h["tracked"] is True
    by_commit = {b["commit"]: b for b in h["versions"]}
    assert by_commit[v1]["runs"] == 3
    assert by_commit[v1]["inferred_runs"] == 3           # date-mapped, honestly marked
    assert by_commit[v1]["ok"] == 3 and by_commit[v1]["fail_rate"] == 0
    assert by_commit[v2]["runs"] == 3
    assert by_commit[v2]["inferred_runs"] == 0           # engine-stamped, exact
    assert by_commit[v2]["failed"] == 3 and by_commit[v2]["fail_rate"] == 1
    assert by_commit[v2]["asks_deferred"] == 6
    assert by_commit[v2]["current"] and not by_commit[v1]["current"]

    reg = h["regression"]
    assert reg["evaluated"] and reg["flagged"] and reg["commit"] == v2
    assert any("fail rate" in r for r in reg["reasons"])


def test_current_version_shown_even_without_runs(tmp_path):
    """A fresh recipe change with zero runs must still appear — 'unproven' is a finding."""
    server, d = _setup(tmp_path)
    _stream(server, [{"routine": "gitr", "run_id": "gitr:a", "depth": 0, "status": "ok",
                      "turns": 5, "tokens": 100, "ts": "2026-07-05T07:00:00+00:00"}])
    (d / "main.md").write_text("# v2\n", encoding="utf-8")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "recipe: v2", date="2026-07-10T10:00:00+00:00")
    h = routine_health(server, d, "gitr")
    current = next(b for b in h["versions"] if b["current"])
    assert current["runs"] == 0
    assert not h["regression"]["evaluated"]


def test_unversioned_dir_degrades_to_untracked(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    d = server.routines_home / "conv"
    d.mkdir(parents=True)
    _stream(server, [{"routine": "conv", "run_id": "conv:a", "depth": 0, "status": "ok",
                      "turns": 3, "tokens": 50, "ts": "2026-07-05T07:00:00+00:00"}])
    h = routine_health(server, d, "conv")
    assert h["tracked"] is False and h["versions"] == []
    assert h["untracked"]["runs"] == 1
    assert not h["regression"]["flagged"]


def test_no_stream_at_all(tmp_path):
    server, d = _setup(tmp_path)
    h = routine_health(server, d, "gitr")
    assert [b["runs"] for b in h["versions"]] == [0]
    assert h["untracked"] is None


def test_payload_carries_the_time_trend_and_the_budget_endings(tmp_path):
    """A cost jump with NO recipe change: `regression` cannot see it (one bucket, nothing
    to compare), `trend` does — and `endings` says a budget forced the partial finishes the
    usage stream files indistinguishably from the ones the model chose."""
    from rsched.readmodels import memo

    server, d = _setup(tmp_path)
    cheap = [{"routine": "gitr", "run_id": f"gitr:c{i}", "depth": 0, "status": "ok",
              "turns": 5, "tokens": 10_000, "ts": f"2026-07-0{i}T07:00:00+00:00"}
             for i in range(1, 6)]
    dear = [{"routine": "gitr", "run_id": f"gitr:d{i}", "depth": 0, "status": "partial",
             "turns": 5, "tokens": 200_000, "ts": f"2026-07-1{i}T07:00:00+00:00"}
            for i in range(1, 6)]
    _stream(server, cheap + dear)
    control = server.routines_home / ".control"
    (control / "health-events.jsonl").write_text(
        json.dumps({"event": "budget_exhausted", "routine": "gitr", "run_id": "gitr:d1",
                    "ts": datetime.now(UTC).isoformat(), "detail": "turns 5/5"}) + "\n",
        encoding="utf-8")
    memo.reset()

    h = routine_health(server, d, "gitr")
    assert not h["regression"]["flagged"]             # one recipe version: nothing to compare
    assert h["trend"]["evaluated"] and h["trend"]["flagged"]
    assert any("tokens ballooned" in r for r in h["trend"]["reasons"])
    assert h["endings"]["budget_exhausted"] == 1 and h["endings"]["run_partial"] == 0


def test_a_continued_run_is_one_run_not_three():
    """Legs of one run are bookkeeping, not cost, and the two halves of a usage record
    disagree about which: `turns` is cumulative across legs while `tokens` is per leg.

    Measured on the live instance 2026-09-23: HALF of all depth-0 records (2,445 over 1,211
    runs) were extra legs, so a five-record window was often two runs plus their legs — and
    folding them dropped the flagged set from 31 routines to 6, all six genuine.
    """
    from rsched.readmodels.run_health import fold_legs

    legs = [
        {"run_id": "r1", "turns": 100, "tokens": 40_000, "cost": 0.1, "status": "ok"},
        {"run_id": "r1", "turns": 148, "tokens": 2_080, "cost": 0.0, "status": "ok"},
        {"run_id": "r2", "turns": 50, "tokens": 9_000, "cost": 0.0, "status": "ok"},
    ]
    folded = fold_legs(legs)
    assert len(folded) == 2
    # the cumulative field takes the LAST leg's value, the per-leg fields SUM
    assert folded[0]["turns"] == 148
    assert folded[0]["tokens"] == 42_080
    assert abs(folded[0]["cost"] - 0.1) < 1e-9
    assert folded[1]["turns"] == 50
    # order is by first appearance — every caller slices these by recency
    assert [r["run_id"] for r in folded] == ["r1", "r2"]


def test_folding_legs_is_what_stops_a_steady_routine_flagging():
    """The live shape that cried wolf: a routine whose real cost never moved, but whose
    window held a short continuation leg."""
    from rsched.readmodels.run_health import recent_trend

    def run(rid, turns, tokens, legs=1):
        out = [{"run_id": rid, "turns": turns, "tokens": tokens, "status": "ok", "depth": 0}]
        if legs > 1:   # a continuation: cumulative turns, a small per-leg token count
            out.append({"run_id": rid, "turns": turns + 2, "tokens": 2_000,
                        "status": "ok", "depth": 0})
        return out

    steady = []
    for i in range(5):
        steady += run(f"a{i}", 100, 50_000)
    for i in range(5):
        steady += run(f"b{i}", 100, 50_000, legs=2)

    assert recent_trend(steady)["flagged"] is False, (
        "a routine whose per-run cost is flat must not flag because its later runs were "
        "continued — that is the alarm everyone learns to ignore")
    # and it must reach that verdict on a full window of RUNS, not be saved by a window too
    # small to judge: a negative assertion alone cannot tell those two apart
    verdict = recent_trend(steady)
    assert verdict["evaluated"], "the verdict must be a judgment, not an abstention"
    assert verdict["before"]["runs"] == verdict["after"]["runs"] == REGRESSION_WINDOW


def test_version_buckets_count_runs_not_legs(tmp_path):
    """The routine page's per-version table reports `runs`, `fail_rate` and both medians —
    and every one of them must be per RUN.

    A continued run appends a further usage record under the same run_id, so tallying raw
    records inflates `runs` (2.02 legs per run on this instance, measured 2026-09-25) and
    drags the medians toward whatever the short bookkeeping leg happened to carry. The
    fail_rate is the worst of them: a partial first leg followed by an ok continuation was
    counted as one failure AND one success in the same bucket.

    Three runs, five legs, one recipe version: the bucket holds 3 runs.
    """
    from rsched.readmodels import memo

    server, d = _setup(tmp_path)
    _stream(server, [
        {"routine": "gitr", "run_id": "gitr:a", "depth": 0, "status": "ok",
         "turns": 10, "tokens": 30_000, "ts": "2026-07-01T07:00:00+00:00"},
        {"routine": "gitr", "run_id": "gitr:a", "depth": 0, "status": "ok",
         "turns": 14, "tokens": 2_000, "ts": "2026-07-01T08:00:00+00:00"},
        {"routine": "gitr", "run_id": "gitr:b", "depth": 0, "status": "ok",
         "turns": 10, "tokens": 30_000, "ts": "2026-07-02T07:00:00+00:00"},
        {"routine": "gitr", "run_id": "gitr:b", "depth": 0, "status": "ok",
         "turns": 12, "tokens": 2_000, "ts": "2026-07-02T08:00:00+00:00"},
        {"routine": "gitr", "run_id": "gitr:c", "depth": 0, "status": "ok",
         "turns": 10, "tokens": 30_000, "ts": "2026-07-03T07:00:00+00:00"},
    ])
    memo.reset()

    h = routine_health(server, d, "gitr")
    bucket = next(b for b in h["versions"] if b["runs"])
    assert bucket["runs"] == 3, (
        f"the bucket reports {bucket['runs']} runs for 3 runs of 5 legs — it counted legs")
    assert bucket["ok"] == 3
    # the per-leg tokens folded back into whole runs, so the median is a run's real cost
    assert bucket["tokens_median"] == 32_000
    # `turns` is cumulative across legs: the last leg's value is the run's total
    assert bucket["turns_median"] == 12


def test_a_continued_partial_is_not_one_failure_and_one_success(tmp_path):
    """The fail_rate defect the fold closes: a run that finished `partial` and was then
    continued to `ok` tallied into BOTH columns of its bucket, so one run moved the fail
    rate by a whole run in each direction at once. Only the final leg's status is the run's
    outcome."""
    from rsched.readmodels import memo

    server, d = _setup(tmp_path)
    _stream(server, [
        {"routine": "gitr", "run_id": "gitr:x", "depth": 0, "status": "partial",
         "turns": 10, "tokens": 20_000, "ts": "2026-07-01T07:00:00+00:00"},
        {"routine": "gitr", "run_id": "gitr:x", "depth": 0, "status": "ok",
         "turns": 18, "tokens": 5_000, "ts": "2026-07-01T09:00:00+00:00"},
    ])
    memo.reset()

    h = routine_health(server, d, "gitr")
    bucket = next(b for b in h["versions"] if b["runs"])
    assert bucket["runs"] == 1
    assert (bucket["ok"], bucket["partial"]) == (1, 0), (
        "the continued run ended ok; its earlier partial leg is not a second outcome")
    assert bucket["fail_rate"] == 0.0
