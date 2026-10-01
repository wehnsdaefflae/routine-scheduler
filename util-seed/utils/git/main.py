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

One verb dispatcher over what were five utils (git-clone, git-log, git-restore, git-sync,
git-inspect). The leading verb is stripped before that verb's own parser, and every git
command any verb runs goes through ONE runner, `_git`: a call that outlives its timeout — or
this util being ended — is TERMINATED with its hooks (SIGTERM to its process group, SIGKILL
only `TERM_GRACE_S` later), because git deletes the `index.lock` it holds in its SIGTERM
handler and nowhere else. A caller-supplied URL or revision is passed after `--` /
`--end-of-options`, so git can never read it as an option. `git selftest` / `git --selftest`
runs every verb's selftest in sequence (offline; git works against local temp repos); each
verb still accepts its own `--selftest` too.
"""

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path

#: What a bad invocation prints: the docstring's own usage block, so the two cannot drift.
USAGE = __doc__[__doc__.index("usage:"):__doc__.index("\ntags:")]

#: One git call's deadline, unless the call names its own (a clone gets CLONE_TIMEOUT_S; the
#: read-only `log` and `inspect` keep none and live within the deadline of the util call).
GIT_TIMEOUT_S = 60
CLONE_TIMEOUT_S = 300
#: Between SIGTERM and SIGKILL for a git call being ended — the scheduler's own grace
#: (`rsched.procgroup.TERM_GRACE_S`), which a uv script cannot import. Git cleans up only in
#: its SIGTERM handler: the SIGKILL `subprocess.run` sends at its timeout left an empty
#: `index.lock` that failed every later write in two routine repos on 2026-09-30
#: (docs/architecture.md, "Git writes"), and the disk under `/home` stalls one I/O for up to
#: thirty seconds, so a git blocked in it runs that handler only once the I/O returns.
TERM_GRACE_S = 30
#: The process groups of the git calls in flight: whom a SIGTERM to this util is handed on to.
_IN_FLIGHT: set[int] = set()


def _end_group(proc: subprocess.Popen) -> None:
    """End the process group `proc` leads the way `rsched.procgroup.terminate` does: SIGTERM to
    every member, up to TERM_GRACE_S for ALL of them to exit (a hook's children too, not just
    git), SIGKILL for whatever is left. The leader is reaped either way."""
    with suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    deadline = time.monotonic() + TERM_GRACE_S
    while True:
        proc.poll()                    # reap the leader first: its zombie answers for the group
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            return
        if time.monotonic() >= deadline:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return
        time.sleep(0.05)


def _hand_on_sigterm(signum, _frame):
    """The scheduler ends this util — its call's deadline, or the run's abort — by SIGTERM to
    the util's process group, which a git call's own group does not share. Pass it on, then
    leave the way SIGTERM would have: `_git` ends the group (and waits for it) on the way out."""
    for pgid in list(_IN_FLIGHT):
        with suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGTERM)
    raise SystemExit(128 + signum)


def _git(repo, *args: str, timeout: float | None = GIT_TIMEOUT_S,
         check: bool = False) -> subprocess.CompletedProcess:
    """Run `git -C repo ARGS` (no `-C` when `repo` is None: a clone) — the one git invoker here.

    Git runs as the leader of a process group of its own, so ending it reaches the hook it is
    waiting on and that hook's children too; a call that outlives `timeout` is ended through
    `_end_group` and raises subprocess.TimeoutExpired, as subprocess.run did — but only once
    the group has stopped, and never by a SIGKILL git gets no chance to clean up after. A
    SIGTERM to this util reaches the group through `_hand_on_sigterm` (installed by `main`).

    Every call reads with `GIT_OPTIONAL_LOCKS=0`, so a read (`status`) never takes the index
    lock and can neither leave one behind nor fail a concurrent writer's commit — the rule
    libgit keeps for the scheduler's own repos. `core.quotePath=false` reports a path as the
    file's own name instead of C-quoted octal (`"Gr\\303\\266\\303\\237e.txt"`), since a caller
    acts on the paths it is given back.
    """
    cmd = ["git", "-c", "core.quotePath=false",
           *(["-C", str(repo)] if repo is not None else []), *args]
    with subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, start_new_session=True,
                          env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}) as proc:
        _IN_FLIGHT.add(proc.pid)
        try:
            out, err = proc.communicate(timeout=timeout)
        except BaseException:          # its own timeout, or this util being ended
            _end_group(proc)
            raise
        finally:
            _IN_FLIGHT.discard(proc.pid)
    done = subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    if check:
        done.check_returncode()
    return done


def _runner_selftest() -> int:
    """A git call that outlives its timeout is TERMINATED with its hook, never killed outright:
    `commit -a` holds the index lock while its pre-commit hook outlasts a one-second timeout.
    Once `_git` raises, the lock must be gone — the SIGKILL `subprocess.run` sent there left
    it behind and the next commit failed on it — and so must the hook's own child, which a
    signal to git alone leaves running with no deadline at all."""
    def alive(pid: int) -> bool:
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
                return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return False

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _git(repo, "init", "-q")
        (repo / "f.txt").write_text("x\n")
        _git(repo, "add", "-A")
        _git(repo, *_FALLBACK_IDENTITY, "commit", "-qm", "base")
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(f"#!/bin/sh\nsleep 30 &\necho $! > {tmp}/hook-child.pid\nwait\n")
        hook.chmod(0o755)
        (repo / "f.txt").write_text("y\n")
        started = time.monotonic()
        try:
            _git(repo, *_FALLBACK_IDENTITY, "commit", "-a", "-qm", "slow", timeout=1)
            raise AssertionError("a hook outliving the timeout must make the call raise")
        except subprocess.TimeoutExpired:
            pass
        assert time.monotonic() - started < TERM_GRACE_S, "git outlived its SIGTERM"
        assert not (repo / ".git" / "index.lock").exists(), "a timed-out git left index.lock"
        child = int(Path(tmp, "hook-child.pid").read_text())
        assert not alive(child), "the hook's child outlived the call it belonged to"
        nxt = _git(repo, *_FALLBACK_IDENTITY, "commit", "-a", "-qm", "next", "--no-verify")
        assert nxt.returncode == 0, nxt.stderr
        # ...and when THIS util is ended — the scheduler's SIGTERM to the util's own group —
        # the git call's group is ended with it instead of running on with no deadline at all
        (repo / "hook-child.pid").unlink()
        (repo / "f.txt").write_text("z\n")
        util = subprocess.Popen([sys.executable, os.path.abspath(__file__), "sync", tmp,
                                 "--no-pull", "--no-push"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        deadline = time.monotonic() + 20
        while not (repo / "hook-child.pid").exists():
            assert util.poll() is None and time.monotonic() < deadline, "the hook never ran"
            time.sleep(0.05)
        os.killpg(util.pid, signal.SIGTERM)
        assert util.wait(timeout=TERM_GRACE_S) == 128 + signal.SIGTERM, util.returncode
        child = int(Path(tmp, "hook-child.pid").read_text())
        assert not alive(child), "a git call outlived the util that was ended"
    print("selftest: ok", file=sys.stderr)
    return 0


# ============================================================ clone (ex git-clone) ===

def run_clone(url: str, target: str, depth: int = 0) -> dict:
    target_path = Path(target).expanduser()
    if target_path.exists() and any(target_path.iterdir()):
        raise ValueError(f"target directory exists and is not empty: {target_path}")
    target_path.mkdir(parents=True, exist_ok=True)
    # `--` before the two caller-supplied words: a URL like `--config=core.sshCommand=…` was
    # otherwise an OPTION, and git ran that command on the next `host:path` it reached.
    depth_args = ["--depth", str(depth)] if depth else []
    r = _git(None, "clone", "--quiet", *depth_args, "--", url, str(target_path),
             timeout=CLONE_TIMEOUT_S)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:500])
    head = _git(target_path, "rev-parse", "--abbrev-ref", "HEAD")
    files = _git(target_path, "ls-files", "-z")
    return {
        "url": url,
        "target": str(target_path),
        "branch": head.stdout.strip() or "HEAD",
        "files": len([f for f in files.stdout.split("\0") if f]),
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
        # a URL that LOOKS like an option is a URL: before `--`, this one set core.sshCommand
        # and git ran it against the scp-style target `evil:repo` (a relative dir, hence chdir)
        marker = Path(tmp) / "option-injected"
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            run_clone(f"--config=core.sshCommand=touch {marker}", "evil:repo")
            raise AssertionError("a URL that is not a repository must fail")
        except RuntimeError:
            pass
        finally:
            os.chdir(cwd)
        assert not marker.exists(), "a dash-prefixed URL was parsed as a git option"
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


def _log_run(repo, since=None, max_n=20, paths=None):
    # Build the revision range / limit. The caller's ref goes after `--end-of-options`: a
    # `--since=--output=FILE` was otherwise git's own --output and wrote the log to FILE.
    log_args = ["log", "--numstat", "--date=short",
                "--pretty=format:@@COMMIT@@%H%x1f%an%x1f%ad%x1f%s"]
    if not since:
        log_args.append(f"-n{max_n}")
    log_args.append("--end-of-options")
    if since:
        log_args.append(f"{since}..HEAD")
    if paths:
        log_args.append("--")
        log_args.extend(paths)
    r = _git(repo, *log_args, timeout=None)
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
    with tempfile.TemporaryDirectory() as d:
        for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", d, *args], check=True)
        for name, text, subject in (("a.txt", "hello\nworld\n", "first"),
                                    ("b.txt", "second file\n", "second")):
            Path(d, name).write_text(text)
            subprocess.run(["git", "-C", d, "add", "."], check=True)
            subprocess.run(["git", "-C", d, "commit", "-q", "-m", subject], check=True)
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
        # --since lists what came after the ref, with or without a pathspec
        first = res["commits"][1]["hash"]
        res_s = _log_run(d, since=first)
        assert [c["subject"] for c in res_s["commits"]] == ["second"], res_s
        assert _log_run(d, since=first, paths=["a.txt"])["count"] == 0
        # ...and a ref that LOOKS like an option is a (bad) ref, never git's own --output
        written = os.path.join(d, "log-out")
        try:
            _log_run(d, since=f"--output={written}")
            raise AssertionError("a dash-prefixed ref must be refused as a revision")
        except RuntimeError:
            pass
        assert not any(n.startswith("log-out") for n in os.listdir(d)), "--since became an option"
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
        return 2
    try:
        res = _log_run(repo, since=since, max_n=max_n, paths=paths or None)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

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
    return 0


# ========================================================== restore (ex git-restore) ===

def _within(root: Path, path: Path) -> bool:
    """Whether `path` itself LIVES under `root`. Only its directory is resolved: an untracked
    symlink inside the repo is the repo's to delete (the link, never its target), while `..`
    or a symlinked directory leading out of the repo is not."""
    try:
        (path.parent.resolve() / path.name).relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _is_repo(repo: Path) -> bool:
    """`repo` is the top of a git work tree: its `.git` is the repository DIRECTORY, or — in a
    linked worktree or a submodule — a FILE naming the git dir that lives elsewhere. Testing for
    a directory alone refused every worktree, and a long piece of work is carried in one.
    """
    return (repo / ".git").exists()


def _restore_run(repo_path: str, files: list[str] | None = None) -> dict:
    """Return the repo, or the named FILES, to HEAD — in the index AND the working tree.

    Staged and unstaged edits alike: `checkout -- .` used to restore the working tree from the
    INDEX, so an edit `git sync` had staged before its commit was refused survived the restore
    that reported it gone. A NAMED path that HEAD lacks is removed — a staged addition from the
    index and the disk, an untracked file from the disk — never one outside the repo or inside
    its `.git`. With no FILE, every path HEAD has is restored and nothing else is touched.

    Every refusal git (or the guard) gives is reported under `errors`, and `ok` is False then:
    git's exit codes were thrown away, so a restore stopped by an `index.lock` read as done.
    """
    repo = Path(repo_path).expanduser()
    if not _is_repo(repo):
        raise ValueError(f"{repo} is not a git repository")
    if _git(repo, "rev-parse", "--verify", "--quiet", "HEAD").returncode != 0:
        raise ValueError(f"{repo} has no commit yet — there is no HEAD to restore to")
    restored: list[str] = []
    removed: list[str] = []
    errors: list[dict] = []

    def failed(path: str, why: str) -> None:
        errors.append({"path": path, "error": why.strip()[:300]})

    for f in files or []:
        # Known = in the index or in HEAD (a staged deletion is still HEAD's to bring back).
        if _git(repo, "ls-files", "--error-unmatch", "--with-tree=HEAD", "--", f).returncode == 0:
            r = _git(repo, "restore", "--source=HEAD", "--staged", "--worktree", "--", f)
            if r.returncode != 0:
                failed(f, r.stderr or r.stdout)
            else:
                (restored if os.path.lexists(repo / f) else removed).append(f)
            continue
        p = repo / f
        if not os.path.lexists(p):
            continue                      # nothing there and git knows nothing of it
        if not _within(repo, p) or _within(repo / ".git", p):
            failed(f, "outside the repository's working tree — not deleted")
        elif p.is_dir() and not p.is_symlink():
            failed(f, "an untracked directory — name the files to delete")
        else:
            p.unlink()
            removed.append(f)
    if not files:
        # --overlay: every path HEAD has goes back to HEAD, and nothing HEAD lacks is removed
        r = _git(repo, "restore", "--source=HEAD", "--staged", "--worktree", "--overlay",
                 "--", ".")
        if r.returncode != 0:
            failed(".", r.stderr or r.stdout)
        else:
            restored.append(".")
    result = {"repo": str(repo), "restored": restored, "removed": removed, "ok": not errors}
    if errors:
        result["errors"] = errors
    return result


def _restore_selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "r"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        try:
            _restore_run(str(repo))
            raise AssertionError("a repo with no commit has no HEAD to restore to")
        except ValueError:
            pass
        (repo / "keep.py").write_text("original\n")
        _git(repo, "add", "-A")
        _git(repo, *_FALLBACK_IDENTITY, "commit", "-qm", "base")
        # modify a tracked file and create a new untracked one, then revert both by name
        (repo / "keep.py").write_text("BROKEN EDIT\n")
        (repo / "new_module.py").write_text("created by the routine\n")
        result = _restore_run(str(repo), files=["keep.py", "new_module.py"])
        assert (repo / "keep.py").read_text() == "original\n", "tracked file not restored"
        assert not (repo / "new_module.py").exists(), "untracked file not removed"
        assert result["restored"] == ["keep.py"] and result["removed"] == ["new_module.py"], result
        assert result["ok"] is True and "errors" not in result, result
        # a path-escape attempt is refused (stays inside the repo), and SAYS so
        outside = Path(tmp) / "outside.txt"
        outside.write_text("safe")
        escaped = _restore_run(str(repo), files=["../outside.txt"])
        assert outside.exists(), "git-restore escaped the repo"
        assert escaped["ok"] is False and escaped["errors"][0]["path"] == "../outside.txt"
        # ...and git's own state is never "an untracked file" to delete
        assert _restore_run(str(repo), files=[".git/HEAD"])["ok"] is False
        assert (repo / ".git" / "HEAD").is_file(), "restore deleted the repository's HEAD"
        # STAGED edits go back to HEAD too — what a hook-refused `git sync` leaves behind
        (repo / "keep.py").write_text("staged edit\n")
        (repo / "added.py").write_text("staged addition\n")
        _git(repo, "add", "-A")
        whole = _restore_run(str(repo))
        assert (repo / "keep.py").read_text() == "original\n", "a staged edit survived restore"
        assert whole == {"repo": str(repo), "restored": ["."], "removed": [], "ok": True}, whole
        assert (repo / "added.py").exists(), "the whole-repo restore removes nothing HEAD lacks"
        named = _restore_run(str(repo), files=["added.py"])
        assert named["removed"] == ["added.py"] and not (repo / "added.py").exists(), named
        assert not _git(repo, "status", "--porcelain").stdout.strip(), "index not back at HEAD"
        # a restore git REFUSES is a failure, never a silent "restored"
        (repo / "keep.py").write_text("edit\n")
        (repo / ".git" / "index.lock").write_text("")
        locked = _restore_run(str(repo), files=["keep.py"])
        assert locked["ok"] is False and locked["restored"] == [], locked
        assert "index.lock" in locked["errors"][0]["error"], locked
        (repo / ".git" / "index.lock").unlink()
        # a linked WORKTREE has a `.git` FILE naming its git dir — still a repository
        wt = Path(tmp) / "linked-wt"
        _git(repo, "worktree", "add", "--detach", "-q", str(wt))
        (wt / "keep.py").write_text("worktree edit\n")
        in_wt = _restore_run(str(wt), files=["keep.py"])
        assert in_wt["ok"] is True and (wt / "keep.py").read_text() == "original\n", in_wt
    print("selftest: ok", file=sys.stderr)
    return 0


def _restore_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="gu git restore", description="Discard uncommitted edits, restoring HEAD.")
    p.add_argument("repo_path", nargs="?", help="path to the git repo")
    p.add_argument("files", nargs="*",
                   help="specific paths to restore, staged or not; one HEAD lacks is deleted "
                        "(default: every path HEAD has, and nothing else)")
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
          f"restored={result['restored']} removed={result['removed']} ok={result['ok']}")
    for err in result.get("errors", []):
        print(f"error: {err['path']}: {err['error']}", file=sys.stderr)
    return 0 if result["ok"] else 1


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
#: simply no longer imposed on repos that have one. That holds for EVERY git call that writes
#: a commit, the rebase of `pull --rebase` included: replaying the local commits rewrites their
#: COMMITTER, and the pull kept passing this fallback unconditionally after the commit had
#: stopped, so each synced commit read "<owner> authored, routine-scheduler committed".
_FALLBACK_IDENTITY = ["-c", "user.name=routine-scheduler", "-c", "user.email=noreply@routine-scheduler.local"]


def _identity_for(repo: Path) -> list[str]:
    """`-c` overrides for this repo's commits, empty when the repo names its own author.

    Read through `git config --get`, so it honours the whole resolution order a human gets
    (repo, then global, then system) rather than only what is written in this repo's file.
    """
    try:
        name = _git(repo, "config", "--get", "user.name").stdout.strip()
        email = _git(repo, "config", "--get", "user.email").stdout.strip()
    except Exception:
        return list(_FALLBACK_IDENTITY)
    return [] if (name and email) else list(_FALLBACK_IDENTITY)


# `rebase --continue` opens an EDITOR to let a human amend the replayed commit's message.
# There is no editor in the engine's container ("Terminal is dumb, but EDITOR unset"), so the
# rebase would stall half-finished. `core.editor=true` accepts the existing message unchanged,
# which is what a machine wants: the message came from the commit being replayed.
NO_EDITOR = ["-c", "core.editor=true"]


#: The SYNC path's ceiling, and why it is 900 s and not the runner's 60 (2026-09-28,
#: library-sync run 20260928-000003): staging, fetching, pulling and pushing are size- and
#: network-bound, and the routine-scheduler-libraries mirror carries multi-hundred-MB state
#: files — staging ~285 changed paths there blew a 60 s limit and killed `git add` mid-run,
#: which left a stale `.git/index.lock` and reported only "timed out after 60 seconds": a repo
#: wedged by the tool meant to sync it. The timeout exists to stop a genuine hang, so it sits
#: well above the slowest legitimate run — and it still ends git through `_git`, SIGTERM first.
_SYNC_TIMEOUT = 900


def _sync_git(repo: Path, *args: str, timeout: float = _SYNC_TIMEOUT) -> subprocess.CompletedProcess:
    """`_git` at the sync path's ceiling (`_SYNC_TIMEOUT`)."""
    return _git(repo, *args, timeout=timeout)


def _stage_all(repo: Path) -> str:
    """`git add -A`, returning git's OWN error text when staging fails ("" on success).

    R1883 (llmsectest-weekday, via self-audit): the return code of this step used to be
    discarded. When staging failed for an environmental reason -- `insufficient permission for
    adding an object to repository database .git/objects`, a stale index.lock, a full disk --
    the sync carried on to the commit step, which reported git's generic "Changes not staged
    for commit". That sentence is true and useless: it describes the state, not the cause, and
    it reads to every caller as "there was nothing to commit". A 416-turn run's entire output
    went uncommitted behind it, and the reporter spent three attempts diagnosing the wrong
    layer. git had already printed the real reason; the util threw it away.
    """
    r = _sync_git(repo, "add", "-A")
    if r.returncode == 0:
        return ""
    return ((r.stderr or r.stdout).strip() or
            f"git add -A failed with exit {r.returncode} and printed nothing")


def _named_git_dir(dot_git_file: Path) -> Path | None:
    """The git dir a `.git` FILE names (`gitdir: <path>`, relative to the file's own dir), or
    None when it names none that exists — paths._named_git_dir's copy (a util cannot import
    the package)."""
    try:
        text = dot_git_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    head, sep, target = text.strip().partition("gitdir:")
    if not sep or head.strip():
        return None
    named = (dot_git_file.parent / target.strip()).resolve()
    return named if named.is_dir() else None


@contextmanager
def _repo_lock(repo: Path, timeout: float = 30.0):
    """The rsched per-repo commit lock (mirrors paths.repo_lock_path / paths.file_lock):
    an fcntl.flock on <git dir>/rsched-commit.lock, shared with the engine's autocommit and
    pre-run recipe snapshot. In a linked worktree or a submodule the git dir is the one its
    `.git` FILE names — never a file in the work tree, which the `add -A` below would stage.
    Best-effort — proceed after `timeout` so a hung holder can never deadlock a sync."""
    gitdir: Path | None = repo / ".git"
    if gitdir is not None and gitdir.is_file():
        gitdir = _named_git_dir(gitdir)
    lock_path = (gitdir / "rsched-commit.lock" if gitdir is not None and gitdir.is_dir()
                 else repo / ".rsched-commit.lock")
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
    git's prose (which is localized and changes between versions). NUL-separated (`-z`), so a
    path comes back exactly as the file is named — the caller opens it to resolve it."""
    out = _sync_git(repo, "ls-files", "-u", "-z").stdout.split("\0")
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
        stage_error = _stage_all(repo)
        if stage_error:
            return {"repo": str(repo), "ok": False, "rebase_in_progress": True,
                    "stage_failed": True, "error": "git add -A failed: " + stage_error[:300]}
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
    if not _is_repo(repo):
        raise ValueError(f"{repo} is not a git repository")
    # The index-touching steps (add, commit, rebase) run under the shared per-repo lock so a
    # routine autocommitting THIS dir at the same instant takes turns instead of colliding.
    identity = _identity_for(repo)          # the commit's author AND the rebase's committer
    with _repo_lock(repo):
        stage_error = _stage_all(repo)
        if stage_error:
            # Fail HERE, naming the layer, instead of letting the commit step report a
            # generic "nothing staged" for what is really a filesystem/index failure.
            return {"repo": str(repo), "ok": False, "committed": False,
                    "stage_failed": True, "error": "git add -A failed: " + stage_error[:300]}
        status = _sync_git(repo, "status", "--porcelain").stdout.strip()
        committed = False
        commit_error = ""
        if status:
            msg = message or "sync"
            r = _sync_git(repo, *identity, "commit", "-qm", msg)
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
            r = _sync_git(repo, *identity, "pull", "--rebase", "--quiet", "origin", branch)
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
        # a linked WORKTREE syncs too — and its commit lock lives in the git dir its `.git`
        # file names, never in the tree its `add -A` stages
        wt = Path(tmp) / "linked-wt"
        _git(repo, "worktree", "add", "--detach", "-q", str(wt))
        (wt / "w.txt").write_text("from the worktree")
        in_wt = _sync_run(str(wt), message="worktree commit", push=False, pull=False)
        assert in_wt["committed"] and in_wt["ok"] is True, in_wt
        assert not (wt / ".rsched-commit.lock").exists(), "the lock is a file in the work tree"
        assert not _git(wt, "status", "--porcelain").stdout.strip(), "the tree is not clean"
        _selftest_stage_failure(Path(tmp))
        _selftest_conflicts(Path(tmp))
        _selftest_hook_refusal(Path(tmp))
        _selftest_identity(Path(tmp))
    print("selftest: ok", file=sys.stderr)
    return 0


def _selftest_stage_failure(tmp: Path) -> None:
    """R1883: a FAILED `git add` must name the staging failure, not look like an empty diff.

    The reporter (llmsectest-weekday, via self-audit) lost a 416-turn run's entire output to
    this: `git add -A` failed with `insufficient permission for adding an object to repository
    database .git/objects`, the return code was discarded, and the commit step then reported
    git's generic \"Changes not staged for commit\" -- which reads as \"nothing to commit\".
    Three attempts at the wrong layer before the real error surfaced.

    Reproduced here the same way it happened in the wild: make the object database unwritable,
    so git itself produces that exact message. The assertion is on WHAT THE CALLER IS TOLD --
    ok=False, stage_failed, and git's own words quoted -- because that is the whole defect.
    """
    repo = tmp / "stagefail"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "work.txt").write_text("output that must not be silently dropped\n")
    objects = repo / ".git" / "objects"
    mode = objects.stat().st_mode
    os.chmod(objects, 0o500)                      # readable, NOT writable: git cannot add
    try:
        res = _sync_run(str(repo), message="should not be reported as nothing-to-commit",
                        push=False, pull=False)
    finally:
        os.chmod(objects, mode)                   # always restore, even on assertion failure
    # running as root defeats the permission bit; skip rather than assert a false pass
    if res.get("committed"):
        return
    assert res["ok"] is False, f"a failed staging must not report ok: {res}"
    assert res.get("stage_failed") is True, f"the staging failure must be NAMED: {res}"
    err = (res.get("error") or "").lower()
    assert "git add" in err, f"the error must say which step failed: {res}"
    assert "permission" in err or "unable" in err or "cannot" in err, \
        f"git's OWN reason must be quoted, not a generic message: {res}"
    assert "not staged for commit" not in err, \
        f"the generic status message must not stand in for the cause: {res}"


def _selftest_identity(tmp: Path) -> None:
    """A repo that names its own author keeps it through the WHOLE sync — the commit, and the
    rebase `pull --rebase` replays that commit with, whose committer the fallback identity
    used to overwrite."""
    def git(repo, *a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)

    bare = tmp / "owned.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    mine, theirs = tmp / "mine", tmp / "theirs"
    subprocess.run(["git", "clone", "-q", str(bare), str(mine)], check=True)
    git(mine, "config", "user.name", "Repo Owner")
    git(mine, "config", "user.email", "owner@example.com")
    (mine / "one.txt").write_text("1\n")
    assert _sync_run(str(mine), message="base", push=True, pull=False)["ok"]
    subprocess.run(["git", "clone", "-q", str(bare), str(theirs)], check=True)
    (theirs / "two.txt").write_text("2\n")
    assert _sync_run(str(theirs), message="theirs", push=True, pull=False)["ok"]
    (mine / "three.txt").write_text("3\n")
    res = _sync_run(str(mine), message="mine", push=False, pull=True)
    assert res["ok"] and res["pulled"], res
    log = git(mine, "log", "-2", "--format=%s|%an <%ae>|%cn <%ce>").stdout.splitlines()
    owner = "Repo Owner <owner@example.com>"
    assert log[0] == f"mine|{owner}|{owner}", ("the rebase re-signed the commit", log)
    assert log[1].startswith("theirs|"), ("the local commit was not replayed on top", log)


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
    (a / "Größe.txt").write_text("base\n")
    _sync_run(str(a), message="base", push=True, pull=False)
    subprocess.run(["git", "clone", "-q", str(bare), str(b)], check=True)

    # B (the "remote" side) edits every file and publishes
    (b / "shared.txt").write_text("from-remote\n")
    (b / "doomed.txt").write_text("improved-remotely\n")
    (b / "Größe.txt").write_text("from-remote\n")
    _sync_run(str(b), message="remote work", push=True, pull=False)

    # A edits the same line of two and DELETES the third — one conflict of each kind, and
    # one whose name git would C-quote (`"Gr\303\266\303\237e.txt"`) without -z
    (a / "shared.txt").write_text("from-local\n")
    (a / "doomed.txt").unlink()
    (a / "Größe.txt").write_text("from-local\n")
    res = _sync_run(str(a), message="local work", push=True, pull=True, on_conflict="hold")
    assert res["rebase_in_progress"] is True, res
    kinds = {c["path"]: c["kind"] for c in res["conflicts"]}
    assert kinds.get("shared.txt") == "both-modified", kinds
    assert kinds.get("doomed.txt") == "modify-delete", kinds
    assert kinds.get("Größe.txt") == "both-modified", ("a conflict path the caller can open", kinds)
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
    (a / "Größe.txt").write_text("merged-by-hand\n")
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


# ======================================================= inspect (ex git-inspect) ===
# Everything that reads or writes a git repo is one catalog entry.

def inspect_repo(repo, path=None) -> dict:
    """Read-only repository state: HEAD, short status, full textual diff, untracked."""
    def git(*args):
        return _git(repo, *args, timeout=None, check=True).stdout
    paths = ["--", path] if path else []
    untracked = git("ls-files", "--others", "--exclude-standard", "-z")
    return {"head": git("rev-parse", "HEAD").strip(),
            "status": git("status", "--short"),
            "diff": git("diff", "HEAD", "--no-ext-diff", *paths),
            "untracked": [p for p in untracked.split("\0") if p]}


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
    """`git selftest` / `git --selftest`: run every verb's selftest in sequence (all offline —
    git works against local temp repos). One line per verb; exit 0 only if every one passes.
    Each verb's own "selftest: ok" stderr line is captured so exactly one line per verb is
    printed here; on a failure the captured stderr and the traceback are echoed."""
    import io
    import traceback
    from contextlib import redirect_stderr
    cases = (
        ("runner", _runner_selftest, "a timed-out git is terminated and leaves no index.lock"),
        ("clone", _clone_selftest, "local bare-repo clone + non-empty target + option-like URL"),
        ("log", _log_selftest, "history + pathspec + --since + option-like ref + flag aliases"),
        ("restore", _restore_selftest,
         "tracked/staged/untracked restore + refusals reported + path-escape and .git guards"),
        ("sync", _sync_selftest,
         "2-clone conflict cases (hold/continue/abort + classifier) + hook refusal + identity"),
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
            traceback.print_exc()
    return 0 if ok else 1


#: verb → its main(argv), handed everything after the verb
VERBS = {"clone": _clone_main, "log": _log_main, "restore": _restore_main,
         "sync": _sync_main, "inspect": _inspect_main}


def main() -> int:
    signal.signal(signal.SIGTERM, _hand_on_sigterm)
    argv = sys.argv[1:]
    if argv[:1] in (["--selftest"], ["selftest"]):
        return _selftest_all()
    if not argv or argv[0] not in VERBS:
        print(USAGE, file=sys.stderr)
        return 2
    return VERBS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main())
