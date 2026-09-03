"""Graceful self-restart: the restart state machine, sentinel helpers, and runner/scheduler wiring.

No process is ever exited here — trigger_shutdown is patched so the state machine is tested in
isolation from signals.
"""

import asyncio
import json

from rsched.config import RoutineConfig, ServerConfig
from rsched.daemon import restart
from rsched.daemon.events import EventBus
from rsched.daemon.runner import Runner
from rsched.daemon.runner_state import ActiveRun
from rsched.daemon.scheduler import Scheduler


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path
    return s


def test_restart_action_state_machine():
    ra = restart.restart_action
    assert ra(False, [], False) == "idle"
    assert ra(False, ["running"], True) == "idle"          # no request → idle
    # a pending restart NEVER blocks a start: with a run active it keeps scheduling (waits), never drains
    assert ra(True, ["running"], False) == "wait"
    assert ra(True, ["running"], True) == "wait"           # active → wait whatever the idle clock says
    # nothing active → restart ONLY once the idle window has elapsed; before that, keep scheduling
    assert ra(True, [], True) == "restart"
    assert ra(True, [], False) == "wait"
    # a parked run → defer: keep scheduling, never restart out from under a live conversation
    assert ra(True, ["waiting_user"], False) == "defer"
    assert ra(True, ["waiting_user"], True) == "defer"     # parked stays defer even past the window
    assert ra(True, ["running", "paused"], True) == "defer"


def test_sentinel_helpers(tmp_path):
    server = _server(tmp_path)
    assert restart.restart_requested(server) is False
    restart.clear_request(server)                            # idempotent when absent
    p = restart.sentinel_path(server)
    assert ".control" in str(p)                              # a dot-dir the registry scan ignores
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"reason": "self-audit fixed X"}')
    assert restart.restart_requested(server) is True
    restart.clear_request(server)
    assert restart.restart_requested(server) is False


def test_runner_active_states_reads_status(tmp_path):
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    rd = tmp_path / "r" / "runs" / "ts"
    rd.mkdir(parents=True)
    (rd / "status.json").write_text(json.dumps({"state": "waiting_user"}))
    runner.active["r"] = ActiveRun(slug="r", run_id="r:ts", run_ts="ts", run_dir=rd)
    assert runner.active_states() == ["waiting_user"]


def test_fire_refused_while_draining(tmp_path):
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    runner.draining = True
    d = tmp_path / "x"
    d.mkdir()
    cfg = RoutineConfig(slug="x", dir=d, enabled=True)
    assert asyncio.run(runner.fire(cfg)) is None            # refused, nothing spawned
    assert runner.active == {}


def test_scheduler_keeps_scheduling_then_restarts_when_idle(tmp_path, monkeypatch):
    """A pending restart never blocks a start (operator, 2026-09-03): with a run active the
    scheduler keeps firing, and it restarts only once the system is idle."""
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    sched = Scheduler(server, runner, EventBus())
    triggered = []
    monkeypatch.setattr(restart, "trigger_shutdown", lambda: triggered.append(True))
    monkeypatch.setattr(restart, "RESTART_IDLE_S", 0)       # fire the instant it is idle

    # no request → normal scheduling, not draining
    assert sched._maybe_restart() is False and runner.draining is False

    # request arrives with a run still executing → KEEP scheduling (a start is never blocked), no restart
    p = restart.sentinel_path(server)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")
    monkeypatch.setattr(runner, "active_states", lambda: ["running"])
    assert sched._maybe_restart() is False                  # fires still allowed while a restart waits
    assert runner.draining is False and triggered == []

    # the run finishes → nothing active, idle window elapsed → restart: shutdown signalled, sentinel cleared
    monkeypatch.setattr(runner, "active_states", list)
    assert sched._maybe_restart() is True
    assert triggered == [True]
    assert sched._shutting_down is True
    assert restart.restart_requested(server) is False


def test_scheduler_waits_out_the_idle_window_before_restarting(tmp_path, monkeypatch):
    """Nothing active but the idle window has not elapsed yet → keep scheduling, do not restart."""
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    sched = Scheduler(server, runner, EventBus())
    monkeypatch.setattr(restart, "trigger_shutdown",
                        lambda: (_ for _ in ()).throw(AssertionError("restarted before the idle window")))
    p = restart.sentinel_path(server)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")
    monkeypatch.setattr(runner, "active_states", list)      # nothing active
    # the first idle tick only STARTS the clock; the default 10s window has not elapsed → keep scheduling
    assert sched._maybe_restart() is False
    assert runner.draining is False
    assert sched._idle_since is not None


def test_scheduler_defers_restart_while_parked(tmp_path, monkeypatch):
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    sched = Scheduler(server, runner, EventBus())
    monkeypatch.setattr(restart, "trigger_shutdown",
                        lambda: (_ for _ in ()).throw(AssertionError("must not restart while parked")))
    p = restart.sentinel_path(server)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")
    monkeypatch.setattr(runner, "active_states", lambda: ["waiting_user"])
    # a parked run must not restart (that would restart out from under a live conversation)
    assert sched._maybe_restart() is False
    assert runner.draining is False


def test_scheduler_resumes_when_request_withdrawn(tmp_path, monkeypatch):
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    sched = Scheduler(server, runner, EventBus())
    runner.draining = True                                   # was draining
    monkeypatch.setattr(runner, "active_states", list)
    # no sentinel present → idle: draining cleared, scheduling resumes
    assert sched._maybe_restart() is False
    assert runner.draining is False
