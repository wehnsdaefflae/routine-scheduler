"""Git for every repo the instance versions: the ONE shared library repo and each routine's own.

Every writer funnels through `commit()`, or `writing()` for a multi-step write: engine
`write_util`/`remove_util` runs, the run-end autocommit, the recipe snapshot, web edits of the
library and of routines, on-demand workflow generation, the boot seed syncs and migrations. Two
things make concurrent writes safe (a run may `write_util` while another run, or a Library-tab
edit, commits the same repo):

- a per-repo file lock (`paths.repo_lock_path`), so two writers never collide on git's
  `index.lock`; and
- a SCOPED stage (`git add -A -- <paths>`), so one writer's `git add` can never sweep a
  sibling's not-yet-committed file into the wrong commit. Callers that changed known paths
  MUST pass them; the unscoped `git add -A` fallback stays only for whole-tree operations.

A commit that does not land is never silent. `commit()` returns a `Commit` — committed, clean,
unversioned, or failed with the step, git's own words and the index lock in the way — and files
a failure as a `commit_failed` health event. It used to answer False for "nothing to commit" and
for every error alike. No caller read it — on 2026-09-30 the settings-patterns migration
recorded 0 failed while two routines' edits sat staged under a lock a killed git had left.

Two causes of that lock are closed here and a third source is recovered from
(docs/architecture.md, "Git writes"):

- A git call that runs out of time is TERMINATED, never killed outright: SIGTERM to its process
  group first (git answers it by deleting the lockfiles it holds) and SIGKILL only for what is
  left `procgroup.TERM_GRACE_S` later. `subprocess.run` sent SIGKILL at the timeout, which git
  cannot clean up after — and the disk under `/home` stalls single I/O commands for up to thirty
  seconds. `procgroup.terminate` is the one way to do it: `utils_run.run_jailed` ends a
  timed-out util, script or `shell` command through it too, since git runs inside those.
- Every call runs with `GIT_OPTIONAL_LOCKS=0`, so a READ (`status`) never takes the index lock
  to write a refreshed index back: a reader can neither leave one behind nor make a concurrent
  writer fail on a live one.
- A lock left by anything else — an OOM kill, a container stop, a person's git — is removed
  before the write when `gitlock` can prove it stale; the removal is filed as `git_lock_cleared`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from . import gitlock, procgroup
from .health_events import log_health_event
from .paths import file_lock, repo_lock_path, repo_root

log = logging.getLogger("rsched.libgit")

_TIMEOUT = 30
#: Added to every call's environment: reads never take the index lock (module docstring).
_ENV = {"GIT_OPTIONAL_LOCKS": "0"}

# The neutral identity for every managed repo — the user's real name never authors a
# commit. Two shapes for the two idioms: persisted `git config` pairs (repo init) and
# per-invocation `-c` flags (commits in repos that may lack the persisted config).
GIT_USER = "routine-scheduler"
GIT_EMAIL = "noreply@routine-scheduler.local"
IDENTITY_PAIRS = (("user.name", GIT_USER), ("user.email", GIT_EMAIL))
IDENTITY_FLAGS = ("-c", f"user.name={GIT_USER}", "-c", f"user.email={GIT_EMAIL}")


def git(home: Path, *args: str, check: bool = False,
        timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    """The one git invoker every module uses (F285) — five per-module `_git` copies once
    drifted on timeout/check semantics; this is the only one.

    Two callers spell `subprocess.run(["git", ...])` themselves, each for a reason this
    signature has no room for: `ls-remote` (web/settings/source) tests a URL outside any repo
    under its own credential env; the run gate's jail-side kit (`gatekit/`) cannot import
    the package at all. Every other git call in the package is this function — a network call
    passes its own `timeout`.

    Git runs in its own process group with no stdin and `GIT_OPTIONAL_LOCKS=0`; a call that
    outlives `timeout` (default `_TIMEOUT`) is ended the way git can clean up after — its group,
    a hook and the hook's children included, through `procgroup.terminate` — and raises
    `subprocess.TimeoutExpired`, exactly as `subprocess.run` did.
    """
    cmd = ["git", "-C", str(home), *args]
    limit = _TIMEOUT if timeout is None else timeout
    with subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, env={**os.environ, **_ENV},
                          start_new_session=True) as proc:
        try:
            out, err = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            procgroup.terminate(proc)
            out, err = proc.communicate()
            raise subprocess.TimeoutExpired(cmd, limit, output=out, stderr=err) from None
        except BaseException:
            procgroup.terminate(proc)
            raise
    done = subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    if check:
        done.check_returncode()
    return done


@dataclass(frozen=True)
class Commit:
    """What one commit did. `status` is the whole answer for most callers; a failure also
    names the `step` that failed (`add` or `commit`), git's own words (`error`), and the index
    lock in the way when there was one, with why it was not removed (`kept`).
    """

    status: Literal["committed", "clean", "unversioned", "failed"]
    step: str = ""
    error: str = ""
    lock: gitlock.IndexLock | None = None
    kept: str = ""

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    def describe(self) -> str:
        """The failure in one line — what the log, the health event and a migration record
        carry. Empty unless it failed.
        """
        if not self.failed:
            return ""
        text = f"the commit did not land: git {self.step} failed"
        if self.lock is not None:
            text += f" on the index lock {self.lock.describe()}"
            text += f", kept because {self.kept}" if self.kept else ""
        return f"{text} ({self.error})" if self.error else text


@dataclass
class Writer:
    """A repo held for writing (`writing()`). `held` is whether its commit lock is ours;
    `blocking` is an index lock still in the way after the stale-lock check, with `kept`
    saying why it stayed — a multi-step writer refuses on it before touching the index.
    """

    home: Path
    held: bool
    routines_home: Path | None
    run_id: str
    blocking: gitlock.IndexLock | None = None
    kept: str = ""

    def commit(self, message: str, *, paths: Sequence[str] | None = None,
               exclude: Sequence[str] = (), only: bool = False, stage: bool = True) -> Commit:
        """`commit()` under this hold. `stage=False` commits what the caller already put in
        the index (a revert's `rm` + `checkout`), so an untracked file in scope is neither
        added nor counted as left behind.
        """
        result = _stage_and_commit(self.home, message, paths, exclude, only=only, stage=stage)
        if result.failed:
            lock = gitlock.inspect(self.home)
            if lock is not None:
                result = replace(result, lock=lock, kept=lock.kept_because(held=self.held))
            log.warning("commit in %s did not land: %s", self.home, result.describe())
            if self.routines_home is not None:
                log_health_event(self.routines_home, "commit_failed",
                                 routine=_subject(self.home), run_id=self.run_id,
                                 detail=result.describe(), repo=str(self.home),
                                 step=result.step,
                                 lock_age_s=round(lock.age_s) if lock is not None else None)
        return result


@contextlib.contextmanager
def writing(home: Path, *, routines_home: Path | None, run_id: str = "") -> Iterator[Writer]:
    """Hold `home`'s repo for a write: its commit lock and a clear way through git's index
    lock — a provably stale one (`gitlock`) is removed first and filed as `git_lock_cleared`;
    one that must stay is left on the writer as `blocking`. `commit()` is this plus one
    `Writer.commit`. `routines_home` is the instance whose health stream hears about it —
    None only where no instance exists yet (install-time seeding, a repo created this instant).
    """
    home = Path(home)
    with file_lock(repo_lock_path(home)) as held:
        writer = Writer(home, held, routines_home, run_id)
        lock = gitlock.inspect(home)
        if lock is not None:
            kept = lock.kept_because(held=held)
            if not kept and gitlock.remove(lock):
                _cleared(writer, lock)
            else:
                writer.blocking, writer.kept = lock, kept or "it changed while being removed"
        yield writer


def _cleared(writer: Writer, lock: gitlock.IndexLock) -> None:
    detail = (f"removed the stale index lock {lock.describe()}: the commit lock was ours, no git "
              "process worked in the repo and the lock held no index, so a git killed mid-write "
              "left it")
    log.warning("%s", detail)
    if writer.routines_home is not None:
        log_health_event(writer.routines_home, "git_lock_cleared",
                         routine=_subject(writer.home), run_id=writer.run_id, detail=detail,
                         repo=str(writer.home), lock_age_s=round(lock.age_s))


def _subject(home: Path) -> str:
    """The name a health event files a repo under: its root directory's — a routine's slug."""
    found = gitlock.locate(home)
    return (found[0] if found else home).name


def commit(home: Path, message: str, *, routines_home: Path | None,
           paths: Sequence[str] | None = None, exclude: Sequence[str] = (),
           only: bool = False, run_id: str = "") -> Commit:
    """Stage (scoped to `paths` when given) and commit under the repo lock, then say what
    happened (`Commit`); a failure is logged and filed in `routines_home`'s health stream.

    `exclude` names repo-relative paths the stage must LEAVE ALONE (git's `:(exclude)`
    pathspec magic): an untracked one stays untracked, a tracked one keeps its committed
    version. The engine autocommit uses it for files over its size ceiling. `only` commits
    exactly `paths` (`git commit -- <paths>`), leaving anything else already staged out of
    this commit — the recipe snapshot's recipe-only promise; each path must then be known to
    the worktree, the index or HEAD. `run_id` names the run a failure belongs to.
    """
    home = Path(home)
    if gitlock.locate(home) is None:
        return Commit("unversioned")
    with writing(home, routines_home=routines_home, run_id=run_id) as writer:
        return writer.commit(message, paths=paths, exclude=exclude, only=only)


def _stage_and_commit(home: Path, message: str, paths: Sequence[str] | None,
                      exclude: Sequence[str], *, only: bool, stage: bool) -> Commit:
    scope = [*(paths or ["."]), *(f":(exclude){p}" for p in exclude)]
    step, error, timed_out = "add", "", False
    try:
        added = git(home, "add", "-A", "--", *scope) if stage else None
        if added is not None and added.returncode != 0:
            error = added.stderr or added.stdout
        else:
            step = "commit"
            done = git(home, *IDENTITY_FLAGS, "commit", "-qm", message,
                       *(("--", *(paths or ())) if only else ()))
            if done.returncode == 0:
                return Commit("committed")
            error = done.stderr or done.stdout
    except subprocess.TimeoutExpired:
        error, timed_out = (f"timed out after {_TIMEOUT} s; git was terminated, which deletes "
                            "its own lock"), True
    except OSError as exc:
        error = str(exc)
    # git did not say it committed. What in scope still differs from HEAD says what happened:
    # nothing means nothing was left to commit — or, when `commit` itself ran out of time, the
    # commit landed and its post-commit push outlived the timeout. Anything means it did not.
    try:
        left = git(home, "status", "--porcelain",
                   *(() if stage else ("--untracked-files=no",)), "--", *scope)
    except (OSError, subprocess.TimeoutExpired):
        left = None
    if left is not None and left.returncode == 0 and not left.stdout.strip():
        return Commit("committed" if timed_out and step == "commit" else "clean")
    return Commit("failed", step, _first_lines(error) or "git reported no reason")


def _first_lines(text: str) -> str:
    """Git's reason in a line: its first two non-empty lines (a lock error's long advice
    follows the one that names the lock), capped for a health event's detail.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return " / ".join(lines[:2])[:300]


def install_push_hook(home: Path, *, overwrite: bool = False) -> None:
    """Install the auto-push-on-commit hook from `deploy/post-commit` — the ONE hook
    source (deploy/install.sh installs the same file). Never overwrites an existing hook
    unless asked (a library may carry its own richer one). Best-effort: no repo or no
    source file is a silent no-op.
    """
    src = repo_root() / "deploy" / "post-commit"
    hook = home / ".git" / "hooks" / "post-commit"
    if not src.exists() or not (home / ".git").is_dir():
        return
    if hook.exists() and not overwrite:
        return
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    hook.chmod(0o755)  # git hooks must be executable


def init_repo(home: Path, *, remote: str = "", first_commit: str = "",
              push_hook: bool = True) -> None:
    """Initialize a managed repo the ONE way (F285): `init -b main`, the neutral identity,
    an optional origin remote, the shared push hook, an optional first commit. Best-effort
    like every helper here — a dir without git still works, callers proceed regardless.

    The first commit files no health event: a repo created this instant holds no stale lock,
    its failure is logged, and the next commit into it — the run-end autocommit, the next
    library write — takes whatever it missed.
    """
    try:
        git(home, "init", "-q", "-b", "main")
        for key, val in IDENTITY_PAIRS:
            git(home, "config", key, val)
        if remote:
            git(home, "remote", "add", "origin", remote)
        if push_hook:
            install_push_hook(home)
        if first_commit:
            commit(home, first_commit, routines_home=None)
    except (OSError, subprocess.TimeoutExpired):
        pass


def git_log(home: Path, rel_path: str | None = None, limit: int = 20) -> list[dict]:
    """Recent commits ({hash, date, subject}) for the repo (or one path) — the Library
    tab's history strip. Two byte-identical copies of this once lived in library_docs and
    workflows.library; this is the only one.
    """
    args = ["log", f"-{limit}", "--format=%h%x09%ad%x09%s", "--date=short"]
    if rel_path:
        args += ["--", rel_path]
    try:
        r = git(home, *args, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            out.append({"hash": parts[0], "date": parts[1], "subject": parts[2]})
    return out


def path_was_deleted(home: Path, rel_path: str) -> bool:
    """Was `rel_path` ever DELETED from this repo's history? The never-resurrect rule keys off
    this: the boot seed-syncs re-install anything the seed carries and the live library lacks,
    which would otherwise undo an operator's deliberate deletion at the next restart — and then
    push it, since every library commit is pushed.

    Any prior deletion counts as intent. The web UI is the only deliberate delete path, and
    treating a historical deletion as intent is the safe reading in both directions: the cost of
    a false positive is one doc the operator re-adds by hand, the cost of a false negative is a
    deletion that silently comes back forever.

    Fails open to False (no repo, git error = nothing to guard).
    """
    try:
        r = git(home, "log", "--diff-filter=D", "--format=%h", "--", rel_path)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())
