# /// script
# dependencies = []
# ///
"""git-sync — bidirectionally sync a git repo with its remote (routines have no shell).

usage: gu git-sync REPO_PATH [-m MESSAGE] [--no-push] [--no-pull] [--on-conflict abort|hold] [--continue] [--abort-rebase] [--json]
calls: (none)
tags: git, dev, sync
net: outbound
fs: roots

Commits any local changes in REPO_PATH under a neutral identity, pulls remote updates
(--rebase), and pushes — keeping local and remote in sync in one call.

CONFLICTS. By default a rebase conflict is ABORTED and reported (pull_error), leaving the
repo untouched. `--on-conflict hold` instead LEAVES the rebase in progress and returns
`conflicts: [{path, kind}]` where kind is both-modified / modify-delete / add-add, so a
caller with no shell can read the conflicted files (git's markers are in the working tree),
write resolutions, and finish with `--continue` — or walk away with `--abort-rebase`. Before
any rebase that could rewrite history, the pre-rebase remote tip is saved as the tag
`git-sync-pre-rebase/<branch>/<utc>`: nothing this util does can make a remote commit
unreachable. modify-delete and add-add are reported but never auto-anything — for those,
neither side's answer can be derived from the diff.

A FAILED push (or pull) to an existing remote is LOUD: the git error is captured
in the result (push_error/pull_error) and the util exits 1, so callers can never mistake a
local-only commit for a synced one. Meant for routines maintaining a repo other than their
own working dir (library sync, the meta routine committing the workflow library). The local
commit + rebase are held under the rsched per-repo lock (<repo>/.git/rsched-commit.lock) so
committing a routine dir that is MID-RUN (the improver git-syncing a target) takes turns with
that routine's own autocommit instead of colliding on git's index.lock.
--selftest runs against a throwaway repo, offline (no remote → no pull/push attempted),
plus a two-clone conflict fixture exercising hold → resolve → continue and the classifier."""

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
# `rebase --continue` opens an EDITOR to let a human amend the replayed commit's message.
# There is no editor in the engine's container ("Terminal is dumb, but EDITOR unset"), so the
# rebase would stall half-finished. `core.editor=true` accepts the existing message unchanged,
# which is what a machine wants: the message came from the commit being replayed.
NO_EDITOR = ["-c", "core.editor=true"]


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


# git's unmerged stage numbers: 1=base, 2=ours, 3=theirs. Which stages a path HAS is what
# distinguishes the conflict kinds — and the two that carry no derivable answer (a file one
# side deleted and the other changed; a path both sides created) are exactly the two missing
# a base stage or an ours/theirs stage.
def _conflicts(repo: Path) -> list[dict]:
    """Every unmerged path with its conflict kind, from the index rather than from parsing
    git's prose (which is localized and changes between versions)."""
    out = _git(repo, "ls-files", "-u").stdout.splitlines()
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
    git_dir = Path(_git(repo, "rev-parse", "--git-dir").stdout.strip() or ".git")
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def _rescue_tag(repo: Path, branch: str) -> str:
    """Tag the REMOTE tip before a rebase rewrites local history on top of it.

    A rebase replays local commits onto origin/<branch>; if a later resolution drops one of
    the remote's changes, that commit is still reachable from this tag. Cheap insurance
    against the failure this util cannot otherwise undo.
    """
    remote_tip = _git(repo, "rev-parse", f"origin/{branch}").stdout.strip()
    if not remote_tip:
        return ""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tag = f"git-sync-pre-rebase/{branch}/{stamp}"
    _git(repo, "tag", "-f", tag, remote_tip)
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
    branch = (_git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
              or _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip() or "main")
    with _repo_lock(repo):
        _git(repo, "add", "-A")
        r = _git(repo, *IDENTITY, *NO_EDITOR, "rebase", "--continue")
        if r.returncode != 0:
            return {"repo": str(repo), "ok": False, "rebase_in_progress": True,
                    "conflicts": _conflicts(repo),
                    "error": (r.stderr or r.stdout).strip()[:300]}
    result: dict = {"repo": str(repo), "rebase_in_progress": False, "resolved": True}
    if push and _git(repo, "remote").stdout.strip():
        pr = _git(repo, "push", "origin", branch)
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
        r = _git(repo, "rebase", "--abort")
    return {"repo": str(repo), "ok": r.returncode == 0, "aborted": r.returncode == 0}


def run(repo_path: str, message: str = "", push: bool = True, pull: bool = True,
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
        held: list[dict] = []
        rescue = ""
        if pull and has_remote:
            # rebase local work on remote; abort cleanly on conflict rather than leave a mess
            pull_attempted = True
            _git(repo, "fetch", "--quiet", "origin", branch)
            rescue = _rescue_tag(repo, branch)
            r = _git(repo, *IDENTITY, "pull", "--rebase", "--quiet", "origin", branch)
            pulled = r.returncode == 0
            if not pulled:
                pull_error = (r.stderr or r.stdout).strip()[:300]
                if on_conflict == "hold" and _rebase_in_progress(repo):
                    # LEAVE it in progress: the caller has no shell, so this is the only
                    # moment the conflicted content is reachable to read and resolve.
                    held = _conflicts(repo)
                else:
                    _git(repo, "rebase", "--abort")
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
        _selftest_conflicts(Path(tmp))
    print("selftest: ok", file=sys.stderr)
    return 0


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
    run(str(a), message="base", push=True, pull=False)
    subprocess.run(["git", "clone", "-q", str(bare), str(b)], check=True)

    # B (the "remote" side) edits both files and publishes
    (b / "shared.txt").write_text("from-remote\n")
    (b / "doomed.txt").write_text("improved-remotely\n")
    run(str(b), message="remote work", push=True, pull=False)

    # A edits the same line of one and DELETES the other — one conflict of each kind
    (a / "shared.txt").write_text("from-local\n")
    (a / "doomed.txt").unlink()
    res = run(str(a), message="local work", push=True, pull=True, on_conflict="hold")
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
    res = run(str(a), message="local work", push=False, pull=True, on_conflict="hold")
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


def main() -> int:
    p = argparse.ArgumentParser(prog="gu git-sync", description="Commit + pull + push a repo.")
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
    args = p.parse_args()
    if args.selftest:
        return selftest()
    if not args.repo_path:
        p.error("provide REPO_PATH")
    try:
        if args.abort_rebase:
            result = abort_rebase(args.repo_path)
        elif args.continue_:
            result = finish_rebase(args.repo_path, push=not args.no_push)
        else:
            result = run(args.repo_path, message=args.message,
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
        print(" ".join(f"{k}={result[k]}" for k in
                       ("committed", "pulled", "pushed", "resolved", "aborted", "ok")
                       if k in result))
    if not result.get("ok"):
        for key in ("pull_error", "push_error", "error"):
            if result.get(key):
                print(f"{key}: {result[key]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
