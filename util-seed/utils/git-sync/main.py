# /// script
# dependencies = []
# ///
"""git-sync — bidirectionally sync a git repo with its remote (routines have no shell).

usage: gu git-sync REPO_PATH [-m MESSAGE] [--no-push] [--no-pull] [--json]
calls: (none)
tags: git, dev, sync

Commits any local changes in REPO_PATH under a neutral identity, pulls remote updates
(--rebase, aborting cleanly on conflict), and pushes — keeping local and remote in sync in
one call. Meant for routines maintaining a repo other than their own working dir (library
sync, the meta routine committing the workflow library). --selftest runs against a
throwaway repo, offline (no remote → no pull/push)."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

IDENTITY = ["-c", "user.name=routine-scheduler", "-c", "user.email=noreply@routine-scheduler.local"]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


def run(repo_path: str, message: str = "", push: bool = True, pull: bool = True) -> dict:
    """Full bidirectional sync: commit local changes → pull --rebase from origin → push.
    Keeps a repo in sync with its remote in one call. Set pull/push False to do less."""
    repo = Path(repo_path).expanduser()
    if not (repo / ".git").is_dir():
        raise ValueError(f"{repo} is not a git repository")
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
    pull_error = ""
    if pull and has_remote:
        # rebase local work on top of remote; abort cleanly on conflict rather than leaving a mess
        r = _git(repo, *IDENTITY, "pull", "--rebase", "--quiet", "origin", branch)
        pulled = r.returncode == 0
        if not pulled:
            _git(repo, "rebase", "--abort")
            pull_error = (r.stderr or r.stdout).strip()[:200]
    pushed = False
    if push and has_remote:
        pushed = _git(repo, "push", "--quiet", "origin", branch).returncode == 0
    return {"repo": str(repo), "committed": committed, "had_changes": bool(status),
            "pulled": pulled, "pushed": pushed,
            **({"pull_error": pull_error} if pull_error else {})}


def selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "r"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        (repo / "f.txt").write_text("hello")
        result = run(str(repo), message="test commit", push=False, pull=False)
        assert result["committed"] and result["had_changes"], result
        log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                             capture_output=True, text=True)
        assert "test commit" in log.stdout, log.stdout
        # a second run with no changes commits nothing; no remote → no pull/push
        second = run(str(repo), push=False, pull=False)
        assert second["committed"] is False and second["pulled"] is False
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
          f"committed={result['committed']} pulled={result['pulled']} pushed={result['pushed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
