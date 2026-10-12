"""GATED PROCESSING end to end (engine/jobops.py): a routine with the job layer on opens each
due job, works in its workspace, checkpoints it — and cannot finish until every due job is
checkpointed; whatever it was owed and never checkpointed is carried to the next run.
"""

from __future__ import annotations

import json

import yaml

from conftest import finish, write_file
from rsched import jobs
from rsched.config import ServerConfig
from rsched.engine.actions import validate_action
from rsched.engine.runtime import run_routine
from rsched.grantpolicy import GrantPolicy

TS = "20261007-080000"


def _job_routine(make_routine, *, jobs_on=True, budgets=None, recipe_for=None):
    d = make_routine(slug="fau", budgets=budgets)
    cfg = yaml.safe_load((d / "routine.yaml").read_text())
    cfg["capabilities"] = {"actions": [], "utils": [], "runs": "last",
                           "jobs": "on" if jobs_on else "off"}
    cfg["permissions"] = []
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg))
    doc = {"version": 1, "deleted": [], "run": {}, "jobs": [
        {"id": "alpha", "title": "Alpha project", "brief": "steward alpha", "state": "active"},
        {"id": "beta", "title": "Beta project", "brief": "steward beta", "state": "active"},
        {"id": "gamma", "title": "Paused one", "brief": "-", "state": "paused"}]}
    jobs.save(d, doc)
    for tid, recipe in (recipe_for or {}).items():
        (d / "jobs" / tid).mkdir(parents=True, exist_ok=True)
        (d / "jobs" / tid / "main.md").write_text(recipe)
    return d


def _run(d):
    server = ServerConfig()
    server.routines_home = d.parent
    server.libraries_home = d.parent.parent / "lib"
    return run_routine(d, server, run_ts=TS)


def _job(verb, say="Job step.", **fields):
    return {"say": say, "kind": "job", "verb": verb, **fields}


def _observations(run_dir):
    rows = [json.loads(line) for line in (run_dir / "transcript.jsonl").read_text().splitlines()]
    return [r["payload"] for r in rows if r.get("type") == "observation"]


def test_a_run_cannot_finish_until_every_due_job_is_checkpointed(make_routine, scripted):
    d = _job_routine(make_routine)
    scripted([
        finish(summary="nothing to do"),                       # refused: alpha, beta owed
        _job("open"),                                         # opens alpha, the next due
        write_file("state/progress.md", content="alpha moved"),
        _job("checkpoint", id="alpha", outcome="advanced", summary="moved alpha"),
        write_file("state/progress.md", content="beta looked at"),   # beta opened itself
        _job("checkpoint", id="beta", outcome="no-work", summary="nothing new"),
        finish(summary="both processed"),
    ])
    status, run_dir = _run(d)
    assert status == "ok"
    # the work landed in each job's WORKSPACE, never in the routine's own state/
    assert (d / "jobs/alpha/state/progress.md").read_text() == "alpha moved"
    assert (d / "jobs/beta/state/progress.md").read_text() == "beta looked at"
    assert not (d / "state/progress.md").exists()
    obs = _observations(run_dir)
    deferred = [o for o in obs if o.get("kind") == "finish" and o.get("jobs_owed")]
    assert len(deferred) == 1 and "alpha, beta" in deferred[0]["message"]
    opened = next(o for o in obs if o.get("kind") == "job" and o.get("verb") == "open")
    assert opened["message"].startswith("JOB OPEN — alpha")
    assert "Workspace: jobs/alpha/" in opened["message"]
    handed = next(o for o in obs if o.get("verb") == "checkpoint" and o.get("id") == "alpha")
    assert handed["opened"] == "beta" and "JOB OPEN — beta" in handed["message"]
    doc = jobs.load(d)
    assert jobs.find(doc, "alpha")["last"]["outcome"] == "advanced"
    assert jobs.find(doc, "beta")["last"]["summary"] == "nothing new"
    assert doc["run"]["done"] == {"alpha": "advanced", "beta": "no-work"}
    assert "last" not in jobs.find(doc, "gamma")          # paused: never due, never opened


def test_a_second_job_cannot_be_opened_while_one_is_open(make_routine, scripted):
    d = _job_routine(make_routine)
    scripted([
        _job("open", id="alpha"),
        _job("open", id="beta"),                              # refused
        _job("checkpoint", id="beta", outcome="advanced", summary="x"),   # refused too
        _job("checkpoint", id="alpha", outcome="deferred", summary="later"),
        _job("checkpoint", id="beta", outcome="no-work", summary="quiet"),
        finish(summary="done"),
    ])
    status, run_dir = _run(d)
    assert status == "ok"
    refusals = [o["reason"] for o in _observations(run_dir) if o.get("rejected")]
    assert any("'alpha' is still open" in r for r in refusals)
    assert any("'alpha' is the open one" in r for r in refusals)
    alpha = jobs.find(jobs.load(d), "alpha")
    assert alpha["carry"].startswith(f"deferred by fau:{TS}")   # the next run is owed it


def test_what_a_run_never_checkpointed_is_carried_and_said(make_routine, scripted):
    d = _job_routine(make_routine, budgets={"max_turns": 2})
    scripted([_job("open"), write_file("state/a.md"),          # turn 2 spends the budget
              finish(summary="out of turns")])                  # the reserved turn stands
    status, run_dir = _run(d)
    assert status == "ok"            # the reserved finish turn is never set aside
    doc = jobs.load(d)
    assert {t["id"] for t in doc["jobs"] if t.get("carry")} == {"alpha", "beta"}
    assert "Job(s) carried to the next run" in (run_dir / "result.md").read_text()


def test_a_checkpoint_accounts_for_the_job_recipes_done_when(make_routine, scripted):
    recipe = "# Alpha\n\n## Done when\n\n- d1 — the alpha page is current\n"
    d = _job_routine(make_routine, recipe_for={"alpha": recipe})
    scripted([
        _job("open", id="alpha"),
        _job("checkpoint", id="alpha", outcome="advanced", summary="x"),        # refused
        _job("checkpoint", id="alpha", outcome="advanced", summary="x",
              accounting=["d1 met: republished rev 4"]),
        _job("checkpoint", id="beta", outcome="no-work", summary="quiet"),
        finish(summary="done"),
    ])
    status, run_dir = _run(d)
    assert status == "ok"
    obs = _observations(run_dir)
    opened = next(o for o in obs if o.get("verb") == "open")
    assert "ITS RECIPE" in opened["message"] and "accounting: one entry per Done-when line (d1)" \
        in opened["message"]
    assert any("no entry for d1" in o.get("reason", "") for o in obs if o.get("rejected"))
    assert jobs.find(jobs.load(d), "alpha")["last"]["accounting"] == [
        "d1 met: republished rev 4"]


def test_memory_and_notes_follow_the_open_job_and_its_store_is_sealed(make_routine, scripted):
    d = _job_routine(make_routine)
    scripted([
        _job("open", id="alpha"),
        {"say": "Remember.", "kind": "memory_write", "name": "partner-quirks",
         "content": "# quirks", "about": "alpha partner quirks",
         "note": "alpha: the partner answers on Fridays"},
        write_file(str(d / "state/jobs.json"), content="{}"),   # refused: engine-owned
        _job("checkpoint", id="alpha", outcome="advanced", summary="noted"),
        _job("checkpoint", id="beta", outcome="no-work", summary="quiet"),
        finish(summary="done"),
    ])
    status, run_dir = _run(d)
    assert status == "ok"
    assert (d / "jobs/alpha/.memory/partner-quirks.md").is_file()
    assert "partner-quirks" in (d / "jobs/alpha/.memory/INDEX.md").read_text()
    assert not (d / ".memory/partner-quirks.md").exists()
    assert "partner answers on Fridays" in (d / "jobs/alpha/state/notes.md").read_text()
    refused = [o for o in _observations(run_dir) if o.get("kind") == "write_file"]
    assert refused and "job store" in refused[0]["error"]
    assert jobs.find(jobs.load(d), "alpha")["last"]["outcome"] == "advanced"


def test_create_update_and_delete_manage_the_list(make_routine, scripted):
    d = _job_routine(make_routine)
    scripted([
        _job("create", id="new-request", title="A new request", brief="handle it",
              wake="2026-10-20"),
        _job("update", id="beta", state="paused"),          # a decision settles it this run
        _job("delete", id="gamma"),
        _job("checkpoint", id="alpha", outcome="no-work", summary="quiet"),
        finish(summary="done"),
    ])
    status, _run_dir = _run(d)
    assert status == "ok"
    doc = jobs.load(d)
    assert jobs.find(doc, "new-request")["wake"] == "2026-10-20"
    assert (d / "jobs/new-request").is_dir()
    assert jobs.find(doc, "beta")["state"] == "paused"
    assert jobs.find(doc, "gamma") is None and doc["deleted"][0]["id"] == "gamma"


def test_the_job_kind_exists_only_with_the_setting():
    action = {"say": "s", "kind": "job", "verb": "list"}
    assert validate_action(action, grants=GrantPolicy(jobs="on")) == []
    denied = validate_action(action, grants=GrantPolicy())
    assert denied and "switched OFF in this routine's settings" in denied[0]
    assert not GrantPolicy().allows_kind("job") and GrantPolicy(jobs="on").allows_kind("job")
