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


def test_policy_for_run_derives_from_routine(tmp_path):
    server = SimpleNamespace(sandbox="strict")
    routine = SimpleNamespace(dir=tmp_path / "r", fs_read_roots=[Path("/data")],
                              fs_write_roots=[Path("/proj")])
    policy = sandbox.policy_for_run(server, routine)
    assert policy.mode == "strict"
    assert policy.read_roots == (Path("/data"),)
    assert policy.write_roots == (tmp_path / "r", Path("/proj"))   # own dir always writable
    base = sandbox.base_policy(server)
    assert base.mode == "strict" and base.read_roots == () and base.write_roots == ()


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
