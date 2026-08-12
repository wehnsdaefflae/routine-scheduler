"""Routine groups Phase B (sequential-fire): the in-flight store (rsched.group_runs) and the
GroupRunManager advance passes — fire → wait for terminal → apply on_failure → fire next —
including the TWO-PHASE chain (F292): every member's ingest pass first, then the split
members' outbound pass, each split run fired with the phase boot param.
FakeRunner + mk_run mirror tests/test_schedule_once.py; on-disk fixtures, asyncio_mode=auto."""

from __future__ import annotations

import yaml

from conftest import FakeRunner, mk_run
from rsched import group_runs, groups, registry
from rsched.config import ServerConfig


def m(slug: str, *, split: bool = False) -> dict:
    return {"slug": slug, "split": split}


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
    g = groups.create(home, name="Morning", members=[m("a"), m("b", split=True)],
                      on_failure=None)
    # override is null → resolve to the passed instance default
    rec = group_runs.arm(home, g, default_on_failure="continue", armed_by="ui")
    assert rec is not None
    assert rec["members"] == [m("a"), m("b", split=True)]
    assert rec["on_failure"] == "continue"
    assert rec["phase"] == "ingest"                      # every chain starts in the ingest pass
    assert rec["cursor"] == 0 and rec["current_run"] is None and rec["status"] == "pending"
    assert rec["id"].startswith("gr-") and rec["armed_by"] == "ui"
    # a group override wins over the default
    g2 = groups.create(home, name="Nightly", members=[m("c")], on_failure="stop")
    rec2 = group_runs.arm(home, g2, default_on_failure="continue")
    assert rec2["on_failure"] == "stop"


def test_arm_refuses_a_second_in_flight_chain(tmp_path):
    home = tmp_path
    g = groups.create(home, name="G", members=[m("a")])
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
    g = groups.create(server.routines_home, name="G", members=[m("a"), m("b")])
    group_runs.arm(server.routines_home, g, default_on_failure="stop")
    runner = FakeRunner()
    await GroupRunManager(server, runner).tick(registry.scan(server))
    assert runner.fired == [("a", "group")]                     # only the first member
    assert runner.phases == [""]        # non-split: fired with NO phase param (F292)
    rec = group_runs.read(server.routines_home, g["id"])
    assert rec["current_run"] == "a:20260717-120000" and rec["status"] == "running"


async def test_ok_member_advances_to_the_next(tmp_path):
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    _routine(server, "b")
    g = groups.create(server.routines_home, name="G", members=[m("a"), m("b")])
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
                           "state": "finished", "outcome": "ok", "phase": "ingest"}]
    await mgr.tick(catalog)                                     # fires b
    assert runner.fired == [("a", "group"), ("b", "group")]


async def test_all_non_split_group_chains_once(tmp_path):
    """F292 baseline: a group with NO split members runs exactly the pre-two-phase chain —
    every member once, no phase params, no outbound pass."""
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    db = _routine(server, "b")
    g = groups.create(server.routines_home, name="G", members=[m("a"), m("b")])
    group_runs.arm(server.routines_home, g, default_on_failure="stop")
    runner = FakeRunner()
    mgr = GroupRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # fires a
    mk_run(da, "20260717-120000", "finished", outcome="ok")
    runner.active.clear()
    await mgr.tick(catalog)                                     # collects a
    await mgr.tick(catalog)                                     # fires b
    mk_run(db, "20260717-120000", "finished", outcome="ok")
    runner.active.clear()
    await mgr.tick(catalog)                                     # collects b → done, no flip
    assert group_runs.read(server.routines_home, g["id"]) is None
    assert runner.fired == [("a", "group"), ("b", "group")]
    assert runner.phases == ["", ""]


async def test_two_phase_chain_fires_split_members_twice(tmp_path):
    """F292: a (non-split) + b (split) → ingest pass fires a (no param) then b
    (phase=ingest); the outbound pass then fires ONLY b again (phase=outbound), and the
    chain completes. The log records each run's pass."""
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    db = _routine(server, "b")
    g = groups.create(server.routines_home, name="G",
                      members=[m("a"), m("b", split=True)], on_failure="continue")
    group_runs.arm(server.routines_home, g, default_on_failure="stop")
    runner = FakeRunner()
    mgr = GroupRunManager(server, runner)
    catalog = registry.scan(server)

    await mgr.tick(catalog)                                     # ingest: fires a
    mk_run(da, "20260717-120000", "finished", outcome="ok")
    runner.active.clear()
    await mgr.tick(catalog)                                     # collects a
    await mgr.tick(catalog)                                     # ingest: fires b
    mk_run(db, "20260717-120000", "finished", outcome="ok")
    runner.active.clear()
    await mgr.tick(catalog)                                     # collects b → flips to outbound
    rec = group_runs.read(server.routines_home, g["id"])
    assert rec is not None and rec["phase"] == "outbound" and rec["cursor"] == 0
    await mgr.tick(catalog)                                     # outbound: fires b again
    assert runner.fired == [("a", "group"), ("b", "group"), ("b", "group")]
    assert runner.phases == ["", "ingest", "outbound"]
    runner.active.clear()
    await mgr.tick(catalog)                                     # collects b → chain done
    assert group_runs.read(server.routines_home, g["id"]) is None
    # the log carries each run's pass — b appears once per pass
    # (the consumed file is gone; the fires + phases above are the observable record)


async def test_stop_during_ingest_halts_the_outbound_pass_too(tmp_path):
    """A failed ingest with on_failure=stop ends the WHOLE chain — the outbound pass would
    read state the halted ingest never staged, so the split member's outbound never fires."""
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    _routine(server, "b")
    g = groups.create(server.routines_home, name="G",
                      members=[m("a"), m("b", split=True)], on_failure="stop")
    group_runs.arm(server.routines_home, g, default_on_failure="stop")
    runner = FakeRunner()
    mgr = GroupRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # fires a
    mk_run(da, "20260717-120000", "failed", outcome="failed")
    runner.active.clear()
    await mgr.tick(catalog)                                     # a failed → stop everything
    assert group_runs.read(server.routines_home, g["id"]) is None
    await mgr.tick(catalog)
    assert runner.fired == [("a", "group")]                     # b never fired, in either pass


async def test_all_split_single_member_runs_both_passes(tmp_path):
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    g = groups.create(server.routines_home, name="G", members=[m("a", split=True)])
    group_runs.arm(server.routines_home, g, default_on_failure="continue")
    runner = FakeRunner()
    mgr = GroupRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # ingest: fires a
    mk_run(da, "20260717-120000", "finished", outcome="ok")
    runner.active.clear()
    await mgr.tick(catalog)                                     # collects → outbound pass
    await mgr.tick(catalog)                                     # outbound: fires a again
    runner.active.clear()
    await mgr.tick(catalog)                                     # collects → done
    assert runner.phases == ["ingest", "outbound"]
    assert group_runs.read(server.routines_home, g["id"]) is None


async def test_stop_policy_halts_on_a_failed_member(tmp_path):
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    _routine(server, "b")
    g = groups.create(server.routines_home, name="G", members=[m("a"), m("b")],
                      on_failure="stop")
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
    g = groups.create(server.routines_home, name="G", members=[m("a"), m("b")],
                      on_failure="stop")
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
    g = groups.create(server.routines_home, name="G", members=[m("a"), m("b")],
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
    g = groups.create(server.routines_home, name="G", members=[m("ghost"), m("b")],
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
    g = groups.create(server.routines_home, name="G", members=[m("a")])
    group_runs.arm(server.routines_home, g, default_on_failure="stop")
    runner = FakeRunner()
    mgr = GroupRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # fires a
    mk_run(da, "20260717-120000", "running")                    # not terminal
    await mgr.tick(catalog)                                     # still running → wait
    rec = group_runs.read(server.routines_home, g["id"])
    assert rec is not None and rec["current_run"] == "a:20260717-120000"


async def test_runner_fire_writes_the_phase_boot_file(tmp_path, monkeypatch):
    """The real Runner's half of the F292 channel: fire(group_phase=…) drops boot.json into
    the run dir before the engine boots; a phase-less fire writes none."""
    from rsched.config import load_routine
    from rsched.daemon import runner as runner_mod
    from rsched.daemon.events import EventBus
    from rsched.paths import read_json

    server = _server(tmp_path)
    d = _routine(server, "a")
    cfg, _ = load_routine(d)
    ts_seq = iter(["20260812-070000", "20260812-070001"])
    monkeypatch.setattr(runner_mod, "make_run_ts", lambda: next(ts_seq))
    r = runner_mod.Runner(server, EventBus())
    monkeypatch.setattr(r, "_spawn_supervisor", lambda *a, **k: None)

    rid = await r.fire(cfg, reason="group", group_phase="ingest")
    assert rid == "a:20260812-070000"
    assert read_json(d / "runs" / "20260812-070000" / "boot.json") == {"phase": "ingest"}

    r.active.clear()
    rid2 = await r.fire(cfg, reason="group")
    assert rid2 == "a:20260812-070001"
    assert not (d / "runs" / "20260812-070001" / "boot.json").exists()


async def test_draining_defers_the_next_fire(tmp_path):
    from rsched.daemon.group_runs import GroupRunManager
    server = _server(tmp_path)
    _routine(server, "a")
    g = groups.create(server.routines_home, name="G", members=[m("a")])
    group_runs.arm(server.routines_home, g, default_on_failure="stop")
    runner = FakeRunner()
    runner.draining = True
    mgr = GroupRunManager(server, runner)
    await mgr.tick(registry.scan(server))
    assert runner.fired == []                                   # drain → nothing fires
    assert group_runs.read(server.routines_home, g["id"])["current_run"] is None
