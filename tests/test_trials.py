"""MODEL TRIALS (rsched/trials.py) — "run this routine on catalog model X for its next N fires".

A trial is config the operator accepted; everything after that is DERIVED: it is active while
fewer than `runs` of the routine's runs have recorded its id in their durable usage record, every
fire while it is active is armed (trial.json + `--model` to the engine), and it stops applying by
itself. Nothing but the operator's next accepted change ever writes the field again.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
import yaml

from conftest import authed_client, finish, make_test_server, write_file
from helpers import server_for, tmp_server, transcript_events
from rsched import configflow, trials
from rsched.config import ModelConfig, load_routine
from rsched.daemon import runner_state
from rsched.daemon.events import EventBus
from rsched.daemon.runner import Runner
from rsched.engine import runrecord
from rsched.engine.config_bridge import trial_value
from rsched.engine.runtime import run_routine
from rsched.health_events import log_workflow_usage

TRIAL = {"id": "t-20261008-sonnet-high", "models": {"main": "sonnet-high"}, "runs": 3,
         "reason": "is the routine's opus overkill for a digest?"}


def _with_trial(d, trial=TRIAL):
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    raw["trial"] = trial
    (d / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return d


def _catalog(server, *names):
    server.models = {n: ModelConfig(name=n, endpoint="e", model=n) for n in names}
    return server


def _record(home, slug, run, *, trial="", depth=0):
    """One usage record the way a finished run writes it (runtime.run_routine)."""
    log_workflow_usage(home, routine=slug, run_id=f"{slug}:{run}", workflow="w", depth=depth,
                       status="ok", turns=1, tokens=1,
                       fingerprint={"engine": "x", "model": "m",
                                    **({"trial": trial} if trial else {})})


# ---------------------------------------------------------------- the config


def test_a_trial_loads_from_routine_yaml(make_routine):
    cfg, problems = load_routine(_with_trial(make_routine(slug="tried")))
    assert not problems
    assert cfg is not None and cfg.trial is not None
    assert (cfg.trial.id, cfg.trial.models, cfg.trial.runs) == (
        "t-20261008-sonnet-high", {"main": "sonnet-high"}, 3)
    assert cfg.models == {}                       # the routine's own binding is untouched


@pytest.mark.parametrize(("bad", "named"), [
    ({**TRIAL, "runs": 99}, "trial.runs"),
    ({**TRIAL, "runs": 0}, "trial.runs"),
    ({**TRIAL, "models": {"decision": "x"}}, "chat roles"),
    ({**TRIAL, "models": {}}, "trial.models"),
    ({**TRIAL, "id": "Not A Slug"}, "kebab-case"),
    ({**TRIAL, "reason": "  "}, "say what the trial should show"),
    ({k: v for k, v in TRIAL.items() if k != "runs"}, "trial.runs"),
])
def test_a_malformed_trial_is_a_problem_and_is_ignored(make_routine, bad, named):
    """Degrade per key like every other field — but a trial with a hole in it is no trial:
    the block is dropped and the problems say what it costs."""
    cfg, problems = load_routine(_with_trial(make_routine(slug="malformed"), bad))
    assert cfg is not None and cfg.trial is None
    assert any(named in p for p in problems), problems
    assert any(p.startswith("trial: ignored") for p in problems)


def test_a_stray_key_in_a_trial_is_reported(make_routine):
    cfg, problems = load_routine(_with_trial(make_routine(slug="stray"), {**TRIAL, "run": 5}))
    assert any("trial.run" in p for p in problems)
    assert cfg is not None and cfg.trial is not None and cfg.trial.runs == 3


def test_the_trial_field_never_moves_the_config_hash(make_routine):
    """A trial is recorded as its own id and model; the field outlives it as history, so
    hashing it would split the runs after a finished trial from the identical runs before."""
    d = make_routine(slug="hashless")
    before = runrecord.config_hash(load_routine(d)[0])
    assert runrecord.config_hash(load_routine(_with_trial(d))[0]) == before


def test_trial_is_a_next_run_routine_field():
    assert configflow.CLASSIFICATION["trial"][0] == configflow.NEXT_RUN
    assert "trial" in configflow.ROUTINE_PATCH_FIELDS
    assert "trial" not in configflow.CONVERSATION_PATCH_FIELDS


# ---------------------------------------------------------------- the state


def test_a_trial_counts_its_runs_from_the_usage_stream(make_routine, tmp_path):
    d = _with_trial(make_routine(slug="counted"))
    server = _catalog(tmp_server(tmp_path, create=False), "sonnet-high")
    cfg, _ = load_routine(d)
    assert trials.state(server, cfg)["state"] == trials.ACTIVE
    assert trials.state(server, cfg)["recorded"] == 0
    home = server.routines_home
    _record(home, "counted", "1", trial=TRIAL["id"])
    _record(home, "counted", "1", trial=TRIAL["id"])        # a second LEG of the same run
    _record(home, "counted", "2")                            # a run on the routine's own model
    _record(home, "counted", "3", trial="t-older")           # another trial's run
    _record(home, "other", "4", trial=TRIAL["id"])           # another routine's run
    _record(home, "counted", "5", trial=TRIAL["id"], depth=1)  # a child, not a run
    now = trials.state(server, cfg)
    assert (now["recorded"], now["state"], now["problem"]) == (1, trials.ACTIVE, "")
    _record(home, "counted", "6", trial=TRIAL["id"])
    _record(home, "counted", "7", trial=TRIAL["id"])
    assert trials.state(server, cfg)["state"] == trials.FINISHED


def test_a_trial_naming_a_model_the_catalog_lacks_is_ignored(make_routine, tmp_path):
    d = _with_trial(make_routine(slug="uncatalogued"))
    server = _catalog(tmp_server(tmp_path, create=False), "something-else")
    cfg, _ = load_routine(d)
    now = trials.state(server, cfg)
    assert now["state"] == trials.IGNORED
    assert "'sonnet-high' is not in the model catalog" in now["problem"]
    run_dir = d / "runs" / "20261008-120000"
    run_dir.mkdir(parents=True)
    assert trials.arm(server, cfg, run_dir) is None
    assert not (run_dir / runrecord.TRIAL_FILE).exists()


def test_a_stored_role_naming_a_model_the_catalog_lacks_is_reported(make_routine, tmp_path):
    """F643: `tv-show-tracker-seedbox-manager` and `library-sync` each carried a bare `Sonnet`
    role, stored nine weeks earlier when that WAS a catalog name. After the rename to
    `Sonnet medium`/`Sonnet high` both died at boot — rc=1, no finish, no summary, an
    `orphaned_run` and nothing to read. The write edges check; a stored name goes stale later."""
    server = _catalog(tmp_server(tmp_path, create=False), "Sonnet medium", "Sonnet high")
    problem = trials.roles_problem(server, {"main": "Sonnet"})
    assert "models.main 'Sonnet'" in problem and "not in the model catalog" in problem
    assert "Settings" in problem, "the line must say where the catalog is named"
    assert trials.roles_problem(server, {"main": "Sonnet medium"}) == ""
    assert trials.roles_problem(server, None) == ""
    assert trials.roles_problem(server, {}) == ""


def test_every_stale_role_is_named_not_only_the_first(make_routine, tmp_path):
    """The uncensored role resolves only when a refusal is referred, so it stays broken longest:
    a line that stopped at `main` would hide it."""
    server = _catalog(tmp_server(tmp_path, create=False), "Sonnet high")
    problem = trials.roles_problem(server, {"main": "Sonnet", "tool_call": "Sonnet high",
                                            "uncensored": "gemma-4-26b-a4b-uncensored"})
    assert "models.main 'Sonnet'" in problem
    assert "models.uncensored 'gemma-4-26b-a4b-uncensored'" in problem
    assert "tool_call" not in problem, "a role the catalog serves must not be named"
    assert " are not in the model catalog" in problem, "two stale roles read as plural"


def test_no_trial_is_no_state(make_routine, tmp_path):
    cfg, _ = load_routine(make_routine(slug="plain"))
    assert trials.state(tmp_server(tmp_path, create=False), cfg) is None


def test_arming_writes_the_trial_into_the_run_dir_only_while_it_is_active(make_routine,
                                                                           tmp_path):
    d = _with_trial(make_routine(slug="armed"), {**TRIAL, "runs": 1})
    server = _catalog(tmp_server(tmp_path, create=False), "sonnet-high")
    cfg, _ = load_routine(d)
    first = d / "runs" / "20261008-120000"
    first.mkdir(parents=True)
    assert trials.arm(server, cfg, first) is not None
    assert json.loads((first / runrecord.TRIAL_FILE).read_text()) == {
        "id": TRIAL["id"], "models": {"main": "sonnet-high"}, "runs": 1}
    assert trials.overrides(first) == {"main": "sonnet-high"}
    _record(server.routines_home, "armed", "20261008-120000", trial=TRIAL["id"])
    second = d / "runs" / "20261008-130000"
    second.mkdir(parents=True)
    assert trials.arm(server, cfg, second) is None             # one run recorded it: done
    assert trials.overrides(second) == {}


# ---------------------------------------------------------------- the daemon


async def _fire_and_capture(monkeypatch, server, cfg, *, resume_ts=""):
    seen: list[dict] = []

    def cmd(passed_server, target, run_ts, *, resume=False, models=None):
        seen.append({"ts": run_ts, "resume": resume, "models": dict(models or {})})
        return ["bash", "-c", "true"]

    monkeypatch.setattr(runner_state, "engine_cmd", cmd)
    runner = Runner(server, EventBus())
    if resume_ts:
        await runner.resume(cfg, resume_ts)
    else:
        await runner.fire(cfg, reason="manual")
    for _ in range(250):
        if seen and not runner.is_active(cfg.slug):
            break
        await asyncio.sleep(0.02)
    assert seen
    return seen[0]


async def test_a_fire_while_the_trial_is_active_names_its_models(make_routine, tmp_path,
                                                                 monkeypatch):
    d = _with_trial(make_routine(slug="fired"))
    server = _catalog(tmp_server(tmp_path, create=False), "sonnet-high")
    cfg, _ = load_routine(d)
    seen = await _fire_and_capture(monkeypatch, server, cfg)
    assert seen["models"] == {"main": "sonnet-high"}
    assert json.loads((d / "runs" / seen["ts"] / runrecord.TRIAL_FILE).read_text())["id"] \
        == TRIAL["id"]


async def test_a_fire_after_the_trial_finished_is_an_ordinary_run(make_routine, tmp_path,
                                                                 monkeypatch):
    d = _with_trial(make_routine(slug="after"), {**TRIAL, "runs": 1})
    server = _catalog(tmp_server(tmp_path, create=False), "sonnet-high")
    _record(server.routines_home, "after", "20261001-120000", trial=TRIAL["id"])
    cfg, _ = load_routine(d)
    seen = await _fire_and_capture(monkeypatch, server, cfg)
    assert seen["models"] == {}
    assert not (d / "runs" / seen["ts"] / runrecord.TRIAL_FILE).exists()


async def test_a_resume_keeps_the_trial_its_run_started_under(make_routine, tmp_path,
                                                              monkeypatch):
    """The run dir's own trial.json decides — even once the trial has finished, a resumed
    trial run is still one."""
    d = _with_trial(make_routine(slug="resumed"), {**TRIAL, "runs": 1})
    server = _catalog(tmp_server(tmp_path, create=False), "sonnet-high")
    _record(server.routines_home, "resumed", "20261001-120000", trial=TRIAL["id"])
    run_dir = d / "runs" / "20261001-120000"
    run_dir.mkdir(parents=True)
    (run_dir / runrecord.TRIAL_FILE).write_text(json.dumps(
        {"id": TRIAL["id"], "models": {"main": "sonnet-high"}, "runs": 1}), encoding="utf-8")
    cfg, _ = load_routine(d)
    seen = await _fire_and_capture(monkeypatch, server, cfg, resume_ts="20261001-120000")
    assert seen["resume"] and seen["models"] == {"main": "sonnet-high"}


# ---------------------------------------------------------------- the engine


def test_a_trial_run_records_its_trial_and_the_model_it_ran_on(make_routine, scripted):
    """The whole loop without a subprocess: armed run dir → the engine on the override →
    the record the next fire counts."""
    d = _with_trial(make_routine(slug="engine"), {**TRIAL, "runs": 1})
    server = _catalog(server_for(d), "sonnet-high")
    cfg, _ = load_routine(d)
    run_dir = d / "runs" / "20261008-140000"
    run_dir.mkdir(parents=True)
    trials.arm(server, cfg, run_dir)
    scripted([write_file("state/a.txt"), finish()])
    status, _ = run_routine(d, server, run_ts="20261008-140000",
                            model_overrides=trials.overrides(run_dir))
    assert status == "ok"
    stream = d.parent / ".control" / "workflow-usage.jsonl"
    (rec,) = [json.loads(x) for x in stream.read_text().splitlines()]
    assert rec["fingerprint"]["trial"] == TRIAL["id"]
    assert rec["fingerprint"]["model"] == "sonnet-high"
    assert trials.state(server, cfg)["state"] == trials.FINISHED


# ---------------------------------------------------------------- the filing check


CATALOG = SimpleNamespace(models={"sonnet-high": object()})


def test_the_filing_check_passes_a_sound_trial_and_a_clear():
    """A config-optimizer proposal for another routine is judged where it is FILED, so the
    operator never gets a decision whose apply button 422s."""
    assert trial_value(CATALOG, {"trial": TRIAL}) == ""
    assert trial_value(CATALOG, {"trial": None}) == ""
    assert trial_value(CATALOG, {"budgets": {"max_turns": 9}}) == ""


def test_the_filing_check_teaches_the_shape_and_names_the_catalog():
    bad = trial_value(CATALOG, {"trial": {**TRIAL, "runs": "five"}})
    assert "runs: Input should be a valid integer" in bad
    assert '"models": {"main": "<catalog model name>"}' in bad and '{"trial": null}' in bad
    gone = trial_value(CATALOG, {"trial": {**TRIAL, "models": {"main": "opus-9"}}})
    assert "'opus-9' is not in the model catalog" in gone and "list_models" in gone


def test_a_run_files_a_trial_proposal_and_a_bad_one_is_refused(make_routine, scripted):
    """End to end through `ask_user`: the sound proposal becomes a decision carrying the trial
    as its `config_patch`; the one naming a model the catalog lacks files nothing."""
    d = make_routine(slug="optimizer")
    server = _catalog(server_for(d), "sonnet-high")
    scripted([
        {"say": "propose a trial", "kind": "ask_user", "mode": "deferred",
         "question": "Try sonnet-high for three runs?", "config_patch": {"trial": TRIAL}},
        {"say": "propose another", "kind": "ask_user", "mode": "deferred",
         "question": "Try opus-9?",
         "config_patch": {"trial": {**TRIAL, "models": {"main": "opus-9"}}}},
        finish(),
    ])
    status, run_dir = run_routine(d, server, run_ts="20261008-150000")
    assert status == "ok"
    (filed,) = (d / "questions" / "pending").glob("*.json")
    assert json.loads(filed.read_text())["config_patch"] == {"trial": TRIAL}
    errors = [e["payload"]["error"] for e in transcript_events(run_dir)
              if e["type"] == "observation" and e["payload"].get("error")]
    assert any("'opus-9' is not in the model catalog" in err for err in errors), errors


# ---------------------------------------------------------------- the web


@pytest.fixture
def web(tmp_path, make_routine):
    make_routine(slug="webr")
    server = make_test_server(tmp_path, models={
        "m": {"endpoint": "dummy", "model": "m"},
        "sonnet-high": {"endpoint": "dummy", "model": "sonnet", "effort": "high"}})
    with authed_client(server) as c:
        yield c, tmp_path / "routines" / "webr"


def test_the_patch_sets_and_clears_a_trial(web):
    c, d = web
    r = c.patch("/api/routines/webr", json={"trial": TRIAL})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == ["trial"]
    assert yaml.safe_load((d / "routine.yaml").read_text())["trial"] == TRIAL
    # an explicit null is the ONE spelling of "clear it" — and is reported as applied, which
    # is what the Decisions page's apply checks its patch keys against
    r = c.patch("/api/routines/webr", json={"trial": None})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == ["trial"]
    assert "trial" not in yaml.safe_load((d / "routine.yaml").read_text())
    # a patch that does not mention it leaves it alone
    c.patch("/api/routines/webr", json={"trial": TRIAL})
    c.patch("/api/routines/webr", json={"tags": ["x"]})
    assert yaml.safe_load((d / "routine.yaml").read_text())["trial"] == TRIAL


def test_the_patch_refuses_a_trial_it_could_not_run(web):
    c, d = web
    unknown = c.patch("/api/routines/webr",
                      json={"trial": {**TRIAL, "models": {"main": "opus-9"}}})
    assert unknown.status_code == 400 and "not in the model catalog" in unknown.text
    for bad in ({**TRIAL, "runs": 21}, {**TRIAL, "extra": 1}, {**TRIAL, "runs": "3"},
                {**TRIAL, "models": {"decision": "m"}}):
        assert c.patch("/api/routines/webr", json={"trial": bad}).status_code == 422, bad
    assert "trial" not in yaml.safe_load((d / "routine.yaml").read_text())


def test_the_routine_detail_carries_the_trial_state(web):
    c, d = web
    assert c.get("/api/routines/webr").json()["trial"] is None
    c.patch("/api/routines/webr", json={"trial": TRIAL})
    _record(d.parent, "webr", "1", trial=TRIAL["id"])
    trial = c.get("/api/routines/webr").json()["trial"]
    assert (trial["state"], trial["recorded"], trial["runs"]) == ("active", 1, 3)
    assert trial["models"] == {"main": "sonnet-high"} and trial["reason"] == TRIAL["reason"]


def test_a_trial_the_catalog_cannot_serve_is_a_routine_problem(web):
    c, d = web
    _with_trial(d, {**TRIAL, "models": {"main": "retired-model"}})   # a hand edit
    body = c.get("/api/routines/webr").json()
    assert body["trial"]["state"] == "ignored"
    assert any("'retired-model' is not in the model catalog" in p for p in body["problems"])


def test_validate_names_a_trial_the_catalog_cannot_serve(cli_server, make_routine, capsys):
    """The loader cannot see the catalog, so `rsched validate` says it beside the file's own
    problems — a trial ignored at every fire is a routine that is not what it says."""
    from rsched import cli

    _with_trial(make_routine(slug="validated"))
    assert cli.main(["validate", "validated"]) == 1
    assert "'sonnet-high' is not in the model catalog" in capsys.readouterr().out
