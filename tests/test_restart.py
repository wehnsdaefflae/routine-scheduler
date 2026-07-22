"""Graceful self-restart: the drain state machine, sentinel helpers, and runner/scheduler wiring.

No process is ever exited here — trigger_shutdown is patched so the state machine is tested in
isolation from signals.
"""

import asyncio
import json

from rsched.config import RoutineConfig, ServerConfig
from rsched.daemon import restart
from rsched.daemon.events import EventBus
from rsched.daemon.runner import ActiveRun, Runner
from rsched.daemon.scheduler import Scheduler


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path
    return s


def test_restart_action_state_machine():
    ra = restart.restart_action
    assert ra(False, [], False) == "idle"
    assert ra(False, ["running"], True) == "idle"          # request withdrawn → idle even mid-drain
    assert ra(True, ["running"], False) == "drain"          # cleanly drainable → start draining
    assert ra(True, [], False) == "restart"                 # nothing active → go
    assert ra(True, [], True) == "restart"
    assert ra(True, ["waiting_user"], False) == "defer"     # parked & not draining → do not freeze
    assert ra(True, ["running", "paused"], False) == "defer"
    assert ra(True, ["waiting_user"], True) == "drain"      # already draining → wait it out, not defer


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


def test_scheduler_drains_then_restarts(tmp_path, monkeypatch):
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    sched = Scheduler(server, runner, EventBus())
    triggered = []
    monkeypatch.setattr(restart, "trigger_shutdown", lambda: triggered.append(True))

    # no request → normal scheduling, not draining
    assert sched._maybe_restart() is False and runner.draining is False

    # request arrives with a run still executing → drain (fire nothing), do not restart yet
    p = restart.sentinel_path(server)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")
    monkeypatch.setattr(runner, "active_states", lambda: ["running"])
    assert sched._maybe_restart() is True
    assert runner.draining is True and triggered == []

    # the run finishes → nothing active → restart: shutdown signalled, sentinel cleared
    monkeypatch.setattr(runner, "active_states", list)
    assert sched._maybe_restart() is True
    assert triggered == [True]
    assert sched._shutting_down is True
    assert restart.restart_requested(server) is False


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
    # a parked run must not begin a drain (that would freeze scheduling on a human)
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


def test_restart_action_waits_for_in_flight_builds():
    ra = restart.restart_action
    assert ra(True, [], False, 0) == "restart"              # no runs, no builds → go
    assert ra(True, [], False, 1) == "drain"                # a build in flight → wait, don't restart
    assert ra(True, ["running"], False, 2) == "drain"       # runs and builds → drain
    assert ra(True, [], True, 3) == "drain"                 # already draining, builds remain → wait
    assert ra(True, [], True, 0) == "restart"               # drained, builds done → go
    assert ra(True, ["waiting_user"], False, 1) == "defer"  # a parked run still defers (a build isn't parked)
    assert ra(False, [], False, 5) == "idle"                # no request → idle regardless of builds


def test_scheduler_waits_for_wizard_build(tmp_path, monkeypatch):
    """A self-restart must drain in-flight wizard builds too (they are unpersisted web-process
    tasks) — do not exit while one is still scaffolding, or it would be stranded half-built."""
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    sched = Scheduler(server, runner, EventBus())
    triggered = []
    monkeypatch.setattr(restart, "trigger_shutdown", lambda: triggered.append(True))
    p = restart.sentinel_path(server)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")
    monkeypatch.setattr(runner, "active_states", list)   # no engine runs active...
    sched.wizard_builds.add(".wizard-x")                        # ...but a build is in flight
    assert sched._maybe_restart() is True                       # → drain, not restart
    assert runner.draining is True and triggered == []
    sched.wizard_builds.discard(".wizard-x")                    # the build finishes
    assert sched._maybe_restart() is True
    assert triggered == [True]                                  # now it restarts
    assert restart.restart_requested(server) is False


# ---- clarify runs hold the restart (2026-07-16 incident) ---------------------------------------


_CLARIFY_N = [0]


def _clarify_session(home, _name, **status):
    """One clarify run's status.json under the REAL layout — the protected clarification
    template's runs/ dir (D13=B; the .wizard-* workspace holds no run state)."""
    _CLARIFY_N[0] += 1
    rd = home / "clarification" / "runs" / f"20260716-10111{_CLARIFY_N[0]}"
    rd.mkdir(parents=True)
    (rd / "status.json").write_text(json.dumps({"state": "running", **status}))
    return rd


def test_clarify_states_counts_live_sessions(tmp_path):
    """Clarify engine runs are spawned by the web layer, never via Runner.fire — invisible
    to active_states(). clarify_states surfaces them from clarification/runs for the
    restart gate: a live pid counts with its state, a dead pid and a terminal run do not."""
    import os

    server = _server(tmp_path)
    assert restart.clarify_states(server) == []                       # no sessions at all
    _clarify_session(tmp_path, ".wizard-a", state="waiting_user", pid=os.getpid())
    _clarify_session(tmp_path, ".wizard-b", state="running", pid=99999999)     # dead pid
    _clarify_session(tmp_path, ".wizard-c", state="finished", pid=os.getpid())  # terminal
    assert restart.clarify_states(server) == ["waiting_user"]


def test_clarify_states_fresh_vs_stale_starting(tmp_path):
    """A pid-less 'starting' run (the engine subprocess is still booting/decomposing) counts
    only while its stamp is fresh — an orphaned session must never block restarts forever."""
    from datetime import UTC, datetime, timedelta

    server = _server(tmp_path)
    now = datetime.now(UTC)
    _clarify_session(tmp_path, ".wizard-fresh", state="starting", pid=None,
                     updated=now.isoformat())
    _clarify_session(tmp_path, ".wizard-stale", state="starting", pid=None,
                     updated=(now - timedelta(hours=2)).isoformat())
    _clarify_session(tmp_path, ".wizard-unstamped", state="starting", pid=None)
    assert restart.clarify_states(server) == ["starting"]


def test_scheduler_waits_for_clarify_run(tmp_path, monkeypatch):
    """The 2026-07-16 incident: a restart drained while a clarification was in flight and killed
    the user's setup conversation at turn 0. The drain must wait for live clarify runs too."""
    import os

    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    sched = Scheduler(server, runner, EventBus())
    triggered = []
    monkeypatch.setattr(restart, "trigger_shutdown", lambda: triggered.append(True))
    p = restart.sentinel_path(server)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")
    monkeypatch.setattr(runner, "active_states", list)   # no engine runs the runner can see
    rd = _clarify_session(tmp_path, ".wizard-live", state="running", pid=os.getpid())
    assert sched._maybe_restart() is True                # → drain, not restart
    assert runner.draining is True and triggered == []
    (rd / "status.json").write_text(json.dumps({"state": "finished"}))   # the clarify run ends
    assert sched._maybe_restart() is True
    assert triggered == [True]
    assert restart.restart_requested(server) is False


def test_scheduler_defers_while_clarify_waits_on_user(tmp_path, monkeypatch):
    """A clarify run parked on a blocking question defers the restart exactly like an
    ordinary waiting_user run — never freeze scheduling on a human mid-conversation."""
    import os

    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    sched = Scheduler(server, runner, EventBus())
    monkeypatch.setattr(restart, "trigger_shutdown",
                        lambda: (_ for _ in ()).throw(AssertionError("must not restart")))
    p = restart.sentinel_path(server)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")
    monkeypatch.setattr(runner, "active_states", list)
    _clarify_session(tmp_path, ".wizard-parked", state="waiting_user", pid=os.getpid())
    assert sched._maybe_restart() is False
    assert runner.draining is False
