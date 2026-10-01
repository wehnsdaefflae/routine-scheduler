"""A file the console serves is proven contained on what it OPENED, not on a path it checked.

Every web read of a file under a run home addresses a directory a jailed util may write — and
the routine token that util may hold reads these routes. A containment check made on a path
before the open can be raced: the util lets the check pass on a real directory and swaps that
directory for a symlink before the open, and the console then reads, for it, a file its own
jail forbids (the daemon's config with the operator's token in it). Each test below replays
exactly that interleaving: the swap lands right after the route's own check returned.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from conftest import mk_run
from rsched.web import api_routine_files, artifacts

SECRET = "token: the-operator-primary-token\n"


@pytest.fixture
def outside(tmp_path) -> Path:
    """A directory no routine may reach, holding what a util would love to read."""
    d = tmp_path / "daemon-config"
    d.mkdir()
    (d / "config.yaml").write_text(SECRET, encoding="utf-8")
    return d


def _swap_for_symlink(directory: Path, target: Path) -> None:
    """The util's move: the directory that passed the check becomes a symlink elsewhere."""
    shutil.rmtree(directory)
    directory.symlink_to(target, target_is_directory=True)


def _bait(routine_dir: Path, sub: str) -> Path:
    d = routine_dir / sub / "d"
    d.mkdir(parents=True)
    (d / "config.yaml").write_text("harmless\n", encoding="utf-8")
    return d


def _swap_after(monkeypatch, module, name: str, directory: Path, target: Path) -> None:
    """Wrap the route's containment check so the swap lands the moment it first PASSES."""
    original = getattr(module, name)

    def checked_then_swapped(*args, **kwargs):
        result = original(*args, **kwargs)
        if result and directory.is_dir() and not directory.is_symlink():
            _swap_for_symlink(directory, target)
        return result

    monkeypatch.setattr(module, name, checked_then_swapped)


def test_an_artifact_swapped_out_after_its_check_is_not_served(api_client, make_routine,
                                                                monkeypatch, outside):
    c, _ = api_client
    d = _bait(make_routine(slug="racer"), "artifacts")
    _swap_after(monkeypatch, artifacts, "_resolve_deliverable", d, outside)
    r = c.get("/api/routines/racer/artifact", params={"path": "artifacts/d/config.yaml"})
    assert SECRET not in r.text
    assert r.status_code == 400 and "changed while it was being served" in r.json()["detail"]


def test_a_recipe_file_swapped_out_after_its_check_is_not_read(api_client, make_routine,
                                                                monkeypatch, outside):
    c, _ = api_client
    d = _bait(make_routine(slug="racer"), "notes")
    _swap_after(monkeypatch, api_routine_files, "resolve_rel", d, outside)
    r = c.get("/api/routines/racer/file", params={"path": "notes/d/config.yaml"})
    assert SECRET not in r.text
    assert r.status_code == 404


def test_a_run_file_swapped_out_after_its_check_is_not_served(api_client, make_routine,
                                                               monkeypatch, outside):
    c, _ = api_client
    routine_dir = make_routine(slug="racer")
    mk_run(routine_dir, "20260707-081500", "finished")
    d = _bait(routine_dir, "artifacts")
    import rsched.paths as paths_mod

    _swap_after(monkeypatch, paths_mod, "within", d, outside)
    r = c.get("/api/runs/racer:20260707-081500/file",
              params={"path": "artifacts/d/config.yaml"})
    assert SECRET not in r.text
    assert r.status_code == 404


def test_a_delete_swapped_out_after_its_check_removes_nothing_outside(api_client, make_routine,
                                                                       monkeypatch, outside):
    """The operator's click, with the util's swap landing under it, used to delete the
    same-named file wherever the symlink pointed."""
    c, _ = api_client
    d = _bait(make_routine(slug="racer"), "artifacts")
    _swap_after(monkeypatch, artifacts, "_resolve_deliverable", d, outside)
    r = c.delete("/api/routines/racer/artifacts", params={"path": "artifacts/d/config.yaml"})
    assert r.status_code == 400
    assert (outside / "config.yaml").read_text(encoding="utf-8") == SECRET


def test_served_files_keep_their_bytes_type_and_freshness(api_client, make_routine):
    """The descriptor-backed response is still a FileResponse: bytes, type, the download
    name, the length — and never cached (R1682), on the run route as on the artifact one."""
    c, _ = api_client
    routine_dir = make_routine(slug="plain")
    mk_run(routine_dir, "20260707-081500", "finished")
    (routine_dir / "artifacts").mkdir()
    (routine_dir / "artifacts" / "report.json").write_text(json.dumps({"ok": 1}),
                                                           encoding="utf-8")
    for url, params in (("/api/routines/plain/artifact", {"path": "artifacts/report.json"}),
                        ("/api/runs/plain:20260707-081500/file",
                         {"path": "artifacts/report.json"})):
        r = c.get(url, params=params)
        assert r.status_code == 200 and r.json() == {"ok": 1}, url
        assert r.headers["content-type"].startswith("application/json")
        assert r.headers["content-length"] == str(len('{"ok": 1}'))
        assert "no-store" in r.headers["cache-control"], url
        assert 'filename="report.json"' in r.headers["content-disposition"]


def test_a_binary_recipe_file_is_a_legible_400(api_client, make_routine):
    c, _ = api_client
    routine_dir = make_routine(slug="plain")
    (routine_dir / "blob.bin").write_bytes(b"\xff\xfe\x00binary")
    r = c.get("/api/routines/plain/file", params={"path": "blob.bin"})
    assert r.status_code == 400 and "not a UTF-8 text file" in r.json()["detail"]
