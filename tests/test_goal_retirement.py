"""A routine that reaches its FINAL GOAL retires itself — without anything writing config.

The operator asked for routines that "disable themselves once they think they reached it". The
invariant it runs into is absolute: a run never writes `routine.yaml`, and the engine never writes
config. Retirement satisfies both because it is DERIVED — the scheduler simply builds no fire
table entry for a routine whose goal-scoped stopping conditions are all met, and clearing one puts
it straight back. The `enabled: false` half is a click on the Decisions page, through the ordinary
config writer.

Covered here: the derived skip (scheduler + group chains), the proposal the finish files, the two
decisions that settle it, and the one-shot migration that converted the live stores.
"""

from __future__ import annotations

import yaml

from conftest import FakeRunner
from rsched import pending, registry
from rsched.config import ServerConfig
from rsched.daemon.events import EventBus
from rsched.daemon.scheduler import Scheduler
from rsched.engine import stopping

NOW = "2026-09-05T09:00:00+02:00"


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.routines_home.mkdir(parents=True, exist_ok=True)
    return s


def _goal_met(routine_dir, text="the application is submitted"):
    stopping.save(routine_dir, {"conditions": [{"text": text, "scope": "goal"}]}, now=NOW)
    stopping.record_accounting(routine_dir, f"[s1] met — {text}", run_id="r:1", now=NOW)


# ---- the derived half: nothing is written, and the routine stops firing -------------------------

def test_a_routine_whose_goal_is_met_gets_no_fire_table_entry(make_routine, tmp_path):
    d = make_routine(slug="finisher")
    sched = Scheduler(_server(tmp_path), FakeRunner(), EventBus())
    sched.rescan()
    assert "finisher" in sched.next_fires            # ordinary routine, ordinary cron

    _goal_met(d)
    sched.rescan()
    assert "finisher" not in sched.next_fires
    # and `enabled` was NOT touched — retirement is derived, not a config write
    assert yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8")).get(
        "enabled", True) is not False


def test_clearing_the_goal_puts_the_routine_straight_back(make_routine, tmp_path):
    d = make_routine(slug="reopened")
    _goal_met(d)
    sched = Scheduler(_server(tmp_path), FakeRunner(), EventBus())
    sched.rescan()
    assert "reopened" not in sched.next_fires

    stopping.reopen_goal(d, now=NOW)
    sched.rescan()
    assert "reopened" in sched.next_fires


def test_a_run_bound_never_retires_anything(make_routine, tmp_path):
    """The whole point of the scope split: a per-run bound reported met is history. If it could
    retire a routine, 22 of the live 31 would have switched themselves off."""
    d = make_routine(slug="perrun")
    stopping.save(d, {"conditions": [{"text": "one increment landed"}]}, now=NOW)
    stopping.record_accounting(d, "[s1] met — landed", run_id="r:1", now=NOW)
    sched = Scheduler(_server(tmp_path), FakeRunner(), EventBus())
    sched.rescan()
    assert "perrun" in sched.next_fires


async def test_boot_catchup_does_not_make_up_runs_for_a_finished_routine(make_routine, tmp_path):
    d = make_routine(slug="catchgoal")
    text = (d / "routine.yaml").read_text().replace("catchup: skip", "catchup: run_once")
    (d / "routine.yaml").write_text(text)
    _goal_met(d)
    fr = FakeRunner()
    sched = Scheduler(_server(tmp_path), fr, EventBus())
    sched.rescan()
    await sched.boot_catchup()
    assert fr.fired == []


def test_the_registry_reports_retired_separately_from_disabled(make_routine, tmp_path):
    done = make_routine(slug="done")
    off = make_routine(slug="off")
    (off / "routine.yaml").write_text(
        (off / "routine.yaml").read_text().replace("enabled: true", "enabled: false"))
    _goal_met(done)
    catalog = registry.scan(_server(tmp_path))
    assert catalog["done"].retired is True and catalog["done"].cfg.enabled is True
    assert catalog["off"].retired is False and catalog["off"].cfg.enabled is False


# ---- group chains: deliberately off is not a broken chain ---------------------------------------

async def test_a_retired_group_member_is_skipped_without_counting_as_a_failure(tmp_path):
    """These used to share the MISSING-member branch and log `outcome: "failed"` — 28 such health
    events on the live instance, and under `on_failure: stop` a retirement would have become a
    daily outage of every later member."""
    from rsched import group_runs, groups
    from rsched.daemon.group_runs import GroupRunManager

    server = _server(tmp_path)
    for slug in ("first", "second"):
        d = server.routines_home / slug
        (d / "inbox").mkdir(parents=True, exist_ok=True)
        (d / "main.md").write_text("# main\n", encoding="utf-8")
        (d / "routine.yaml").write_text(yaml.safe_dump({
            "slug": slug, "name": slug, "enabled": True, "description": "member",
            "schedule": {"cron": "", "tz": "Europe/Berlin"}}), encoding="utf-8")
    _goal_met(server.routines_home / "first")

    g = groups.create(server.routines_home, name="lane",
                      members=[{"slug": "first"}, {"slug": "second"}])
    group_runs.arm(server.routines_home, g, default_on_failure="stop")
    fr = FakeRunner()
    mgr = GroupRunManager(server, fr)
    catalog = registry.scan(server)
    await mgr.tick(catalog)      # member 0 is retired → skipped, cursor advances
    await mgr.tick(catalog)      # member 1 fires

    rec = group_runs.read(server.routines_home, g["id"])
    row = rec["log"][0]
    assert row["slug"] == "first" and row["outcome"] == "skipped"    # NOT "failed"
    assert fr.fired == [("second", "group")]                        # under on_failure=stop


# ---- the proposal, and the two decisions that settle it -----------------------------------------

def test_the_finish_that_meets_the_goal_files_one_proposal(make_routine, scripted,
                                                          monkeypatch):
    from rsched.engine import verifier
    from rsched.engine.runtime import run_routine
    from test_loop import TS, finish, probe
    from test_loop import _server as loop_server

    d = make_routine(slug="goalrun")
    server = loop_server(d)
    # the v2 verifier makes its own subcall; this test is about the PROPOSAL, not the judge
    monkeypatch.setattr(verifier, "refuted", lambda loop, summary: [])
    stopping.save(d, {"conditions": [{"text": "the report is published", "scope": "goal"}]},
                  now=NOW)
    scripted([probe(), finish(summary="[s1] met — published and verified")])
    status, _run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"

    queued = pending.load_all(server.routines_home)
    assert [r["kind"] for r in queued] == ["goal-reached"]
    assert queued[0]["routine"] == "goalrun"
    assert queued[0]["fields"]["conditions"][0]["id"] == "s1"
    assert "final goal met" in queued[0]["summary"]


def test_only_one_proposal_however_many_runs_follow(make_routine, tmp_path):
    """A met goal is STICKY, so without queue-once every later run files an identical row."""
    from types import SimpleNamespace

    from rsched.engine import goalreached

    d = make_routine(slug="once")
    server = _server(tmp_path)
    _goal_met(d)
    ctx = SimpleNamespace(depth=0, server=server, routine=SimpleNamespace(
        dir=d, slug="once", name="Once"), run_id="once:1",
        transcript=SimpleNamespace(event=lambda *a, **k: None))
    assert goalreached.maybe_propose_retirement(ctx) != ""
    assert goalreached.maybe_propose_retirement(ctx) == ""
    assert len(pending.load_all(server.routines_home)) == 1


def test_approving_writes_enabled_false_through_the_one_config_writer(api_client, make_routine):
    c, _tmp = api_client
    d = make_routine(slug="retire-me")
    _goal_met(d)
    rec = pending.queue(d.parent, kind="goal-reached", routine="retire-me", run_id="retire-me:1",
                        fields={"conditions": []}, summary="goal met")

    r = c.post(f"/api/pending-creations/{rec['id']}/materialize")
    assert r.status_code == 200, r.text
    assert r.json()["retired"] == "retire-me"
    assert yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))["enabled"] is False
    assert pending.load_all(d.parent) == []


def test_declining_reopens_the_goal_so_the_routine_runs_again(api_client, make_routine):
    """Discarding a retirement means "not yet". It HAS to change the goal document — dropping the
    record alone would leave the routine unscheduled with nothing on the page left to act on."""
    c, _tmp = api_client
    d = make_routine(slug="not-yet")
    _goal_met(d)
    assert stopping.goal_reached(d) is True
    rec = pending.queue(d.parent, kind="goal-reached", routine="not-yet", run_id="not-yet:1",
                        fields={"conditions": []}, summary="goal met")

    r = c.post(f"/api/pending-creations/{rec['id']}/discard", json={"reason": "not really"})
    assert r.status_code == 200, r.text
    assert r.json()["reopened"] == ["s1"]
    assert stopping.goal_reached(d) is False
    # the evidence the run recorded survives being overruled
    assert stopping.load(d)["conditions"][0]["last_verdict"] == "met"


# ---- the one-shot migration ----------------------------------------------------------------------

def test_migration_makes_every_live_condition_a_run_bound_again(make_routine, tmp_path):
    """22 of 31 live routines were reading "the job is DONE. Finish NOW" every run, because the
    0.286.x backfill wrote per-run bounds into a store whose `met` was sticky."""
    from rsched.migrate_stopping_scope import migrate_stopping_scope
    from rsched.paths import atomic_write_json, read_json

    server = _server(tmp_path)
    server.conversations_home = tmp_path / "conv"
    server.background_home = tmp_path / "bg"
    d = make_routine(slug="legacy")
    (d / "state").mkdir(exist_ok=True)
    atomic_write_json(d / "state" / "stopping.json", {
        "mode": "all", "groups": [{"id": "g1", "mode": "all"}],
        "conditions": [
            {"id": "s1", "text": "one increment landed", "status": "met", "group": "g1",
             "note": "shipped", "resolved_run": "legacy:20260904-000000"},
            {"id": "s2", "text": "nothing new was found", "status": "open", "group": "g1"},
            {"id": "s3", "text": "abandoned", "status": "dropped", "group": "g1"}]})

    assert migrate_stopping_scope(server) == 1
    rows = {c["id"]: c for c in read_json(d / "state" / "stopping.json")["conditions"]}
    assert rows["s1"]["scope"] == "run" and rows["s1"]["status"] == "open"
    assert rows["s1"]["last_verdict"] == "met"          # the verdict is kept, as history
    assert rows["s1"]["note"] == "shipped"              # and so is its evidence
    assert rows["s2"]["status"] == "open"
    assert rows["s3"]["status"] == "dropped"            # a user-retired condition is left alone
    assert stopping.goal_reached(d) is False            # nothing retires as a side effect
    assert migrate_stopping_scope(server) == 0          # idempotent


# ---- creation asks BOTH questions ----------------------------------------------------------------

def test_creation_seeds_run_bounds_and_the_final_goal_apart(tmp_path, make_routine):
    """Two different questions, one document. `stopping` is what one run must achieve; `goal` is
    the state after which the routine is finished — and only the second can retire it."""
    from rsched.config import ServerConfig
    from rsched.workflows.scaffold import scaffold

    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir(parents=True, exist_ok=True)
    server.libraries_home = tmp_path / "lib"
    from rsched.bootstrap import seed_libraries
    seed_libraries(server.libraries_home)

    d = scaffold(server, slug="applier", name="Applier",
                 instruction="Prepare and submit the grant application.",
                 workflow_slug="general-task",
                 stopping=["one section was drafted and reviewed"],
                 goal=["the application is submitted before 27 Sep 2026"])
    rows = {c["scope"]: c for c in stopping.load(d)["conditions"]}
    assert rows["run"]["text"] == "one section was drafted and reviewed"
    assert rows["goal"]["text"] == "the application is submitted before 27 Sep 2026"
    # a fresh routine is NOT retired: its goal is open
    assert stopping.goal_reached(d) is False


def test_a_routine_created_without_a_goal_is_perpetual(tmp_path, make_routine):
    """The common case, and it must stay silent: a monitor with no declared end has no verdict
    to report and nothing that could ever switch it off."""
    from rsched.bootstrap import seed_libraries
    from rsched.config import ServerConfig
    from rsched.workflows.scaffold import scaffold

    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir(parents=True, exist_ok=True)
    server.libraries_home = tmp_path / "lib"
    seed_libraries(server.libraries_home)

    d = scaffold(server, slug="watcher", name="Watcher", instruction="Watch the feed.",
                 workflow_slug="general-task", stopping=["the feed was checked"])
    doc = stopping.load(d)
    assert {c["scope"] for c in doc["conditions"]} == {"run"}
    assert stopping.evaluate(doc)["goal_satisfied"] is None
    assert stopping.goal_reached(d) is False
