"""Sandbox POLICY logic — the strict/permissive/off decision matrix, spec assembly, and
policy derivation. Everything here is kernel-independent (availability is monkeypatched);
the real-enforcement assertions live in test_landlock.py.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rsched import landlock, sandbox

CMD = ["uv", "run", "--script", "/x/utils/demo/main.py", "--json"]


def _force_abi(monkeypatch, version: int) -> None:
    monkeypatch.setattr(landlock, "_abi", version)


def test_mode_off_never_wraps(tmp_path, monkeypatch):
    _force_abi(monkeypatch, 4)
    policy = sandbox.SandboxPolicy(mode="off")
    assert sandbox.wrap(CMD, policy=policy, libraries_home=tmp_path, net=True) == CMD


def test_available_wraps_with_spec(tmp_path, monkeypatch):
    _force_abi(monkeypatch, 4)
    policy = sandbox.SandboxPolicy(mode="permissive",
                                   read_roots=(Path("/data/in"),),
                                   write_roots=(tmp_path / "routine",))
    cmd = sandbox.wrap(CMD, policy=policy, libraries_home=tmp_path, net=False)
    assert cmd[:2] == [__import__("sys").executable, str(Path(landlock.__file__).resolve())]
    assert cmd[-len(CMD):] == CMD and cmd[-len(CMD) - 1] == "--"
    spec = json.loads(cmd[2])
    assert spec["net"] is False
    assert str(tmp_path) in spec["ro"] and "/data/in" in spec["ro"]      # library + read root
    assert str(tmp_path / "routine") in spec["rw"]
    assert "/tmp" in spec["rw"]  # noqa: S108 — asserting the jail root list, not using tmp
    assert "/usr" in spec["ro"] and "/etc" in spec["ro"]                 # toolchain
    # the high-value targets are NOT visible: no HOME, no ~/.config/routine-scheduler
    home = str(Path.home())
    assert home not in spec["ro"] and home not in spec["rw"]
    assert not any(p.endswith(".config/routine-scheduler") for p in spec["ro"] + spec["rw"])
    assert not any(p.endswith(".credentials") for p in spec["ro"] + spec["rw"])
    assert not any(p.endswith(".ssh") for p in spec["ro"] + spec["rw"])


def test_unavailable_strict_refuses(tmp_path, monkeypatch):
    _force_abi(monkeypatch, 0)
    policy = sandbox.SandboxPolicy(mode="strict")
    with pytest.raises(sandbox.SandboxRefusal, match="strict"):
        sandbox.wrap(CMD, policy=policy, libraries_home=tmp_path, net=True)


def test_unavailable_permissive_runs_bare_and_warns_once(tmp_path, monkeypatch, caplog):
    _force_abi(monkeypatch, 0)
    monkeypatch.setattr(sandbox, "_warned", set())
    policy = sandbox.SandboxPolicy(mode="permissive")
    with caplog.at_level("WARNING", logger="rsched.sandbox"):
        assert sandbox.wrap(CMD, policy=policy, libraries_home=tmp_path, net=True) == CMD
        assert sandbox.wrap(CMD, policy=policy, libraries_home=tmp_path, net=True) == CMD
    assert sum("UNSANDBOXED" in r.message for r in caplog.records) == 1


def test_net_denial_needs_abi4(tmp_path, monkeypatch):
    """On a fs-only Landlock kernel (ABI < 4), net: none is unenforceable: strict refuses,
    permissive degrades to the filesystem jail with net open (warned once)."""
    _force_abi(monkeypatch, 3)
    monkeypatch.setattr(sandbox, "_warned", set())
    with pytest.raises(sandbox.SandboxRefusal, match="ABI"):
        sandbox.wrap(CMD, policy=sandbox.SandboxPolicy(mode="strict"),
                     libraries_home=tmp_path, net=False)
    cmd = sandbox.wrap(CMD, policy=sandbox.SandboxPolicy(mode="permissive"),
                       libraries_home=tmp_path, net=False)
    assert json.loads(cmd[2])["net"] is True     # fs jail engages, TCP stays open
    # net: outbound utils are unaffected by the ABI gap
    cmd = sandbox.wrap(CMD, policy=sandbox.SandboxPolicy(mode="strict"),
                       libraries_home=tmp_path, net=True)
    assert json.loads(cmd[2])["net"] is True


def test_prewarm_opens_network_for_build_time_dep_install(tmp_path, monkeypatch):
    """R40: a util's net: declaration governs its RUNTIME, not the one-time build-time
    dependency install. prewarm_script_deps must resolve deps with the network OPEN
    (net=True) regardless — otherwise a net: none util can never install a third-party dep
    (its selftest fetch is denied) and authors are pushed to mis-declare net: outbound."""
    from rsched import utils_lib
    _force_abi(monkeypatch, 4)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=1, stdout="", stderr="offline")  # install fails

    monkeypatch.setattr(utils_lib.subprocess, "run", fake_run)
    policy = sandbox.SandboxPolicy(mode="permissive")
    # A prewarm failure must NOT raise — the real run reports the genuine error.
    utils_lib.prewarm_script_deps("/x/utils/demo/main.py", policy, tmp_path)
    wrapped = captured["cmd"]
    assert wrapped[-4:] == ["uv", "sync", "--script", "/x/utils/demo/main.py"]
    assert json.loads(wrapped[2])["net"] is True     # network open for the install phase
    assert str(tmp_path) in json.loads(wrapped[2])["ro"]   # filesystem still jailed


def test_wrap_creates_missing_granted_write_roots(tmp_path, monkeypatch, caplog):
    """R244/F293: a granted write root that does not exist yet is silently dropped from
    the jail (the child wrapper skips paths it cannot open), so the util's first mkdir
    under it dies with PermissionError. The grant implies the directory: wrap() creates
    missing write roots daemon-side; missing READ roots are only warned about (creating
    them would mask a config typo)."""
    _force_abi(monkeypatch, 4)
    monkeypatch.setattr(sandbox, "_warned", set())
    fresh = tmp_path / "sessions" / "4917"          # nested: parents are created too
    gone = tmp_path / "not-there"
    policy = sandbox.SandboxPolicy(mode="permissive", read_roots=(gone,),
                                   write_roots=(fresh,))
    with caplog.at_level("WARNING", logger="rsched.sandbox"):
        cmd = sandbox.wrap(CMD, policy=policy, libraries_home=tmp_path, net=False)
    assert fresh.is_dir()                            # the grant implies the directory
    assert str(fresh) in json.loads(cmd[2])["rw"]    # so the jail rule can attach to it
    assert not gone.exists()                         # read roots are never created
    assert sum(str(gone) in r.message for r in caplog.records) == 1


def _ctx(server, routine, extra_read=(), extra_write=()):
    """A live-run stand-in: the ctx surface policy_for_ctx consumes — the EFFECTIVE roots
    (config + one-time fs grants), exactly what RunContext.read_roots/write_roots return."""
    return SimpleNamespace(server=server, routine=routine,
                           read_roots=lambda: [*routine.fs_read_roots, *extra_read],
                           write_roots=lambda: [*routine.fs_write_roots, *extra_write])


def test_policy_for_ctx_derives_from_the_run(tmp_path):
    server = SimpleNamespace(sandbox="strict")
    routine = SimpleNamespace(dir=tmp_path / "r", fs_read_roots=[Path("/data")],
                              fs_write_roots=[Path("/proj")])
    policy = sandbox.policy_for_ctx(_ctx(server, routine))
    assert policy.mode == "strict"
    assert policy.read_roots == (Path("/data"),)
    assert policy.write_roots == (tmp_path / "r", Path("/proj"))   # own dir always writable
    base = sandbox.base_policy(server)
    assert base.mode == "strict" and base.read_roots == () and base.write_roots == ()


def test_policy_for_ctx_carries_one_time_fs_grants(tmp_path):
    """A once-granted fs root reaches the util sandbox exactly like a configured one —
    one policy source, two enforcers (the engine path gates read the same ctx roots)."""
    server = SimpleNamespace(sandbox="strict")
    routine = SimpleNamespace(dir=tmp_path / "r", fs_read_roots=[], fs_write_roots=[])
    policy = sandbox.policy_for_ctx(_ctx(server, routine,
                                         extra_read=[Path("/granted-read")],
                                         extra_write=[Path("/granted-write")]))
    assert Path("/granted-read") in policy.read_roots
    assert Path("/granted-write") in policy.write_roots


def test_policy_for_ctx_includes_staged_shared_read_roots(tmp_path):
    """A run's util sandbox also sees operator-staged shared read-only asset dirs (the
    NopeCHA browser extension launch-captcha-browser loads) — existence-guarded, derived
    from server.routines_home, never the routine's own roots. (R21/R28)"""
    rhome = tmp_path / "rhome"
    ext = rhome / ".control" / "nopecha-extension"
    ext.mkdir(parents=True)
    server = SimpleNamespace(sandbox="permissive", routines_home=rhome)
    routine = SimpleNamespace(dir=tmp_path / "r", fs_read_roots=[Path("/data")],
                              fs_write_roots=[])
    policy = sandbox.policy_for_ctx(_ctx(server, routine))
    assert ext in policy.read_roots and Path("/data") in policy.read_roots

    # Not staged → contributes nothing (a fresh deploy is unaffected).
    empty = tmp_path / "empty"
    empty.mkdir()
    policy2 = sandbox.policy_for_ctx(_ctx(
        SimpleNamespace(sandbox="permissive", routines_home=empty), routine))
    assert all("nopecha-extension" not in str(p) for p in policy2.read_roots)

    # A server double without routines_home degrades cleanly (no shared roots).
    policy3 = sandbox.policy_for_ctx(_ctx(SimpleNamespace(sandbox="off"), routine))
    assert policy3.read_roots == (Path("/data"),)


@pytest.mark.skipif(__import__("shutil").which("uv") is None,
                    reason="uv required (run_util checks it before the sandbox)")
def test_strict_refusal_reaches_util_observation(tmp_path, monkeypatch):
    """run_util turns a strict refusal into the util's error observation — the model sees
    an actionable message, the util never runs."""
    from rsched import utils_lib

    _force_abi(monkeypatch, 0)
    utils_lib.ensure_library(tmp_path)
    utils_lib.write_util_file(tmp_path, "demo", '"""demo — d.\n\nusage: gu demo\n"""\n')
    code, _out, err = utils_lib.run_util(tmp_path, "demo", [],
                                         policy=sandbox.SandboxPolicy(mode="strict"))
    assert code == 2 and "strict" in err and "unsandboxed" in err
