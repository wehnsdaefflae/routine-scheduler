#!/usr/bin/env python3
"""The ONE merge path to `main`: rebase, renumber, classify the delta, then fast-forward.

Three kinds of writer ship to this `main` with nothing ordering them — `self-audit`,
`scheduler-builder` and the operator's own sessions. The release gate serializes the TEST runs
behind `/tmp/rsched-gates.lock`; it does not serialize the MERGES. So whoever goes green second
finds `main` moved, and the costs are all the same shape: a version number taken while a gate
ran, a refused fast-forward that reads like a success when only the push is checked, and a
verdict that describes bytes nobody is merging any more.

This script is that missing step, and it has three properties:

- **The version is assigned at MERGE time.** `next_version` reads `main`'s own newest CHANGELOG
  header and `renumber` rewrites the candidate's top header and `__version__` to the next minor
  above it. "Your number was taken" stops being a conflict anyone resolves by hand.
- **One merger at a time.** The MERGE LEASE is its own lock file, never the gate's: a candidate
  still running tests must not block a candidate that is ready to land, which is exactly what
  sharing one lock would do.
- **The re-gate rule is code, not judgement.** `classify` partitions the files `main` gained
  that the candidate did not write: prose only, test modules only, or anything else. Each class
  names its own answer, and the answer goes into the merge commit's trailer with the stamp that
  covered the merged bytes — so "which verdict covers which bytes" stays answerable afterwards.

Stdlib only, and no `rsched` import: the gate script, the builder's close-out and a person at a
terminal all have to be able to run it, and two of those three are not inside the package.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

#: The merge lease. Deliberately NOT the gate lock: a candidate under test must never block a
#: candidate ready to land, and the two waits have nothing to say to each other.
LEASE = Path("/tmp/rsched-merge.lock")  # noqa: S108 — a box-wide lease, not temp-file handling

#: How long to wait for the lease before giving up. A merge is seconds of work; a wait longer
#: than this means the holder died without releasing, which the caller should see rather than
#: sit through.
LEASE_WAIT_S = 300

#: The CHANGELOG release header this repo writes, with an EM DASH. `tests/test_policy.py` pins
#: the same grammar from the other side.
HEADER = re.compile(r"^## \[(\d+)\.(\d+)\.(\d+)\] — (\d{4}-\d{2}-\d{2})$", re.MULTILINE)

#: Files whose change cannot alter a test outcome: narration and the two ledgers of narration.
PROSE_SUFFIXES = (".md", ".txt", ".rst")

#: Test files that EVERY test can be affected by, so a change to one is never "test modules
#: only". A conftest is matched by name wherever it sits.
SHARED_TESTS = ("tests/helpers.py", "tests/ui/helpers.py", "tests/browsersource.py",
                "tests/memgate.py")


class MergeRefused(Exception):  # noqa: N818 — a refusal decision, not an error condition
    """The merge did not happen, and the message says what to do about it."""


@dataclass
class Delta:
    """How `main` moved under a candidate, and what that costs it."""

    files: list[str] = field(default_factory=list)
    #: `prose` · `tests` · `source` — the three classes, in ascending cost.
    kind: str = "prose"
    #: The test modules to re-run when `kind` is `tests`.
    modules: list[str] = field(default_factory=list)

    @property
    def owes(self) -> str:
        """The re-gate this delta owes, in words a caller can act on."""
        if self.kind == "prose":
            return "nothing: no file main gained can change a test outcome"
        if self.kind == "tests":
            return f"the quality stage plus {' '.join(self.modules)} on the rebased tree"
        return "a full re-gate from the fast stage"


def git(repo: Path, *args: str, check: bool = True) -> str:
    """One git call in `repo`, its stdout stripped. Reads never take the index lock."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          timeout=120, check=False, env=env)
    if check and proc.returncode != 0:
        raise MergeRefused(f"git {' '.join(args)} failed (exit {proc.returncode}): "
                           f"{(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def main_worktree(start: Path) -> Path:
    """The repository's MAIN checkout, whatever directory this was invoked from.

    This is not a convenience. The script lives in `deploy/`, so a candidate's worktree holds a
    copy of it, and that copy is the one a run naturally calls — which made `parents[1]` resolve
    to the WORKTREE and gave three wrong answers at once on 2026-11-22: the next version was read
    from the candidate's own CHANGELOG (so the number it had just written became the number it
    was renumbered ABOVE, 0.588.0 → 0.589.0), the gate evidence was looked for under a path only
    the main checkout has, and `merge --ff-only` would have run in a worktree that already has
    the candidate branch checked out, i.e. merged nothing while reporting success.

    `--git-common-dir` is the one directory every worktree of a repository shares, so its parent
    is the main checkout by construction rather than by where anybody happened to stand.
    """
    common = git(start, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        raise MergeRefused(f"{start} is not inside a git repository")
    return Path(common).parent


def newest_version(changelog: str) -> tuple[int, int, int]:
    """The newest release in a CHANGELOG, as a tuple. Refuses a file with no release header."""
    found = HEADER.search(changelog)
    if not found:
        raise MergeRefused("CHANGELOG.md carries no '## [x.y.z] — YYYY-MM-DD' release header")
    return int(found.group(1)), int(found.group(2)), int(found.group(3))


def next_version(main_changelog: str) -> str:
    """The number a merge assigns: the next MINOR above what `main` has already shipped.

    The minor is this repo's unit of a user-facing revision, and a merge is always one of those.
    A candidate that numbered itself 0.581.0 while main shipped 0.583.0 gets 0.584.0 here —
    nobody renumbers by hand and nobody ships a repeated version.
    """
    major, minor, _patch = newest_version(main_changelog)
    return f"{major}.{minor + 1}.0"


def main_changelog(repo: Path) -> str:
    """`main`'s OWN CHANGELOG, read out of the ref rather than off any working tree.

    Reading `repo / "CHANGELOG.md"` was wrong even once `repo` is the main checkout: that file
    is whatever is checked out there, which a session mid-release has already edited. The ref is
    the only thing that says what main has SHIPPED, which is the question `next_version` asks.
    """
    return git(repo, "show", "main:CHANGELOG.md")


def renumber(changelog: str, init_py: str, version: str, today: str) -> tuple[str, str]:
    """Rewrite the candidate's TOP header and `__version__` to `version`.

    Only the top header moves: every header below it is history that main already carries, and
    the repo's own `test_changelog_versions_never_repeat_or_go_backwards` reads the whole file.
    """
    found = HEADER.search(changelog)
    if not found:
        raise MergeRefused("the candidate's CHANGELOG.md has no release header to renumber")
    rewritten = changelog[:found.start()] + f"## [{version}] — {today}" + changelog[found.end():]
    bumped, hits = re.subn(r'^__version__ = "[^"]+"$', f'__version__ = "{version}"',
                           init_py, flags=re.MULTILINE)
    if hits != 1:
        raise MergeRefused(f"src/rsched/__init__.py declares __version__ {hits} times, not once")
    return rewritten, bumped


def classify(files: list[str]) -> Delta:
    """Partition the files `main` gained that the candidate did not write.

    The order of the three classes is the order of their cost, and each test is about what a
    file can REACH: prose reaches no assertion; a leaf test module reaches only itself; a
    conftest, a shared helper or anything under `src/`, `static/` or the config reaches
    everything, so it owes the whole suite.
    """
    delta = Delta(files=sorted(files))
    if not files:
        return delta
    modules, prose_only = [], True
    for name in delta.files:
        if name.endswith(PROSE_SUFFIXES):
            continue
        prose_only = False
        if (name.startswith("tests/") and name.endswith(".py")
                and Path(name).name != "conftest.py" and name not in SHARED_TESTS):
            modules.append(name)
            continue
        delta.kind = "source"
        return delta
    if prose_only:
        return delta
    delta.kind, delta.modules = "tests", modules
    return delta


def gate_verdict(repo: Path, stamp: str, tree: Path) -> dict:
    """The recorded verdict of `stamp`, refusing anything that is not green over `tree`.

    A stamp covers BYTES, not a branch: its own result says which tree it ran over, and a
    verdict from another worktree says nothing about this one.
    """
    result = repo / ".audit-wt" / ".gate-evidence" / f"{stamp}.result.json"
    if not result.is_file():
        raise MergeRefused(f"gate stamp {stamp} has no recorded result at {result}")
    summary = json.loads(result.read_text(encoding="utf-8"))
    if summary.get("verdict") not in ("green", "flaky"):
        raise MergeRefused(f"gate {stamp} is {summary.get('verdict')!r}, not green: a merge "
                           f"lease is taken only by a candidate that passed")
    if Path(str(summary.get("root") or "")).resolve() != tree.resolve():
        raise MergeRefused(f"gate {stamp} ran over {summary.get('root')}, not {tree}")
    summary["stamp_written_at"] = result.stat().st_mtime
    return summary


def lease(wait_s: int = LEASE_WAIT_S):
    """Take the merge lease, or refuse after `wait_s`. Returns the held file object."""
    handle = LEASE.open("a+")
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise MergeRefused(f"another merge holds {LEASE} after {wait_s}s — read it for "
                                   f"who, and whether that process still exists") from None
            time.sleep(2)
        else:
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()} {time.time():.0f}\n")
            handle.flush()
            return handle


def plan(repo: Path, tree: Path, branch: str) -> dict:
    """What this candidate would land: its base, the delta it must answer for, its number."""
    git(repo, "fetch", "--quiet", "origin")
    base = git(repo, "merge-base", branch, "main")
    gained = [f for f in git(repo, "diff", "--name-only", f"{base}..main").splitlines() if f]
    mine = {f for f in git(repo, "diff", "--name-only", f"{base}..{branch}").splitlines() if f}
    delta = classify([f for f in gained if f not in mine])
    version = next_version(main_changelog(repo))
    return {"repo": str(repo), "tree": str(tree), "branch": branch, "base": base,
            "main": git(repo, "rev-parse", "main"), "branch_head": git(repo, "rev-parse", branch),
            "delta": {"kind": delta.kind, "files": delta.files, "modules": delta.modules,
                      "owes": delta.owes},
            "version": version}


def merge(repo: Path, tree: Path, branch: str, stamp: str, *, dry_run: bool = False) -> dict:
    """Rebase, renumber, fast-forward, push and read `origin` back — under the lease."""
    if tree.resolve() == repo.resolve():
        raise MergeRefused(
            f"the candidate tree and the main checkout are the same directory ({repo}): the "
            "rebase would rewrite main's own checkout and the fast-forward would merge a branch "
            "into the tree that already has it checked out — moving nothing while the push "
            "reports success. Build in a worktree (.audit-wt/<item>) and name it with --tree")
    # The stamp is read BEFORE anything asks git a question: it is the cheapest refusal and the
    # most common one, and a caller who named a stamp that does not exist should be told that
    # rather than something about the checkout it also got wrong.
    verdict = gate_verdict(repo, stamp, tree)
    on = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if on != "main":
        raise MergeRefused(f"the main checkout has {on} checked out, not main: the fast-forward "
                           "would land on the wrong branch")
    ahead = plan(repo, tree, branch)
    delta = classify(ahead["delta"]["files"])
    head_at = float(git(repo, "show", "-s", "--format=%ct", branch) or 0)
    covered = verdict["stamp_written_at"] > head_at
    if delta.kind != "prose" and not covered:
        raise MergeRefused(
            f"main gained {delta.kind} changes this candidate never gated ({len(delta.files)} "
            f"files) and {stamp} predates its head: owed is {delta.owes}")
    held = lease()
    try:
        git(tree, "rebase", "main")
        # Re-read under the lease: main can have moved between `plan` above and the lease below,
        # and the number that matters is the one true at the instant of the merge.
        version = next_version(main_changelog(repo))
        changelog, init = (tree / "CHANGELOG.md"), (tree / "src/rsched/__init__.py")
        today = time.strftime("%Y-%m-%d")
        new_log, new_init = renumber(changelog.read_text(encoding="utf-8"),
                                     init.read_text(encoding="utf-8"), version, today)
        if dry_run:
            return {**ahead, "version": version, "dry_run": True, "covered": covered}
        changelog.write_text(new_log, encoding="utf-8")
        init.write_text(new_init, encoding="utf-8")
        git(tree, "add", "--", "CHANGELOG.md", "src/rsched/__init__.py")
        git(tree, "commit", "-q", "-m", f"The merge assigns this candidate {version}\n\n"
            f"Merge-gate-stamp: {stamp}\nMerge-delta-class: {delta.kind}\n"
            f"Merge-delta-answer: {delta.owes}\n")
        git(repo, "merge", "--ff-only", branch)
        git(repo, "push", "--quiet", "origin", "main")
        git(repo, "fetch", "--quiet", "origin")
        landed, remote = git(repo, "rev-parse", "main"), git(repo, "rev-parse", "origin/main")
        if landed != remote:
            raise MergeRefused(f"main is {landed} and origin/main is {remote}: the push did not "
                               f"land what was merged")
        served = re.search(r'__version__ = "([^"]+)"',
                           (repo / "src/rsched/__init__.py").read_text(encoding="utf-8"))
        return {**ahead, "version": version, "commit": landed, "origin": remote,
                "served_version": served.group(1) if served else None,
                "delta": {"kind": delta.kind, "files": delta.files, "owes": delta.owes},
                "gate_stamp": stamp, "covered": covered}
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()


def main(argv: list[str] | None = None) -> int:
    """The CLI. `plan` reads; `merge` writes; both print JSON when asked to."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("verb", choices=("plan", "merge"))
    parser.add_argument("--repo", type=Path, default=None,
                        help="the MAIN checkout (default: derived from git's own common dir, so "
                             "running this from inside a candidate's worktree still merges into "
                             "the main checkout rather than into that worktree)")
    parser.add_argument("--tree", type=Path, help="the candidate's worktree (default: the "
                                                  "directory this was invoked from)")
    parser.add_argument("--branch", required=True)
    parser.add_argument("--gate-stamp", help="the green stamp covering the merged bytes")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        tree = (args.tree or Path.cwd()).resolve()
        repo = (args.repo or main_worktree(tree)).resolve()
        if args.verb == "plan":
            out = plan(repo, tree, args.branch)
        else:
            if not args.gate_stamp:
                parser.error("merge needs --gate-stamp: the lease is for green candidates only")
            out = merge(repo, tree, args.branch, args.gate_stamp, dry_run=args.dry_run)
    except MergeRefused as refused:
        print(f"merge-to-main: {refused}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2) if args.json else
          f"{args.verb}: {out.get('version')} · delta {out['delta']['kind']} · "
          f"owes {out['delta']['owes']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
