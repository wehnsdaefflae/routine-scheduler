"""Health-by-recipe-version (rsched.run_health): the deterministic regression heuristic —
every threshold constant exercised on both sides — and the bucketing read-model over the
durable usage stream + a real git recipe history.
"""

import json
import subprocess

from rsched.config import ServerConfig
from rsched.run_health import (
    BALLOON_RATIO,
    FAIL_RATE_JUMP,
    MIN_RUNS,
    REGRESSION_WINDOW,
    TOKENS_FLOOR,
    TURNS_FLOOR,
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


# ---- the read-model over stream + git -------------------------------------------------


def _git(d, *args, date="2026-07-01T10:00:00+00:00"):
    import os
    subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t",
                    *args], capture_output=True, text=True, check=True,
                   env={**os.environ, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date})


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
