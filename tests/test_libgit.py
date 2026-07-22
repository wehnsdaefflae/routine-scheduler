"""Concurrency safety for the shared library repo: the scoped-add anti-sweep, the per-repo
lock path, the advisory file lock, and mode-preserving atomic writes.
"""

from __future__ import annotations

import subprocess

from rsched import libgit, utils_lib
from rsched.paths import atomic_write, file_lock, repo_lock_path


def _git(home, *args: str) -> str:
    return subprocess.run(["git", "-C", str(home), *args],
                          capture_output=True, text=True, check=True).stdout


def _head_files(home) -> list[str]:
    return _git(home, "show", "--name-only", "--format=", "HEAD").split()


def test_commit_scoped_add_does_not_sweep_sibling(tmp_path):
    """The core race fix: writer B committing its own path must NOT sweep writer A's
    just-written-but-uncommitted file into B's commit."""
    utils_lib.ensure_library(tmp_path)
    (tmp_path / "utils" / "adder").mkdir(parents=True)
    (tmp_path / "utils" / "adder" / "main.py").write_text("# a\n", encoding="utf-8")
    (tmp_path / "playbooks" / "brief").mkdir(parents=True)
    (tmp_path / "playbooks" / "brief" / "MAIN.md").write_text("# b\n", encoding="utf-8")

    assert libgit.commit(tmp_path, "add playbook", paths=["playbooks/brief"]) is True
    assert _head_files(tmp_path) == ["playbooks/brief/MAIN.md"]        # A's util NOT swept in
    assert "utils/adder/main.py" in _git(tmp_path, "status", "--porcelain", "-uall")

    assert libgit.commit(tmp_path, "add util", paths=["utils/adder"]) is True
    assert _head_files(tmp_path) == ["utils/adder/main.py"]


def test_commit_scoped_add_stages_a_deletion(tmp_path):
    """`-A -- <path>` inside the scope stages removals too (remove_util's case)."""
    utils_lib.ensure_library(tmp_path)
    d = tmp_path / "utils" / "gone"
    d.mkdir(parents=True)
    (d / "main.py").write_text("# x\n", encoding="utf-8")
    libgit.commit(tmp_path, "add", paths=["utils/gone"])
    (d / "main.py").unlink()
    d.rmdir()
    assert libgit.commit(tmp_path, "remove", paths=["utils/gone"]) is True
    assert _head_files(tmp_path) == ["utils/gone/main.py"]             # the deletion


def test_commit_unscoped_stages_everything(tmp_path):
    utils_lib.ensure_library(tmp_path)
    (tmp_path / "one.txt").write_text("1\n", encoding="utf-8")
    (tmp_path / "two.txt").write_text("2\n", encoding="utf-8")
    assert libgit.commit(tmp_path, "both") is True
    assert set(_head_files(tmp_path)) == {"one.txt", "two.txt"}


def test_commit_nothing_to_commit_returns_false(tmp_path):
    utils_lib.ensure_library(tmp_path)
    assert libgit.commit(tmp_path, "empty", paths=["utils/nope"]) is False


def test_repo_lock_path_targets_the_git_dir(tmp_path):
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "utils" / "x"
    sub.mkdir(parents=True)
    want = tmp_path.resolve() / ".git" / "rsched-commit.lock"
    assert repo_lock_path(sub) == want            # any subdir agrees on one lock
    assert repo_lock_path(tmp_path) == want


def test_file_lock_is_exclusive_then_reacquirable(tmp_path):
    lock = tmp_path / "x.lock"
    with file_lock(lock) as first:
        assert first is True
        with file_lock(lock, timeout=0.1) as second:
            assert second is False                # held → caller proceeds best-effort
    with file_lock(lock, timeout=0.1) as third:   # released on exit
        assert third is True


def _init_repo(path) -> None:
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *args], check=True)


def test_autocommit_takes_the_repo_lock(tmp_path):
    """The routine-dir autocommit runs under the same per-repo lock the git-sync util takes,
    so a target's autocommit and the improver's git-sync of that target take turns."""
    from rsched.engine.autocommit import autocommit

    _init_repo(tmp_path)
    (tmp_path / "state.txt").write_text("x\n", encoding="utf-8")
    autocommit(tmp_path, "run state")
    assert (tmp_path / ".git" / "rsched-commit.lock").exists()
    assert "run state" in _git(tmp_path, "log", "-1", "--format=%s")


def test_recipe_snapshot_takes_the_repo_lock(tmp_path):
    from rsched.recipes import current_recipe_commit

    _init_repo(tmp_path)
    (tmp_path / "main.md").write_text("# recipe\n", encoding="utf-8")   # a dirty recipe file
    assert current_recipe_commit(tmp_path)                             # snapshotted into a commit
    assert (tmp_path / ".git" / "rsched-commit.lock").exists()


def test_atomic_write_mode(tmp_path):
    p = tmp_path / "f"
    atomic_write(p, "x\n", mode=0o750)
    assert p.stat().st_mode & 0o777 == 0o750
    atomic_write(p, "y\n", mode=p.stat().st_mode & 0o7777)   # overwrite preserves bits
    assert p.stat().st_mode & 0o777 == 0o750
    atomic_write(tmp_path / "h", "z\n")                       # no mode → mkstemp 0600
    assert (tmp_path / "h").stat().st_mode & 0o777 == 0o600
