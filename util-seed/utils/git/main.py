# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""git — clone, log, restore, sync and inspect git repositories (verb-dispatched; routines have no shell).

usage: gu git <clone|log|restore|sync|inspect> …
  git clone URL TARGET_DIR [--depth N] [--json]
  git log <repo> [--since <ref>] [--max <n>] [--path <pathspec>] [--json]
           (aliases: --repo <repo>, --since-commit <ref>, --limit <n>, --file <pathspec>)
  git restore REPO_PATH [FILE ...] [--json]
  git sync REPO_PATH [-m MESSAGE] [--no-push] [--no-pull] [--on-conflict abort|hold]
           [--continue] [--abort-rebase] [--json]
  git inspect REPO [--path PATH] [--json]
           read-only HEAD + short status + full `diff HEAD` + untracked list
  git selftest   (same as: git --selftest)
tags: git, dev, history, code, sync
calls: (none)
net: outbound
fs: roots
secrets: (none)

Mechanical merge of the five former utils — git-clone, git-log, git-restore, git-sync,
git-inspect —
into one verb dispatcher. Each subcommand's logic is preserved VERBATIM below under a
namespaced section; module-level helpers were renamed ONLY where two sources collided
(`run` -> `_log_run`/`_restore_run`/`_sync_run`, etc.). The leading verb is stripped
before each subcommand's parser, so every subcommand's flags behave byte-identically
to the original util. `git selftest` / `git --selftest` runs all four originals'
selftests in sequence (offline; git works against local temp repos); each subcommand
still accepts its own `--selftest` too.
"""

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

USAGE = """usage: gu git <clone|log|restore|sync|inspect> …
  git clone URL TARGET_DIR [--depth N] [--json]
  git log <repo> [--since <ref>] [--max <n>] [--path <pathspec>] [--json]
           (aliases: --repo <repo>, --since-commit <ref>, --limit <n>, --file <pathspec>)
  git restore REPO_PATH [FILE ...] [--json]
  git sync REPO_PATH [-m MESSAGE] [--no-push] [--no-pull] [--on-conflict abort|hold]
           [--continue] [--abort-rebase] [--json]
  git inspect REPO [--path PATH] [--json]
  git selftest   (same as: git --selftest)"""


# ============================================================ clone (ex git-clone) ===

def run_clone(url: str, target: str, depth: int = 0) -> dict:
    target_path = Path(target).expanduser()
    if target_path.exists() and any(target_path.iterdir()):
        raise ValueError(f"target directory exists and is not empty: {target_path}")
    target_path.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--quiet"]
    if depth:
        cmd += ["--depth", str(depth)]
    cmd += [url, str(target_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:500])
    head = subprocess.run(
        ["git", "-C", str(target_path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, timeout=60,
    )
    files = subprocess.run(
        ["git", "-C", str(target_path), "ls-files"],
        capture_output=True, text=True, timeout=60,
    )
    return {
        "url": url,
        "target": str(target_path),
        "branch": head.stdout.strip() or "HEAD",
        "files": len(files.stdout.splitlines()),
        "ok": True,
    }


def _clone_selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        bare = Path(tmp) / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        seed = Path(tmp) / "seed"
        seed.mkdir()
        subprocess.run(["git", "-C", str(seed), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
        (seed / "hello.txt").write_text("hi")
        subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
        subprocess.run(["git", "-C", str(seed), "commit", "-qm", "init"], check=True)
        subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(bare)], check=True)
        subprocess.run(["git", "-C", str(seed), "push", "-q", "-u", "origin", "main"], check=True)
        dest = Path(tmp) / "clone"
        result = run_clone(f"file://{bare}", str(dest))
        assert result["ok"] and result["files"] == 1, result
        assert (dest / "hello.txt").read_text() == "hi"
        try:
            run_clone(f"file://{bare}", str(dest))
            raise AssertionError("should refuse non-empty target")
        except ValueError:
            pass
    print("selftest: ok", file=sys.stderr)
    return 0


def _clone_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="gu git clone", description="Clone a git repository.")
    p.add_argument("url", nargs="?", help="repository URL (https, ssh, or local file path)")
    p.add_argument("target", nargs="?", help="target directory (created if missing)")
    p.add_argument("--depth", type=int, default=0, help="shallow clone depth (0 = full)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        return _clone_selftest()
    if not args.url or not args.target:
        p.error("provide URL and TARGET_DIR")
    try:
        result = run_clone(args.url, args.target, depth=args.depth)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result))
    else:
        print(f"cloned {result['url']} -> {result['target']} ({result['branch']}, {result['files']} files)")
    return 0


# ================================================================ log (ex git-log) ===

_log_USAGE = ("gu git log <repo> [--since <ref>] [--max <n>] [--path <pathspec>] [--json]"
              "   (aliases: --repo <repo>, --since-commit <ref>, --limit <n>, --file <pathspec>)")


def _log_git(repo, args):
    return subprocess.run(
        ["git", "-C", repo] + args,
        capture_output=True, text=True,
    )


def _log_run(repo, since=None, max_n=20, paths=None):
    # Build the revision range / limit.
    log_args = ["log", "--numstat", "--date=short",
                "--pretty=format:@@COMMIT@@%H%x1f%an%x1f%ad%x1f%s"]
    if since:
        log_args.append(f"{since}..HEAD")
    else:
        log_args.append(f"-n{max_n}")
    if paths:
        log_args.append("--")
        log_args.extend(paths)
    r = _log_git(repo, log_args)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "git log failed")

    commits = []
    cur = None
    for line in r.stdout.splitlines():
        if line.startswith("@@COMMIT@@"):
            if cur:
                commits.append(cur)
            h, an, ad, s = line[len("@@COMMIT@@"):].split("\x1f", 3)
            cur = {"hash": h, "short": h[:10], "author": an, "date": ad,
                   "subject": s, "files": [], "added": 0, "removed": 0}
        elif line.strip() and cur is not None:
            parts = line.split("\t")
            if len(parts) == 3:
                add, rem, path = parts
                a = int(add) if add.isdigit() else 0
                d = int(rem) if rem.isdigit() else 0
                cur["files"].append({"add": a, "rem": d, "path": path})
                cur["added"] += a
                cur["removed"] += d
    if cur:
        commits.append(cur)

    # Churn hot-spots across the logged range.
    churn = {}
    for c in commits:
        for f in c["files"]:
            churn[f["path"]] = churn.get(f["path"], 0) + f["add"] + f["rem"]
    hot = sorted(churn.items(), key=lambda kv: kv[1], reverse=True)[:15]

    return {"repo": repo, "since": since, "paths": paths or None,
            "count": len(commits),
            "commits": commits, "hotspots": [{"path": p, "churn": c} for p, c in hot]}


def build_parser():
    ap = argparse.ArgumentParser(usage=_log_USAGE)
    ap.add_argument("repo", nargs="?")
    ap.add_argument("--repo", dest="repo_flag", metavar="REPO",
                    help="alias for the positional <repo>")
    ap.add_argument("--since", metavar="REF")
    ap.add_argument("--since-commit", dest="since_commit", metavar="REF",
                    help="alias for --since")
    ap.add_argument("--max", type=int, default=None)
    ap.add_argument("--limit", dest="limit", type=int, default=None,
                    help="alias for --max")
    ap.add_argument("--path", action="append", default=None, metavar="PATHSPEC",
                    help="restrict to commits touching this path (git pathspec; repeatable)")
    ap.add_argument("--file", dest="file_paths", action="append", default=None,
                    metavar="PATHSPEC", help="alias for --path")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    return ap


def _log_selftest() -> int:
    # extracted VERBATIM from git-log's original main() --selftest branch
    import tempfile, os
    d = tempfile.mkdtemp()
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    with open(os.path.join(d, "a.txt"), "w") as f:
        f.write("hello\nworld\n")
    subprocess.run(["git", "-C", d, "add", "."], check=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "first"], check=True)
    with open(os.path.join(d, "b.txt"), "w") as f:
        f.write("second file\n")
    subprocess.run(["git", "-C", d, "add", "."], check=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "second"], check=True)
    res = _log_run(d, max_n=5)
    assert res["count"] == 2, res
    assert res["commits"][0]["subject"] == "second"
    assert any(f["path"] == "a.txt" for f in res["commits"][1]["files"])
    # --path filters to commits touching that path only
    res_p = _log_run(d, max_n=5, paths=["a.txt"])
    assert res_p["count"] == 1 and res_p["commits"][0]["subject"] == "first", res_p
    # glob pathspec works
    res_g = _log_run(d, max_n=5, paths=["b.*"])
    assert res_g["count"] == 1 and res_g["commits"][0]["subject"] == "second", res_g
    # the flag aliases resolve to the same inputs as the canonical forms
    ns = build_parser().parse_args(["--repo", "/x", "--since-commit", "abc"])
    assert (ns.repo or ns.repo_flag) == "/x", ns
    assert (ns.since or ns.since_commit) == "abc", ns
    # --limit is an alias for --max; --file for --path
    ns2 = build_parser().parse_args(["/x", "--limit", "3", "--file", "y.txt"])
    assert (ns2.max if ns2.max is not None else ns2.limit) == 3, ns2
    assert (ns2.path or ns2.file_paths) == ["y.txt"], ns2
    print("selftest: ok", file=sys.stderr)
    return 0


def _log_main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.selftest:
        return _log_selftest()

    repo = args.repo or args.repo_flag
    since = args.since or args.since_commit
    # --max and --limit are equivalent; default to 20 when neither is given.
    max_n = args.max if args.max is not None else args.limit
    if max_n is None:
        max_n = 20
    paths = (args.path or []) + (args.file_paths or [])
    if not repo:
        print(f"error: repo required\nusage: {_log_USAGE}", file=sys.stderr)
        sys.exit(2)
    try:
        res = _log_run(repo, since=since, max_n=max_n, paths=paths or None)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"repo: {res['repo']}  commits: {res['count']}  since: {res['since']}"
              + (f"  paths: {res['paths']}" if res.get("paths") else ""))
        for c in res["commits"]:
            print(f"\n{c['short']} {c['date']} {c['author']}: {c['subject']}")
            print(f"  +{c['added']} -{c['removed']} across {len(c['files'])} files")
            for f in c["files"]:
                print(f"    +{f['add']} -{f['rem']} {f['path']}")
        print("\nchurn hot-spots:")
        for h in res["hotspots"]:
            print(f"  {h['churn']:6d}  {h['path']}")


# ========================================================== restore (ex git-restore) ===

_restore_IDENTITY = ["-c", "user.name=routine-scheduler", "-c", "user.email=noreply@routine-scheduler.local"]


def _restore_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


def _within(repo: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(repo.resolve())
        return True
    except ValueError:
        return False


def _restore_run(repo_path: str, files: list[str] | None = None) -> dict:
    repo = Path(repo_path).expanduser()
    if not (repo / ".git").is_dir():
        raise ValueError(f"{repo} is not a git repository")
    restored: list[str] = []
    removed: list[str] = []
    if files:
        for f in files:
            tracked = _restore_git(repo, "ls-files", "--error-unmatch", "--", f).returncode == 0
            if tracked:
                _restore_git(repo, "checkout", "HEAD", "--", f)
                restored.append(f)
            else:
                p = repo / f
                if p.exists() and _within(repo, p):   # only ever delete inside the repo
                    p.unlink()
                    removed.append(f)
    else:
        _restore_git(repo, "checkout", "--", ".")             # all modified tracked files → HEAD
        restored.append(".")
    return {"repo": str(repo), "restored": restored, "removed": removed}


def _restore_selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "r"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        (repo / "keep.py").write_text("original\n")
        _restore_git(repo, "add", "-A")
        _restore_git(repo, *_restore_IDENTITY, "commit", "-qm", "base")
        # modify a tracked file and create a new untracked one, then revert both by name
        (repo / "keep.py").write_text("BROKEN EDIT\n")
        (repo / "new_module.py").write_text("created by the routine\n")
        result = _restore_run(str(repo), files=["keep.py", "new_module.py"])
        assert (repo / "keep.py").read_text() == "original\n", "tracked file not restored"
        assert not (repo / "new_module.py").exists(), "untracked file not removed"
        assert result["restored"] == ["keep.py"] and result["removed"] == ["new_module.py"], result
        # a path-escape attempt is refused (stays inside the repo)
        outside = Path(tmp) / "outside.txt"
        outside.write_text("safe")
        _restore_run(str(repo), files=["../outside.txt"])
        assert outside.exists(), "git-restore escaped the repo"
    print("selftest: ok", file=sys.stderr)
    return 0


def _restore_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="gu git restore", description="Discard uncommitted edits, restoring HEAD.")
    p.add_argument("repo_path", nargs="?", help="path to the git repo")
    p.add_argument("files", nargs="*", help="specific paths to restore (default: all modified tracked files)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        return _restore_selftest()
    if not args.repo_path:
        p.error("provide REPO_PATH")
    try:
        result = _restore_run(args.repo_path, files=args.files or None)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result) if args.json else
          f"restored={result['restored']} removed={result['removed']}")
    return 0


# ================================================================ sync (ex git-sync) ===

#: The identity a commit is made under, when the repository itself names none.
#:
#: **A repo that configures `user.name`/`user.email` now keeps them** (2026-09-17). This used
#: to be an unconditional `-c user.name=routine-scheduler -c user.email=noreply@…`, and `-c`
#: beats `.git/config`, so a project that had deliberately set its author could not keep it:
#: every commit any routine made went out as the scheduler. On a public repository that reads
#: as a bot maintaining the project rather than the person whose work it is, and the owner of
#: the LLMSecTest repositories asked for it in exactly those terms — commits belong in his
#: name. Setting `[user]` in `.git/config` had no effect while this override stood, which is
#: the kind of silent defeat that costs a debugging session.
#:
#: The fallback is unchanged and still matters: a repo naming no author at all would fail the
#: commit outright ("Please tell me who you are"), so an identity is always supplied — it is
#: simply no longer imposed on repos that have one.
_FALLBACK_IDENTITY = ["-c", "user.name=routine-scheduler", "-c", "user.email=noreply@routine-scheduler.local"]


def _identity_for(repo: Path) -> list[str]:
    """`-c` overrides for this repo's commits, empty when the repo names its own author.

    Read through `git config --get`, so it honours the whole resolution order a human gets
    (repo, then global, then system) rather than only what is written in this repo's file.
    """
    try:
        name = subprocess.run(["git", "-C", str(repo), "config", "--get", "user.name"],
                              capture_output=True, text=True, timeout=30).stdout.strip()
        email = subprocess.run(["git", "-C", str(repo), "config", "--get", "user.email"],
                               capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return list(_FALLBACK_IDENTITY)
    return [] if (name and email) else list(_FALLBACK_IDENTITY)


#: Kept as a module-level name because the selftests and older call sites reference it; it is
#: the fallback, never an override of a configured author.
_sync_IDENTITY = _FALLBACK_IDENTITY
# `rebase --continue` opens an EDITOR to let a human amend the replayed commit's message.
# There is no editor in the engine's container ("Terminal is dumb, but EDITOR unset"), so the
# rebase would stall half-finished. `core.editor=true` accepts the existing message unchanged,
# which is what a machine wants: the message came from the commit being replayed.
NO_EDITOR = ["-c", "core.editor=true"]


def _sync_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
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


# git's unmerged stage numbers: 1=base, 2=ours, 3=theirs. Which stages a path HAS is what
# distinguishes the conflict kinds — and the two that carry no derivable answer (a file one
# side deleted and the other changed; a path both sides created) are exactly the two missing
# a base stage or an ours/theirs stage.
def _conflicts(repo: Path) -> list[dict]:
    """Every unmerged path with its conflict kind, from the index rather than from parsing
    git's prose (which is localized and changes between versions)."""
    out = _sync_git(repo, "ls-files", "-u").stdout.splitlines()
    stages: dict[str, set[int]] = {}
    for line in out:
        # "<mode> <sha> <stage>\t<path>"
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if path and len(parts) >= 3 and parts[2].isdigit():
            stages.setdefault(path, set()).add(int(parts[2]))
    kinds = []
    for path, st in sorted(stages.items()):
        if 1 not in st:
            kind = "add-add"
        elif 2 not in st or 3 not in st:
            kind = "modify-delete"
        else:
            kind = "both-modified"
        kinds.append({"path": path, "kind": kind})
    return kinds


def _rebase_in_progress(repo: Path) -> bool:
    git_dir = Path(_sync_git(repo, "rev-parse", "--git-dir").stdout.strip() or ".git")
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def _rescue_tag(repo: Path, branch: str) -> str:
    """Tag the REMOTE tip before a rebase rewrites local history on top of it.

    A rebase replays local commits onto origin/<branch>; if a later resolution drops one of
    the remote's changes, that commit is still reachable from this tag. Cheap insurance
    against the failure this util cannot otherwise undo.
    """
    remote_tip = _sync_git(repo, "rev-parse", f"origin/{branch}").stdout.strip()
    if not remote_tip:
        return ""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tag = f"git-sync-pre-rebase/{branch}/{stamp}"
    _sync_git(repo, "tag", "-f", tag, remote_tip)
    return tag


def finish_rebase(repo_path: str, push: bool = True) -> dict:
    """`--continue`: stage whatever the caller resolved and finish the held rebase.

    The caller edited files in the working tree, so staging is `add -A` — the same thing a
    human does before `git rebase --continue`. If conflicts remain unresolved (markers still
    unmerged in the index), git refuses and we say which paths are still open rather than
    leaving a half-finished rebase nobody knows about.
    """
    repo = Path(repo_path).expanduser()
    if not _rebase_in_progress(repo):
        return {"repo": str(repo), "ok": False, "error": "no rebase in progress"}
    branch = (_sync_git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
              or _sync_git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip() or "main")
    with _repo_lock(repo):
        _sync_git(repo, "add", "-A")
        r = _sync_git(repo, *_identity_for(repo), *NO_EDITOR, "rebase", "--continue")
        if r.returncode != 0:
            return {"repo": str(repo), "ok": False, "rebase_in_progress": True,
                    "conflicts": _conflicts(repo),
                    "error": (r.stderr or r.stdout).strip()[:300]}
    result: dict = {"repo": str(repo), "rebase_in_progress": False, "resolved": True}
    if push and _sync_git(repo, "remote").stdout.strip():
        pr = _sync_git(repo, "push", "origin", branch)
        result["pushed"] = pr.returncode == 0
        if pr.returncode != 0:
            result["push_error"] = (pr.stderr or pr.stdout).strip()[:300]
    result["ok"] = bool(result.get("pushed", True))
    return result


def abort_rebase(repo_path: str) -> dict:
    """`--abort-rebase`: walk away, leaving the repo exactly as it was before the pull."""
    repo = Path(repo_path).expanduser()
    if not _rebase_in_progress(repo):
        return {"repo": str(repo), "ok": True, "aborted": False,
                "note": "no rebase in progress"}
    with _repo_lock(repo):
        r = _sync_git(repo, "rebase", "--abort")
    return {"repo": str(repo), "ok": r.returncode == 0, "aborted": r.returncode == 0}


def _sync_run(repo_path: str, message: str = "", push: bool = True, pull: bool = True,
              on_conflict: str = "abort") -> dict:
    """Full bidirectional sync: commit local changes → pull --rebase from origin → push.
    Keeps a repo in sync with its remote in one call. Set pull/push False to do less.
    The result carries attempted/error fields so failures are visible, not silent."""
    repo = Path(repo_path).expanduser()
    if not (repo / ".git").is_dir():
        raise ValueError(f"{repo} is not a git repository")
    # The index-touching steps (add, commit, rebase) run under the shared per-repo lock so a
    # routine autocommitting THIS dir at the same instant takes turns instead of colliding.
    with _repo_lock(repo):
        _sync_git(repo, "add", "-A")
        status = _sync_git(repo, "status", "--porcelain").stdout.strip()
        committed = False
        commit_error = ""
        if status:
            msg = message or "sync"
            r = _sync_git(repo, *_identity_for(repo), "commit", "-qm", msg)
            committed = r.returncode == 0
            if not committed:
                # **A REFUSED COMMIT IS A FAILURE AND MUST SAY SO (2026-09-11).** This
                # recorded `committed: False` and threw the reason away, so a repo with a
                # commit-msg or pre-commit hook — which is the normal shape of a project
                # that gates its own quality — answered `ok=True, committed=False` and the
                # caller read it as "nothing to commit". The work stayed staged and
                # unrecorded while the run believed it had landed. The hook's own text is
                # the only thing that says what to change, so it travels with the result.
                commit_error = (r.stderr or r.stdout).strip()[:500]
        has_remote = bool(_sync_git(repo, "remote").stdout.strip())
        branch = _sync_git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip() or "main"
        pulled = False
        pull_attempted = False
        pull_error = ""
        held: list[dict] = []
        rescue = ""
        if pull and has_remote:
            # rebase local work on remote; abort cleanly on conflict rather than leave a mess
            pull_attempted = True
            _sync_git(repo, "fetch", "--quiet", "origin", branch)
            rescue = _rescue_tag(repo, branch)
            r = _sync_git(repo, *_sync_IDENTITY, "pull", "--rebase", "--quiet", "origin", branch)
            pulled = r.returncode == 0
            if not pulled:
                pull_error = (r.stderr or r.stdout).strip()[:300]
                if on_conflict == "hold" and _rebase_in_progress(repo):
                    # LEAVE it in progress: the caller has no shell, so this is the only
                    # moment the conflicted content is reachable to read and resolve.
                    held = _conflicts(repo)
                else:
                    _sync_git(repo, "rebase", "--abort")
    if held:
        # a held rebase means HEAD is mid-replay — pushing now would publish a partial state
        return {"repo": str(repo), "committed": committed, "had_changes": bool(status),
                "pulled": False, "pull_attempted": True, "pull_error": pull_error,
                "rebase_in_progress": True, "conflicts": held,
                "rescue_tag": rescue, "pushed": False, "push_attempted": False, "ok": False}
    pushed = False
    push_attempted = False
    push_error = ""
    if push and has_remote:
        push_attempted = True
        r = _sync_git(repo, "push", "origin", branch)
        pushed = r.returncode == 0
        if not pushed:
            push_error = (r.stderr or r.stdout).strip()[:300]
    result = {"repo": str(repo), "committed": committed, "had_changes": bool(status),
              "pulled": pulled, "pull_attempted": pull_attempted,
              "pushed": pushed, "push_attempted": push_attempted}
    if commit_error:
        result["commit_error"] = commit_error
    if pull_error:
        result["pull_error"] = pull_error
    if push_error:
        result["push_error"] = push_error
    # ok = every attempted operation succeeded (a skipped one is not a failure). A commit
    # that was ATTEMPTED — there were changes — and refused counts here: it is the whole
    # point of the call, and reporting ok on it loses the run's work silently.
    result["ok"] = (not commit_error
                    and (not pull_attempted or pulled)
                    and (not push_attempted or pushed))
    return result


def _sync_selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "r"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        (repo / "f.txt").write_text("hello")
        result = _sync_run(str(repo), message="test commit", push=False, pull=False)
        assert result["committed"] and result["had_changes"], result
        assert (repo / ".git" / "rsched-commit.lock").exists(), "per-repo lock not taken"
        assert result["ok"] is True, result
        log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                             capture_output=True, text=True)
        assert "test commit" in log.stdout, log.stdout
        # a second run with no changes commits nothing; no remote → no pull/push attempted
        second = _sync_run(str(repo), push=False, pull=False)
        assert second["committed"] is False and second["pulled"] is False
        assert second["push_attempted"] is False and second["ok"] is True, second
        # a repo WITH a remote that cannot be reached must fail LOUDLY: ok=False + push_error
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                        str(Path(tmp) / "nonexistent-remote.git")], check=True)
        (repo / "g.txt").write_text("more")
        third = _sync_run(str(repo), message="second commit", push=True, pull=True)
        assert third["push_attempted"] is True and third["pushed"] is False, third
        assert third["ok"] is False and third.get("push_error"), third
        _selftest_conflicts(Path(tmp))
    print("selftest: ok", file=sys.stderr)
    return 0


def _selftest_hook_refusal(tmp: Path) -> None:
    """A repo whose commit-msg hook REFUSES the commit (2026-09-11 regression, R1412/R1421).

    Every other fixture here builds a hook-free repo, and a green-path-only fixture cannot
    catch a refusal: the bug was that `committed=False, ok=True` reads exactly like "nothing
    to commit", so a caller's whole body of work stayed staged while the run was told the
    call succeeded. A repo that gates its own quality with a hook is the normal shape of a
    real project, so it belongs in the fixtures.
    """
    repo = tmp / "hooked"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "commit-msg"
    hook.write_text(
        "#!/bin/sh\n"
        'if grep -qi "forbidden-tell" "$1"; then\n'
        '  echo "commit-msg hook: rejected AI-writing tell [forbidden-tell]" >&2\n'
        "  exit 1\n"
        "fi\n"
        "exit 0\n"
    )
    hook.chmod(0o755)

    (repo / "work.txt").write_text("a whole run's worth of work\n")
    refused = _sync_run(str(repo), message="add forbidden-tell thing", push=False, pull=False)
    # the three assertions that would ALL have failed before the fix
    assert refused["had_changes"] is True, refused
    assert refused["committed"] is False, refused
    assert refused["ok"] is False, "a refused commit must not report ok — it loses the work"
    assert "forbidden-tell" in refused.get("commit_error", ""), refused
    # and the work really is still uncommitted, which is what ok=False is telling the caller
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                         capture_output=True, text=True)
    assert log.stdout.strip() == "", "nothing should have been committed"

    # the same repo with an ACCEPTABLE message commits normally and reports ok
    accepted = _sync_run(str(repo), message="add thing", push=False, pull=False)
    assert accepted["committed"] is True and accepted["ok"] is True, accepted
    assert "commit_error" not in accepted, accepted


def _selftest_conflicts(tmp: Path) -> None:
    """Two clones of one bare remote, each committing over the other — the real shape of the
    divergence this feature exists for. Covers: hold leaves the rebase live, the classifier
    tells both-modified from modify-delete, the rescue tag pins the remote tip, resolve +
    --continue lands, and --abort-rebase restores the pre-pull state.
    """
    def git(repo, *a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)

    bare = tmp / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    a, b = tmp / "a", tmp / "b"
    subprocess.run(["git", "clone", "-q", str(bare), str(a)], check=True)
    (a / "shared.txt").write_text("base\n")
    (a / "doomed.txt").write_text("original\n")
    _sync_run(str(a), message="base", push=True, pull=False)
    subprocess.run(["git", "clone", "-q", str(bare), str(b)], check=True)

    # B (the "remote" side) edits both files and publishes
    (b / "shared.txt").write_text("from-remote\n")
    (b / "doomed.txt").write_text("improved-remotely\n")
    _sync_run(str(b), message="remote work", push=True, pull=False)

    # A edits the same line of one and DELETES the other — one conflict of each kind
    (a / "shared.txt").write_text("from-local\n")
    (a / "doomed.txt").unlink()
    res = _sync_run(str(a), message="local work", push=True, pull=True, on_conflict="hold")
    assert res["rebase_in_progress"] is True, res
    kinds = {c["path"]: c["kind"] for c in res["conflicts"]}
    assert kinds.get("shared.txt") == "both-modified", kinds
    assert kinds.get("doomed.txt") == "modify-delete", kinds
    assert res["rescue_tag"], res
    # the rescue tag must pin the REMOTE tip, so B's work survives any resolution
    tagged = git(a, "rev-parse", res["rescue_tag"]).stdout.strip()
    assert tagged == git(a, "rev-parse", "origin/main").stdout.strip(), res["rescue_tag"]

    # abort restores the pre-pull state exactly
    assert abort_rebase(str(a))["aborted"] is True
    assert not _rebase_in_progress(a)
    assert (a / "shared.txt").read_text() == "from-local\n"

    # ...and holding again, resolving in the working tree, continues and lands
    res = _sync_run(str(a), message="local work", push=False, pull=True, on_conflict="hold")
    assert res["rebase_in_progress"] is True, res
    (a / "shared.txt").write_text("merged-by-hand\n")      # what a caller would write
    (a / "doomed.txt").write_text("improved-remotely\n")   # keep the remote's version
    done = finish_rebase(str(a), push=True)
    assert done["ok"] and done["resolved"], done
    assert not _rebase_in_progress(a)
    assert (a / "shared.txt").read_text() == "merged-by-hand\n"
    # B's commit is still reachable, and A's resolution is now on the remote
    assert git(a, "cat-file", "-e", tagged).returncode == 0
    fresh = tmp / "c"
    subprocess.run(["git", "clone", "-q", str(bare), str(fresh)], check=True)
    assert (fresh / "shared.txt").read_text() == "merged-by-hand\n"


def _sync_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="gu git sync", description="Commit + pull + push a repo.")
    p.add_argument("repo_path", nargs="?", help="path to the git repo")
    p.add_argument("-m", "--message", default="", help="commit message (default 'sync')")
    p.add_argument("--no-push", action="store_true", help="do not push")
    p.add_argument("--no-pull", action="store_true", help="do not pull remote updates")
    p.add_argument("--on-conflict", choices=("abort", "hold"), default="abort",
                   help="hold: leave the rebase in progress and report the conflicts, so a "
                        "shell-less caller can resolve them in the working tree")
    p.add_argument("--continue", dest="continue_", action="store_true",
                   help="stage resolutions and finish a held rebase, then push")
    p.add_argument("--abort-rebase", action="store_true",
                   help="discard a held rebase; the repo returns to its pre-pull state")
    p.add_argument("--json", action="store_true")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        return _sync_selftest()
    if not args.repo_path:
        p.error("provide REPO_PATH")
    try:
        if args.abort_rebase:
            result = abort_rebase(args.repo_path)
        elif args.continue_:
            result = finish_rebase(args.repo_path, push=not args.no_push)
        else:
            result = _sync_run(args.repo_path, message=args.message,
                               push=not args.no_push, pull=not args.no_pull,
                               on_conflict=args.on_conflict)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result))
    elif result.get("conflicts"):
        # the held case: name every conflicted path and its kind, since that is the whole
        # point of holding — and say plainly which ones must not be resolved mechanically
        print(f"CONFLICTS ({len(result['conflicts'])}) — rebase held in progress; "
              f"pre-rebase remote tip tagged {result.get('rescue_tag') or '(none)'}")
        for c in result["conflicts"]:
            print(f"  {c['kind']:<14} {c['path']}")
    else:
        line = " ".join(f"{k}={result[k]}" for k in
                        ("committed", "pulled", "pushed", "resolved", "aborted", "ok")
                        if k in result)
        # R1519: `committed=False pushed=False ok=False` is three facts and no reason, and a
        # caller reads it as "the push failed". Say which step stopped the call, so the flags
        # cannot be read as contradicting each other.
        why = ""
        if result.get("commit_error"):
            why = "commit refused (see commit_error) — nothing was pushed"
        elif "committed" in result and not result.get("committed") and not result.get("had_changes"):
            why = "nothing to commit — working tree clean"
        elif result.get("push_attempted") and not result.get("pushed"):
            why = "push failed (see push_error)"
        elif "push_attempted" in result and not result.get("push_attempted") and result.get("committed"):
            why = "not pushed — no remote configured"
        print(line + (f"  [{why}]" if why else ""))
    if not result.get("ok"):
        # R1519: commit_error was NOT in this list, so a commit-msg hook's refusal — the
        # single most common reason a sync does nothing — was invisible unless the caller
        # happened to pass --json. The reason a call failed must reach the human output.
        for key in ("commit_error", "pull_error", "push_error", "error"):
            if result.get(key):
                print(f"{key}: {result[key]}", file=sys.stderr)
        return 1
    return 0


# ================================================================ dispatcher ===

# ======================================================= inspect (ex git-inspect) ===
# Transplanted VERBATIM from git-inspect, which has been REMOVED from the registry:
# everything that reads or writes a git repo is one catalog entry.

def inspect_repo(repo, path=None) -> dict:
    """Read-only repository state: HEAD, short status, full textual diff, untracked."""
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True).stdout
    paths = ["--", path] if path else []
    return {"head": git("rev-parse", "HEAD").strip(),
            "status": git("status", "--short"),
            "diff": git("diff", "HEAD", "--no-ext-diff", *paths),
            "untracked": git("ls-files", "--others", "--exclude-standard").splitlines()}


def _inspect_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="gu git inspect", allow_abbrev=False,
                                 usage="gu git inspect REPO [--path PATH] [--json]")
    ap.add_argument("repo", nargs="?")
    ap.add_argument("--path")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _inspect_selftest()
    if not args.repo:
        print("error: REPO is required", file=sys.stderr)
        print("usage: gu git inspect REPO [--path PATH] [--json]", file=sys.stderr)
        return 2
    repo = Path(args.repo).expanduser()
    if not repo.exists():
        print(f"error: no such path: {repo}", file=sys.stderr)
        return 2
    try:
        result = inspect_repo(repo, args.path)
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or "").strip() or f"git exited {e.returncode}"
        print(f"error: cannot inspect {repo}: {msg}", file=sys.stderr)
        return 1
    # Output is JSON either way (the original always printed JSON); --json is accepted
    # for symmetry with the other verbs so a caller never has to remember which is which.
    print(json.dumps(result, indent=2))
    return 0


def _inspect_selftest() -> int:
    """Offline: a temp repo with one commit and one untracked file, then a tracked edit."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init", "-q", tmp], check=True)
        cfg = ["-c", "user.name=test", "-c", "user.email=test@example.com"]
        subprocess.run(["git", "-C", tmp, *cfg, "commit", "--allow-empty", "-qm", "test"],
                       check=True)
        Path(tmp, "new.txt").write_text("test")
        result = inspect_repo(tmp)
        assert result["untracked"] == ["new.txt"], result
        assert "?? new.txt" in result["status"], result
        assert not result["diff"], result
        assert len(result["head"]) == 40, result
        # a TRACKED modification must show up in the diff — the original only ever
        # asserted the empty case, so a diff that silently returned "" would have passed.
        tracked = Path(tmp, "tracked.txt")
        tracked.write_text("one\n")
        subprocess.run(["git", "-C", tmp, "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", tmp, *cfg, "commit", "-qm", "add"], check=True)
        tracked.write_text("two\n")
        changed = inspect_repo(tmp)
        assert "tracked.txt" in changed["diff"] and "+two" in changed["diff"], changed["diff"]
        # --path scopes the diff to one pathspec
        scoped = inspect_repo(tmp, "new.txt")
        assert "tracked.txt" not in scoped["diff"], scoped["diff"]
    print("selftest: ok", file=sys.stderr)
    return 0


def _selftest_all() -> int:
    """`git selftest` / `git --selftest`: run ALL four originals' selftests in sequence
    (all offline — git works against local temp repos). One line per source; exit 0
    only if every one passes. Each original selftest's test cases are preserved exactly;
    their own "selftest: ok" stderr line is captured so exactly one line per source is
    printed here (on failure the captured stderr is echoed)."""
    import io
    from contextlib import redirect_stderr
    cases = (
        ("clone", _clone_selftest, "local bare-repo clone + non-empty-target refusal"),
        ("log", _log_selftest, "history + pathspec filter + flag aliases"),
        ("restore", _restore_selftest, "tracked restore + untracked delete + path-escape guard"),
        ("sync", _sync_selftest, "2-clone conflict cases (hold/continue/abort + classifier)"),
        ("inspect", _inspect_selftest, "temp-repo status/untracked + tracked diff + pathspec"),
    )
    ok = True
    for name, fn, note in cases:
        captured = io.StringIO()
        try:
            with redirect_stderr(captured):
                rc = fn()
            if rc not in (None, 0):
                ok = False
                print(f"selftest FAIL: {name} (exit {rc})")
                continue
            print(f"selftest ok: {name} ({note})")
        except Exception as exc:
            ok = False
            print(f"selftest FAIL: {name} ({exc})")
            if captured.getvalue():
                sys.stderr.write(captured.getvalue())
    return 0 if ok else 1


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--selftest":
        return _selftest_all()
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    verb, rest = argv[0], argv[1:]
    if verb == "selftest":
        return _selftest_all()
    if verb == "clone":
        rc = _clone_main(rest)
        return rc if isinstance(rc, int) else 0
    if verb == "log":
        rc = _log_main(rest)
        return rc if isinstance(rc, int) else 0
    if verb == "restore":
        rc = _restore_main(rest)
        return rc if isinstance(rc, int) else 0
    if verb == "sync":
        rc = _sync_main(rest)
        return rc if isinstance(rc, int) else 0
    if verb == "inspect":
        rc = _inspect_main(rest)
        return rc if isinstance(rc, int) else 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
