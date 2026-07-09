# /// script
# dependencies = []
# ///
"""git-restore — discard uncommitted edits in a repo, restoring it to HEAD (routines have no shell).

usage: gu git-restore REPO_PATH [FILE ...] [--json]
calls: (none)
tags: git, dev, code

The revert a self-modifying routine runs when its edit fails the test gate: put the working tree
back exactly as HEAD has it, so a bad edit is never left behind or committed. With FILEs, only
those paths are touched — tracked ones are checked out from HEAD, and files the routine newly
created (untracked) are deleted (guarded to stay inside the repo). With no FILEs, all modified
TRACKED files are restored (untracked files are left alone, so runs/state are never nuked).
--selftest runs against a throwaway repo, offline."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

IDENTITY = ["-c", "user.name=routine-scheduler", "-c", "user.email=noreply@routine-scheduler.local"]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


def _within(repo: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(repo.resolve())
        return True
    except ValueError:
        return False


def run(repo_path: str, files: list[str] | None = None) -> dict:
    repo = Path(repo_path).expanduser()
    if not (repo / ".git").is_dir():
        raise ValueError(f"{repo} is not a git repository")
    restored: list[str] = []
    removed: list[str] = []
    if files:
        for f in files:
            tracked = _git(repo, "ls-files", "--error-unmatch", "--", f).returncode == 0
            if tracked:
                _git(repo, "checkout", "HEAD", "--", f)
                restored.append(f)
            else:
                p = repo / f
                if p.exists() and _within(repo, p):   # only ever delete inside the repo
                    p.unlink()
                    removed.append(f)
    else:
        _git(repo, "checkout", "--", ".")             # all modified tracked files → HEAD
        restored.append(".")
    return {"repo": str(repo), "restored": restored, "removed": removed}


def selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "r"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        (repo / "keep.py").write_text("original\n")
        _git(repo, "add", "-A")
        _git(repo, *IDENTITY, "commit", "-qm", "base")
        # modify a tracked file and create a new untracked one, then revert both by name
        (repo / "keep.py").write_text("BROKEN EDIT\n")
        (repo / "new_module.py").write_text("created by the routine\n")
        result = run(str(repo), files=["keep.py", "new_module.py"])
        assert (repo / "keep.py").read_text() == "original\n", "tracked file not restored"
        assert not (repo / "new_module.py").exists(), "untracked file not removed"
        assert result["restored"] == ["keep.py"] and result["removed"] == ["new_module.py"], result
        # a path-escape attempt is refused (stays inside the repo)
        outside = Path(tmp) / "outside.txt"
        outside.write_text("safe")
        run(str(repo), files=["../outside.txt"])
        assert outside.exists(), "git-restore escaped the repo"
    print("selftest: ok", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="gu git-restore", description="Discard uncommitted edits, restoring HEAD.")
    p.add_argument("repo_path", nargs="?", help="path to the git repo")
    p.add_argument("files", nargs="*", help="specific paths to restore (default: all modified tracked files)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        return selftest()
    if not args.repo_path:
        p.error("provide REPO_PATH")
    try:
        result = run(args.repo_path, files=args.files or None)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result) if args.json else
          f"restored={result['restored']} removed={result['removed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
