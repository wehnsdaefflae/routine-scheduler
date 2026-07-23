"""Scheduler fire logic and Runner subprocess supervision (stub engines, real processes)."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import rsched.daemon.runner as runner_mod
import rsched.daemon.scheduler as sched_mod
from conftest import FakeRunner
from rsched.config import ServerConfig, load_routine
from rsched.daemon.events import EventBus
from rsched.daemon.runner import Runner, _notable_stderr
from rsched.daemon.scheduler import Scheduler
from rsched.engine.transcript import read_events
from rsched.paths import atomic_write_json, read_json
from rsched.registry import read_run, scan


def _server(tmp_path, max_concurrent=2) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.max_concurrent_runs = max_concurrent
    return s


def test_rescan_keeps_owed_fires(make_routine, tmp_path):
    make_routine(slug="owed")
    sched = Scheduler(_server(tmp_path), FakeRunner(), EventBus())
    sched.rescan()
    assert "owed" in sched.next_fires and sched.next_fires["owed"] > datetime.now(UTC)
    past = datetime.now(UTC) - timedelta(seconds=30)
    sched.next_fires["owed"] = past
    sched.rescan()
    assert sched.next_fires["owed"] == past  # a due fire survives the rescan


async def test_boot_catchup_fires_run_once(make_routine, tmp_path):
    d = make_routine(slug="catchup")
    text = (d / "routine.yaml").read_text().replace("catchup: skip", "catchup: run_once")
    (d / "routine.yaml").write_text(text)
    make_routine(slug="skipper")  # default skip policy → no catch-up
    fr = FakeRunner()
    sched = Scheduler(_server(tmp_path), fr, EventBus())
    sched.rescan()
    await sched.boot_catchup()
    assert fr.fired == [("catchup", "catchup")]


async def test_fire_on_due_tick(make_routine, tmp_path, monkeypatch):
    make_routine(slug="ticker")
    monkeypatch.setattr(sched_mod, "TICK_S", 0.02)
    fr = FakeRunner()
    sched = Scheduler(_server(tmp_path), fr, EventBus())
    task = asyncio.create_task(sched.run_forever())
    await asyncio.sleep(0.05)
    sched.next_fires["ticker"] = datetime.now(UTC) - timedelta(seconds=1)
    await asyncio.sleep(0.1)
    task.cancel()
    assert ("ticker", "schedule") in fr.fired
    assert sched.next_fires["ticker"] > datetime.now(UTC)  # advanced past the fire


async def test_library_sync_fires_on_due_tick(tmp_path, monkeypatch):
    server = _server(tmp_path)
    server.library_sync.enabled = True
    monkeypatch.setattr(sched_mod, "TICK_S", 0.02)
    ran = []
    monkeypatch.setattr(sched_mod.library_sync, "run_sync",
                        lambda s: ran.append(s) or {"status": "ok"})
    sched = Scheduler(server, FakeRunner(), EventBus())
    task = asyncio.create_task(sched.run_forever())
    await asyncio.sleep(0.05)
    assert sched.sync_next is not None                     # scheduled from config at rescan
    sched.sync_next = datetime.now(UTC) - timedelta(seconds=1)
    assert await _wait_for(lambda: ran)
    task.cancel()
    assert ran == [server]
    assert sched.sync_next > datetime.now(UTC)    # advanced past the fire
    assert sched.snapshot()["library_sync_next"]


async def test_library_sync_disabled_never_scheduled(tmp_path):
    sched = Scheduler(_server(tmp_path), FakeRunner(), EventBus())
    sched.rescan()
    assert sched.sync_next is None
    assert sched.snapshot()["library_sync_next"] is None


# --- Runner with stub engine processes -------------------------------------------


def _stub_engine(monkeypatch, script: str):
    """Replace the engine subprocess with a bash stub. The stub runs with cwd=<routine dir>;
    $1 is the run ts (script references runs/$TS/...)."""

    def cmd(slug, run_ts, *, resume=False):
        return ["bash", "-c", script.replace("{TS}", run_ts)]

    monkeypatch.setattr(runner_mod, "engine_cmd", cmd)


async def _wait_for(cond, wait_s=5.0):
    for _ in range(int(wait_s / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_fire_reap_and_overrun(make_routine, tmp_path, monkeypatch):
    d = make_routine(slug="stub")
    cfg, _ = load_routine(d)
    _stub_engine(monkeypatch, "sleep 0.2")
    runner = Runner(_server(tmp_path), EventBus())
    run_id = await runner.fire(cfg)
    assert run_id and runner.is_active("stub")
    st = read_json(d / "runs" / run_id.split(":")[1] / "status.json")
    assert st["state"] == "queued"
    assert await runner.fire(cfg) is None  # overrun skipped
    assert await _wait_for(lambda: not runner.is_active("stub"))
    # stub never wrote a real status → daemon closed it out with a synthetic finish
    run_dir = d / "runs" / run_id.split(":")[1]
    info = read_run(run_dir, "stub")
    assert info.state == "failed" and "engine exited" in info.summary
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert events[-1]["type"] == "finish"


async def test_concurrency_cap_queues_spawn(make_routine, tmp_path, monkeypatch):
    d1 = make_routine(slug="one")
    d2 = make_routine(slug="two")
    cfg1, _ = load_routine(d1)
    cfg2, _ = load_routine(d2)
    _stub_engine(monkeypatch, "sleep 0.3")
    runner = Runner(_server(tmp_path, max_concurrent=1), EventBus())
    await runner.fire(cfg1)
    await runner.fire(cfg2)
    assert await _wait_for(lambda: runner.active["one"].proc is not None)
    assert runner.active["two"].proc is None  # queued: no process until a slot frees
    assert await _wait_for(lambda: "one" not in runner.active)
    assert await _wait_for(lambda: runner.active["two"].proc is not None)
    assert await _wait_for(lambda: not runner.active)


async def test_waiting_user_releases_slot(make_routine, tmp_path, monkeypatch):
    d1 = make_routine(slug="asker")
    d2 = make_routine(slug="worker")
    cfg1, _ = load_routine(d1)
    cfg2, _ = load_routine(d2)
    monkeypatch.setattr(runner_mod, "STATUS_POLL_S", 0.03)
    _stub_engine(monkeypatch,
                 'printf \'{"state": "waiting_user", "pid": 1}\' > runs/{TS}/status.json.tmp '
                 '&& mv runs/{TS}/status.json.tmp runs/{TS}/status.json && sleep 0.6')
    runner = Runner(_server(tmp_path, max_concurrent=1), EventBus())
    await runner.fire(cfg1)
    assert await _wait_for(lambda: runner.active["asker"].proc is not None)
    await runner.fire(cfg2)
    # asker parks in waiting_user → its slot frees → worker spawns while asker still lives
    assert await _wait_for(lambda: runner.active.get("worker") and runner.active["worker"].proc)
    assert runner.is_active("asker")
    assert await _wait_for(lambda: not runner.active, wait_s=8)


async def test_abort_active_run(make_routine, tmp_path, monkeypatch):
    d = make_routine(slug="abortee")
    cfg, _ = load_routine(d)
    _stub_engine(monkeypatch, "sleep 30")
    monkeypatch.setattr(runner_mod, "KILL_GRACE_S", 1)
    runner = Runner(_server(tmp_path), EventBus())
    await runner.fire(cfg)
    assert await _wait_for(lambda: runner.active["abortee"].proc is not None)
    assert await runner.abort("abortee") is True
    assert await _wait_for(lambda: not runner.active)
    assert await runner.abort("abortee") is False


def test_recover_orphans(make_routine, tmp_path):
    d = make_routine(slug="orphan")
    run_dir = d / "runs" / "20260701-070000"
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "status.json",
                      {"run_id": "orphan:20260701-070000", "state": "running", "pid": 999999})
    (run_dir / "transcript.jsonl").write_text(json.dumps({"type": "header"}) + "\n")
    runner = Runner(_server(tmp_path), EventBus())
    fixed = runner.recover_orphans(scan(_server(tmp_path)))
    assert fixed == 1
    info = read_run(run_dir, "orphan")
    assert info.state == "failed" and "orphaned" in info.summary


def test_notable_stderr_extracts_only_warnings_and_errors():
    # Info/debug chatter is dropped; WARNING/ERROR/CRITICAL/traceback lines are kept, tail-first.
    assert _notable_stderr(b"") == ""
    assert _notable_stderr(b"2026 rsched INFO run_started\n2026 rsched INFO run_finished") == ""
    out = _notable_stderr(b"2026 rsched INFO ok\n"
                          b"2026 rsched.util_stats WARNING snapshot write to /x failed: boom\n"
                          b"2026 rsched INFO more")
    assert "WARNING" in out and "snapshot write to /x failed" in out and "INFO" not in out
    assert "Traceback (most recent call last)" in _notable_stderr(
        b"Traceback (most recent call last):\n  File ...\nValueError: x")
    # A chatty run can't flood the log: only the tail of notable lines is kept.
    flood = b"\n".join(f"2026 rsched ERROR e{i}".encode() for i in range(50))
    kept = _notable_stderr(flood, max_lines=12)
    assert kept.count("ERROR") == 12 and "e49" in kept and "e0 " not in kept


async def test_reap_surfaces_clean_exit_diagnostics(make_routine, tmp_path, monkeypatch, caplog):
    # A run that finishes cleanly but logged a WARNING (stdout is DEVNULL, stderr otherwise
    # dropped) must still leave that line in the daemon log — the F97 breadcrumb that vanished.
    d = make_routine(slug="warner")
    cfg, _ = load_routine(d)
    _stub_engine(monkeypatch,
                 'printf \'{"state": "finished", "pid": 1}\' > runs/{TS}/status.json.tmp '
                 '&& mv runs/{TS}/status.json.tmp runs/{TS}/status.json '
                 '&& echo "2026 rsched.util_stats WARNING util-stats snapshot write failed: boom" 1>&2')
    runner = Runner(_server(tmp_path), EventBus())
    with caplog.at_level(logging.WARNING, logger="rsched.runner"):
        await runner.fire(cfg)
        assert await _wait_for(lambda: not runner.is_active("warner"))
    surfaced = [r.getMessage() for r in caplog.records if "finished but logged" in r.getMessage()]
    assert surfaced and "util-stats snapshot write failed" in surfaced[0]
