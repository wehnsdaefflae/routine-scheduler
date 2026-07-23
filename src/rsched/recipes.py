"""Recipe-version identity and rollback for a routine dir's git history.

A routine's RECIPE is main.md + stages/ + traits/ + tuning.yaml (grants.RECIPE_PREFIXES —
the same set the write gates protect). Its "recipe version" is the last git commit that
touched any of those files — NOT the dir's HEAD, which moves on every run because the
engine autocommits state/outputs at run end. The engine stamps this commit into each run's
status.json and workflow-usage record at run start, so run outcomes are attributable to
the recipe that produced them (the health view buckets by it).

The one wrinkle: the routine-improver edits a TARGET routine's recipe under its
fs_write_root, and nothing commits the target dir until its own next run ends — so at that
next run's start the new recipe would be on disk but uncommitted, and `git log` would name
the OLD version. `current_recipe_commit` therefore snapshots dirty recipe files into a
recipe-only commit first: every recipe version is a real, revertable commit, cleanly
separated from run-state noise.

Reverting restores the recipe files to their state just BEFORE a given recipe commit
(`<commit>^`) and commits ONLY those paths — routine.yaml and state/ are never touched
(config is the user's; state is the run's). The web layer calls this behind its
no-active-run guard.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .grants import RECIPE_PREFIXES
from .paths import file_lock, repo_lock_path

# git pathspecs for the recipe set — RECIPE_PREFIXES minus the dir-prefix slashes
RECIPE_PATHSPECS: tuple[str, ...] = tuple(p.rstrip("/") for p in RECIPE_PREFIXES)

from .libgit import IDENTITY_FLAGS as _GIT_IDENTITY  # noqa: E402 — one identity home


class RecipeError(Exception):
    """A revert request that cannot be honored (bad commit, no parent, no git)."""


def _git(routine_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(routine_dir), *args],
                          capture_output=True, text=True, timeout=30, check=False)


def _recipe_paths_dirty(routine_dir: Path) -> bool:
    r = _git(routine_dir, "status", "--porcelain", "--", *RECIPE_PATHSPECS)
    return r.returncode == 0 and bool(r.stdout.strip())


def _matchable_specs(routine_dir: Path) -> list[str]:
    """The recipe pathspecs git add/commit may name without a fatal unmatched-pathspec
    error: those present in the worktree or known to HEAD (status/log tolerate unmatched
    pathspecs, add/commit/checkout do not — a routine without traits/ or tuning.yaml is
    the normal case, not an error).
    """
    return [spec for spec in RECIPE_PATHSPECS
            if (routine_dir / spec).exists()
            or _git(routine_dir, "cat-file", "-e", f"HEAD:{spec}").returncode == 0]


def current_recipe_commit(routine_dir: Path) -> str | None:
    """The commit hash of the routine's current recipe version, or None (no git — a
    conversation, a wizard workspace — or no recipe-touching commit yet). Dirty recipe
    files are snapshotted into a recipe-only commit first (see the module docstring),
    so the returned commit always matches what is on disk. Best-effort: any git failure
    returns None rather than blocking a run start.
    """
    if not (routine_dir / ".git").is_dir():
        return None
    try:
        if _recipe_paths_dirty(routine_dir) and (specs := _matchable_specs(routine_dir)):
            # Under the per-repo lock: the improver may be committing this same target dir
            # via git-sync at this instant (this snapshot runs at the target's run start).
            with file_lock(repo_lock_path(routine_dir)):
                _git(routine_dir, "add", "-A", "--", *specs)
                _git(routine_dir, *_GIT_IDENTITY, "commit", "-qm", "recipe: pre-run snapshot",
                     "--", *specs)
        r = _git(routine_dir, "log", "-1", "--format=%H", "--", *RECIPE_PATHSPECS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    commit = r.stdout.strip()
    return commit if r.returncode == 0 and commit else None


def recipe_log(routine_dir: Path, limit: int = 50) -> list[dict]:
    """The routine's recipe-version series, newest first: every commit that touched a
    recipe file, as {commit (full), short, date (ISO committer date), subject}. Empty for
    a dir without git history.
    """
    if not (routine_dir / ".git").is_dir():
        return []
    try:
        r = _git(routine_dir, "log", f"-{limit}", "--format=%H%x09%h%x09%cI%x09%s",
                 "--", *RECIPE_PATHSPECS)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) == 4:
            out.append({"commit": parts[0], "short": parts[1],
                        "date": parts[2], "subject": parts[3]})
    return out


def revert_recipe(routine_dir: Path, commit: str) -> dict:
    """Roll the recipe files back to their state just BEFORE `commit` (i.e. to
    `<commit>^`) and commit ONLY those paths. Raises RecipeError when the request can't
    be honored: no git, unknown commit, a commit that touched no recipe file, or the
    routine's first commit (nothing before it). Only main.md / stages/ / traits/ /
    tuning.yaml are staged and committed — routine.yaml and state files are untouched.
    """
    if not (routine_dir / ".git").is_dir():
        raise RecipeError("this dir has no git history (conversations are unversioned)")
    ref = commit.strip()
    if not ref or any(c not in "0123456789abcdef" for c in ref.lower()):
        raise RecipeError(f"not a commit hash: {commit!r}")
    try:
        if _git(routine_dir, "cat-file", "-e", f"{ref}^{{commit}}").returncode != 0:
            raise RecipeError(f"unknown commit {ref!r}")
        touched = _git(routine_dir, "show", "--name-only", "--format=", ref,
                       "--", *RECIPE_PATHSPECS)
        if not touched.stdout.strip():
            raise RecipeError(f"commit {ref!r} touched no recipe file — nothing to revert")
        parent = _git(routine_dir, "rev-parse", "--short", f"{ref}^")
        if parent.returncode != 0:
            raise RecipeError(f"commit {ref!r} is the first commit — no version before it")
        # Restore the recipe set as of the parent: remove what exists now (so files ADDED
        # by the reverted change disappear), then check out the parent's copies. Per-path
        # checkout with check=False skips paths absent in the parent (e.g. no tuning.yaml
        # yet) — the staged removal keeps those deleted, which is exactly the parent state.
        # Under the per-repo lock (like autocommit / the pre-run snapshot / the git-sync util),
        # so this multi-step restore is not interleaved with another writer of this dir.
        with file_lock(repo_lock_path(routine_dir)):
            _git(routine_dir, "rm", "-rq", "--ignore-unmatch", "--", *RECIPE_PATHSPECS)
            for spec in RECIPE_PATHSPECS:
                _git(routine_dir, "checkout", f"{ref}^", "--", spec)
            # commit only pathspecs git can name post-restore (worktree or HEAD — HEAD still
            # holds a file the revert deletes, so its deletion is committed too)
            specs = _matchable_specs(routine_dir)
            msg = f"recipe: revert to pre-{parent.stdout.strip() or ref[:9]} (web)"
            committed = _git(routine_dir, *_GIT_IDENTITY, "commit", "-qm", msg, "--", *specs)
            if committed.returncode != 0:
                # nothing to commit — the working recipe already matches the pre-change state
                _git(routine_dir, "checkout", "HEAD", "--", *specs)
                raise RecipeError("the recipe already matches the state before that commit")
            new = _git(routine_dir, "log", "-1", "--format=%H", "--", *RECIPE_PATHSPECS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecipeError(f"git failed: {exc}") from exc
    return {"reverted": ref, "restored_from": f"{ref}^",
            "new_commit": new.stdout.strip() or None}
