"""The engine-spawn contract: what the daemon tells `engine-run`, and what `engine-run`
refuses when it is not told (F394).

The regression these pin is an ESCAPE, not a crash. A test's engine stub stopped taking
after a refactor moved the symbol it patched; the daemon's Runner spawned the real
`rsched.cli engine-run`; that fresh interpreter inherited nothing, loaded the production
config from `~`, and ran a tmp-homed fixture routine against the live instance — real
endpoint, real money, real rows in the report ledger. Both halves are covered here: the
spawner must NAME its config and its homes, and the child must REFUSE anything else.
"""

from __future__ import annotations

import sys

import pytest
import yaml

from rsched.cli import main
from rsched.config import ServerConfig, load_server_config
from rsched.daemon.runner_state import engine_cmd
from rsched.registry import homes_fingerprint


def _write_config(tmp_path, **overrides):
    """A real config file, so the loaded ServerConfig carries a `source` like the daemon's."""
    path = tmp_path / "config.yaml"
    body = {"routines_home": str(tmp_path / "routines"),
            "conversations_home": str(tmp_path / "conversations"),
            "background_home": str(tmp_path / "background"), **overrides}
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    server, _ = load_server_config(path)
    return path, server


# ---------------------------------------------------------------- the spawner's half


def test_engine_cmd_names_the_config_and_the_homes(tmp_path):
    path, server = _write_config(tmp_path)
    cmd = engine_cmd(server, str(tmp_path / "routines" / "r"), "20260827-190000")
    assert cmd[:2] == [sys.executable, "-m"]
    assert cmd[cmd.index("--config") + 1] == str(path)
    assert cmd[cmd.index("--homes") + 1] == homes_fingerprint(server)
    assert str(tmp_path / "routines") in cmd[cmd.index("--homes") + 1]
    assert "--resume" not in cmd
    assert "--resume" in engine_cmd(server, "r", "20260827-190000", resume=True)


def test_engine_cmd_refuses_a_config_that_was_never_loaded_from_a_file(tmp_path):
    """THE F394 regression. A ServerConfig built in memory — which is what every test's
    tmp-homed server is — cannot tell the child where to look, so the child would fall back
    to `~/.config/routine-scheduler/config.yaml`: the production instance. Refused before a
    process exists, and the message says where the run would otherwise have gone.
    """
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    with pytest.raises(RuntimeError) as exc:
        engine_cmd(server, str(tmp_path / "routines" / "strand"), "20260827-190000")
    assert "source is None" in str(exc.value)
    assert "config/routine-scheduler/config.yaml" in str(exc.value)


async def test_runner_hands_its_own_server_to_engine_cmd(tmp_path, make_routine, monkeypatch):
    """The runner must pass ITS config, not re-derive one — the whole contract rests on the
    spawned engine getting the homes the daemon is actually using.
    """
    import asyncio

    from rsched.config import load_routine
    from rsched.daemon import runner_state
    from rsched.daemon.events import EventBus
    from rsched.daemon.runner import Runner

    cfg, _ = load_routine(make_routine(slug="handover"))
    _, server = _write_config(tmp_path)
    seen: list = []

    def cmd(passed_server, target, run_ts, *, resume=False):
        seen.append((passed_server, target))
        return ["bash", "-c", "true"]

    monkeypatch.setattr(runner_state, "engine_cmd", cmd)
    runner = Runner(server, EventBus())
    await runner.fire(cfg)
    for _ in range(250):
        if seen:
            break
        await asyncio.sleep(0.02)
    assert seen and seen[0][0] is server


# ---------------------------------------------------------------- the child's half


def test_engine_run_requires_both_flags(capsys):
    """argparse, not a default: a hand-typed `engine-run` cannot silently adopt a config."""
    for argv in (["engine-run", "r", "--run-ts", "t"],
                 ["engine-run", "r", "--run-ts", "t", "--config", "/x"],
                 ["engine-run", "r", "--run-ts", "t", "--homes", "routines=/x"]):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--config" in err and "--homes" in err


def test_engine_run_refuses_a_config_it_cannot_read(tmp_path, capsys):
    code = main(["engine-run", "r", "--run-ts", "t",
                 "--config", str(tmp_path / "gone.yaml"), "--homes", "routines=/x"])
    assert code == 2
    assert "no such file" in capsys.readouterr().err


def test_engine_run_refuses_homes_that_are_not_the_spawners(tmp_path, capsys):
    """The loud half: a config that resolves elsewhere than the spawner is running is a
    refusal, never a reconciliation — the run does not happen somewhere the caller did not
    choose. This is what stands between a stale/foreign config and a production run.
    """
    path, server = _write_config(tmp_path)
    code = main(["engine-run", str(tmp_path / "routines" / "r"), "--run-ts", "t",
                 "--config", str(path), "--homes", "routines=/elsewhere"])
    assert code == 2
    err = capsys.readouterr().err
    assert "--homes mismatch" in err
    assert str(server.routines_home) in err and "/elsewhere" in err


def test_engine_run_accepts_the_matching_pair(tmp_path, monkeypatch, make_routine):
    """The production path is untouched: config + the homes it resolves to → the run runs."""
    _, server = _write_config(tmp_path, routines_home=str(tmp_path / "routines"))
    d = make_routine(slug="matched")
    seen = {}

    def fake_run_routine(routine_dir, srv, **kw):
        seen["dir"], seen["home"] = routine_dir, srv.routines_home
        return "ok", None

    monkeypatch.setattr("rsched.engine.runtime.run_routine", fake_run_routine)
    monkeypatch.setattr("rsched.cli.signal.signal", lambda *a: None)
    code = main(["engine-run", str(d), "--run-ts", "20260827-190000",
                 "--config", str(server.source), "--homes", homes_fingerprint(server)])
    assert code == 0
    assert seen["dir"] == d and seen["home"] == server.routines_home
