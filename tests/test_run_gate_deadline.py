"""Real preparation isolation and lifecycle/security regressions for F457."""
import asyncio
import os
import time

import pytest

from rsched.daemon import run_gate, runner_reap, runner_state
from rsched.paths import atomic_write_json, read_json
from test_run_gate import finish, script, skip_body
from test_run_gate import setup_gate as gate_fixture


@pytest.fixture
def setup_gate(tmp_path, monkeypatch):
    return gate_fixture.__wrapped__(tmp_path, monkeypatch)


def bootstrap_patch(monkeypatch, code):
    original = run_gate._bootstrap_cmd
    def command():
        cmd = original()
        cmd[-1] = cmd[-1].replace("child_main()", code + "; child_main()")
        return cmd
    monkeypatch.setattr(run_gate, "_bootstrap_cmd", command)


async def test_blocked_preparation_deadline_keeps_loop_live(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    cfg.run_gate.timeout_s = 1
    bootstrap_patch(monkeypatch, "import time; time.sleep(30)")
    started = time.monotonic()
    ticks = []
    async def ticker():
        for _ in range(8):
            await asyncio.sleep(.1)
            ticks.append(1)
    await asyncio.gather(finish(runner, cfg), ticker())
    assert time.monotonic() - started < 4
    assert len(ticks) == 8
    st = read_json(next((cfg.dir / "runs").iterdir()) / "status.json")
    assert "deadline exceeded (1s, including preparation)" in st["summary"]


async def test_abort_during_preparation_leaves_no_child(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    bootstrap_patch(monkeypatch, "import time; time.sleep(30)")
    await runner.fire(cfg)
    run = runner.active[cfg.slug]
    for _ in range(100):
        if run.proc:
            break
        await asyncio.sleep(.01)
    pid = run.proc.pid
    await runner.abort(cfg.slug)
    await asyncio.gather(*runner._supervisors)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert read_json(run.run_dir / "status.json")["state"] == "aborted"


@pytest.mark.parametrize("granted", [True, False])
async def test_declared_optional_and_granted_secrets(setup_gate, granted):
    cfg, _, runner = setup_gate
    store = cfg.dir.parents[1] / "secrets.env"
    store.write_text("TOKEN=central-test-value\nUNDECLARED=must-not-leak\n")
    cfg.grants = {"secret:TOKEN": granted, "secret:UNDECLARED": True}
    script(cfg, '"""gate — predicate\nsecrets: TOKEN?\n"""\n'
           'import os\n'
           f'assert os.environ.get("TOKEN") == {("central-test-value" if granted else None)!r}\n'
           'assert "UNDECLARED" not in os.environ\n' + skip_body())
    _, _, st = await finish(runner, cfg)
    assert st["outcome"] == "skipped", st


async def test_required_granted_and_owned_secret(setup_gate):
    cfg, _, runner = setup_gate
    home = cfg.dir.parents[1]
    (home / "secrets.env").write_text("TOKEN=central-test-value\n")
    (home / "secrets.d").mkdir()
    (home / "secrets.d" / f"{cfg.slug}.env").write_text("OWN=owned-test-value\n")
    cfg.grants = {"secret:TOKEN": True}
    script(cfg, '"""gate — predicate\nsecrets: TOKEN, OWN\n"""\n'
           'import os\nassert os.environ["TOKEN"] == "central-test-value"\n'
           'assert os.environ["OWN"] == "owned-test-value"\n' + skip_body())
    _, _, st = await finish(runner, cfg)
    assert st["outcome"] == "skipped", st


async def test_nonzero_preserves_stderr(setup_gate):
    cfg, _, runner = setup_gate
    script(cfg, 'import sys; print("diagnostic sentinel", file=sys.stderr); sys.exit(7)')
    _, run, st = await finish(runner, cfg)
    assert "rc=7" in st["summary"]
    assert "diagnostic sentinel" in (run.run_dir / "gate-stderr.txt").read_text()


async def test_cancel_after_admission_never_spawns(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    async def admitted(run, *args):
        await asyncio.sleep(0)
        run.cancelled = True
        return True
    monkeypatch.setattr(run_gate, "admit", admitted)
    monkeypatch.setattr(runner_state, "engine_cmd", lambda *a, **k: pytest.fail("engine boot"))
    await finish(runner, cfg)


async def test_skipped_reap_never_stranded_resumes(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    script(cfg, skip_body())
    monkeypatch.setattr(runner_reap, "_stranded_user_messages", lambda *_: True)
    monkeypatch.setattr(runner_reap, "resume_for_stranded", lambda *_: pytest.fail("skip resumed"))
    _, _, st = await finish(runner, cfg)
    assert st["outcome"] == "skipped"


def test_terminal_preserves_prior_telemetry(tmp_path):
    run = runner_state.ActiveRun(slug="r", run_id="r:t", run_ts="t", run_dir=tmp_path)
    atomic_write_json(tmp_path / "status.json", {"turn": 19, "usage": {"in": 321, "out": 45}})
    run_gate.terminal(run, "aborted", "failed", "cancelled")
    st = read_json(tmp_path / "status.json")
    assert st["turn"] == 19 and st["usage"] == {"in": 321, "out": 45}


async def test_real_landlock_denies_tcp(setup_gate):
    cfg, _, runner = setup_gate
    script(cfg, 'import socket\n'
           's = socket.socket()\n'
           'try:\n s.bind(("127.0.0.1", 0))\n'
           'except PermissionError:\n pass\n'
           'else:\n raise RuntimeError("TCP unexpectedly allowed")\n' + skip_body())
    _, _, st = await finish(runner, cfg)
    assert st["outcome"] == "skipped", st


@pytest.mark.parametrize("mode", ["strict", "permissive", "off"])
def test_gate_never_silently_widens_sandbox(setup_gate, monkeypatch, mode):
    cfg, server, _ = setup_gate
    server.sandbox = mode
    script(cfg, skip_body())
    captured = {}
    def wrap(cmd, **kwargs):
        captured.update(kwargs)
        return cmd
    monkeypatch.setattr(run_gate.sandbox, "wrap", wrap)
    run_gate._prepare(cfg, server)
    assert captured["policy"].mode == "strict"
