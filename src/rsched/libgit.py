"""Serialized commits to the ONE shared library repo.

Every writer of the library repo — engine `write_util`/`remove_util` runs, web
playbook/workflow/util/doc edits, on-demand workflow generation — funnels through
`commit()`. Two things make concurrent writes safe (a run may `write_util` while another
run, or a Library-tab edit, commits the same repo):

- a per-repo file lock (`paths.repo_lock_path`), so two writers never collide on git's
  `index.lock`; and
- a SCOPED stage (`git add -A -- <paths>`), so one writer's `git add` can never sweep a
  sibling's not-yet-committed file into the wrong commit. Callers that changed known paths
  MUST pass them; the unscoped `git add -A` fallback stays only for whole-tree operations.

Best-effort, exactly like the per-module helpers it replaces: git/OS errors return False.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from .paths import file_lock, repo_lock_path, repo_root

_TIMEOUT = 30

# The neutral identity for every managed repo — the user's real name never authors a
# commit. Two shapes for the two idioms: persisted `git config` pairs (repo init) and
# per-invocation `-c` flags (commits in repos that may lack the persisted config).
GIT_USER = "routine-scheduler"
GIT_EMAIL = "noreply@routine-scheduler.local"
IDENTITY_PAIRS = (("user.name", GIT_USER), ("user.email", GIT_EMAIL))
IDENTITY_FLAGS = ("-c", f"user.name={GIT_USER}", "-c", f"user.email={GIT_EMAIL}")


def git(home: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    """The one git invoker every module uses (F285) — five per-module `_git` copies once
    drifted on timeout/check semantics; this is the only one.
    """
    return subprocess.run(["git", "-C", str(home), *args], capture_output=True,
                          text=True, timeout=_TIMEOUT, check=check)


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
            commit(home, first_commit)
    except (OSError, subprocess.TimeoutExpired):
        pass


def git_log(home: Path, rel_path: str | None = None, limit: int = 20) -> list[dict]:
    """Recent commits ({hash, date, subject}) for the repo (or one path) — the Library
    tab's history strip. Two byte-identical copies of this once lived in library_docs and
    workflows.library; this is the only one.
    """
    cmd = ["git", "-C", str(home), "log", f"-{limit}", "--format=%h%x09%ad%x09%s",
           "--date=short"]
    if rel_path:
        cmd += ["--", rel_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except OSError:
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


def commit(home: Path, message: str, *, paths: Sequence[str] | None = None) -> bool:
    """Stage (scoped to `paths` when given) and commit under the repo lock. Returns True on
    a successful commit, False on nothing-to-commit or any git/OS error.
    """
    home = Path(home)
    try:
        with file_lock(repo_lock_path(home)):
            if paths:
                git(home, "add", "-A", "--", *paths)
            else:
                git(home, "add", "-A")
            return git(home, *IDENTITY_FLAGS, "commit", "-qm", message).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
