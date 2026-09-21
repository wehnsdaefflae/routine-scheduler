"""Launch-handshake ownership and declaration narrowing regressions (F457)."""
import asyncio
import contextlib
import json
import os
import signal
import sys
from pathlib import Path

import pytest

from rsched.daemon import run_gate, runner_reap, runner_state
from rsched.paths import read_json
from test_run_gate import script, skip_body
from test_run_gate import setup_gate as gate_fixture


@pytest.fixture
def setup_gate(tmp_path, monkeypatch):
    return gate_fixture.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("cancel", ["abort", "task", "task_twice"])
async def test_delayed_engine_handle_owned_and_reaped(setup_gate, monkeypatch, cancel):
    cfg, _, runner = setup_gate
    acquired, release = asyncio.Event(), asyncio.Event()
    real_spawn = asyncio.create_subprocess_exec
    child_file = cfg.dir / "descendant.pid"
    code = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); pid=os.fork(); "
        f"Path({str(child_file)!r}).write_text(str(os.getpid())) if pid == 0 else None; "
        "time.sleep(60)"
    )
    monkeypatch.setattr(runner_state, "engine_cmd", lambda *a, **k: [sys.executable, "-c", code])
    events, reaps = [], []
    monkeypatch.setattr(runner.bus, "publish", events.append)
    real_reap = runner_reap.reap
    def reap(*args):
        reaps.append(1)
        real_reap(*args)
    monkeypatch.setattr(runner_reap, "reap", reap)
    processes = []
    async def delayed(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        processes.append(proc)
        async with asyncio.timeout(5):
            # External OS-process readiness cannot signal an asyncio.Event directly.
            while not child_file.exists() or not child_file.read_text():  # noqa: ASYNC110
                await asyncio.sleep(.01)
        acquired.set()
        await release.wait()
        return proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    await runner.fire(cfg, reason="manual")
    run = runner.active[cfg.slug]
    task = next(iter(runner._supervisors))
    try:
        await asyncio.wait_for(acquired.wait(), 5)
        assert run.proc is None
        descendant = int(child_file.read_text())
        if cancel == "abort":
            assert await runner.abort(cfg.slug)
        else:
            task.cancel()
            await asyncio.sleep(0)
            if cancel == "task_twice":
                task.cancel()
                await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
        proc = processes[0]
        assert proc.returncode is not None
        with pytest.raises(ProcessLookupError):
            os.kill(proc.pid, 0)
        # A dead orphan may briefly remain a zombie until the host's PID 1 reaps it.
        stat = Path(f"/proc/{descendant}/stat")
        async with asyncio.timeout(5):
            # Kernel process state, not an asyncio producer: bounded OS polling.
            while stat.exists() and stat.read_text().split(") ", 1)[1][0] != "Z":  # noqa: ASYNC110
                await asyncio.sleep(.01)
        assert read_json(run.run_dir / "status.json")["state"] == "aborted"
        assert not any(e["event"] == "run_started" for e in events)
        assert not runner.active and not run.holds_slot
        assert runner.semaphore._value == runner.server.max_concurrent_runs
        assert reaps == [1]
    finally:
        release.set()
        for proc in processes:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("declaration", [None, "none", "ro", "unauthorized", "roots"])
def test_filesystem_declarations_intersect_grants(setup_gate, monkeypatch, declaration):
    cfg, server, _ = setup_gate
    first, second, forbidden = (cfg.dir.parent / n for n in ("first", "second", "forbidden"))
    for p in (first, second, forbidden):
        p.mkdir()
    cfg.fs_write_roots = [first, second]
    text = {None: "", "none": "fs: none", "ro": f"fs: ro {first}",
            "unauthorized": f"fs: rw {forbidden}", "roots": "fs: roots"}[declaration]
    script(cfg, f'"""gate\n{text}\n"""\n' + skip_body())
    monkeypatch.setattr(run_gate.sandbox, "available", lambda: True)
    monkeypatch.setattr(run_gate.sandbox.landlock, "abi_version", lambda: 6)
    cmd, _ = run_gate._prepare(cfg, server)
    spec = json.loads(cmd[2])
    assert (str(first) in spec["ro"]) == (declaration == "ro")
    assert (str(first) in spec["rw"]) == (declaration == "roots")
    assert (str(second) in spec["rw"]) == (declaration == "roots")
    assert str(forbidden) not in spec["ro"] + spec["rw"]
    assert str(cfg.dir) in spec["rw"]


@pytest.mark.parametrize("declaration", ["", "garbage", "ro relative", "none, roots", "none\nfs: roots"])
def test_malformed_filesystem_fails_closed(setup_gate, declaration):
    cfg, server, _ = setup_gate
    script(cfg, f'"""gate\nfs: {declaration}\n"""\n' + skip_body())
    with pytest.raises(run_gate.GateError, match="declaration"):
        run_gate._prepare(cfg, server)


@pytest.mark.parametrize("mode", ["strict", "permissive", "off"])
async def test_actual_refusal_is_strict_in_every_server_mode(setup_gate, monkeypatch, mode):
    cfg, server, runner = setup_gate
    server.sandbox = mode
    script(cfg, skip_body())
    # Inject the unavailable capability into the actual isolated preparation process.
    original = run_gate._bootstrap_cmd
    def command():
        cmd = original()
        cmd[-1] = cmd[-1].replace("child_main()", "import rsched.sandbox as s; s.available=lambda: False; child_main()")
        return cmd
    monkeypatch.setattr(run_gate, "_bootstrap_cmd", command)
    monkeypatch.setattr(runner_state, "engine_cmd", lambda *a, **k: pytest.fail("engine boot"))
    await runner.fire(cfg)
    run = runner.active[cfg.slug]
    await asyncio.gather(*runner._supervisors)
    st = read_json(run.run_dir / "status.json")
    assert st["state"] == "failed"
    error = (run.run_dir / "gate-stderr.txt").read_text()
    assert "enable the required kernel/LSM support" in error
    assert "set `sandbox: permissive`" not in error
    assert "Changing server sandbox mode cannot relax" in error
