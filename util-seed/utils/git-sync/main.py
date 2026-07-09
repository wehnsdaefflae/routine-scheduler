# /// script
# dependencies = []
# ///
"""git-sync — stage, commit, and push a git repo (routines have no shell; use this).

usage: gu git-sync REPO_PATH [-m MESSAGE] [--no-push] [--json]
calls: (none)

Commits all changes in REPO_PATH under a neutral identity and pushes to origin if one is
configured (best-effort). Meant for routines that maintain a repo other than their own
working dir (e.g. the meta routine committing the workflow library). --selftest runs
against a throwaway repo, offline (no push).
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

IDENTITY = ["-c", "user.name=routine-scheduler", "-c", "user.email=noreply@routine-scheduler.local"]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


def run(repo_path: str, message: str = "", push: bool = True) -> dict:
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
    pushed = False
    if push and _git(repo, "remote").stdout.strip():
        branch = _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip() or "main"
        pushed = _git(repo, "push", "--quiet", "origin", branch).returncode == 0
    return {"repo": str(repo), "committed": committed, "pushed": pushed, "had_changes": bool(status)}


def selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "r"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        (repo / "f.txt").write_text("hello")
        result = run(str(repo), message="test commit", push=False)
        assert result["committed"] and result["had_changes"], result
        log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                             capture_output=True, text=True)
        assert "test commit" in log.stdout, log.stdout
        # a second run with no changes commits nothing
        assert run(str(repo), push=False)["committed"] is False
    print("selftest: ok", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="gu git-sync", description="Commit + push a repo.")
    p.add_argument("repo_path", nargs="?", help="path to the git repo")
    p.add_argument("-m", "--message", default="", help="commit message (default 'sync')")
    p.add_argument("--no-push", action="store_true", help="commit only, do not push")
    p.add_argument("--json", action="store_true")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        return selftest()
    if not args.repo_path:
        p.error("provide REPO_PATH")
    try:
        result = run(args.repo_path, message=args.message, push=not args.no_push)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result) if args.json else
          f"committed={result['committed']} pushed={result['pushed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
