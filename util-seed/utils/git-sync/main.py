# /// script
# dependencies = []
# ///
"""git-sync — bidirectionally sync a git repo with its remote (routines have no shell).

usage: gu git-sync REPO_PATH [-m MESSAGE] [--no-push] [--no-pull] [--json]
calls: (none)
tags: git, dev, sync
net: outbound

Commits any local changes in REPO_PATH under a neutral identity, pulls remote updates
(--rebase, aborting cleanly on conflict), and pushes — keeping local and remote in sync in
one call. A FAILED push (or pull) to an existing remote is LOUD: the git error is captured
in the result (push_error/pull_error) and the util exits 1, so callers can never mistake a
local-only commit for a synced one. Meant for routines maintaining a repo other than their
own working dir (library sync, the meta routine committing the workflow library). The local
commit + rebase are held under the rsched per-repo lock (<repo>/.git/rsched-commit.lock) so
committing a routine dir that is MID-RUN (the improver git-syncing a target) takes turns with
that routine's own autocommit instead of colliding on git's index.lock.
--selftest runs against a throwaway repo, offline (no remote → no pull/push attempted)."""

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

IDENTITY = ["-c", "user.name=routine-scheduler", "-c", "user.email=noreply@routine-scheduler.local"]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


@contextmanager
def _repo_lock(repo: Path, timeout: float = 30.0):
    """The rsched per-repo commit lock (mirrors paths.repo_lock_path / paths.file_lock):
    an fcntl.flock on <repo>/.git/rsched-commit.lock, shared with the engine's autocommit and
    pre-run recipe snapshot. Best-effort — proceed after `timeout` so a hung holder can never
    deadlock a sync."""
    gitdir = repo / ".git"
    lock_path = gitdir / "rsched-commit.lock" if gitdir.is_dir() else repo / ".rsched-commit.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        yield
        return
    acquired = False
    try:
        end = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= end:
                    break
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def run(repo_path: str, message: str = "", push: bool = True, pull: bool = True) -> dict:
    """Full bidirectional sync: commit local changes → pull --rebase from origin → push.
    Keeps a repo in sync with its remote in one call. Set pull/push False to do less.
    The result carries attempted/error fields so failures are visible, not silent."""
    repo = Path(repo_path).expanduser()
    if not (repo / ".git").is_dir():
        raise ValueError(f"{repo} is not a git repository")
    # The index-touching steps (add, commit, rebase) run under the shared per-repo lock so a
    # routine autocommitting THIS dir at the same instant takes turns instead of colliding.
    with _repo_lock(repo):
        _git(repo, "add", "-A")
        status = _git(repo, "status", "--porcelain").stdout.strip()
        committed = False
        if status:
            msg = message or "sync"
            r = _git(repo, *IDENTITY, "commit", "-qm", msg)
            committed = r.returncode == 0
        has_remote = bool(_git(repo, "remote").stdout.strip())
        branch = _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip() or "main"
        pulled = False
        pull_attempted = False
        pull_error = ""
        if pull and has_remote:
            # rebase local work on remote; abort cleanly on conflict rather than leave a mess
            pull_attempted = True
            r = _git(repo, *IDENTITY, "pull", "--rebase", "--quiet", "origin", branch)
            pulled = r.returncode == 0
            if not pulled:
                _git(repo, "rebase", "--abort")
                pull_error = (r.stderr or r.stdout).strip()[:300]
    pushed = False
    push_attempted = False
    push_error = ""
    if push and has_remote:
        push_attempted = True
        r = _git(repo, "push", "origin", branch)
        pushed = r.returncode == 0
        if not pushed:
            push_error = (r.stderr or r.stdout).strip()[:300]
    result = {"repo": str(repo), "committed": committed, "had_changes": bool(status),
              "pulled": pulled, "pull_attempted": pull_attempted,
              "pushed": pushed, "push_attempted": push_attempted}
    if pull_error:
        result["pull_error"] = pull_error
    if push_error:
        result["push_error"] = push_error
    # ok = every attempted remote operation succeeded (a skipped one is not a failure)
    result["ok"] = (not pull_attempted or pulled) and (not push_attempted or pushed)
    return result


def selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "r"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        (repo / "f.txt").write_text("hello")
        result = run(str(repo), message="test commit", push=False, pull=False)
        assert result["committed"] and result["had_changes"], result
        assert (repo / ".git" / "rsched-commit.lock").exists(), "per-repo lock not taken"
        assert result["ok"] is True, result
        log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                             capture_output=True, text=True)
        assert "test commit" in log.stdout, log.stdout
        # a second run with no changes commits nothing; no remote → no pull/push attempted
        second = run(str(repo), push=False, pull=False)
        assert second["committed"] is False and second["pulled"] is False
        assert second["push_attempted"] is False and second["ok"] is True, second
        # a repo WITH a remote that cannot be reached must fail LOUDLY: ok=False + push_error
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                        str(Path(tmp) / "nonexistent-remote.git")], check=True)
        (repo / "g.txt").write_text("more")
        third = run(str(repo), message="second commit", push=True, pull=True)
        assert third["push_attempted"] is True and third["pushed"] is False, third
        assert third["ok"] is False and third.get("push_error"), third
    print("selftest: ok", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="gu git-sync", description="Commit + pull + push a repo.")
    p.add_argument("repo_path", nargs="?", help="path to the git repo")
    p.add_argument("-m", "--message", default="", help="commit message (default 'sync')")
    p.add_argument("--no-push", action="store_true", help="do not push")
    p.add_argument("--no-pull", action="store_true", help="do not pull remote updates")
    p.add_argument("--json", action="store_true")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        return selftest()
    if not args.repo_path:
        p.error("provide REPO_PATH")
    try:
        result = run(args.repo_path, message=args.message,
                     push=not args.no_push, pull=not args.no_pull)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result) if args.json else
          f"committed={result['committed']} pulled={result['pulled']} pushed={result['pushed']} ok={result['ok']}")
    if not result["ok"]:
        for key in ("pull_error", "push_error"):
            if result.get(key):
                print(f"{key}: {result[key]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
