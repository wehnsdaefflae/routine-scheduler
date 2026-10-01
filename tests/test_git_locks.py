"""Git index locks against REAL repos (libgit + gitlock, docs/architecture.md "Git writes").

What these pin is the 2026-09-30 incident: the disk under /home stalled I/O, libgit's timeout
SIGKILLed the settings-patterns migration's `git commit` in two routine repos mid-refresh, and
the empty `index.lock` each left made every later write there fail — while `libgit.commit`
answered False exactly as it did for a clean tree. The migration recorded 0 failed.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from contextlib import nullcontext
from pathlib import Path

import pytest

from conftest import git_in
from rsched import gitlock, libgit


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git_in(path, "init", "-q", "-b", "main")
    (path / "a.txt").write_text("1\n", encoding="utf-8")
    git_in(path, "add", "-A")
    git_in(path, "commit", "-qm", "first")
    return path


def _lock(repo: Path, *, age_s: float = 0.0, content: bytes = b"") -> Path:
    lock = repo / ".git" / "index.lock"
    lock.write_bytes(content)
    if age_s:
        then = time.time() - age_s
        os.utime(lock, (then, then))
    return lock


def _hook(repo: Path, name: str, body: str) -> None:
    hook = repo / ".git" / "hooks" / name
    hook.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    hook.chmod(0o755)


def _events(routines_home: Path, name: str) -> list[dict]:
    path = routines_home / ".control" / "health-events.jsonl"
    if not path.exists():
        return []
    return [rec for rec in map(json.loads, path.read_text(encoding="utf-8").splitlines())
            if rec["event"] == name]


def _subject(repo: Path) -> str:
    return git_in(repo, "log", "-1", "--format=%s").stdout.strip()


def test_a_lock_in_the_way_makes_the_commit_say_so(tmp_path):
    """A lock left in the repo is a FAILED commit: the lock named and aged, the failure filed
    where the instance reads it — never the answer a clean tree also gets."""
    home = tmp_path / "routines"
    repo = _repo(home / "sprind")
    (repo / "a.txt").write_text("2\n", encoding="utf-8")
    lock = _lock(repo)                   # fresh: an unseen git may still hold it
    result = libgit.commit(repo, "settings pattern", routines_home=home)
    assert result.failed and result.step == "add"
    assert result.lock is not None and result.lock.path == lock
    assert "younger than 10 min" in result.kept
    assert str(lock) in result.describe()
    assert lock.exists() and _subject(repo) == "first"
    [event] = _events(home, "commit_failed")
    assert event["routine"] == "sprind" and event["step"] == "add"
    assert event["repo"] == str(repo) and event["lock_age_s"] < 60


def test_a_provably_stale_lock_is_removed_and_the_commit_lands(tmp_path):
    """Empty, two hours old, no git working in the repo, the commit lock ours: removed before
    the write, which then lands — and the removal is on record."""
    home = tmp_path / "routines"
    repo = _repo(home / "self-audit")
    (repo / "a.txt").write_text("2\n", encoding="utf-8")
    lock = _lock(repo, age_s=2 * 3600)
    result = libgit.commit(repo, "settings pattern", routines_home=home)
    assert result.status == "committed" and not lock.exists()
    assert _subject(repo) == "settings pattern"
    [event] = _events(home, "git_lock_cleared")
    assert event["routine"] == "self-audit" and event["lock_age_s"] >= 7000
    assert _events(home, "commit_failed") == []


def test_a_lock_with_content_is_left_for_a_person(tmp_path):
    """A written index in the lock is what an interactive `git commit -a` holds while its
    editor is open — however old, it stays."""
    repo = _repo(tmp_path / "r")
    (repo / "a.txt").write_text("2\n", encoding="utf-8")
    lock = _lock(repo, age_s=3600, content=b"DIRC")
    result = libgit.commit(repo, "x", routines_home=None)
    assert result.failed and "bytes of index" in result.kept and lock.exists()


def test_a_lock_is_kept_while_another_writer_holds_the_commit_lock(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "r")
    (repo / "a.txt").write_text("2\n", encoding="utf-8")
    lock = _lock(repo, age_s=3600)
    monkeypatch.setattr(libgit, "file_lock", lambda _path: nullcontext(False))
    result = libgit.commit(repo, "x", routines_home=None)
    assert result.failed and "commit lock" in result.kept and lock.exists()


@pytest.mark.parametrize("via", ["cwd", "--git-dir"])
def test_a_lock_a_live_git_works_under_is_kept_until_it_exits(tmp_path, via):
    """A git in the repo — by its working directory, or pointed at its git dir from outside
    (which, unlike `-C`, changes no working directory) — may be the holder, however old the
    lock is. Once it has exited, the next commit clears the lock."""
    repo = _repo(tmp_path / "r")
    (repo / "a.txt").write_text("2\n", encoding="utf-8")
    lock = _lock(repo, age_s=3600)
    cmd = ["git", "hash-object", "--stdin"]
    if via == "--git-dir":
        cmd[1:1] = [f"--git-dir={repo / '.git'}"]
    live = subprocess.Popen(cmd, cwd=repo if via == "cwd" else tmp_path,
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while (Path(f"/proc/{live.pid}/comm").read_text(encoding="utf-8").strip() != "git"
               and time.monotonic() < deadline):
            time.sleep(0.02)                 # until the child has exec'd git
        result = libgit.commit(repo, "x", routines_home=None)
    finally:
        live.communicate(b"")
    assert result.failed and f"pid {live.pid}" in result.kept and lock.exists()
    assert libgit.commit(repo, "x", routines_home=None).status == "committed"
    assert not lock.exists()


def test_a_git_that_runs_out_of_time_deletes_its_own_lock(tmp_path):
    """The cause. `git commit -a` holds the index lock while its pre-commit hook runs, so a
    hook that hangs keeps git inside the lock. The timeout used to SIGKILL git, which cannot
    clean up after that; it is terminated now. The hook goes with its process group."""
    repo = _repo(tmp_path / "r")
    _hook(repo, "pre-commit", "sleep 60")
    (repo / "a.txt").write_text("2\n", encoding="utf-8")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        libgit.git(repo, *libgit.IDENTITY_FLAGS, "commit", "-a", "-qm", "x", timeout=1)
    assert time.monotonic() - started < 20
    assert not (repo / ".git" / "index.lock").exists()


def test_what_ignores_the_terminate_is_killed_with_the_group(tmp_path):
    """A hook that ignores SIGTERM would hold git's output pipes open for its whole sleep;
    git itself still cleans up; the group's SIGKILL ends the rest."""
    repo = _repo(tmp_path / "r")
    _hook(repo, "pre-commit", "trap '' TERM\nsleep 60")
    (repo / "a.txt").write_text("2\n", encoding="utf-8")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        libgit.git(repo, *libgit.IDENTITY_FLAGS, "commit", "-a", "-qm", "x", timeout=1)
    assert time.monotonic() - started < 20
    assert not (repo / ".git" / "index.lock").exists()


def test_a_commit_whose_push_hook_outlives_the_timeout_still_landed(tmp_path, monkeypatch):
    """The post-commit hook runs after the commit exists: its hang is not a failed commit."""
    monkeypatch.setattr(libgit, "_TIMEOUT", 2)
    repo = _repo(tmp_path / "r")
    _hook(repo, "post-commit", "sleep 60")
    (repo / "a.txt").write_text("2\n", encoding="utf-8")
    result = libgit.commit(repo, "x", routines_home=None)
    assert result.status == "committed" and _subject(repo) == "x"


def test_a_read_never_writes_the_index(tmp_path):
    """`GIT_OPTIONAL_LOCKS=0`: after a file's stat data changes, `git status` refreshes the
    index and writes it back, taking the index lock to do it. Through libgit it does not."""
    repo = _repo(tmp_path / "r")
    index = repo / ".git" / "index"
    later = time.time() + 5
    os.utime(repo / "a.txt", (later, later))         # same content, new stat data
    before = index.stat().st_mtime_ns
    libgit.git(repo, "status", "--porcelain")
    assert index.stat().st_mtime_ns == before
    git_in(repo, "status", "--porcelain")            # the premise: plain git writes it
    assert index.stat().st_mtime_ns != before


def test_the_option_reader_follows_git_to_where_it_works():
    """`-C` composes, `--git-dir`/`--work-tree` count in either spelling, a `-c` value is
    skipped, and nothing after the subcommand is git's to read."""
    args = ["-c", "user.name=t", "-C", "sub", "-C", "deeper", "--git-dir=/g", "--work-tree",
            "/w", "--no-pager", "commit", "-C", "HEAD"]
    assert gitlock._option_places(Path("/cwd"), args) == [
        Path("/cwd/sub"), Path("/cwd/sub/deeper"), Path("/g"), Path("/w")]


def test_a_worktree_lock_is_found_where_its_git_file_points(tmp_path):
    repo = _repo(tmp_path / "main")
    git_in(repo, "worktree", "add", "-q", str(tmp_path / "wt"))
    root, gitdir = gitlock.locate(tmp_path / "wt")
    assert root == (tmp_path / "wt").resolve()
    assert gitdir == (repo / ".git" / "worktrees" / "wt").resolve()
    (gitdir / "index.lock").write_bytes(b"")
    found = gitlock.inspect(tmp_path / "wt")
    assert found is not None and found.path == gitdir / "index.lock"


@pytest.mark.parametrize(("seconds", "words"), [
    (45, "45 s"), (720, "12 min"), (7380, "2 h 3 min"), (3 * 86400, "3 d")])
def test_a_lock_age_reads_in_words(seconds, words):
    assert gitlock.age_words(seconds) == words
