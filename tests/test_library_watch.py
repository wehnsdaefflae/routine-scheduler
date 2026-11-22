"""The third layer: a library change that arrives with no writer to gate."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from helpers import util_src
from rsched import libgit, pending
from rsched.daemon.library_watch import LibraryWatch


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "PATH": "/usr/bin:/bin", "HOME": str(repo)})



def build_world(tmp_path, monkeypatch):
    """A library repo with one holder routine — the world both drift suites resolve against."""
    monkeypatch.setattr("rsched.secrets.load_secrets", dict)
    lib, routines = tmp_path / "lib", tmp_path / "routines"
    for sub in ("utils", "permissions", "rules"):
        (lib / sub).mkdir(parents=True)
    (routines / ".control").mkdir(parents=True)
    (lib / "utils" / "sig").mkdir()
    (lib / "utils" / "sig" / "main.py").write_text(util_src("sig"), encoding="utf-8")
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


@pytest.fixture
def world(tmp_path, monkeypatch):
    return build_world(tmp_path, monkeypatch)


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

    (lib / "utils" / "sig" / "main.py").write_text(util_src("sig", secrets="NEW_PIN"),
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
    (lib / "utils" / "sig" / "main.py").write_text(util_src("sig", secrets="NEW_PIN"),
                                                   encoding="utf-8")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "one")
    watch._check()
    (lib / "utils" / "sig" / "main.py").write_text(
        util_src("sig", secrets="NEW_PIN") + "# unrelated\n", encoding="utf-8")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "two")
    watch._check()
    assert len(pending.load_all(routines)) == 1


def _break_sig(lib) -> None:
    (lib / "utils" / "sig" / "main.py").write_text(util_src("sig", secrets="NEW_PIN"),
                                                   encoding="utf-8")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "sig: require a PIN")


def test_a_library_change_that_closes_the_gap_withdraws_its_record(world):
    """A record whose gap is closed asks for a decision nobody needs to make any more."""
    server, lib, routines = world
    watch = LibraryWatch(server)
    watch._check()
    _break_sig(lib)
    watch._check()
    assert len(pending.load_all(routines)) == 1
    (lib / "utils" / "sig" / "main.py").write_text(util_src("sig"), encoding="utf-8")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "sig: the PIN is optional again")
    watch._check()
    assert pending.load_all(routines) == []


def test_a_gap_closed_with_the_library_standing_still_is_withdrawn_at_the_next_start(
        world, monkeypatch):
    """The secret is added to the store: nothing in the library moved, so only the sweep a
    new process makes at its first check can see the record is moot."""
    server, lib, routines = world
    watch = LibraryWatch(server)
    watch._check()
    _break_sig(lib)
    watch._check()
    (routines / "archived").mkdir()                   # a record for a routine that is gone…
    pending.queue(server, kind="library-drift", routine="archived", run_id="",
                  fields={"entity": "archived:secret:X", "node": {}, "head": ""}, summary="s")
    assert len(pending.load_all(routines)) == 2
    monkeypatch.setattr("rsched.secrets.load_secrets", lambda *a, **k: {"NEW_PIN": "1234"})
    LibraryWatch(server)._check()                     # a new process, the same HEAD
    assert pending.load_all(routines) == []           # …goes with it


def test_a_gap_that_is_still_open_keeps_its_record_through_the_sweep(world):
    server, lib, routines = world
    watch = LibraryWatch(server)
    watch._check()
    _break_sig(lib)
    watch._check()
    LibraryWatch(server)._check()
    assert [r["fields"]["entity"] for r in pending.load_all(routines)] == ["holder:secret:NEW_PIN"]


def test_an_unchanged_head_does_no_work(world):
    server, _lib, routines = world
    watch = LibraryWatch(server)
    watch._check()
    watch._check()
    assert pending.load_all(routines) == []


def test_git_reads_go_through_the_one_invoker(world, monkeypatch):
    """F285: five per-module `_git` copies once drifted on timeout and check semantics, so
    every git call in the package is `libgit.git`. This watcher spelled its own
    `subprocess.run(["git", ...])` twice — the exact drift that rule exists to prevent."""
    server, _lib, _routines = world
    seen: list[tuple] = []
    real = libgit.git

    def spy(home, *args, **kw):
        seen.append(args)
        return real(home, *args, **kw)

    monkeypatch.setattr(libgit, "git", spy)
    LibraryWatch(server)._check()
    assert seen[0] == ("rev-parse", "HEAD")
    assert any(a[:2] == ("log", "-1") for a in seen)


def test_a_missing_git_repo_never_reaches_the_tick_guard(world, monkeypatch):
    """libgit.git does not catch a missing repo or a missing binary; this module must,
    because `tick`'s catch-all log.exceptions and both are ordinary states here."""
    server, _lib, _routines = world

    def boom(home, *args, **kw):
        raise OSError("git is not installed")

    monkeypatch.setattr(libgit, "git", boom)
    LibraryWatch(server)._check()          # no raise: an empty head is "nothing to compare"


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


async def test_head_is_read_on_its_own_interval_not_once_per_tick(world, monkeypatch):
    """A `rev-parse` is cheap but it is still a FORK, and the scheduler ticks every 5s — 17k
    subprocesses a day on the loop's shared executor to watch a value that moves a handful of
    times. The boot check still runs immediately; the next one waits out CHECK_EVERY_S."""
    import time

    from rsched.daemon import library_watch

    server, _lib, _routines = world
    reads: list[str] = []
    real_head = library_watch._head
    monkeypatch.setattr(library_watch, "_head",
                        lambda repo: reads.append(str(repo)) or real_head(repo))
    watch = LibraryWatch(server)
    await watch.tick()                       # boot: read
    await watch.tick()                       # immediately after: skipped
    assert len(reads) == 1
    watch._last_check = time.monotonic() - library_watch.CHECK_EVERY_S - 1
    await watch.tick()
    assert len(reads) == 2


def _health(routines: Path) -> list[dict]:
    path = routines / ".control" / "health-events.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _break_converse(lib: Path, routines: Path) -> None:
    """The real 2026-11-20 damage: the D241 migration rewrote the WORD `subtask` into prose
    inside the pattern's import list, so `converse.py` stopped parsing. Committed, because the
    watcher only looks when HEAD moves."""
    wdir = lib / "workflows"
    wdir.mkdir(exist_ok=True)
    (wdir / "converse.py").write_text(
        "from routine.actions import (read_file, sequential child run, finish)\n"
        'META = {"name": "Converse", "slug": "converse"}\n', encoding="utf-8")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "the migration rewrote prose into an import list")


def test_a_library_pattern_that_stopped_parsing_reaches_the_health_stream(world):
    """`converse.py` is what every conversation is materialized from, so a pattern that does
    not parse 500s conversation creation for everyone — and the boot seeder restores only a
    MISSING seed doc, never a corrupted one. For 30 hours on 2026-11-21 nothing anywhere said
    so: the lint knew, and nothing ran it."""
    server, lib, routines = world
    watch = LibraryWatch(server)
    watch._check()                                   # first boot: record HEAD
    _break_converse(lib, routines)
    watch._check()
    rows = [r for r in _health(routines) if r["event"] == "library_doc_unreadable"]
    assert len(rows) == 1, _health(routines)
    assert "converse.py" in rows[0]["detail"] and rows[0]["docs"] == 1
    assert rows[0]["routine"] == "(library)"         # the damage is nobody's and everybody's


def test_the_same_breakage_is_not_refiled_at_every_restart(world):
    """The cadence of a standing condition, latched ON DISK: the obligation watch's in-memory
    day latch re-filed 56 rows for 4 routines across 13 restarts on 2026-11-21. A fresh watcher
    over an unchanged, still-broken library files nothing new."""
    server, lib, routines = world
    LibraryWatch(server)._check()
    _break_converse(lib, routines)
    LibraryWatch(server)._check()
    for _ in range(3):                                # three restarts, same broken HEAD
        LibraryWatch(server)._check()
    rows = [r for r in _health(routines) if r["event"] == "library_doc_unreadable"]
    assert len(rows) == 1, [r["detail"] for r in rows]


def test_a_doc_with_only_style_problems_is_not_reported(world):
    """The linter reports far more than unreadability, and 13 live docs carry such problems
    today. Telling the fleet about those would make the signal worthless — only a doc nothing
    can READ is a fleet condition."""
    server, lib, routines = world
    LibraryWatch(server)._check()
    wdir = lib / "workflows"
    wdir.mkdir(exist_ok=True)
    # parses, but its slug does not match its filename and META is incomplete
    (wdir / "tidy.py").write_text('META = {"name": "T", "slug": "other"}\n', encoding="utf-8")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "a pattern with style problems only")
    LibraryWatch(server)._check()
    assert [r for r in _health(routines) if r["event"] == "library_doc_unreadable"] == []
