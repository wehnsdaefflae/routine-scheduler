"""The one-shot that carries this release's seed-util fixes to the live library
(MIGRATION(expires=2026-11-15)): the seed sync only ADDS missing utils, so an existing one is
replaced here — and only while it is the superseded seed byte for byte."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from rsched import libgit
from rsched import migrate_seed_utils as mig
from rsched.bootstrap import repo_root

OLD, NEW = "# the superseded seed\n", "# the seed this release ships\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _instance(tmp_path, monkeypatch, *, seeded: tuple[str, ...]):
    """A live library and a source checkout whose util-seed carries NEW for `seeded`."""
    repo = tmp_path / "repo"
    for name in seeded:
        seed = repo / "util-seed" / "utils" / name / "main.py"
        seed.parent.mkdir(parents=True)
        seed.write_text(NEW, encoding="utf-8")
    monkeypatch.setattr("rsched.bootstrap.repo_root", lambda: repo)
    server = SimpleNamespace(routines_home=tmp_path / "routines", libraries_home=tmp_path / "lib")
    server.routines_home.mkdir()
    (server.libraries_home / "utils").mkdir(parents=True)
    return server


def _live(server, name: str, text: str):
    path = server.libraries_home / "utils" / name / "main.py"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_only_a_pristine_copy_is_replaced_and_every_other_is_named(tmp_path, monkeypatch):
    names = ("pristine", "edited", "current", "deleted")
    monkeypatch.setattr(mig, "SUPERSEDED", {n: (_sha(OLD), f"fixes {n}") for n in names})
    server = _instance(tmp_path, monkeypatch, seeded=names)
    pristine = _live(server, "pristine", OLD)
    pristine.chmod(0o755)
    edited = _live(server, "edited", "# a routine's write_util revision\n")
    current = _live(server, "current", NEW)

    record = mig.run_migration(server)

    assert pristine.read_text(encoding="utf-8") == NEW
    assert pristine.stat().st_mode & 0o777 == 0o755, "the util keeps its mode"
    assert edited.read_text(encoding="utf-8") == "# a routine's write_util revision\n"
    assert current.read_text(encoding="utf-8") == NEW
    assert record["replaced"] == {"pristine": "fixes pristine"}
    assert "by hand" in record["left"]["edited"]
    assert record["left"]["current"] == "already current"
    assert record["left"]["deleted"].startswith("not read"), "a util deleted live stays gone"
    stored = json.loads((server.routines_home / mig.RECORD).read_text(encoding="utf-8"))
    assert stored["replaced"] == record["replaced"]


def test_the_replacement_is_committed_and_it_runs_once(tmp_path, monkeypatch):
    monkeypatch.setattr(mig, "SUPERSEDED", {"remote": (_sha(OLD), "fixes remote")})
    server = _instance(tmp_path, monkeypatch, seeded=("remote",))
    live = _live(server, "remote", OLD)
    libgit.init_repo(server.libraries_home, first_commit="init")

    mig.run_migration(server)

    lib = server.libraries_home
    assert libgit.git(lib, "log", "-1", "--format=%s").stdout.startswith("seed utils: remote")
    assert libgit.git(lib, "status", "--porcelain").stdout.strip() == ""
    live.write_text(OLD, encoding="utf-8")       # a later boot finds the record and stops
    assert mig.run_migration(server) == {}
    assert live.read_text(encoding="utf-8") == OLD


def test_every_util_it_carries_is_a_seed_util_this_release_changed():
    """The table is the migration's whole reach: each entry must name a real seed util, and
    the seed must no longer BE the version it supersedes — or there is nothing to carry."""
    for name, (superseded, fixes) in mig.SUPERSEDED.items():
        seed = repo_root() / "util-seed" / "utils" / name / "main.py"
        assert seed.is_file(), name
        assert hashlib.sha256(seed.read_bytes()).hexdigest() != superseded, name
        assert fixes.strip(), name
