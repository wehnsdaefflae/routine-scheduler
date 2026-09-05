"""LANE CHAINS (sequential fire): the in-flight store (rsched.lane_runs), the LaneRunManager
advance passes — fire → wait for terminal → apply on_failure → fire the next — and the
chain health events an audit reads (F316). A chain fires each member exactly ONCE, in the
lane's order (D90).

Only the TEMPORAL axis is here. What a lane's members share — the inherited config block and
the shared store — is a DOMAIN and has nothing to do with the order they fire in; the chain
neither reads it nor changes it (tests/test_domains.py).

FakeRunner + mk_run mirror tests/test_schedule_once.py; on-disk fixtures, asyncio_mode=auto."""

from __future__ import annotations

import yaml

from conftest import FakeRunner, mk_run
from rsched import lane_runs, lanes, registry
from rsched.config import ServerConfig


def m(slug: str) -> dict:
    return {"slug": slug}


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
        "description": "lane member test routine",
        "schedule": {"cron": "", "tz": "Europe/Berlin"},
    }), encoding="utf-8")
    return d


# -- the in-flight store -------------------------------------------------------------------


def test_arm_resolves_policy_and_snapshots_members(tmp_path):
    home = tmp_path
    lane = lanes.create(home, name="Morning", members=[m("a"), m("b")],
                        on_failure=None)
    # override is null → resolve to the passed instance default
    rec = lane_runs.arm(home, lane, default_on_failure="continue", armed_by="ui")
    assert rec is not None
    assert rec["members"] == [m("a"), m("b")]
    assert rec["on_failure"] == "continue"
    assert rec["cursor"] == 0 and rec["current_run"] is None and rec["status"] == "pending"
    assert rec["id"].startswith("lr-") and rec["armed_by"] == "ui"
    # a lane's own override wins over the default
    lane2 = lanes.create(home, name="Nightly", members=[m("c")], on_failure="stop")
    rec2 = lane_runs.arm(home, lane2, default_on_failure="continue")
    assert rec2["on_failure"] == "stop"


def test_arm_refuses_a_second_in_flight_chain(tmp_path):
    home = tmp_path
    lane = lanes.create(home, name="G", members=[m("a")])
    assert lane_runs.arm(home, lane, default_on_failure="stop") is not None
    assert lane_runs.arm(home, lane, default_on_failure="stop") is None   # already in flight
    assert len(lane_runs.in_flight(home)) == 1
    assert lane_runs.remove(home, lane["id"]) is True
    assert lane_runs.remove(home, lane["id"]) is False                    # idempotent
    assert lane_runs.in_flight(home) == []


# -- the manager: sequential advance -------------------------------------------------------


async def test_first_tick_fires_member_zero(tmp_path):
    from rsched.daemon.lane_runs import LaneRunManager
    server = _server(tmp_path)
    _routine(server, "a")
    _routine(server, "b")
    lane = lanes.create(server.routines_home, name="G", members=[m("a"), m("b")])
    lane_runs.arm(server.routines_home, lane, default_on_failure="stop")
    runner = FakeRunner()
    await LaneRunManager(server, runner).tick(registry.scan(server))
    # only the first member, firing with reason "lane" — the label the daemon broadcasts on
    # the run-started event, which is how an operator reading the activity stream tells a
    # chained member run from a cron one.
    assert runner.fired == [("a", "lane")]
    rec = lane_runs.read(server.routines_home, lane["id"])
    assert rec["current_run"] == "a:20260717-120000" and rec["status"] == "running"


async def test_ok_member_advances_to_the_next(tmp_path):
    from rsched.daemon.lane_runs import LaneRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    _routine(server, "b")
    lane = lanes.create(server.routines_home, name="G", members=[m("a"), m("b")])
    lane_runs.arm(server.routines_home, lane, default_on_failure="stop")
    runner = FakeRunner()
    mgr = LaneRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # fires a
    # a finishes cleanly
    mk_run(da, "20260717-120000", "finished", outcome="ok")
    runner.active.clear()
    await mgr.tick(catalog)                                     # records a, advances cursor
    rec = lane_runs.read(server.routines_home, lane["id"])
    assert rec["cursor"] == 1 and rec["current_run"] is None
    assert rec["log"] == [{"slug": "a", "run_id": "a:20260717-120000",
                           "state": "finished", "outcome": "ok"}]
    await mgr.tick(catalog)                                     # fires b
    assert runner.fired == [("a", "lane"), ("b", "lane")]


async def test_a_lane_chains_once(tmp_path):
    """The baseline chain: every member once, in order, then the file is consumed."""
    from rsched.daemon.lane_runs import LaneRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    db = _routine(server, "b")
    lane = lanes.create(server.routines_home, name="G", members=[m("a"), m("b")])
    lane_runs.arm(server.routines_home, lane, default_on_failure="stop")
    runner = FakeRunner()
    mgr = LaneRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # fires a
    mk_run(da, "20260717-120000", "finished", outcome="ok")
    runner.active.clear()
    await mgr.tick(catalog)                                     # collects a
    await mgr.tick(catalog)                                     # fires b
    mk_run(db, "20260717-120000", "finished", outcome="ok")
    runner.active.clear()
    await mgr.tick(catalog)                                     # collects b → done
    assert lane_runs.read(server.routines_home, lane["id"]) is None
    assert runner.fired == [("a", "lane"), ("b", "lane")]


async def test_stop_policy_halts_on_a_failed_member(tmp_path):
    from rsched.daemon.lane_runs import LaneRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    _routine(server, "b")
    lane = lanes.create(server.routines_home, name="G", members=[m("a"), m("b")],
                        on_failure="stop")
    lane_runs.arm(server.routines_home, lane, default_on_failure="stop")
    runner = FakeRunner()
    mgr = LaneRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # fires a
    mk_run(da, "20260717-120000", "failed", outcome="failed")
    runner.active.clear()
    await mgr.tick(catalog)                                     # a failed → stop
    assert lane_runs.read(server.routines_home, lane["id"]) is None   # chain consumed
    await mgr.tick(catalog)
    assert runner.fired == [("a", "lane")]                      # b never fired


async def test_stop_policy_halts_on_a_partial_member(tmp_path):
    from rsched.daemon.lane_runs import LaneRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    _routine(server, "b")
    lane = lanes.create(server.routines_home, name="G", members=[m("a"), m("b")],
                        on_failure="stop")
    lane_runs.arm(server.routines_home, lane, default_on_failure="stop")
    runner = FakeRunner()
    mgr = LaneRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)
    # budget-exhausted: state folds to "finished" but outcome is "partial" → NOT ok → stop
    mk_run(da, "20260717-120000", "finished", outcome="partial")
    runner.active.clear()
    await mgr.tick(catalog)
    assert lane_runs.read(server.routines_home, lane["id"]) is None
    assert runner.fired == [("a", "lane")]


async def test_continue_policy_fires_remaining_after_a_failure(tmp_path):
    from rsched.daemon.lane_runs import LaneRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    db = _routine(server, "b")
    lane = lanes.create(server.routines_home, name="G", members=[m("a"), m("b")],
                        on_failure="continue")
    lane_runs.arm(server.routines_home, lane, default_on_failure="stop")
    runner = FakeRunner()
    mgr = LaneRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # fires a
    mk_run(da, "20260717-120000", "failed", outcome="failed")
    runner.active.clear()
    await mgr.tick(catalog)                                     # a failed → continue anyway
    rec = lane_runs.read(server.routines_home, lane["id"])
    assert rec is not None and rec["cursor"] == 1
    await mgr.tick(catalog)                                     # fires b despite a's failure
    assert runner.fired == [("a", "lane"), ("b", "lane")]
    mk_run(db, "20260717-120000", "finished", outcome="ok")
    runner.active.clear()
    await mgr.tick(catalog)                                     # b done → chain complete
    assert lane_runs.read(server.routines_home, lane["id"]) is None


async def test_missing_or_disabled_member_is_recorded_as_failure(tmp_path):
    from rsched.daemon.lane_runs import LaneRunManager
    server = _server(tmp_path)
    _routine(server, "b", enabled=False)                        # present but disabled
    lane = lanes.create(server.routines_home, name="G", members=[m("ghost"), m("b")],
                        on_failure="continue")
    lane_runs.arm(server.routines_home, lane, default_on_failure="continue")
    runner = FakeRunner()
    mgr = LaneRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # ghost missing → skip+record
    await mgr.tick(catalog)                                     # b disabled → skip+record
    await mgr.tick(catalog)                                     # cursor past end → done
    assert runner.fired == []                                   # neither ever fired
    assert lane_runs.read(server.routines_home, lane["id"]) is None
    # the log rows recording both as failures go with the consumed file, so the fire
    # behaviour + the clean consumption are what there is to assert


async def test_a_still_running_member_defers(tmp_path):
    from rsched.daemon.lane_runs import LaneRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    lane = lanes.create(server.routines_home, name="G", members=[m("a")])
    lane_runs.arm(server.routines_home, lane, default_on_failure="stop")
    runner = FakeRunner()
    mgr = LaneRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # fires a
    mk_run(da, "20260717-120000", "running")                    # not terminal
    await mgr.tick(catalog)                                     # still running → wait
    rec = lane_runs.read(server.routines_home, lane["id"])
    assert rec is not None and rec["current_run"] == "a:20260717-120000"


async def test_draining_defers_the_next_fire(tmp_path):
    from rsched.daemon.lane_runs import LaneRunManager
    server = _server(tmp_path)
    _routine(server, "a")
    lane = lanes.create(server.routines_home, name="G", members=[m("a")])
    lane_runs.arm(server.routines_home, lane, default_on_failure="stop")
    runner = FakeRunner()
    runner.draining = True
    mgr = LaneRunManager(server, runner)
    await mgr.tick(registry.scan(server))
    assert runner.fired == []                                   # drain → nothing fires
    assert lane_runs.read(server.routines_home, lane["id"])["current_run"] is None


# -- F316: chain health events -------------------------------------------------------------


def _health_events(server):
    import json
    p = server.routines_home / ".control" / "health-events.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


async def test_chain_end_emits_a_done_health_event(tmp_path):
    """F316: a chain's end writes lane_chain_done — the periodic heartbeat whose ABSENCE
    is how an audit detects a silently starved lane (the in-flight file is consumed at
    finalize, so this event is the chain's only durable record)."""
    from rsched.daemon.lane_runs import LaneRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    db = _routine(server, "b")
    lane = lanes.create(server.routines_home, name="Nightly", members=[m("a"), m("b")],
                        on_failure="continue")
    lane_runs.arm(server.routines_home, lane, default_on_failure="continue")
    runner = FakeRunner()
    mgr = LaneRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # fires a
    mk_run(da, "20260717-120000", "finished", outcome="ok")
    runner.active.clear()
    await mgr.tick(catalog)                                     # collects a
    await mgr.tick(catalog)                                     # fires b
    mk_run(db, "20260717-120000", "finished", outcome="failed")
    runner.active.clear()
    await mgr.tick(catalog)                                     # collects b → chain done
    evs = [e for e in _health_events(server) if e["event"] == "lane_chain_done"]
    assert len(evs) == 1
    ev = evs[0]
    assert ev["routine"] == lane["id"] and ev["run_id"].startswith("lr-")
    assert "Nightly: 2 member runs, 1 not-ok (b)" in ev["detail"]
    assert "armed_by=ui" in ev["detail"]


async def test_stop_emits_a_stopped_health_event(tmp_path):
    """A policy stop is lane_chain_stopped, never lane_chain_done."""
    from rsched.daemon.lane_runs import LaneRunManager
    server = _server(tmp_path)
    da = _routine(server, "a")
    _routine(server, "b")
    lane = lanes.create(server.routines_home, name="G", members=[m("a"), m("b")],
                        on_failure="stop")
    lane_runs.arm(server.routines_home, lane, default_on_failure="continue")
    runner = FakeRunner()
    mgr = LaneRunManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)                                     # fires a
    mk_run(da, "20260717-120000", "finished", outcome="failed")
    runner.active.clear()
    await mgr.tick(catalog)                                     # collects failure → stop
    evs = _health_events(server)
    stopped = [e for e in evs if e["event"] == "lane_chain_stopped"]
    assert len(stopped) == 1 and "1 not-ok (a)" in stopped[0]["detail"]
    assert not [e for e in evs if e["event"] == "lane_chain_done"]


async def test_skipped_member_emits_a_health_event(tmp_path):
    """A missing/disabled member is visible to audit consumers, not only to rec['log']."""
    from rsched.daemon.lane_runs import LaneRunManager
    server = _server(tmp_path)
    _routine(server, "a")
    lane = lanes.create(server.routines_home, name="G", members=[m("ghost"), m("a")],
                        on_failure="continue")
    lane_runs.arm(server.routines_home, lane, default_on_failure="continue")
    runner = FakeRunner()
    mgr = LaneRunManager(server, runner)
    await mgr.tick(registry.scan(server))                       # skips ghost
    evs = [e for e in _health_events(server)
           if e["event"] == "lane_chain_member_skipped"]
    assert len(evs) == 1
    assert evs[0]["routine"] == "ghost" and evs[0]["run_id"] == ""
    assert "chain continues" in evs[0]["detail"]
