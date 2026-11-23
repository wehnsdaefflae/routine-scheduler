"""Whether a machine's login has root rights is read off the box (rsched/machine_privilege.py):
`id` answers it, the record is keyed to the login it was probed as, and a box never probed —
or one that would not answer — stays checked."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from rsched import machine_privilege as mp
from rsched import sandbox, utils_run
from rsched.config import MachineConfig


def _mac(user="mark", **kw) -> MachineConfig:
    return MachineConfig(name="box", host="10.0.0.9", user=user, key_var="K", **kw)


@pytest.fixture(autouse=True)
def _no_jail(monkeypatch):
    monkeypatch.setattr(sandbox, "base_policy", lambda server: None)


def _server(tmp_path, mac: MachineConfig | None = None):
    return SimpleNamespace(routines_home=tmp_path, libraries_home=tmp_path / "lib",
                           machines={"box": mac or _mac()})


@pytest.mark.parametrize(("stdout", "expect"), [
    ("uid=0\ngroups=root\n", (True, "logs in as uid 0 (root)")),
    ("uid=1000\ngroups=mark adm sudo docker\n", (True, "member of docker, sudo")),
    ("uid=1001\ngroups=agent video render\n", (False, None)),
    ("", None),
    ("Permission denied\n", None),
])
def test_id_output_says_whether_the_login_is_root(stdout, expect):
    got = mp.parse(stdout)
    if expect is None:
        assert got is None
    else:
        assert got is not None and got[0] is expect[0]
        assert expect[1] is None or got[1] == expect[1]


def test_a_record_belongs_to_the_login_it_was_probed_as(tmp_path):
    """Switching the machine's user (or host, or port) makes the old answer someone else's: it
    reads as never probed, so the check comes back on."""
    mp.record(tmp_path, "box", _mac(), privileged=False, why="uid 1000")
    assert mp.privileged(tmp_path, "box", _mac()) is False
    assert mp.privileged(tmp_path, "box", _mac(user="root")) is None


def test_unknown_counts_as_root_and_only_a_known_unprivileged_login_does_not(tmp_path):
    server = _server(tmp_path)
    assert mp.is_root(server, "box"), "never probed must read as root"
    mp.record(tmp_path, "box", server.machines["box"], privileged=False, why="uid 1000")
    assert not mp.is_root(server, "box")
    assert not mp.is_root(server, "elsewhere"), "a machine outside the catalog is unreachable"
    rows = mp.catalog_rows(server)
    assert [(r["name"], r["root"]) for r in rows] == [("box", False)]


def test_the_probe_records_what_the_box_said(tmp_path, monkeypatch):
    calls = []

    def run_util(home, name, args, **kw):
        calls.append(args)
        return 0, json.dumps({"exit": 0, "stdout": "uid=1000\ngroups=mark sudo\n"}), ""

    monkeypatch.setattr(utils_run, "run_util", run_util)
    server = _server(tmp_path)
    got = mp.probe(server, "box")
    assert got["privileged"] is True and got["why"] == "member of sudo"
    assert calls == [["exec", "box", "--command", mp.PROBE_COMMAND, "--json"]]
    assert mp.privileged(tmp_path, "box", server.machines["box"]) is True


def test_a_box_that_does_not_answer_keeps_its_last_record(tmp_path, monkeypatch):
    server = _server(tmp_path)
    mp.record(tmp_path, "box", server.machines["box"], privileged=False, why="uid 1000")
    monkeypatch.setattr(utils_run, "run_util", lambda *a, **k: (1, "", "connection refused"))
    got = mp.probe(server, "box")
    assert got["privileged"] is None and "connection refused" in got["why"]
    assert mp.privileged(tmp_path, "box", server.machines["box"]) is False


def test_a_run_asks_each_unknown_box_once(tmp_path, monkeypatch):
    """At run start (and before a checked call) a never-probed machine is asked, once per
    process — an unreachable box costs one attempt per run, a known one none."""
    asked: list[str] = []
    monkeypatch.setattr(mp, "_asked", set())
    monkeypatch.setattr("rsched.utils_lib.exists", lambda home, name: True)
    monkeypatch.setattr(mp, "probe", lambda server, name, timeout: asked.append(name) or {})
    server = _server(tmp_path)
    server.machines |= {"known": _mac()}
    mp.record(tmp_path, "known", server.machines["known"], privileged=False, why="uid 1000")
    mp.ensure_probed(server, ["box", "known", "missing"])
    mp.ensure_probed(server, ["box"])
    assert asked == ["box"]
