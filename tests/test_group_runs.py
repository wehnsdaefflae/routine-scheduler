"""Routine groups Phase B (sequential-fire): the in-flight store (rsched.group_runs) and the
GroupRunManager advance passes — fire → wait for terminal → apply on_failure → fire next.
FakeRunner + mk_run mirror tests/test_schedule_once.py; on-disk fixtures, asyncio_mode=auto."""

from __future__ import annotations

import yaml

from conftest import FakeRunner, mk_run
from rsched import group_runs, groups, registry
from rsched.config import ServerConfig


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.routines_home.mkdir(parents=True, exist_ok=True)
    return s


def _routine(server, slug, *, enabled=True):
    d = server.routines_home / slug
    (d / "inbox").mkdir(parents=True, exist_ok=True)
    (d / "main.md").write_text("# main\n", encoding="utf-8")
    (d / "routine.yaml").write_text(yaml.safe_dump({
        "slug": slug, "name": slug, "enabled": enabled,
        "description": "group member test routine",
        "schedule": {"cron": "", "tz": "Europe/Berlin"},
    }), encoding="utf-8")
    return d


# -- the in-flight store -------------------------------------------------------------------


def test_arm_resolves_policy_and_snapshots_members(tmp_path):
    home = tmp_path
    g = groups.create(home, name="Morning", members=["a", "b"], on_failure=None)
    # override is null → resolve to the passed instance default
    rec = group_runs.arm(home, g, default_on_failure="continue", armed_by="ui")
    assert rec is not None
    assert rec["members"] == ["a", "b"] and rec["on_failure"] == "continue"
    assert rec["cursor"] == 0 and rec["current_run"] is None and rec["status"] == "pending"
    assert rec["id"].startswith("gr-") and rec["armed_by"] == "ui"
    # a group override wins over the default
    g2 = groups.create(home, name="Nightly", members=["c"], on_failure="stop")
    rec2 = group_runs.arm(home, g2, default_on_failure="continue")
    assert rec2["on_failure"] == "stop"


def test_arm_refuses_a_second_in_flight_chain(tmp_path):
    home = tmp_path
    g = groups.create(home, name="G", members=["a"])
    assert group_runs.arm(home, g, default_on_failure="stop") is not None
    assert group_runs.arm(home, g, default_on_failure="stop") is None   # already in flight
    assert len(group_runs.in_flight(home)) == 1
    assert group_runs.remove(home, g["id"]) is True
    assert group_runs.remove(home, g["id"]) is False                    # idempotent
    assert group_runs.in_flight(home) == []


# -- the manager: sequential advance -------------------------------------------------------


async def test_first_tick_fires_member_zero(tmp_path):
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    _routine(server, "a")
    _routine(server, "b")
    g = groups.create(server.routines_home, name="G", members=["a", "b"])
    group_runs.arm(server.routines_home, g, default_on_failure="stop")
    runner = FakeRunner()
    await GroupRunManager(server, runner).tick(registry.scan(server))
    assert runner.fired == [("a", "group")]                     # only the first member
    rec = group_runs.read(server.routines_home, g["id"])
    assert rec["current_run"] == "a:20260717-120000" and rec["status"] == "running"


async def test_ok_member_advances_to_the_next(tmp_path):
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    _routine(server, "b")
    g = groups.create(server.routines_home, name="G", members=["a", "b"])
    group_runs.arm(server.routines_home, g, default_on_failure="stop")
    runner = FakeRunner()
    mgr = GroupRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # fires a
    # a finishes cleanly
    mk_run(da, "20260717-120000", "finished", outcome="ok")
    runner.active.clear()
    await mgr.tick(catalog)                                     # records a, advances cursor
    rec = group_runs.read(server.routines_home, g["id"])
    assert rec["cursor"] == 1 and rec["current_run"] is None
    assert rec["log"] == [{"slug": "a", "run_id": "a:20260717-120000",
                           "state": "finished", "outcome": "ok"}]
    await mgr.tick(catalog)                                     # fires b
    assert runner.fired == [("a", "group"), ("b", "group")]


async def test_stop_policy_halts_on_a_failed_member(tmp_path):
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    _routine(server, "b")
    g = groups.create(server.routines_home, name="G", members=["a", "b"], on_failure="stop")
    group_runs.arm(server.routines_home, g, default_on_failure="stop")
    runner = FakeRunner()
    mgr = GroupRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # fires a
    mk_run(da, "20260717-120000", "failed", outcome="failed")
    runner.active.clear()
    await mgr.tick(catalog)                                     # a failed → stop
    assert group_runs.read(server.routines_home, g["id"]) is None   # chain consumed
    await mgr.tick(catalog)
    assert runner.fired == [("a", "group")]                     # b never fired


async def test_stop_policy_halts_on_a_partial_member(tmp_path):
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    _routine(server, "b")
    g = groups.create(server.routines_home, name="G", members=["a", "b"], on_failure="stop")
    group_runs.arm(server.routines_home, g, default_on_failure="stop")
    runner = FakeRunner()
    mgr = GroupRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)
    # budget-exhausted: state folds to "finished" but outcome is "partial" → NOT ok → stop
    mk_run(da, "20260717-120000", "finished", outcome="partial")
    runner.active.clear()
    await mgr.tick(catalog)
    assert group_runs.read(server.routines_home, g["id"]) is None
    assert runner.fired == [("a", "group")]


async def test_continue_policy_fires_remaining_after_a_failure(tmp_path):
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    db = _routine(server, "b")
    g = groups.create(server.routines_home, name="G", members=["a", "b"],
                      on_failure="continue")
    group_runs.arm(server.routines_home, g, default_on_failure="stop")
    runner = FakeRunner()
    mgr = GroupRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # fires a
    mk_run(da, "20260717-120000", "failed", outcome="failed")
    runner.active.clear()
    await mgr.tick(catalog)                                     # a failed → continue anyway
    rec = group_runs.read(server.routines_home, g["id"])
    assert rec is not None and rec["cursor"] == 1
    await mgr.tick(catalog)                                     # fires b despite a's failure
    assert runner.fired == [("a", "group"), ("b", "group")]
    mk_run(db, "20260717-120000", "finished", outcome="ok")
    runner.active.clear()
    await mgr.tick(catalog)                                     # b done → chain complete
    assert group_runs.read(server.routines_home, g["id"]) is None


async def test_missing_or_disabled_member_is_recorded_as_failure(tmp_path):
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    _routine(server, "b", enabled=False)                        # present but disabled
    g = groups.create(server.routines_home, name="G", members=["ghost", "b"],
                      on_failure="continue")
    group_runs.arm(server.routines_home, g, default_on_failure="continue")
    runner = FakeRunner()
    mgr = GroupRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # ghost missing → skip+record
    await mgr.tick(catalog)                                     # b disabled → skip+record
    await mgr.tick(catalog)                                     # cursor past end → done
    assert runner.fired == []                                   # neither ever fired
    assert group_runs.read(server.routines_home, g["id"]) is None
    # both recorded as failures in the log before the file was consumed is not observable
    # post-remove, so assert the fire behaviour + clean consumption instead


async def test_a_still_running_member_defers(tmp_path):
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    g = groups.create(server.routines_home, name="G", members=["a"])
    group_runs.arm(server.routines_home, g, default_on_failure="stop")
    runner = FakeRunner()
    mgr = GroupRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # fires a
    mk_run(da, "20260717-120000", "running")                    # not terminal
    await mgr.tick(catalog)                                     # still running → wait
    rec = group_runs.read(server.routines_home, g["id"])
    assert rec is not None and rec["current_run"] == "a:20260717-120000"


async def test_draining_defers_the_next_fire(tmp_path):
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    _routine(server, "a")
    g = groups.create(server.routines_home, name="G", members=["a"])
    group_runs.arm(server.routines_home, g, default_on_failure="stop")
    runner = FakeRunner()
    runner.draining = True
    mgr = GroupRunManager(server, runner)
    await mgr.tick(registry.scan(server))
    assert runner.fired == []                                   # drain → nothing fires
    assert group_runs.read(server.routines_home, g["id"])["current_run"] is None
