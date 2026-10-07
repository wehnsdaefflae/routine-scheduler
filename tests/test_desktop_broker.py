"""The agent desktops' broker (deploy/desktop-broker): who is calling, what it may mount.

Every desktop is one routine's, and every caller holds the same bearer token, so the broker
learns WHICH routine is calling from what that routine's own sandbox lets it do: write a
one-time file into its working directory (identity), write into a folder (a read-write share)
or list it (a read-only share). These tests hold that proof logic, the util's half of it (the
digest must be computed the same way on both sides of the container boundary), the share
refusals, and the HTTP routing — with the VM fleet stubbed, since a VM needs KVM.
"""
from __future__ import annotations

import importlib.util
import json
import os
import secrets
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deploy" / "desktop-broker"))

import broker  # noqa: E402
import proof  # noqa: E402
import shares  # noqa: E402
import vms  # noqa: E402


def _util():
    spec = importlib.util.spec_from_file_location(
        "desktop_util", REPO / "util-seed" / "utils" / "desktop" / "main.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _proof(directory: Path) -> str:
    name = proof.PROOF_PREFIX + secrets.token_hex(8)
    value = secrets.token_hex(32)
    (directory / name).write_text(value)
    return f"{name}:{value}"


# ---- identity --------------------------------------------------------------------------------


def test_identity_is_the_top_of_the_routine_or_conversation_a_directory_sits_in(tmp_path):
    (tmp_path / "routines" / "foo" / "runs" / "x").mkdir(parents=True)
    (tmp_path / "conversations" / "chat").mkdir(parents=True)
    assert proof.identity(str(tmp_path / "routines" / "foo"), tmp_path) == "routines--foo"
    assert proof.identity(str(tmp_path / "routines/foo/runs/x"), tmp_path) == "routines--foo"
    assert proof.identity(str(tmp_path / "conversations/chat"), tmp_path) == "conversations--chat"
    for outside in (tmp_path / "routines", tmp_path, tmp_path / "nope"):
        with pytest.raises(proof.ProofError):
            proof.identity(str(outside), tmp_path)


def test_a_symlink_into_another_routine_is_judged_where_it_points(tmp_path):
    (tmp_path / "routines" / "victim").mkdir(parents=True)
    (tmp_path / "routines" / "mine").mkdir()
    (tmp_path / "routines" / "mine" / "door").symlink_to(tmp_path / "routines" / "victim")
    assert proof.identity(str(tmp_path / "routines/mine/door"), tmp_path) == "routines--victim"


def test_a_proof_holds_once_and_is_consumed(tmp_path):
    token = _proof(tmp_path)
    proof.check_file(tmp_path, token)
    assert not list(tmp_path.iterdir()), "the proof file must be consumed"
    with pytest.raises(proof.ProofError):
        proof.check_file(tmp_path, token)                       # replayed


def test_a_wrong_or_malformed_or_linked_proof_is_refused(tmp_path):
    name, _, _ = _proof(tmp_path).partition(":")
    with pytest.raises(proof.ProofError):
        proof.check_file(tmp_path, f"{name}:{'0' * 64}")
    assert not (tmp_path / name).exists(), "a failed proof is consumed too"
    for bad in ("", "x:y", f"{proof.PROOF_PREFIX}zz:{'0' * 64}", "../a:" + "0" * 64):
        with pytest.raises(proof.ProofError):
            proof.check_file(tmp_path, bad)
    target = tmp_path / "elsewhere"
    target.write_text("0" * 64)
    link = proof.PROOF_PREFIX + "0123456789abcdef"
    (tmp_path / link).symlink_to(target)
    with pytest.raises(proof.ProofError):
        proof.check_file(tmp_path, f"{link}:{'0' * 64}")


def test_the_util_and_the_broker_compute_one_listing_digest(tmp_path):
    """The util runs in the engine container, the broker in the sidecar; both read the same
    files through binds, and a digest that differed would refuse every read-only mount."""
    util = _util()
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    _proof(tmp_path)                                            # never part of the digest
    assert util.listing_digest(tmp_path) == proof.listing_digest(tmp_path)
    proof.check_digest(tmp_path, util.listing_digest(tmp_path))
    with pytest.raises(proof.ProofError):
        proof.check_digest(tmp_path, "0" * 64)


def test_an_empty_folder_cannot_prove_read_access(tmp_path):
    with pytest.raises(proof.ProofError):
        proof.listing_digest(tmp_path)


# ---- shares ----------------------------------------------------------------------------------


def test_a_share_name_is_safe_and_unique():
    assert shares.mount_name(Path("/x/My Project!"), None, set()) == "my-project"
    assert shares.mount_name(Path("/x/data"), None, {"data", "data-2"}) == "data-3"
    assert shares.mount_name(Path("/"), None, set()) == "share"
    assert shares.NAME.fullmatch(shares.mount_name(Path("/x/" + "a" * 90), None, set()))


def test_a_read_write_share_never_holds_a_routines_own_root(tmp_path):
    home = tmp_path / "routines"
    (home / "foo" / "artifacts").mkdir(parents=True)
    (home / "foo" / "routine.yaml").write_text("x")
    roots, homes = [tmp_path], [home]
    shares.check_shareable(home / "foo" / "artifacts", True, roots, homes)
    shares.check_shareable(home / "foo", False, roots, homes)     # read-only is fine
    for rw_refused in (home / "foo", home, tmp_path):
        with pytest.raises(shares.ShareError):
            shares.check_shareable(rw_refused, True, roots, homes)


def test_only_the_mirrored_homes_can_be_shared(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    shares.check_shareable(tmp_path / "in", False, [tmp_path / "in"], [])
    with pytest.raises(shares.ShareError):
        shares.check_shareable(tmp_path / "out", False, [tmp_path / "in"], [])
    with pytest.raises(shares.ShareError):
        shares.check_shareable(tmp_path / "missing", False, [tmp_path], [])


# ---- the fleet's pure parts ------------------------------------------------------------------


def test_each_slot_is_its_own_slash_30():
    assert vms.slot_net(0) == ("192.168.249.2", "192.168.249.1")
    assert vms.slot_net(1) == ("192.168.249.6", "192.168.249.5")
    line = vms.cmdline("routines--foo", 1, "Europe/Berlin")
    assert "rsched.ip=192.168.249.6/30" in line and "rsched.gw=192.168.249.5" in line
    assert "rsched.tz=Europe/Berlin" in line and "rsched.name=routines--foo" in line


def test_a_screen_is_reached_by_a_random_token_never_by_its_routines_name(tmp_path):
    vm = vms.VM(name="routines--foo", slot=1, dir=tmp_path)
    text = vms.vnc_tokens({vm.name: vm})
    assert text == f"{vm.vnc}: 192.168.249.6:5900\n"
    assert "foo" not in text and len(vm.vnc) == 32


def test_settings_read_the_environment_and_ignore_nonsense(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_STATE", str(tmp_path))
    monkeypatch.setenv("DESKTOP_MAX_VMS", "3")
    monkeypatch.setenv("DESKTOP_MEM_MB", "lots")
    monkeypatch.setenv("DESKTOP_IDLE_MIN", "0")
    s = vms.Settings.from_env()
    assert (s.max_vms, s.mem_mb, s.idle_s) == (3, 3072, 20 * 60)


def test_a_full_fleet_says_who_holds_it(tmp_path):
    fleet = vms.Fleet(vms.Settings(state=tmp_path, max_vms=1))
    fleet.vms["routines--a"] = vms.VM(name="routines--a", slot=0, dir=tmp_path / "a")
    with pytest.raises(vms.BusyError, match="routines--a"):
        fleet._free_slot()


# ---- the HTTP door ---------------------------------------------------------------------------


@pytest.fixture
def door(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "routines" / "alpha").mkdir(parents=True)
    monkeypatch.setattr(broker, "HOME", home)
    monkeypatch.setenv("DESKTOP_OPERATOR_TOKEN", "op-secret")
    # A real fleet with its VM-touching methods replaced: booting one needs KVM.
    fleet = vms.Fleet(vms.Settings(state=tmp_path / "state", max_vms=2))
    vm = vms.VM(name="", slot=0, dir=tmp_path)
    stops: list = []

    def ensure(name):
        vm.name = name
        return vm

    monkeypatch.setattr(fleet, "ensure", ensure)
    monkeypatch.setattr(fleet, "status", lambda *, operator=False:
                        [{"name": "routines--a", "vnc": "v" * 32}] if operator else [])
    monkeypatch.setattr(fleet, "stop", lambda name, why: stops.append(("stop", name)) or True)
    monkeypatch.setattr(broker.Handler, "fleet", fleet, raising=False)
    sent: list = []
    monkeypatch.setattr(vms, "agent_call",
                        lambda vm, op, raw, timeout: sent.append((vm.name, op)) or {"ok": True})
    server = ThreadingHTTPServer(("127.0.0.1", 0), broker.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def post(op, body=None, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/{op}",
                                     data=json.dumps(body or {}).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 — loopback
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    yield post, home / "routines" / "alpha", stops, sent
    server.shutdown()
    server.server_close()


def test_a_proved_call_reaches_its_own_routines_desktop(door):
    post, workdir, _, sent = door
    status, _ = post("windows", headers={"X-Desktop-Workdir": str(workdir),
                                         "X-Desktop-Proof": _proof(workdir)})
    assert status == 200 and sent == [("routines--alpha", "windows")]


def test_an_unproved_call_reaches_no_desktop(door):
    post, workdir, _, sent = door
    status, body = post("windows", headers={"X-Desktop-Workdir": str(workdir),
                                            "X-Desktop-Proof": "x:y"})
    assert status == 403 and sent == [], body
    status, body = post("mount_share", headers={"X-Desktop-Workdir": str(workdir),
                                                "X-Desktop-Proof": _proof(workdir)})
    assert status == 400 and "no such operation" in body["error"] and sent == []


def test_the_fleet_and_its_screens_are_the_operators_alone(door):
    post, workdir, stops, _ = door
    status, _ = post("fleet")
    assert status == 403
    status, _ = post("fleet", headers={"X-Desktop-Operator": "wrong"})
    assert status == 403
    status, body = post("fleet", headers={"X-Desktop-Operator": "op-secret"})
    assert status == 200 and body["desktops"][0]["vnc"] == "v" * 32
    status, body = post("fleet_stop", {"name": "routines--a"},
                        headers={"X-Desktop-Operator": "op-secret"})
    assert status == 200 and stops == [("stop", "routines--a")]
    # a routine's own status never carries a screen token
    status, body = post("vm_status", headers={"X-Desktop-Workdir": str(workdir),
                                              "X-Desktop-Proof": _proof(workdir)})
    assert status == 200 and "vnc" not in json.dumps(body)


def test_without_an_operator_token_configured_nobody_is_the_operator(door, monkeypatch):
    post, *_ = door
    monkeypatch.delenv("DESKTOP_OPERATOR_TOKEN")
    status, _ = post("fleet", headers={"X-Desktop-Operator": ""})
    assert status == 403
    assert os.environ.get("DESKTOP_OPERATOR_TOKEN") is None
