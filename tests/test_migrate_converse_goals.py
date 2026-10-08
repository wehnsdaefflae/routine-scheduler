"""The one-shot that carries the converse pattern's goals revision to the live library
(MIGRATION(expires=2026-11-30)): the seed sync only ADDS missing patterns, so the existing one is
replaced here — and only while it is the superseded version-4 seed byte for byte."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from rsched import libgit
from rsched import migrate_converse_goals as mig

OLD, NEW = "# converse, version 4\n", "# converse, version 5\n"


def _instance(tmp_path, monkeypatch, live: str | None):
    repo = tmp_path / "repo"
    seed = repo / "library-seed" / mig.PATTERN
    seed.parent.mkdir(parents=True)
    seed.write_text(NEW, encoding="utf-8")
    monkeypatch.setattr("rsched.bootstrap.repo_root", lambda: repo)
    monkeypatch.setattr(mig, "SUPERSEDED", hashlib.sha256(OLD.encode()).hexdigest())
    server = SimpleNamespace(routines_home=tmp_path / "routines", libraries_home=tmp_path / "lib")
    server.routines_home.mkdir()
    (server.libraries_home / "workflows").mkdir(parents=True)
    if live is not None:
        (server.libraries_home / mig.PATTERN).write_text(live, encoding="utf-8")
    return server


@pytest.mark.parametrize(("live", "after", "left"), [
    ("# an operator's own edit\n", "# an operator's own edit\n", "by hand"),
    (NEW, NEW, "already current"),
    (None, None, "not read"),                     # deleted live: it stays deleted
])
def test_anything_but_the_pristine_seed_is_left_and_named(tmp_path, monkeypatch, live, after,
                                                          left):
    server = _instance(tmp_path, monkeypatch, live)
    record = mig.run_migration(server)
    path = server.libraries_home / mig.PATTERN
    assert (path.read_text(encoding="utf-8") if path.exists() else None) == after
    assert left in record["left"] and "replaced" not in record


def test_the_pristine_seed_is_replaced_committed_and_carried_once(tmp_path, monkeypatch):
    server = _instance(tmp_path, monkeypatch, OLD)
    lib = server.libraries_home
    libgit.init_repo(lib, first_commit="init")

    record = mig.run_migration(server)

    assert (lib / mig.PATTERN).read_text(encoding="utf-8") == NEW
    assert record["replaced"] == "workflows/converse.py"
    assert libgit.git(lib, "log", "-1", "--format=%s").stdout.startswith("converse: keep")
    stored = json.loads((server.routines_home / mig.RECORD).read_text(encoding="utf-8"))
    assert stored["replaced"] == record["replaced"]
    (lib / mig.PATTERN).write_text(OLD, encoding="utf-8")   # a later boot finds the record
    assert mig.run_migration(server) == {}
    assert (lib / mig.PATTERN).read_text(encoding="utf-8") == OLD


def test_the_superseded_hash_is_the_version_4_this_release_replaces():
    """The pin must name a pattern that is NOT the seed this release ships — a hash of the new
    seed would replace nothing and say "already current" forever."""
    from rsched.bootstrap import repo_root

    shipped = (repo_root() / "library-seed" / mig.PATTERN).read_bytes()
    assert hashlib.sha256(shipped).hexdigest() != mig.SUPERSEDED
    assert b'"version": 5' in shipped
