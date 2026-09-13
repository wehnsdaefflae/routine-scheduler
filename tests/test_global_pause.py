"""Global scheduling pause holds active routine turns without clearing manual pauses."""
from types import SimpleNamespace

from rsched.config import ServerConfig
from rsched.daemon import pause
from rsched.engine.control import pause_gate
from rsched.paths import atomic_write_json, read_json


def test_global_pause_gate_preserves_manual_pause(tmp_path, monkeypatch):
    """Global resume must not release an independently paused run."""
    server = ServerConfig(routines_home=tmp_path / "routines")
    root = server.routines_home / "worker" / "runs" / "20260913-000000"
    root.mkdir(parents=True)
    atomic_write_json(root / "control.json", {"scheduling_pause": True, "pause": True})
    pause.set_paused(server, True)
    statuses, suspended, polls = [], [], []
    ctx = SimpleNamespace(root_run_dir=root, server=server,
                          write_status=statuses.append, credit_suspended=suspended.append)
    loop = SimpleNamespace(ctx=ctx, _aborted=lambda: False)

    def release(_seconds):
        polls.append(True)
        if len(polls) == 1:
            pause.set_paused(server, False)
        else:
            atomic_write_json(root / "control.json", {"scheduling_pause": True, "pause": False})

    monkeypatch.setattr("rsched.engine.control.time.sleep", release)
    pause_gate(loop, poll_s=0)
    assert len(polls) == 2
    assert statuses == ["paused", "running"]
    assert len(suspended) == 1


def test_global_pause_gate_holds_marked_run_until_resume(tmp_path, monkeypatch):
    """A globally marked routine must stop before its next turn."""
    server = ServerConfig(routines_home=tmp_path / "routines")
    root = server.routines_home / "worker" / "runs" / "20260913-000000"
    root.mkdir(parents=True)
    pause.set_paused(server, True)
    cycle = pause.generation(server)
    atomic_write_json(root / "control.json", {"scheduling_pause": cycle})
    statuses, suspended = [], []
    ctx = SimpleNamespace(root_run_dir=root, server=server,
                          write_status=statuses.append, credit_suspended=suspended.append)
    loop = SimpleNamespace(ctx=ctx, _aborted=lambda: False)
    monkeypatch.setattr("rsched.engine.control.time.sleep", lambda _: pause.set_paused(server, False))
    pause_gate(loop, poll_s=0)
    assert statuses == ["paused", "running"]
    assert len(suspended) == 1
    assert read_json(root / "control.json") == {"scheduling_pause": cycle}
    # A later pause must not revive this run's old hold (manual terminal resume).
    pause.set_paused(server, True)
    assert pause.generation(server) != cycle
    statuses.clear()
    pause_gate(loop, poll_s=0)
    assert statuses == []
