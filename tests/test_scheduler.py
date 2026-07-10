"""Scheduler fire logic and Runner subprocess supervision (stub engines, real processes)."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

import rsched.daemon.runner as runner_mod
import rsched.daemon.scheduler as sched_mod
from rsched.config import ServerConfig, load_routine
from rsched.daemon.events import EventBus
from rsched.daemon.registry import read_run, scan
from rsched.daemon.runner import Runner
from rsched.daemon.scheduler import Scheduler
from rsched.engine.transcript import read_events
from rsched.paths import atomic_write_json, read_json


def _server(tmp_path, max_concurrent=2) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.max_concurrent_runs = max_concurrent
    return s


class FakeRunner:
    def __init__(self):
        self.fired: list[tuple[str, str]] = []
        self.active: dict = {}
        self.draining = False

    async def fire(self, cfg, *, reason="schedule"):
        self.fired.append((cfg.slug, reason))
        return f"{cfg.slug}:x"

    def active_states(self):
        return []

    def recover_orphans(self, catalog):
        return 0


def test_rescan_keeps_owed_fires(make_routine, tmp_path):
    make_routine(slug="owed")
    sched = Scheduler(_server(tmp_path), FakeRunner(), EventBus())
    sched.rescan()
    assert "owed" in sched.next_fires and sched.next_fires["owed"] > datetime.now(timezone.utc)
    past = datetime.now(timezone.utc) - timedelta(seconds=30)
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
    sched.next_fires["ticker"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    await asyncio.sleep(0.1)
    task.cancel()
    assert ("ticker", "schedule") in fr.fired
    assert sched.next_fires["ticker"] > datetime.now(timezone.utc)  # advanced past the fire


# --- Runner with stub engine processes -------------------------------------------


def _stub_engine(monkeypatch, script: str):
    """Replace the engine subprocess with a bash stub. The stub runs with cwd=<routine dir>;
    $1 is the run ts (script references runs/$TS/...)."""

    def cmd(slug, run_ts, *, resume=False):
        return ["bash", "-c", script.replace("{TS}", run_ts)]

    monkeypatch.setattr(runner_mod, "engine_cmd", cmd)


async def _wait_for(cond, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
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
    assert await _wait_for(lambda: not runner.active, timeout=8)


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
