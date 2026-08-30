"""The third layer: a library change that arrives with no writer to gate.

The engine's authoring approval and the Library tab's confirm digest cover the two interactive
writers. This covers the rest — a sync pull, a hand edit, a restored bundle — which are exactly
the paths nobody is watching when they happen.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from rsched import pending
from rsched.daemon.library_watch import LibraryWatch


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "PATH": "/usr/bin:/bin", "HOME": str(repo)})


def _util_src(name: str, secrets: str = "(none)") -> str:
    return (f'"""{name} — t.\n\nusage: gu {name}\ncalls: (none)\ntags: t\n'
            f'secrets: {secrets}\nnet: none\nfs: none\n"""\n')


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr("rsched.secrets.load_secrets", dict)
    lib, routines = tmp_path / "lib", tmp_path / "routines"
    for sub in ("utils", "permissions", "rules"):
        (lib / sub).mkdir(parents=True)
    (routines / ".control").mkdir(parents=True)
    (lib / "utils" / "sig").mkdir()
    (lib / "utils" / "sig" / "main.py").write_text(_util_src("sig"), encoding="utf-8")
    (routines / "holder").mkdir()
    (routines / "holder" / "routine.yaml").write_text(yaml.safe_dump(
        {"description": "t", "permissions": [], "rules": [],
         "capabilities": {"utils": ["sig"]}}), encoding="utf-8")
    _git(lib, "init", "-q", "-b", "main")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "initial")
    server = SimpleNamespace(libraries_home=lib, permissions_home=lib / "permissions",
                             rules_home=lib / "rules", routines_home=routines, machines={})
    return server, lib, routines


def test_first_boot_records_head_without_reporting(world):
    """A fresh install must not announce its whole library as drift."""
    server, _lib, routines = world
    LibraryWatch(server)._check()
    assert pending.load_all(routines) == []
    assert (routines / ".control" / "library-head.json").is_file()


def test_a_commit_that_breaks_a_holder_queues_a_decision(world):
    """A break is not a notification — it is a decision (expose the secret, or unbind), which
    the Decisions page already settles on entity ids from the existing vocabulary."""
    server, lib, routines = world
    watch = LibraryWatch(server)
    watch._check()                                   # baseline

    (lib / "utils" / "sig" / "main.py").write_text(_util_src("sig", "NEW_PIN"),
                                                   encoding="utf-8")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "sig: require a PIN")
    watch._check()

    recs = pending.load_all(routines)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["kind"] == "library-drift" and rec["routine"] == "holder"
    assert "NEW_PIN" in rec["summary"] and "sig: require a PIN" in rec["summary"]
    assert rec["fields"]["entity"] == "holder:secret:NEW_PIN"


def test_the_same_gap_is_queued_once_not_once_per_commit(world):
    """Otherwise a busy library turns one real signal into a list nobody reads."""
    server, lib, routines = world
    watch = LibraryWatch(server)
    watch._check()
    (lib / "utils" / "sig" / "main.py").write_text(_util_src("sig", "NEW_PIN"),
                                                   encoding="utf-8")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "one")
    watch._check()
    (lib / "utils" / "sig" / "main.py").write_text(
        _util_src("sig", "NEW_PIN") + "# unrelated\n", encoding="utf-8")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "two")
    watch._check()
    assert len(pending.load_all(routines)) == 1


def test_an_unchanged_head_does_no_work(world):
    server, _lib, routines = world
    watch = LibraryWatch(server)
    watch._check()
    watch._check()
    assert pending.load_all(routines) == []


def test_a_non_git_library_is_a_no_op(tmp_path, monkeypatch):
    """The watcher must never be the reason a daemon tick fails."""
    monkeypatch.setattr("rsched.secrets.load_secrets", dict)
    lib, routines = tmp_path / "lib", tmp_path / "routines"
    lib.mkdir()
    routines.mkdir()
    server = SimpleNamespace(libraries_home=lib, permissions_home=lib / "permissions",
                             rules_home=lib / "rules", routines_home=routines, machines={})
    LibraryWatch(server)._check()
    assert not (routines / ".control" / "library-head.json").exists()


def test_only_blocking_rows_queue(world):
    """An interrupt already asks the user at the moment it matters; queueing those too would
    bury the genuine signal."""
    server, lib, routines = world
    watch = LibraryWatch(server)
    watch._check()
    (lib / "rules" / "status-page.md").write_text(
        "---\ntags: [a, b, c]\nexpects:\n  fs-write: ['*']\n---\n# rule: status page — x\n",
        encoding="utf-8")
    (routines / "holder" / "routine.yaml").write_text(yaml.safe_dump(
        {"description": "t", "permissions": [], "rules": ["status-page"],
         "capabilities": {"utils": []}}), encoding="utf-8")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "status-page expects a root")
    watch._check()
    assert pending.load_all(routines) == []          # the expects: row is an interrupt
