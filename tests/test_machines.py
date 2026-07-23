"""Remote-machine binding: the catalog resolves a routine's `machines:` names + the Secrets
store into the RSCHED_MACHINES / RSCHED_MACHINE_KEYS env, and those reach a util ONLY under the
same declared-var gate OAuth tokens use. Mirrors test_connection_injection."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import finish, write_file
from rsched import machines, secrets, utils_lib
from rsched.config import MachineConfig, RoutineConfig, ServerConfig, load_server_config
from rsched.engine import runtime
from rsched.engine.executor import _extra_secrets, _machine_env
from rsched.engine.runtime import run_routine

DECLARING = '''"""remoteish — declares the machine vars.

usage: gu remoteish
tags: test
secrets: RSCHED_MACHINES, RSCHED_MACHINE_KEYS
net: outbound
"""
print("hi")
'''

PLAIN = '''"""plainish — declares no secrets.

usage: gu plainish
tags: test
"""
print("hi")
'''


def _lib(tmp_path, name, body):
    d = tmp_path / "utils" / name
    d.mkdir(parents=True)
    (d / "main.py").write_text(body, encoding="utf-8")
    return tmp_path


def _mac(name, **kw):
    m = MachineConfig(host=kw.pop("host", "10.0.0.9"), user=kw.pop("user", "rs"), **kw)
    m.name = name
    return m


# --------------------------------------------------------------------------- resolution ------
def test_resolve_builds_metadata_and_keys():
    cat = {"gpu": _mac("gpu", key_var="GPU_KEY", host_key="ssh-ed25519 AAor", tags=["gpu"],
                       description="RTX 4090")}
    meta, keys, warnings = machines.resolve_machines(["gpu"], cat, {"GPU_KEY": "PEM"})
    assert warnings == []
    assert keys == {"gpu": "PEM"}
    assert meta[0]["name"] == "gpu" and meta[0]["has_key"] is True
    assert meta[0]["has_host_key"] is True and meta[0]["tags"] == ["gpu"]


def test_resolve_warns_on_missing_catalog_and_unset_key():
    cat = {"gpu": _mac("gpu", key_var="GPU_KEY")}          # key_var set but secret absent
    meta, keys, warnings = machines.resolve_machines(["gpu", "ghost"], cat, {})
    assert keys == {}                                      # no PEM available
    assert any("ghost" in w and "not in the catalog" in w for w in warnings)
    assert any("GPU_KEY" in w and "not set" in w for w in warnings)
    # metadata is still returned for the catalogued-but-keyless machine (so `remote list` shows it)
    assert [m["name"] for m in meta] == ["gpu"] and meta[0]["has_key"] is False


def test_resolve_warns_when_no_key_var():
    _meta, keys, warnings = machines.resolve_machines(["gpu"], {"gpu": _mac("gpu")}, {})
    assert keys == {} and any("no key_var" in w for w in warnings)


def test_machines_for_routine_env_shape():
    cat = {"gpu": _mac("gpu", key_var="GPU_KEY")}
    env, warnings = machines.machines_for_routine(["gpu"], cat, secrets={"GPU_KEY": "PEM"})
    assert set(env) == {machines.MACHINES_VAR, machines.MACHINE_KEYS_VAR}
    assert json.loads(env[machines.MACHINE_KEYS_VAR]) == {"gpu": "PEM"}
    assert json.loads(env[machines.MACHINES_VAR])[0]["host"] == "10.0.0.9"
    assert warnings == []


def test_machines_for_routine_no_bindings():
    assert machines.machines_for_routine([], {}) == ({}, [])


def test_machine_env_vars():
    assert machines.machine_env_vars() == {"RSCHED_MACHINES", "RSCHED_MACHINE_KEYS"}


def test_dedupes_bindings():
    cat = {"gpu": _mac("gpu", key_var="K")}
    _meta, keys, warnings = machines.resolve_machines(["gpu", "gpu"], cat, {"K": "P"})
    assert keys == {"gpu": "P"} and warnings == []


# ----------------------------------------------------------------- the declared-var gate -----
def test_declared_machine_vars_injected(tmp_path):
    home = _lib(tmp_path, "remoteish", DECLARING)
    env = utils_lib._child_env(home, "remoteish",
                               {"RSCHED_MACHINES": "[]", "RSCHED_MACHINE_KEYS": '{"g":"PEM"}'})
    assert env["RSCHED_MACHINE_KEYS"] == '{"g":"PEM"}'
    assert env["RSCHED_MACHINES"] == "[]"


def test_undeclared_machine_vars_absent(tmp_path):
    home = _lib(tmp_path, "plainish", PLAIN)
    env = utils_lib._child_env(home, "plainish", {"RSCHED_MACHINE_KEYS": '{"g":"PEM"}'})
    assert "RSCHED_MACHINE_KEYS" not in env


def test_machine_keys_scrubbed_even_if_inherited(tmp_path, monkeypatch):
    # the engine injects the key via extra_secrets; an undeclaring util gets NEITHER the injected
    # value NOR any inherited one (the scrub pops it), so the key never leaks to the wrong util
    monkeypatch.setenv("RSCHED_MACHINE_KEYS", "leaked")
    home = _lib(tmp_path, "plainish", PLAIN)
    env = utils_lib._child_env(home, "plainish", {"RSCHED_MACHINE_KEYS": '{"g":"PEM"}'})
    assert "RSCHED_MACHINE_KEYS" not in env


def test_ssh_agent_vars_always_stripped(tmp_path, monkeypatch):
    # SSH_AUTH_SOCK / SSH_AGENT_PID never reach a util (they'd bypass the machine binding)
    monkeypatch.setenv("SSH_AUTH_SOCK", "agent.sock")
    monkeypatch.setenv("SSH_AGENT_PID", "1234")
    home = _lib(tmp_path, "plainish", PLAIN)
    env = utils_lib._child_env(home, "plainish", {})
    assert "SSH_AUTH_SOCK" not in env and "SSH_AGENT_PID" not in env


# --------------------------------------------------------------------- executor injection ----
def test_machine_env_resolves_bindings(monkeypatch):
    monkeypatch.setattr(secrets, "load_secrets", lambda: {"GPU_KEY": "PEM"})
    ctx = SimpleNamespace(routine=SimpleNamespace(machines=["gpu"]),
                          server=SimpleNamespace(machines={"gpu": _mac("gpu", key_var="GPU_KEY")}))
    env = _machine_env(ctx)
    assert json.loads(env[machines.MACHINE_KEYS_VAR]) == {"gpu": "PEM"}


def test_machine_env_no_bindings():
    ctx = SimpleNamespace(routine=SimpleNamespace(machines=[]),
                          server=SimpleNamespace(machines={}))
    assert _machine_env(ctx) == {}


def test_extra_secrets_merges_connections_and_machines(monkeypatch):
    monkeypatch.setattr(secrets, "load_secrets", lambda: {"GPU_KEY": "PEM"})
    ctx = SimpleNamespace(routine=SimpleNamespace(connections={}, machines=["gpu"]),
                          server=SimpleNamespace(machines={"gpu": _mac("gpu", key_var="GPU_KEY")}))
    env = _extra_secrets(ctx)
    assert machines.MACHINE_KEYS_VAR in env


# --------------------------------------------------------------------------- config ----------
def test_catalog_parses_and_names_fill(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "machines:\n"
        "  gpu-box:\n    host: 10.0.0.9\n    user: rs\n    key_var: GPU_KEY\n    tags: [gpu]\n"
        "  bad:\n    user: rs\n",   # missing host → dropped with a problem
        encoding="utf-8")
    cfg, problems = load_server_config(path)
    assert list(cfg.machines) == ["gpu-box"] and cfg.machines["gpu-box"].name == "gpu-box"
    assert cfg.machines["gpu-box"].port == 22
    assert any("bad" in p for p in problems)


def test_routine_binding_none_as_absent(tmp_path):
    d = str(tmp_path / "r")
    rc = RoutineConfig.model_validate({"slug": "r", "dir": d, "machines": None})
    assert rc.machines == []
    rc2 = RoutineConfig.model_validate({"slug": "r", "dir": d, "machines": ["a", "b"]})
    assert rc2.machines == ["a", "b"]


def test_unknown_machine_key_surfaced(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("machines:\n  g:\n    host: h\n    user: u\n    bogus: 1\n", encoding="utf-8")
    _cfg, problems = load_server_config(path)
    assert any("machines.g.bogus" in p for p in problems)



# ---------------------------------------------------------------------- share mounts ---------
def test_sshfs_argv_shape():
    mac = _mac("gpu", port=2222, share="/srv/data")
    argv = machines.sshfs_argv(mac, Path("/r/mnt/gpu"), Path("/k/key"), Path("/k/known"))
    assert argv[0] == "sshfs"
    assert f"{mac.user}@{mac.host}:/srv/data" in argv and "/r/mnt/gpu" in argv
    assert "-p" in argv and "2222" in argv and "StrictHostKeyChecking=yes" in argv
    assert any(a.startswith("IdentityFile=") for a in argv)
    assert any(a.startswith("UserKnownHostsFile=") for a in argv)


def test_known_hosts_lines_by_port():
    assert machines.known_hosts_lines("h", 22, "ssh-ed25519 AAA") == ["h ssh-ed25519 AAA"]
    assert machines.known_hosts_lines("h", 2222, "x ssh-rsa BBB") == ["[h]:2222 ssh-rsa BBB"]


def test_routine_mount_dir(tmp_path):
    assert machines.routine_mount_dir(tmp_path) == tmp_path / "mnt"



def test_ensure_gitignore_idempotent(tmp_path):
    (tmp_path / ".gitignore").write_text("runs/\n", encoding="utf-8")
    machines._ensure_mnt_gitignored(tmp_path)
    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "mnt/" in gi and "runs/" in gi
    machines._ensure_mnt_gitignored(tmp_path)               # idempotent
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").count("mnt/") == 1


def _share_setup(tmp_path, monkeypatch, *, sshfs=True, run_rc=0):
    monkeypatch.setattr(machines.shutil, "which",
                        lambda b: "/usr/bin/sshfs" if (sshfs and b == "sshfs") else None)
    monkeypatch.setattr(machines, "_mount_base", lambda: tmp_path / ".mounts")
    (tmp_path / ".mounts").mkdir(exist_ok=True)
    if run_rc is not None:
        monkeypatch.setattr(machines.subprocess, "run",
                            lambda *a, **k: SimpleNamespace(returncode=run_rc, stdout="", stderr="x"))
    mac = _mac("gpu", key_var="K", host_key="ssh-ed25519 AAA", share="/srv")
    routine = SimpleNamespace(dir=tmp_path / "routine", machines=["gpu"])
    (tmp_path / "routine").mkdir(exist_ok=True)
    return routine, SimpleNamespace(machines={"gpu": mac})


def test_mount_skips_when_no_share(tmp_path):
    routine = SimpleNamespace(dir=tmp_path, machines=["gpu"])
    server = SimpleNamespace(machines={"gpu": _mac("gpu", key_var="K")})   # no share
    assert machines.mount_routine_shares(routine, server, secrets={"K": "P"}) == []


def test_mount_skips_when_sshfs_missing(tmp_path, monkeypatch):
    routine, server = _share_setup(tmp_path, monkeypatch, sshfs=False, run_rc=None)
    assert machines.mount_routine_shares(routine, server, secrets={"K": "P"}) == []


def test_mount_skips_when_key_unset(tmp_path, monkeypatch):
    routine, server = _share_setup(tmp_path, monkeypatch, run_rc=None)
    assert machines.mount_routine_shares(routine, server, secrets={}) == []   # key_var not set


def test_mount_nonfatal_on_sshfs_failure(tmp_path, monkeypatch):
    routine, server = _share_setup(tmp_path, monkeypatch, run_rc=1)       # sshfs exits nonzero
    assert machines.mount_routine_shares(routine, server, secrets={"K": "PEM"}) == []


# ------------------------------------------------------------- engine mount lifecycle --------
def _server_for(d):
    server = ServerConfig()
    server.routines_home = d.parent
    server.libraries_home = d.parent.parent / "lib"
    return server


def test_run_routine_mounts_then_unmounts(make_routine, scripted, monkeypatch):
    calls: list = []
    monkeypatch.setattr(machines, "mount_routine_shares",
                        lambda routine, server, **k: (calls.append("mount"), ["SENTINEL"])[1])
    monkeypatch.setattr(machines, "unmount_routine_shares",
                        lambda mounted: calls.append(("unmount", mounted)))
    d = make_routine(slug="mountr")
    scripted([write_file("state/out.txt", content="x"), finish(summary="done")])
    status, _ = run_routine(d, _server_for(d), run_ts="20260708-070000")
    assert status == "ok"
    assert calls[0] == "mount" and calls[-1] == ("unmount", ["SENTINEL"])


def test_run_routine_unmounts_even_when_loop_raises(make_routine, scripted, monkeypatch):
    unmounted: list = []
    monkeypatch.setattr(machines, "mount_routine_shares", lambda routine, server, **k: ["S"])
    monkeypatch.setattr(machines, "unmount_routine_shares", unmounted.append)

    class BoomLoop:
        def __init__(self, *a, **k):
            pass

        def run(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(runtime, "EngineLoop", BoomLoop)
    d = make_routine(slug="boomr")
    scripted([])
    with pytest.raises(RuntimeError, match="boom"):
        run_routine(d, _server_for(d), run_ts="20260708-070000")
    assert unmounted == [["S"]]         # the finally ran despite the crash


def test_mount_success_returns_share_and_scopes_key(tmp_path, monkeypatch):
    """The success path: sshfs exits 0 -> a MountedShare with the routine's mnt/<name>
    mountpoint, the PEM written 0600 into a daemon-private keydir beside a pinned
    known_hosts, and mnt/ gitignored; unmount removes the keydir again."""
    routine, server = _share_setup(tmp_path, monkeypatch, run_rc=0)
    got = machines.mount_routine_shares(routine, server, secrets={"K": "PEM-KEY"})
    assert len(got) == 1
    ms = got[0]
    assert ms.name == "gpu"
    assert ms.mountpoint == tmp_path / "routine" / "mnt" / "gpu"
    key = ms.keydir / "key"
    assert key.read_text(encoding="utf-8") == "PEM-KEY\n"
    assert (key.stat().st_mode & 0o777) == 0o600
    assert "ssh-ed25519 AAA" in (ms.keydir / "known_hosts").read_text(encoding="utf-8")
    assert "mnt/" in (tmp_path / "routine" / ".gitignore").read_text(encoding="utf-8")

    machines.unmount_routine_shares(got)
    assert not ms.keydir.exists()              # the PEM never outlives the run


def test_sweep_survives_unreadable_mounts(tmp_path, monkeypatch):
    """An unreadable .mounts/ must be a loudly-skipped sweep, never an exception —
    sweep_stale_mount_keys runs on run_forever's BOOT path, before the tick loop's
    per-tick guard exists, so an escape there kills scheduling for good (F145)."""
    base = tmp_path / ".mounts"
    base.mkdir()
    (base / "stale").mkdir()
    monkeypatch.setattr(machines, "config_file", lambda: tmp_path / "config.yaml")
    base.chmod(0o000)
    try:
        assert machines.sweep_stale_mount_keys() == 0
    finally:
        base.chmod(0o755)
