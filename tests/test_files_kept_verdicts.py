"""Files-card rows on a NETWORK share are judged off the request; local rows stay fresh (2026-11-21).

`/api/runs/{id}/files` resolved and statted every row inside the request, and a card listing
hundreds of rows on the NAS sshfs share took 60-74 s (`web/fileverdicts.py`). Here a tmp dir plays
the share (`netmounts.network_mounts` is patched to name it), so each test pins one property the
change rests on: a share row is never touched by the request, its verdict lands from one
background job, an unknown verdict is never offered as servable, and a local row is still judged
afresh on every call — F896's guarantee, which `tests/test_run_files_concurrency.py` keeps pinning.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import mk_run
from rsched.readmodels import memo
from rsched.readmodels.fileactivity import file_activity
from rsched.web import fileverdicts, netmounts, servable
from rsched.web.filechecks import FileChecks

TS = "20261121-120000"
URL = f"/api/runs/files:{TS}/files"


def _record(routine_dir: Path, paths) -> Path:
    return mk_run(routine_dir, TS, "finished", transcript=[
        {"type": "observation", "turn": turn,
         "payload": {"kind": "read_file", "path": str(path)}}
        for turn, path in enumerate(paths, 1)])


def _grant(routine_dir: Path, root: Path) -> None:
    config_path = routine_dir / "routine.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cfg["fs_read_roots"] = [str(root)]
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _share(tmp: Path, monkeypatch) -> Path:
    share = tmp / "share"
    share.mkdir()
    monkeypatch.setattr(netmounts, "network_mounts", lambda mounts=None: (share,))
    return share


def _settle(client, run_dir: Path) -> None:
    kept = client.app.state.file_verdicts
    deadline = time.monotonic() + 10
    while kept.checking(run_dir):
        assert time.monotonic() < deadline, "the background check never finished"
        time.sleep(0.01)


def _gated(share: Path, gate: threading.Event, seen: list[tuple[str, str]]):
    """`servable_rel`, recording which thread asked about which path; share paths wait for
    `gate`, so a test controls when the background job may finish."""
    real = servable.servable_rel

    def predicate(path, run_dir, roots, bases=()):
        seen.append((threading.current_thread().name, str(path)))
        if str(path).startswith(str(share)) or any(str(r).startswith(str(share)) for r in roots):
            assert gate.wait(10)
        return real(path, run_dir, roots, bases)

    return predicate


# --------------------------------------------------------------------------- the mount table

def test_network_mounts_come_from_the_mount_table(tmp_path):
    table = tmp_path / "mounts"
    table.write_text(
        "overlay / overlay rw 0 0\n"
        "/dev/sda1 /home ext4 rw 0 0\n"
        "systemd-1 /mnt/nas autofs rw 0 0\n"
        "sshd@nas:/x /mnt/nas fuse.sshfs rw 0 0\n"
        "mark@laptop:/home /mnt/laptop fuse.sshfs rw 0 0\n"
        "/dev/sdb1 /mnt/usb fuseblk rw 0 0\n"
        "srv:/e /mnt/my\\040share nfs4 rw 0 0\n", encoding="utf-8")
    assert set(netmounts.network_mounts(table)) == {
        Path("/mnt/nas"), Path("/mnt/laptop"), Path("/mnt/my share")}
    assert netmounts.network_mounts(tmp_path / "absent") == ()      # unreadable: all local


def test_on_network_is_a_path_prefix_never_a_string_prefix():
    mounts = (Path("/mnt/nas"),)
    assert netmounts.on_network(Path("/mnt/nas"), mounts)
    assert netmounts.on_network(Path("/mnt/nas/grants/x.png"), mounts)
    assert not netmounts.on_network(Path("/mnt/nasty/x.png"), mounts)
    assert not netmounts.on_network(Path("/home/mark/x"), mounts)


def test_a_skipped_root_is_never_resolved(api_client, make_routine):
    client, tmp = api_client
    routine_dir = make_routine(slug="files")
    share = tmp / "share"
    share.mkdir()
    _grant(routine_dir, share)
    server = client.app.state.server
    assert share in servable.granted_roots(server, routine_dir)
    asked: list[Path] = []

    def skip(root: Path) -> bool:
        asked.append(root)
        return root == share

    roots = servable.roots_for_run(server, routine_dir / "runs" / TS, routine_dir, skip=skip)
    assert share not in roots and share in asked


# --------------------------------------------------------------------------- the route

def test_a_share_row_lands_from_one_background_job_never_from_the_request(api_client,
                                                                          make_routine,
                                                                          monkeypatch):
    client, tmp = api_client
    share = _share(tmp, monkeypatch)
    routine_dir = make_routine(slug="files")
    (share / "deck.png").write_bytes(b"png")
    local = routine_dir / "notes.md"
    local.write_text("local", encoding="utf-8")
    run_dir = _record(routine_dir, [share / "deck.png", local])
    _grant(routine_dir, share)
    gate, seen = threading.Event(), []
    monkeypatch.setattr(servable, "servable_rel", _gated(share, gate, seen))

    # Were the share row checked in the request, the gated predicate would hold the request
    # itself until the gate opened (and fail after 10 s): an immediate `None` proves it was not.
    first = client.get(URL).json()
    assert [r["servable"] for r in first["files"]] == [None, True]   # unknown, never offered
    assert first["pending"] is True
    for _ in range(3):                                                # polls while it checks
        assert client.get(URL).json()["files"][0]["servable"] is None
    gate.set()
    _settle(client, run_dir)
    done = client.get(URL).json()
    assert [r["servable"] for r in done["files"]] == [True, True] and done["pending"] is False
    assert sum(1 for _, path in seen if path == str(share / "deck.png")) == 1   # one job


def test_local_rows_beside_a_share_stay_fresh(api_client, make_routine, monkeypatch):
    client, tmp = api_client
    _share(tmp, monkeypatch)
    routine_dir = make_routine(slug="files")
    local = routine_dir / "notes.md"
    local.write_text("local", encoding="utf-8")
    _record(routine_dir, [local])
    assert client.get(URL).json()["files"][0]["servable"] is True
    local.unlink()
    assert client.get(URL).json()["files"][0]["servable"] is False   # no kept verdict for it


def test_a_relative_row_only_a_share_root_can_hold_waits_for_its_check(api_client,
                                                                       make_routine,
                                                                       monkeypatch):
    client, tmp = api_client
    share = _share(tmp, monkeypatch)
    routine_dir = make_routine(slug="files")
    (share / "only-there.txt").write_text("x", encoding="utf-8")
    (routine_dir / "here.txt").write_text("x", encoding="utf-8")
    run_dir = _record(routine_dir, ["only-there.txt", "here.txt"])
    _grant(routine_dir, share)
    gate, seen = threading.Event(), []
    monkeypatch.setattr(servable, "servable_rel", _gated(share, gate, seen))
    first = client.get(URL).json()["files"]
    assert [r["servable"] for r in first] == [None, True]     # local hit answered at once
    gate.set()
    _settle(client, run_dir)
    assert [r["servable"] for r in client.get(URL).json()["files"]] == [True, True]


def test_the_shared_rows_are_never_annotated(api_client, make_routine, monkeypatch):
    """`file_activity` is now handed out SHARED: the card must copy before it writes a verdict."""
    client, tmp = api_client
    _share(tmp, monkeypatch)
    routine_dir = make_routine(slug="files")
    run_dir = _record(routine_dir, [routine_dir / "x.md"])
    client.get(URL)
    assert "servable" not in file_activity(run_dir)[0]


def test_a_warm_file_activity_copies_nothing(make_routine, monkeypatch):
    routine_dir = make_routine(slug="files")
    run_dir = _record(routine_dir, [routine_dir / "x.md"])
    file_activity(run_dir)

    def no_copy(*_a, **_kw):
        raise AssertionError("file_activity deep-copied its rows")

    monkeypatch.setattr(memo, "copy", SimpleNamespace(deepcopy=no_copy))
    assert file_activity(run_dir)[0]["path"] == str(routine_dir / "x.md")


# --------------------------------------------------------------------------- the keeper

@pytest.fixture
def kept():
    checks = FileChecks(workers=2)
    keeper = fileverdicts.KeptVerdicts(checks)
    try:
        yield keeper
    finally:
        keeper.shutdown()
        checks.shutdown(wait=True)


def _wait(keeper, run_dir):
    deadline = time.monotonic() + 10
    while keeper.checking(run_dir):
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_a_check_that_raised_leaves_its_rows_unknown(kept, monkeypatch, tmp_path):
    def boom(*_a, **_kw):
        raise PermissionError("the share refused")

    monkeypatch.setattr(servable, "servable_rel", boom)
    rows = [{"path": "/mnt/nas/a"}, {"path": "/mnt/nas/b"}]
    kept.refresh(tmp_path, rows, lambda: (tmp_path,))
    _wait(kept, tmp_path)
    verdicts, due = kept.current(tmp_path, rows)
    assert verdicts == [None, None] and due is True
    assert kept.checking(tmp_path) is False                  # nothing pending: the card stops
    kept.refresh(tmp_path, rows, lambda: (tmp_path,))        # an open card polls again...
    assert kept.checking(tmp_path) is False                  # ...and starts no job inside the gap


def test_a_verdict_ages_out_to_unknown_and_is_refreshed_before_that(kept, monkeypatch, tmp_path):
    monkeypatch.setattr(servable, "servable_rel", lambda *_a, **_kw: True)
    rows = [{"path": "/mnt/nas/a", "bases": ["sub/1"]}]
    kept.refresh(tmp_path, rows, lambda: (tmp_path,))
    _wait(kept, tmp_path)
    assert kept.current(tmp_path, rows) == ([True], False)
    start = fileverdicts.monotonic()
    monkeypatch.setattr(fileverdicts, "monotonic",
                        lambda: start + fileverdicts.REFRESH_AFTER_S + 1)
    assert kept.current(tmp_path, rows) == ([True], True)    # still served, refresh due
    monkeypatch.setattr(fileverdicts, "monotonic",
                        lambda: start + fileverdicts.VERDICT_MAX_AGE_S + 1)
    assert kept.current(tmp_path, rows) == ([None], True)    # too old: unknown, not last answer


def test_the_bases_are_part_of_a_rows_identity(kept, monkeypatch, tmp_path):
    monkeypatch.setattr(servable, "servable_rel", lambda *_a, **_kw: True)
    kept.refresh(tmp_path, [{"path": "x.txt", "bases": ["sub/1"]}], lambda: (tmp_path,))
    _wait(kept, tmp_path)
    assert kept.current(tmp_path, [{"path": "x.txt", "bases": ["sub/2"]}])[0] == [None]


def test_the_age_bound_outlasts_several_refreshes():
    assert fileverdicts.VERDICT_MAX_AGE_S >= 3 * fileverdicts.REFRESH_AFTER_S
