"""Remote-machine binding: the catalog resolves a routine's `machines:` names + the Secrets
store into the RSCHED_MACHINES / RSCHED_MACHINE_KEYS env, and those reach a util ONLY under the
same declared-var gate OAuth tokens use. Mirrors test_connection_injection."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import finish, write_file
from rsched import machine_mounts, machines, secrets, utils_run
from rsched.config import MachineConfig, RoutineConfig, ServerConfig, load_server_config
from rsched.engine import runtime
from rsched.engine.exec_env import _extra_secrets, _machine_env
from rsched.engine.runtime import run_routine

DECLARING = '''"""remoteish — declares the machine vars.

usage: gu remoteish
tags: test
secrets: RSCHED_MACHINES, RSCHED_MACHINE_KEYS
net: outbound
fs: roots
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
    env = utils_run._child_env(home, "remoteish",
                               {"RSCHED_MACHINES": "[]", "RSCHED_MACHINE_KEYS": '{"g":"PEM"}'})
    assert env["RSCHED_MACHINE_KEYS"] == '{"g":"PEM"}'
    assert env["RSCHED_MACHINES"] == "[]"


def test_undeclared_machine_vars_absent(tmp_path):
    home = _lib(tmp_path, "plainish", PLAIN)
    env = utils_run._child_env(home, "plainish", {"RSCHED_MACHINE_KEYS": '{"g":"PEM"}'})
    assert "RSCHED_MACHINE_KEYS" not in env


def test_machine_keys_scrubbed_even_if_inherited(tmp_path, monkeypatch):
    # the engine injects the key via extra_secrets; an undeclaring util gets NEITHER the injected
    # value NOR any inherited one (the scrub pops it), so the key never leaks to the wrong util
    monkeypatch.setenv("RSCHED_MACHINE_KEYS", "leaked")
    home = _lib(tmp_path, "plainish", PLAIN)
    env = utils_run._child_env(home, "plainish", {"RSCHED_MACHINE_KEYS": '{"g":"PEM"}'})
    assert "RSCHED_MACHINE_KEYS" not in env


def test_ssh_agent_vars_always_stripped(tmp_path, monkeypatch):
    # SSH_AUTH_SOCK / SSH_AGENT_PID never reach a util (they'd bypass the machine binding)
    monkeypatch.setenv("SSH_AUTH_SOCK", "agent.sock")
    monkeypatch.setenv("SSH_AGENT_PID", "1234")
    home = _lib(tmp_path, "plainish", PLAIN)
    env = utils_run._child_env(home, "plainish", {})
    assert "SSH_AUTH_SOCK" not in env and "SSH_AGENT_PID" not in env


# --------------------------------------------------------------------- executor injection ----
def test_machine_env_resolves_bindings(monkeypatch):
    monkeypatch.setattr(secrets, "load_secrets", lambda: {"GPU_KEY": "PEM"})
    ctx = SimpleNamespace(routine=SimpleNamespace(slug="mach", machines=["gpu"]),
                          granted_now=set(), grant_args={},
                          server=SimpleNamespace(machines={"gpu": _mac("gpu", key_var="GPU_KEY")}))
    env = _machine_env(ctx)
    assert json.loads(env[machines.MACHINE_KEYS_VAR]) == {"gpu": "PEM"}


def test_machine_env_no_bindings():
    ctx = SimpleNamespace(routine=SimpleNamespace(slug="mach", machines=[]),
                          granted_now=set(), grant_args={},
                          server=SimpleNamespace(machines={}))
    assert _machine_env(ctx) == {}


def test_extra_secrets_merges_connections_and_machines(monkeypatch):
    monkeypatch.setattr(secrets, "load_secrets", lambda: {"GPU_KEY": "PEM"})
    ctx = SimpleNamespace(routine=SimpleNamespace(slug="mach", connections={}, machines=["gpu"]),
                          granted_now=set(), grant_args={},
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
    argv = machine_mounts.sshfs_argv(mac, Path("/r/mnt/gpu"), Path("/k/key"), Path("/k/known"))
    assert argv[0] == "sshfs"
    assert f"{mac.user}@{mac.host}:/srv/data" in argv and "/r/mnt/gpu" in argv
    assert "-p" in argv and "2222" in argv and "StrictHostKeyChecking=yes" in argv
    assert any(a.startswith("IdentityFile=") for a in argv)
    assert any(a.startswith("UserKnownHostsFile=") for a in argv)


def test_known_hosts_lines_by_port():
    assert machine_mounts.known_hosts_lines("h", 22, "ssh-ed25519 AAA") == ["h ssh-ed25519 AAA"]
    assert machine_mounts.known_hosts_lines("h", 2222, "x ssh-rsa BBB") == ["[h]:2222 ssh-rsa BBB"]


def test_routine_mount_dir(tmp_path):
    assert machine_mounts.routine_mount_dir(tmp_path) == tmp_path / "mnt"



def test_ensure_gitignore_idempotent(tmp_path):
    (tmp_path / ".gitignore").write_text("runs/\n", encoding="utf-8")
    machine_mounts._ensure_mnt_gitignored(tmp_path)
    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "mnt/" in gi and "runs/" in gi
    machine_mounts._ensure_mnt_gitignored(tmp_path)               # idempotent
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").count("mnt/") == 1


def _share_setup(tmp_path, monkeypatch, *, sshfs=True, run_rc=0, live=True):
    monkeypatch.setattr(machine_mounts.shutil, "which",
                        lambda b: "/usr/bin/sshfs" if (sshfs and b == "sshfs") else None)
    # No real sshfs runs in the suite, so nothing is ever a real mount: steer the liveness
    # probe directly. `live=False` is the R514 case — sshfs exits 0, the mount never comes up.
    monkeypatch.setattr(machine_mounts, "mount_is_live", lambda mp: live)
    monkeypatch.setattr(machine_mounts, "MOUNT_LIVE_TIMEOUT_S", 0.0)
    monkeypatch.setattr(machine_mounts, "_mount_base", lambda: tmp_path / ".mounts")
    (tmp_path / ".mounts").mkdir(exist_ok=True)
    if run_rc is not None:
        monkeypatch.setattr(machine_mounts.subprocess, "run",
                            lambda *a, **k: SimpleNamespace(returncode=run_rc, stdout="", stderr="x"))
    mac = _mac("gpu", key_var="K", host_key="ssh-ed25519 AAA", share="/srv")
    routine = SimpleNamespace(dir=tmp_path / "routine", machines=["gpu"])
    (tmp_path / "routine").mkdir(exist_ok=True)
    return routine, SimpleNamespace(machines={"gpu": mac})


def test_mount_skips_when_no_share(tmp_path):
    routine = SimpleNamespace(dir=tmp_path, machines=["gpu"])
    server = SimpleNamespace(machines={"gpu": _mac("gpu", key_var="K")})   # no share
    assert machine_mounts.mount_routine_shares(routine, server, secrets={"K": "P"}) == ([], {})


def test_mount_skips_when_sshfs_missing(tmp_path, monkeypatch):
    routine, server = _share_setup(tmp_path, monkeypatch, sshfs=False, run_rc=None)
    mounted, unavailable = machine_mounts.mount_routine_shares(routine, server, secrets={"K": "P"})
    assert mounted == [] and "sshfs" in unavailable["gpu"]


def test_mount_skips_when_key_unset(tmp_path, monkeypatch):
    routine, server = _share_setup(tmp_path, monkeypatch, run_rc=None)
    mounted, unavailable = machine_mounts.mount_routine_shares(routine, server, secrets={})
    assert mounted == [] and unavailable == {"gpu": "no private key"}


def test_mount_nonfatal_on_sshfs_failure(tmp_path, monkeypatch):
    routine, server = _share_setup(tmp_path, monkeypatch, run_rc=1)       # sshfs exits nonzero
    mounted, unavailable = machine_mounts.mount_routine_shares(routine, server, secrets={"K": "PEM"})
    assert mounted == [] and "sshfs failed" in unavailable["gpu"]


# ------------------------------------------------------------- R514: mount liveness ----------
def test_mount_is_live_distinguishes_plain_dir_from_mount(tmp_path, monkeypatch):
    """A plain directory is NOT a live share. This is the whole R514 failure: `mkdir` runs
    before sshfs, so a failed mount leaves an empty dir that reads exactly like an empty
    share — `dir-tree` answered `entries: 0` for a populated remote box.
    """
    d = tmp_path / "mp"
    d.mkdir()
    assert machine_mounts.mount_is_live(d) is False              # exists, readable, but not a mount
    monkeypatch.setattr(machine_mounts.Path, "is_mount", lambda self: True)
    assert machine_mounts.mount_is_live(d) is True


def test_mount_is_live_reports_stale_endpoint_as_dead(tmp_path, monkeypatch):
    """A stale FUSE endpoint raises ENOTCONN on readdir. That must read as NOT LIVE, never
    propagate, and never be mistaken for an empty share."""
    d = tmp_path / "mp"
    d.mkdir()
    monkeypatch.setattr(machine_mounts.Path, "is_mount", lambda self: True)

    def boom(_p):
        raise OSError(107, "Transport endpoint is not connected")

    monkeypatch.setattr(machine_mounts.os, "scandir", boom)
    assert machine_mounts.mount_is_live(d) is False


def test_mount_removes_lookalike_dir_when_never_live(tmp_path, monkeypatch):
    """sshfs exits 0 but the mount never comes up (R514, observed on predator). The share is
    reported unavailable WITH a reason, and no empty mnt/<name>/ directory is left standing —
    so a read fails on a missing path instead of answering `entries: 0`, and a write cannot
    silently land on local disk.
    """
    routine, server = _share_setup(tmp_path, monkeypatch, run_rc=0, live=False)
    mounted, unavailable = machine_mounts.mount_routine_shares(routine, server, secrets={"K": "PEM"})
    assert mounted == []
    assert "never became a readable mount" in unavailable["gpu"]
    assert not (tmp_path / "routine" / "mnt" / "gpu").exists()


def test_remove_lookalike_never_deletes_data(tmp_path):
    """Clearing a lookalike must never become deleting data: a non-empty mountpoint stays."""
    mp = tmp_path / "mp"
    mp.mkdir()
    (mp / "keep.txt").write_text("data", encoding="utf-8")
    machine_mounts._remove_lookalike_mountpoint(mp)
    assert (mp / "keep.txt").read_text(encoding="utf-8") == "data"


# ------------------------------------------------------------- engine mount lifecycle --------
# The lifecycle tests never mount anything; this stands in for what mount_routine_shares
# hands back, and carries the `.name` the run context records (R514).
SENTINEL_SHARE = SimpleNamespace(name="gpu")


def _server_for(d):
    server = ServerConfig()
    server.routines_home = d.parent
    server.libraries_home = d.parent.parent / "lib"
    return server


def test_run_routine_mounts_then_unmounts(make_routine, scripted, monkeypatch):
    calls: list = []
    monkeypatch.setattr(machine_mounts, "mount_routine_shares",
                        lambda routine, server, **k: (calls.append("mount"),
                                                      ([SENTINEL_SHARE], {}))[1])
    monkeypatch.setattr(machine_mounts, "unmount_routine_shares",
                        lambda mounted: calls.append(("unmount", mounted)))
    d = make_routine(slug="mountr")
    scripted([write_file("state/out.txt", content="x"), finish(summary="done")])
    status, _ = run_routine(d, _server_for(d), run_ts="20260708-070000")
    assert status == "ok"
    assert calls[0] == "mount" and calls[-1] == ("unmount", [SENTINEL_SHARE])


def test_run_routine_unmounts_even_when_loop_raises(make_routine, scripted, monkeypatch):
    unmounted: list = []
    monkeypatch.setattr(machine_mounts, "mount_routine_shares",
                        lambda routine, server, **k: ([SENTINEL_SHARE], {}))
    monkeypatch.setattr(machine_mounts, "unmount_routine_shares", unmounted.append)

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
    assert unmounted == [[SENTINEL_SHARE]]    # the finally ran despite the crash


def test_mount_success_returns_share_and_scopes_key(tmp_path, monkeypatch):
    """The success path: sshfs exits 0 -> a MountedShare with the routine's mnt/<name>
    mountpoint, the PEM written 0600 into a daemon-private keydir beside a pinned
    known_hosts, and mnt/ gitignored; unmount removes the keydir again."""
    routine, server = _share_setup(tmp_path, monkeypatch, run_rc=0)
    got, unavailable = machine_mounts.mount_routine_shares(routine, server, secrets={"K": "PEM-KEY"})
    assert len(got) == 1 and unavailable == {}
    ms = got[0]
    assert ms.name == "gpu"
    assert ms.mountpoint == tmp_path / "routine" / "mnt" / "gpu"
    key = ms.keydir / "key"
    assert key.read_text(encoding="utf-8") == "PEM-KEY\n"
    assert (key.stat().st_mode & 0o777) == 0o600
    assert "ssh-ed25519 AAA" in (ms.keydir / "known_hosts").read_text(encoding="utf-8")
    assert "mnt/" in (tmp_path / "routine" / ".gitignore").read_text(encoding="utf-8")

    machine_mounts.unmount_routine_shares(got)
    assert not ms.keydir.exists()              # the PEM never outlives the run


def test_sweep_survives_unreadable_mounts(tmp_path, monkeypatch):
    """An unreadable .mounts/ must be a loudly-skipped sweep, never an exception —
    sweep_stale_mount_keys runs on run_forever's BOOT path, before the tick loop's
    per-tick guard exists, so an escape there kills scheduling for good (F145)."""
    base = tmp_path / ".mounts"
    base.mkdir()
    (base / "stale").mkdir()
    monkeypatch.setattr(machine_mounts, "config_file", lambda: tmp_path / "config.yaml")
    base.chmod(0o000)
    try:
        assert machine_mounts.sweep_stale_mount_keys() == 0
    finally:
        base.chmod(0o755)
