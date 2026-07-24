"""Per-util telemetry seams + the run-analytics payload extensions, end to end: the
executor counts execution outcomes, the validation seam counts denials/rejections (a
denied call never reaches the executor), interact counts deferred-question churn, and a
finished run lands recipe_commit / utils / asks_deferred in status.json and the durable
workflow-usage record.
"""

import json
import subprocess

from conftest import finish, util, write_file
from rsched.config import ServerConfig, load_routine
from rsched.engine.actions import util_rejection_outcome
from rsched.engine.executor import dispatch
from rsched.engine.run_context import Budgets, RunContext
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import Transcript
from rsched.grants import GrantPolicy
from rsched.paths import read_json

TS = "20260716-210000"


def _ctx(make_routine, tmp_path, slug="counter") -> RunContext:
    d = make_routine(slug=slug)
    cfg, _ = load_routine(d)
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True)
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.libraries_home = tmp_path / "empty-library"
    return RunContext(routine=cfg, server=server, registry=None, run_ts=TS,
                      run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                      budgets=Budgets.from_config(cfg.budgets))


# ---- executor seam ---------------------------------------------------------------------


def test_do_util_counts_outcomes(make_routine, tmp_path, monkeypatch):
    from rsched import utils_lib

    ctx = _ctx(make_routine, tmp_path)
    codes = iter([0, 2, 3])
    monkeypatch.setattr(utils_lib, "exists", lambda home, name: name != "ghost")
    monkeypatch.setattr(utils_lib, "list_utils", lambda home: [])
    monkeypatch.setattr(utils_lib, "run_util",
                        lambda home, name, args, **_kw: (next(codes), "out", "err"))
    for _ in range(3):
        dispatch({"kind": "util", "name": "fetch", "args": []}, ctx)
    dispatch({"kind": "util", "name": "ghost", "args": []}, ctx)
    assert ctx.util_stats["fetch"] == {"ok": 1, "usage_error": 1, "error": 1}
    assert ctx.util_stats["ghost"] == {"missing": 1}


def test_pseudo_utils_are_not_counted(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    dispatch({"kind": "util", "name": "list", "args": []}, ctx)
    dispatch({"kind": "util", "name": "show", "args": ["whatever"]}, ctx)
    assert ctx.util_stats == {}


# ---- the validation-seam classifier ----------------------------------------------------


def test_rejection_classifier():
    reserved = GrantPolicy(gated_utils={"discord": ("communication",)})
    # a reserved util switched off → a permission problem, whatever else is wrong
    assert util_rejection_outcome({"kind": "util", "name": "discord"},
                                  grants=reserved) == ("discord", "denied")
    # the util kind excluded by the workflow's tools: also a permission problem
    assert util_rejection_outcome({"kind": "util", "name": "fetch"},
                                  allowed_kinds={"read_file"}) == ("fetch", "denied")
    # malformed but permitted → rejected
    assert util_rejection_outcome({"kind": "util", "name": "fetch", "path": "x"},
                                  grants=GrantPolicy()) == ("fetch", "rejected")
    # not attributable: no name, pseudo-utils, other kinds
    assert util_rejection_outcome({"kind": "util"}) is None
    assert util_rejection_outcome({"kind": "util", "name": "list"}) is None
    assert util_rejection_outcome({"kind": "write_file", "name": "x"}) is None


# ---- end to end through scripted runs ---------------------------------------------------


def _reserve_discord(server: ServerConfig) -> None:
    server.permissions_home.mkdir(parents=True, exist_ok=True)
    (server.permissions_home / "communication.md").write_text(
        "---\nrequires:\n  utils: [discord]\n---\n# permission: communication\nbody\n",
        encoding="utf-8")


def test_denied_util_lands_in_the_usage_record(make_routine, scripted):
    """A reserved-util call is rejected at validation (never a turn) yet still counted —
    and the finished run's stream record carries the breakdown."""
    d = make_routine(slug="denyr")
    server = ServerConfig()
    server.routines_home = d.parent
    server.libraries_home = d.parent.parent / "lib"
    _reserve_discord(server)
    # the denied call never becomes a turn; the probe grounds the finish (fabrication guard)
    scripted([util("discord", say="Pinging."),
              write_file("state/probe.txt", content="probe"), finish()])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    st = read_json(run_dir / "status.json")
    assert st["utils"] == {"discord": {"denied": 1}}
    assert st["recipe_commit"] is None                      # no git in the fixture dir
    rec = json.loads((d.parent / ".control" / "workflow-usage.jsonl")
                     .read_text(encoding="utf-8").splitlines()[-1])
    assert rec["utils"] == {"discord": {"denied": 1}}
    assert rec["recipe_commit"] is None
    assert rec["asks_deferred"] == 0


def test_deferred_ask_churn_is_counted(make_routine, scripted):
    d = make_routine(slug="askr")
    server = ServerConfig()
    server.routines_home = d.parent
    server.libraries_home = d.parent.parent / "lib"
    scripted([{"say": "Need input.", "kind": "ask_user", "mode": "deferred",
               "question": "Which source should win?"},
              write_file("state/probe.txt", content="probe"), finish()])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    assert read_json(run_dir / "status.json")["asks_deferred"] == 1
    rec = json.loads((d.parent / ".control" / "workflow-usage.jsonl")
                     .read_text(encoding="utf-8").splitlines()[-1])
    assert rec["asks_deferred"] == 1


def test_recipe_commit_stamped_from_git(make_routine, scripted):
    """A versioned routine dir's runs carry the recipe commit in status.json and the
    stream record; the engine's run-end autocommit does NOT move the recipe version."""
    import os

    d = make_routine(slug="gitr")
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "scaffold")):
        subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t",
                        *args], capture_output=True, check=True, env=os.environ)
    from rsched.recipes import current_recipe_commit

    expected = current_recipe_commit(d)
    assert expected
    server = ServerConfig()
    server.routines_home = d.parent
    server.libraries_home = d.parent.parent / "lib"
    scripted([write_file("state/probe.txt", content="probe"), finish()])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    assert read_json(run_dir / "status.json")["recipe_commit"] == expected
    rec = json.loads((d.parent / ".control" / "workflow-usage.jsonl")
                     .read_text(encoding="utf-8").splitlines()[-1])
    assert rec["recipe_commit"] == expected
    # the run's own autocommit changed HEAD, not the recipe version
    assert current_recipe_commit(d) == expected


# ---- F182: a resumed leg must report cumulative elapsed, not a reset clock --------------


def test_resumed_leg_reports_cumulative_elapsed(make_routine, tmp_path):
    """A resume builds a FRESH RunContext (the monotonic clock restarts), so status.json
    used to report elapsed_s near 0 on a resumed run. The prior leg's persisted elapsed_s
    must seed elapsed_base_s at construction, and write_status must report the cumulative
    total — while the budget meter stays window-scoped (a resume gets a fresh window,
    mirroring usage_base)."""
    d = make_routine(slug="elapsed")
    cfg, _ = load_routine(d)
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True)
    # the prior leg's telemetry, as the runner's resume queued-write leaves it (F140)
    (run_dir / "status.json").write_text(
        json.dumps({"run_id": f"elapsed:{TS}", "state": "queued", "elapsed_s": 120}),
        encoding="utf-8")
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.libraries_home = tmp_path / "empty-library"
    ctx = RunContext(routine=cfg, server=server, registry=None, run_ts=TS,
                     run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    assert ctx.elapsed_base_s == 120
    ctx.write_status("running")
    st = read_json(run_dir / "status.json")
    assert st["elapsed_s"] >= 120                     # cumulative, not ~0 (the F182 bug)
    # budgets keep a fresh wall-clock window: the base must not eat max_wall_clock_min
    assert ctx.meter()["wall_clock"] < 1.0


def test_fresh_run_carries_no_elapsed_base(make_routine, tmp_path):
    """A fresh run dir (queued status without elapsed_s, or none at all) seeds base 0."""
    ctx = _ctx(make_routine, tmp_path, slug="freshclk")
    assert ctx.elapsed_base_s == 0.0
    ctx.write_status("running")
    assert read_json(ctx.run_dir / "status.json")["elapsed_s"] <= 1
