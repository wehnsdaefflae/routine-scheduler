"""Admission uses real bounded subprocesses, isolated routine homes, and no model boot."""
import asyncio
import sys

import pytest

from rsched.config import RoutineConfig, ServerConfig
from rsched.config.routine import RunGateConfig
from rsched.daemon import run_gate, runner_state
from rsched.daemon.events import EventBus
from rsched.daemon.runner import Runner
from rsched.paths import read_json


@pytest.fixture
def setup_gate(tmp_path, monkeypatch):
    root = tmp_path / "routines" / "gate-test"
    (root / "scripts").mkdir(parents=True)
    server = ServerConfig(routines_home=root.parent, libraries_home=tmp_path / "lib",
                          conversations_home=tmp_path / "conversations",
                          background_home=tmp_path / "background", sandbox="strict")
    cfg = RoutineConfig(slug=root.name, dir=root, run_gate=RunGateConfig(enabled=True, timeout_s=8))
    monkeypatch.setenv("RSCHED_CONFIG", str(tmp_path / "config.yaml"))
    return cfg, server, Runner(server, EventBus())


def script(cfg, body):
    (cfg.dir / "scripts/gate.py").write_text(body)


async def finish(runner, cfg, reason="schedule"):
    rid = await runner.fire(cfg, reason=reason)
    run = runner.active[cfg.slug]
    await asyncio.gather(*runner._supervisors)
    assert cfg.slug not in runner.active
    assert runner.semaphore._value == runner.server.max_concurrent_runs
    return rid, run, read_json(run.run_dir / "status.json")


def skip_body():
    return 'print(\'{"version":1,"decision":"skip","reason":"no new work"}\')'


async def test_skip_never_builds_engine_command(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    script(cfg, skip_body())
    monkeypatch.setattr(runner_state, "engine_cmd", lambda *a, **k: pytest.fail("engine boot"))
    rid, run, st = await finish(runner, cfg)
    assert st["run_id"] == rid and st["state"] == "finished" and st["outcome"] == "skipped"
    assert st["usage"] == {"in": 0, "out": 0} and st["turn"] == 0
    assert "no new work" in (run.run_dir / "result.md").read_text()
    assert read_json(run.run_dir / "gate.json")["decision"] == "skip"


@pytest.mark.parametrize("body", ['print("junk")', "raise SystemExit(1)",
    'print(\'{"version":true,"decision":"skip","reason":"x"}\')',
    'print("x" * 20000)', 'import sys; sys.stderr.write("x" * 20000)',
    "import time; time.sleep(30)", ""])
async def test_errors_never_skip_or_boot(setup_gate, monkeypatch, body):
    cfg, _, runner = setup_gate
    script(cfg, body)
    monkeypatch.setattr(runner_state, "engine_cmd", lambda *a, **k: pytest.fail("engine boot"))
    _, run, st = await finish(runner, cfg)
    assert st["state"] == "failed" and st["outcome"] == "failed"
    assert read_json(run.run_dir / "gate.json")["decision"] == "error"


def fake_engine(monkeypatch, cfg):
    marker = cfg.dir / "engine-started"
    monkeypatch.setattr(runner_state, "engine_cmd", lambda *a, **k:
        [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"])
    return marker


@pytest.mark.parametrize("reason", ["manual", "resume", "trigger", "schedule_once"])
async def test_scope_bypasses_missing_gate(setup_gate, monkeypatch, reason):
    cfg, _, runner = setup_gate
    marker = fake_engine(monkeypatch, cfg)
    _, run, _ = await finish(runner, cfg, reason)
    assert marker.exists()
    assert read_json(run.run_dir / "gate.json")["decision"] == "bypass"


async def test_arriving_inbox_overrides_skip(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    script(cfg, 'from pathlib import Path\nPath("inbox").mkdir()\n'
                'Path("inbox/report.json").write_text("{}")\n' + skip_body())
    marker = fake_engine(monkeypatch, cfg)
    _, run, _ = await finish(runner, cfg)
    assert marker.exists() and (cfg.dir / "inbox/report.json").exists()
    assert read_json(run.run_dir / "gate.json")["reason"] == "inbox arrived during gate"


async def test_existing_inbox_bypasses_missing_script(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    (cfg.dir / "inbox").mkdir()
    (cfg.dir / "inbox/report.json").write_text("{}")
    marker = fake_engine(monkeypatch, cfg)
    await finish(runner, cfg)
    assert marker.exists()


@pytest.mark.parametrize("body", ['"""gate — predicate\ncalls: other\n"""',
    '"""gate — predicate\nsecrets: TOKEN\n"""',
    '# /// script\n# dependencies = ["requests"]\n# ///\n'])
async def test_unauthorized_preparation_fails_closed(setup_gate, body):
    cfg, _, runner = setup_gate
    script(cfg, body + "\n" + skip_body())
    _, _, st = await finish(runner, cfg)
    assert st["state"] == "failed"


async def test_abort_gate_reaps_and_releases(setup_gate):
    cfg, _, runner = setup_gate
    script(cfg, "import time; time.sleep(30)")
    await runner.fire(cfg)
    run = runner.active[cfg.slug]
    for _ in range(100):
        if run.proc is not None:
            break
        await asyncio.sleep(.01)
    assert run.proc is not None
    await runner.abort(cfg.slug)
    await asyncio.gather(*runner._supervisors)
    assert not runner.active and not run.holds_slot
    assert read_json(run.run_dir / "status.json")["state"] == "aborted"


async def test_gate_waits_for_slot(setup_gate):
    cfg, _, runner = setup_gate
    script(cfg, 'from pathlib import Path\nPath("gate-ran").touch()\n' + skip_body())
    runner.semaphore = asyncio.Semaphore(0)
    await runner.fire(cfg)
    await asyncio.sleep(.05)
    assert not (cfg.dir / "gate-ran").exists()
    runner.semaphore.release()
    await asyncio.gather(*runner._supervisors)
    assert (cfg.dir / "gate-ran").exists()


async def test_cancel_queued_supervisor_cleans_up(setup_gate):
    cfg, _, runner = setup_gate
    runner.semaphore = asyncio.Semaphore(0)
    await runner.fire(cfg)
    await asyncio.sleep(.01)
    tasks = list(runner._supervisors)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert not runner.active
    assert runner.semaphore._value == 0


async def test_run_decision_starts_engine(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    script(cfg, skip_body().replace("skip", "run"))
    marker = fake_engine(monkeypatch, cfg)
    _, run, _ = await finish(runner, cfg)
    assert marker.exists()
    assert read_json(run.run_dir / "gate.json")["decision"] == "run"


async def test_missing_script_fails_closed(setup_gate):
    cfg, _, runner = setup_gate
    _, _, st = await finish(runner, cfg)
    assert st["state"] == "failed"


async def test_sandbox_refusal_fails_closed(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    script(cfg, skip_body())
    # Inject only trusted bootstrap test code; run the refusal in the preparation child.
    original = run_gate._bootstrap_cmd
    def command():
        cmd = original()
        cmd[-1] = cmd[-1].replace("child_main()", "import rsched.sandbox as s; s.available = lambda: False; child_main()")
        return cmd
    monkeypatch.setattr(run_gate, "_bootstrap_cmd", command)
    _, run, st = await finish(runner, cfg)
    assert st["state"] == "failed"
    assert "Landlock is unavailable" in (run.run_dir / "gate-stderr.txt").read_text()


async def test_symlink_escape_fails_closed(setup_gate, tmp_path):
    cfg, _, runner = setup_gate
    external = tmp_path / "external.py"
    external.write_text(skip_body())
    (cfg.dir / "scripts/gate.py").symlink_to(external)
    _, _, st = await finish(runner, cfg)
    assert st["state"] == "failed"


async def test_timeout_kills_descendants(setup_gate):
    cfg, _, runner = setup_gate
    cfg.run_gate.timeout_s = 2
    script(cfg, 'import subprocess, sys, time\n'
        'subprocess.Popen([sys.executable, "-c", '
        '\'import time; from pathlib import Path; time.sleep(3); Path("escaped").touch()\'])\n'
        'time.sleep(30)')
    _, _, st = await finish(runner, cfg)
    assert st["state"] == "failed"
    await asyncio.sleep(1.5)
    assert not (cfg.dir / "escaped").exists()
