"""Recipe-version identity and rollback for a routine dir's git history.

A routine's RECIPE is main.md + stages/ + tuning.yaml (grants.RECIPE_PREFIXES —
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

from . import libgit
from .grants import RECIPE_PREFIXES

# git pathspecs for the recipe set — RECIPE_PREFIXES minus the dir-prefix slashes
RECIPE_PATHSPECS: tuple[str, ...] = tuple(p.rstrip("/") for p in RECIPE_PREFIXES)


class RecipeError(Exception):
    """A revert request that cannot be honored (bad commit, no parent, no git)."""


def _recipe_paths_dirty(routine_dir: Path) -> bool:
    r = libgit.git(routine_dir, "status", "--porcelain", "--", *RECIPE_PATHSPECS)
    return r.returncode == 0 and bool(r.stdout.strip())


def _matchable_specs(routine_dir: Path) -> list[str]:
    """The recipe pathspecs git add/commit may name without a fatal unmatched-pathspec
    error: those present in the worktree or known to HEAD (status/log tolerate unmatched
    pathspecs, add/commit/checkout do not — a routine without stages/ or tuning.yaml is
    the normal case, not an error).
    """
    return [spec for spec in RECIPE_PATHSPECS
            if (routine_dir / spec).exists()
            or libgit.git(routine_dir, "cat-file", "-e", f"HEAD:{spec}").returncode == 0]


def current_recipe_commit(routine_dir: Path, *, routines_home: Path | None) -> str | None:
    """The commit hash of the routine's current recipe version, or None (no git — a
    conversation, a clarify workspace — or no recipe-touching commit yet). Dirty recipe
    files are snapshotted into a recipe-only commit first (see the module docstring),
    so the returned commit always matches what is on disk — and a snapshot that did not
    land answers None, never the older version it would misattribute the run to (the
    failure itself is filed in `routines_home`'s health stream). Best-effort: any git
    failure returns None rather than blocking a run start.
    """
    if not (routine_dir / ".git").is_dir():
        return None
    try:
        specs = _matchable_specs(routine_dir) if _recipe_paths_dirty(routine_dir) else []
        # Under the per-repo lock (libgit.commit): the improver may be committing this same
        # target dir via `git sync` at this instant (this snapshot runs at the target's run
        # start). `only` keeps the snapshot recipe-only whatever else is staged.
        if specs and libgit.commit(routine_dir, "recipe: pre-run snapshot",
                                   routines_home=routines_home, paths=specs, only=True).failed:
            return None
        r = libgit.git(routine_dir, "log", "-1", "--format=%H", "--", *RECIPE_PATHSPECS)
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
        r = libgit.git(routine_dir, "log", f"-{limit}", "--format=%H%x09%h%x09%cI%x09%s",
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


def revert_recipe(routine_dir: Path, commit: str, *, routines_home: Path | None) -> dict:
    """Roll the recipe files back to their state just BEFORE `commit` (i.e. to
    `<commit>^`) and commit ONLY those paths. Raises RecipeError when the request can't
    be honored: no git, unknown commit, a commit that touched no recipe file, the
    routine's first commit (nothing before it), or a repo git cannot write — an index lock
    in the way, or a commit that did not land (filed in `routines_home`'s health stream).
    Only main.md / stages/ / tuning.yaml are staged and committed — routine.yaml and state
    files are untouched.
    """
    if not (routine_dir / ".git").is_dir():
        raise RecipeError("this dir has no git history (conversations are unversioned)")
    ref = commit.strip()
    if not ref or any(c not in "0123456789abcdef" for c in ref.lower()):
        raise RecipeError(f"not a commit hash: {commit!r}")
    try:
        if libgit.git(routine_dir, "cat-file", "-e", f"{ref}^{{commit}}").returncode != 0:
            raise RecipeError(f"unknown commit {ref!r}")
        touched = libgit.git(routine_dir, "show", "--name-only", "--format=", ref,
                             "--", *RECIPE_PATHSPECS)
        if not touched.stdout.strip():
            raise RecipeError(f"commit {ref!r} touched no recipe file — nothing to revert")
        parent = libgit.git(routine_dir, "rev-parse", "--short", f"{ref}^")
        if parent.returncode != 0:
            raise RecipeError(f"commit {ref!r} is the first commit — no version before it")
        # Restore the recipe set as of the parent: remove what exists now (so files ADDED
        # by the reverted change disappear), then check out the parent's copies. Per-path
        # checkout with check=False skips paths absent in the parent (e.g. no tuning.yaml
        # yet) — the staged removal keeps those deleted, which is exactly the parent state.
        # Under the per-repo lock (like autocommit / the pre-run snapshot / `git sync`),
        # so this multi-step restore is not interleaved with another writer of this dir. An
        # index lock that must stay refuses the revert BEFORE any step touches the index:
        # every step would fail — the empty commit at the end then read as "already matches".
        with libgit.writing(routine_dir, routines_home=routines_home) as writer:
            if writer.blocking is not None:
                raise RecipeError(f"git cannot write this routine's repo: the index lock "
                                  f"{writer.blocking.describe()} is in the way ({writer.kept})")
            libgit.git(routine_dir, "rm", "-rq", "--ignore-unmatch", "--", *RECIPE_PATHSPECS)
            for spec in RECIPE_PATHSPECS:
                libgit.git(routine_dir, "checkout", f"{ref}^", "--", spec)
            # commit only pathspecs git can name post-restore (worktree or HEAD — HEAD still
            # holds a file the revert deletes, so its deletion is committed too)
            specs = _matchable_specs(routine_dir)
            msg = f"recipe: revert to pre-{parent.stdout.strip() or ref[:9]} (web)"
            landed = writer.commit(msg, paths=specs, only=True, stage=False)
            if landed.status != "committed":
                # nothing to commit — the working recipe already matches the pre-change state —
                # or a commit that did not land: either way the recipe goes back to HEAD
                libgit.git(routine_dir, "checkout", "HEAD", "--", *specs)
                raise RecipeError(
                    f"the revert did not land: {landed.describe()}" if landed.failed
                    else "the recipe already matches the state before that commit")
            new = libgit.git(routine_dir, "log", "-1", "--format=%H", "--", *RECIPE_PATHSPECS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecipeError(f"git failed: {exc}") from exc
    return {"reverted": ref, "restored_from": f"{ref}^",
            "new_commit": new.stdout.strip() or None}
