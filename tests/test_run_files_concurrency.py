"""F896: the served files endpoint overlaps real resolution without weakening D172."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import yaml

from conftest import mk_run

TS = "20261117-140000"
URL = f"/api/runs/files:{TS}"


def _record(routine_dir, paths):
    return mk_run(routine_dir, TS, "finished", transcript=[
        {"type": "observation", "turn": turn,
         "payload": {"kind": "read_file", "path": str(path)}}
        for turn, path in enumerate(paths, 1)])


def _card(client):
    response = client.get(f"{URL}/files")
    assert response.status_code == 200
    return response.json()


def _fetch(client, path):
    return client.get(f"{URL}/file", params={"path": str(path)})


def _grants(routine_dir, *, reads=(), writes=()):
    config_path = routine_dir / "routine.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cfg["fs_read_roots"] = [str(path) for path in reads]
    cfg["fs_write_roots"] = [str(path) for path in writes]
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def test_served_endpoint_overlaps_resolution_and_preserves_card_order(api_client, make_routine,
                                                                    monkeypatch):
    client, _ = api_client
    routine_dir = make_routine(slug="files")
    paths = [routine_dir / name for name in ("z.txt", "a.txt", "middle.txt")]
    for path in paths:
        path.write_text(path.name, encoding="utf-8")
    run_dir = _record(routine_dir, paths)
    history = run_dir / "history"
    history.mkdir()
    for name in ("z.md", "a.md"):
        (history / name).write_text(name, encoding="utf-8")
    (history / "directory-not-a-file").mkdir()
    rendezvous = threading.Barrier(len(paths))
    lock = threading.Lock()
    waiting = set(paths)
    original_resolve = Path.resolve

    def gated_resolve(path, *args, **kwargs):
        # paths.within resolves candidates again: gate each candidate only ONCE.
        with lock:
            gate = path in waiting
            waiting.discard(path)
        if gate:
            rendezvous.wait(timeout=5)             # the old sequential route cannot pass
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", gated_resolve)
    card = _card(client)
    assert not waiting
    assert card["history"] == ["a.md", "z.md"]
    assert card["files"] == [
        {"path": str(path), "reads": 1, "writes": 0, "edits": 0, "bytes": 0,
         "errors": 0, "sub": False, "bases": [""], "servable": True}
        for path in paths]


def test_deleted_file_loses_chip_without_a_transcript_change(api_client, make_routine):
    client, _ = api_client
    routine_dir = make_routine(slug="files")
    run_dir = _record(routine_dir, ["out.txt"])
    target = run_dir / "out.txt"
    target.write_text("first bytes", encoding="utf-8")
    transcript = (run_dir / "transcript.jsonl").read_bytes()
    assert _card(client)["files"][0]["servable"] is True
    assert _fetch(client, "out.txt").text == "first bytes"
    target.unlink()
    assert _card(client)["files"][0]["servable"] is False
    assert _fetch(client, "out.txt").status_code == 404
    target.write_text("replacement", encoding="utf-8")
    assert _card(client)["files"][0]["servable"] is True
    assert _fetch(client, "out.txt").text == "replacement"
    assert (run_dir / "transcript.jsonl").read_bytes() == transcript


def test_live_read_grant_revocation_removes_chip_and_access(api_client, make_routine):
    client, tmp = api_client
    routine_dir = make_routine(slug="files")
    granted = tmp / "granted"
    granted.mkdir()
    target = granted / "source.txt"
    target.write_text("granted bytes", encoding="utf-8")
    run_dir = _record(routine_dir, [target])
    transcript = (run_dir / "transcript.jsonl").read_bytes()
    assert _card(client)["files"][0]["servable"] is False
    assert _fetch(client, target).status_code == 400
    _grants(routine_dir, reads=[granted])
    assert _card(client)["files"][0]["servable"] is True
    assert _fetch(client, target).text == "granted bytes"
    _grants(routine_dir)
    assert _card(client)["files"][0]["servable"] is False
    response = _fetch(client, target)
    assert response.status_code == 400 and "granted bytes" not in response.text
    assert (run_dir / "transcript.jsonl").read_bytes() == transcript


def test_symlink_outside_roots_into_grant_is_servable_and_retarget_is_fresh(api_client,
                                                                           make_routine):
    client, tmp = api_client
    routine_dir = make_routine(slug="files")
    granted = tmp / "granted"
    granted.mkdir()
    allowed = granted / "source.txt"
    allowed.write_text("allowed bytes", encoding="utf-8")
    denied = tmp / "private-target.txt"
    denied.write_text("PRIVATE BYTES", encoding="utf-8")
    alias = tmp / "outside-all-roots-link"
    alias.symlink_to(allowed)
    run_dir = _record(routine_dir, [alias])
    transcript = (run_dir / "transcript.jsonl").read_bytes()
    _grants(routine_dir, reads=[granted])
    assert _card(client)["files"][0]["servable"] is True
    assert _fetch(client, alias).text == "allowed bytes"   # no lexical rejection of alias
    alias.unlink()
    alias.symlink_to(denied)
    card = _card(client)
    assert card["files"][0]["path"] == str(alias)
    assert card["files"][0]["servable"] is False
    assert str(denied) not in json.dumps(card)       # no resolved target/root detail leakage
    response = _fetch(client, alias)
    assert response.status_code == 400
    assert "PRIVATE BYTES" not in response.text and str(denied) not in response.text
    alias.unlink()
    alias.symlink_to(allowed)
    assert _card(client)["files"][0]["servable"] is True
    assert (run_dir / "transcript.jsonl").read_bytes() == transcript


def test_denied_write_only_broken_and_directory_rows_have_no_chip(api_client, make_routine):
    client, tmp = api_client
    routine_dir = make_routine(slug="files")
    private = tmp / "private"
    private.mkdir()
    secret = private / "secret.txt"
    secret.write_text("PRIVATE BYTES", encoding="utf-8")
    write_only = tmp / "write-only"
    write_only.mkdir()
    written = write_only / "written.txt"
    written.write_text("WRITE-ONLY BYTES", encoding="utf-8")
    escape = routine_dir / "escape"
    escape.symlink_to(secret)
    broken = routine_dir / "broken"
    broken.symlink_to(private / "missing.txt")
    directory = routine_dir / "a-directory"
    directory.mkdir()
    paths = [escape, broken, directory, written]
    _record(routine_dir, paths)
    _grants(routine_dir, writes=[write_only])
    card = _card(client)
    assert [row["path"] for row in card["files"]] == [str(path) for path in paths]
    assert all(row["servable"] is False for row in card["files"])
    assert str(private) not in json.dumps(card)      # only original row paths are exposed
    for path in paths:
        response = _fetch(client, path)
        assert response.status_code == 400
        assert "PRIVATE BYTES" not in response.text and "WRITE-ONLY BYTES" not in response.text


def test_child_bases_still_resolve_for_chip_and_download(api_client, make_routine):
    client, _ = api_client
    run_dir = _record(make_routine(slug="files"), [])
    child = run_dir / "sub" / "1"
    target = child / "build" / "out.txt"
    target.parent.mkdir(parents=True)
    target.write_text("child bytes", encoding="utf-8")
    (child / "transcript.jsonl").write_text(json.dumps({
        "type": "observation", "turn": 1,
        "payload": {"kind": "write_file", "path": "build/out.txt", "bytes": 11}}) + "\n",
        encoding="utf-8")
    row = _card(client)["files"][0]
    assert row["sub"] is True and row["bases"] == ["sub/1"] and row["servable"] is True
    response = _fetch(client, "build/out.txt")
    assert response.status_code == 200 and response.text == "child bytes"


def test_empty_card_and_invalid_or_missing_run_keep_their_errors(api_client, make_routine,
                                                               monkeypatch):
    client, _ = api_client
    _record(make_routine(slug="files"), [])

    def unexpected(*_args, **_kwargs):
        raise AssertionError("empty/invalid/missing run submitted a row check")

    monkeypatch.setattr(client.app.state.file_checks._pool, "submit", unexpected)
    assert _card(client) == {"files": [], "history": []}
    assert client.get("/api/runs/not-a-run/files").status_code == 400
    assert client.get("/api/runs/files:20990101-000000/files").status_code == 404
