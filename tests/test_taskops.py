"""GATED PROCESSING end to end (engine/taskops.py): a routine with the task layer on opens each
due task, works in its workspace, checkpoints it — and cannot finish until every due task is
checkpointed; whatever it was owed and never checkpointed is carried to the next run.
"""

from __future__ import annotations

import json

import yaml

from conftest import finish, write_file
from rsched import tasks
from rsched.config import ServerConfig
from rsched.engine.actions import validate_action
from rsched.engine.runtime import run_routine
from rsched.grantpolicy import GrantPolicy

TS = "20261007-080000"


def _task_routine(make_routine, *, tasks_on=True, budgets=None, recipe_for=None):
    d = make_routine(slug="fau", budgets=budgets)
    cfg = yaml.safe_load((d / "routine.yaml").read_text())
    cfg["capabilities"] = {"actions": [], "utils": [], "runs": "last",
                           "tasks": "on" if tasks_on else "off"}
    cfg["permissions"] = []
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg))
    doc = {"version": 1, "deleted": [], "run": {}, "tasks": [
        {"id": "alpha", "title": "Alpha project", "brief": "steward alpha", "state": "active"},
        {"id": "beta", "title": "Beta project", "brief": "steward beta", "state": "active"},
        {"id": "gamma", "title": "Paused one", "brief": "-", "state": "paused"}]}
    tasks.save(d, doc)
    for tid, recipe in (recipe_for or {}).items():
        (d / "tasks" / tid).mkdir(parents=True, exist_ok=True)
        (d / "tasks" / tid / "main.md").write_text(recipe)
    return d


def _run(d):
    server = ServerConfig()
    server.routines_home = d.parent
    server.libraries_home = d.parent.parent / "lib"
    return run_routine(d, server, run_ts=TS)


def _task(verb, say="Task step.", **fields):
    return {"say": say, "kind": "task", "verb": verb, **fields}


def _observations(run_dir):
    rows = [json.loads(line) for line in (run_dir / "transcript.jsonl").read_text().splitlines()]
    return [r["payload"] for r in rows if r.get("type") == "observation"]


def test_a_run_cannot_finish_until_every_due_task_is_checkpointed(make_routine, scripted):
    d = _task_routine(make_routine)
    scripted([
        finish(summary="nothing to do"),                       # refused: alpha, beta owed
        _task("open"),                                         # opens alpha, the next due
        write_file("state/progress.md", content="alpha moved"),
        _task("checkpoint", id="alpha", outcome="advanced", summary="moved alpha"),
        write_file("state/progress.md", content="beta looked at"),   # beta opened itself
        _task("checkpoint", id="beta", outcome="no-work", summary="nothing new"),
        finish(summary="both processed"),
    ])
    status, run_dir = _run(d)
    assert status == "ok"
    # the work landed in each task's WORKSPACE, never in the routine's own state/
    assert (d / "tasks/alpha/state/progress.md").read_text() == "alpha moved"
    assert (d / "tasks/beta/state/progress.md").read_text() == "beta looked at"
    assert not (d / "state/progress.md").exists()
    obs = _observations(run_dir)
    deferred = [o for o in obs if o.get("kind") == "finish" and o.get("tasks_owed")]
    assert len(deferred) == 1 and "alpha, beta" in deferred[0]["message"]
    opened = next(o for o in obs if o.get("kind") == "task" and o.get("verb") == "open")
    assert opened["message"].startswith("TASK OPEN — alpha")
    assert "Workspace: tasks/alpha/" in opened["message"]
    handed = next(o for o in obs if o.get("verb") == "checkpoint" and o.get("id") == "alpha")
    assert handed["opened"] == "beta" and "TASK OPEN — beta" in handed["message"]
    doc = tasks.load(d)
    assert tasks.find(doc, "alpha")["last"]["outcome"] == "advanced"
    assert tasks.find(doc, "beta")["last"]["summary"] == "nothing new"
    assert doc["run"]["done"] == {"alpha": "advanced", "beta": "no-work"}
    assert "last" not in tasks.find(doc, "gamma")          # paused: never due, never opened


def test_a_second_task_cannot_be_opened_while_one_is_open(make_routine, scripted):
    d = _task_routine(make_routine)
    scripted([
        _task("open", id="alpha"),
        _task("open", id="beta"),                              # refused
        _task("checkpoint", id="beta", outcome="advanced", summary="x"),   # refused too
        _task("checkpoint", id="alpha", outcome="deferred", summary="later"),
        _task("checkpoint", id="beta", outcome="no-work", summary="quiet"),
        finish(summary="done"),
    ])
    status, run_dir = _run(d)
    assert status == "ok"
    refusals = [o["reason"] for o in _observations(run_dir) if o.get("rejected")]
    assert any("'alpha' is still open" in r for r in refusals)
    assert any("'alpha' is the open one" in r for r in refusals)
    alpha = tasks.find(tasks.load(d), "alpha")
    assert alpha["carry"].startswith(f"deferred by fau:{TS}")   # the next run is owed it


def test_what_a_run_never_checkpointed_is_carried_and_said(make_routine, scripted):
    d = _task_routine(make_routine, budgets={"max_turns": 2})
    scripted([_task("open"), write_file("state/a.md"),          # turn 2 spends the budget
              finish(summary="out of turns")])                  # the reserved turn stands
    status, run_dir = _run(d)
    assert status == "ok"            # the reserved finish turn is never set aside
    doc = tasks.load(d)
    assert {t["id"] for t in doc["tasks"] if t.get("carry")} == {"alpha", "beta"}
    assert "Task(s) carried to the next run" in (run_dir / "result.md").read_text()


def test_a_checkpoint_accounts_for_the_task_recipes_done_when(make_routine, scripted):
    recipe = "# Alpha\n\n## Done when\n\n- d1 — the alpha page is current\n"
    d = _task_routine(make_routine, recipe_for={"alpha": recipe})
    scripted([
        _task("open", id="alpha"),
        _task("checkpoint", id="alpha", outcome="advanced", summary="x"),        # refused
        _task("checkpoint", id="alpha", outcome="advanced", summary="x",
              accounting=["d1 met: republished rev 4"]),
        _task("checkpoint", id="beta", outcome="no-work", summary="quiet"),
        finish(summary="done"),
    ])
    status, run_dir = _run(d)
    assert status == "ok"
    obs = _observations(run_dir)
    opened = next(o for o in obs if o.get("verb") == "open")
    assert "ITS RECIPE" in opened["message"] and "accounting: one entry per Done-when line (d1)" \
        in opened["message"]
    assert any("no entry for d1" in o.get("reason", "") for o in obs if o.get("rejected"))
    assert tasks.find(tasks.load(d), "alpha")["last"]["accounting"] == [
        "d1 met: republished rev 4"]


def test_memory_and_notes_follow_the_open_task_and_its_store_is_sealed(make_routine, scripted):
    d = _task_routine(make_routine)
    scripted([
        _task("open", id="alpha"),
        {"say": "Remember.", "kind": "memory_write", "name": "partner-quirks",
         "content": "# quirks", "about": "alpha partner quirks",
         "note": "alpha: the partner answers on Fridays"},
        write_file(str(d / "state/tasks.json"), content="{}"),   # refused: engine-owned
        _task("checkpoint", id="alpha", outcome="advanced", summary="noted"),
        _task("checkpoint", id="beta", outcome="no-work", summary="quiet"),
        finish(summary="done"),
    ])
    status, run_dir = _run(d)
    assert status == "ok"
    assert (d / "tasks/alpha/.memory/partner-quirks.md").is_file()
    assert "partner-quirks" in (d / "tasks/alpha/.memory/INDEX.md").read_text()
    assert not (d / ".memory/partner-quirks.md").exists()
    assert "partner answers on Fridays" in (d / "tasks/alpha/state/notes.md").read_text()
    refused = [o for o in _observations(run_dir) if o.get("kind") == "write_file"]
    assert refused and "task store" in refused[0]["error"]
    assert tasks.find(tasks.load(d), "alpha")["last"]["outcome"] == "advanced"


def test_create_update_and_delete_manage_the_list(make_routine, scripted):
    d = _task_routine(make_routine)
    scripted([
        _task("create", id="new-request", title="A new request", brief="handle it",
              wake="2026-10-20"),
        _task("update", id="beta", state="paused"),          # a decision settles it this run
        _task("delete", id="gamma"),
        _task("checkpoint", id="alpha", outcome="no-work", summary="quiet"),
        finish(summary="done"),
    ])
    status, _run_dir = _run(d)
    assert status == "ok"
    doc = tasks.load(d)
    assert tasks.find(doc, "new-request")["wake"] == "2026-10-20"
    assert (d / "tasks/new-request").is_dir()
    assert tasks.find(doc, "beta")["state"] == "paused"
    assert tasks.find(doc, "gamma") is None and doc["deleted"][0]["id"] == "gamma"


def test_the_task_kind_exists_only_with_the_setting():
    action = {"say": "s", "kind": "task", "verb": "list"}
    assert validate_action(action, grants=GrantPolicy(tasks="on")) == []
    denied = validate_action(action, grants=GrantPolicy())
    assert denied and "switched OFF in this routine's settings" in denied[0]
    assert not GrantPolicy().allows_kind("task") and GrantPolicy(tasks="on").allows_kind("task")
