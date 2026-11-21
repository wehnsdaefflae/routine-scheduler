"""UNDO A RUN: revert what one run wrote to a versioned routine (D269).

A routine's working directory is a git repo autocommitted at every run end
(`engine/autocommit.py`), so the material to undo is a commit. (The unversioned case — a
conversation's artifact snapshots — is `engine/artsnap.py`.)

**Three load-bearing bounds:**

1. **Deliverables and the recipe — NEVER `state/`.** `state/` is what the NEXT run reads;
   reverting it makes the next run repeat work or act on a world that no longer exists. A run
   that touched only `state/` has nothing to undo, said as such.
2. **Refused when a LATER commit touched the same paths** — a repo has several writers, and a
   revert that silently loses a later edit is worse than none. The refusal names the commits.
3. **A RESUMED run is several commits** (one per leg); undoing the run reverts all of them,
   newest first, never only the last leg.

**It reverts, it never rewrites:** history keeps both the work and its undo, so the undo is
undoable and nothing pushed needs a force-update.

`plan()` is read-only: what an undo WOULD do and why it would be refused, read by the run page
before it offers the control.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..libgit import git

#: The one directory an undo never touches — a run's own forward state (plan, notes, phase,
#: obligations, the ledgers a later run reads). See bound 1 in the module docstring.
PROTECTED = "state/"


@dataclass
class Plan:
    """What an undo of one run would do. `ok` is False when it cannot or need not run.

    `commits` has NO DEFAULT on purpose: with `run_id` the only required field, every
    `SimpleNamespace(run_id=…)` in the suite would read as a stand-in for this class to
    `tests/test_double_conformance.py`.
    """

    run_id: str
    commits: list[str]                                    # newest first, the run's own
    paths: list[str] = field(default_factory=list)        # what would be reverted
    protected: list[str] = field(default_factory=list)    # touched but NOT reverted (state/)
    blocked_by: list[dict] = field(default_factory=list)  # later commits on the same paths
    ok: bool = False
    why: str = ""

    def as_dict(self) -> dict:
        return {"run_id": self.run_id, "commits": self.commits, "paths": self.paths,
                "protected": self.protected, "blocked_by": self.blocked_by,
                "ok": self.ok, "why": self.why}


def _lines(routine_dir: Path, *args: str) -> list[str]:
    """`git …` output as lines, or [] when the call fails. Every read here is best-effort: a
    repo that cannot be read must leave the control unoffered, never raise into a page.
    """
    try:
        r = git(routine_dir, *args, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []


def run_commits(routine_dir: Path, run_id: str) -> list[str]:
    """This run's own commits, NEWEST FIRST.

    Matched on the commit SUBJECT, which `engine/loopend` writes as `<run id>: <status>` (and
    `<run id>: recipe revision (<status>)` for a revision leg) — the one place a run's identity
    reaches its repo. A resumed run owns one per leg, which is why this returns a list.
    """
    rows = _lines(routine_dir, "log", "--format=%H%x09%s", "-n", "200")
    return [row.split("\t", 1)[0] for row in rows
            if len(parts := row.split("\t", 1)) == 2 and parts[1].startswith(f"{run_id}:")]


def _touched(routine_dir: Path, commit: str) -> list[str]:
    """The repo-relative paths one commit changed."""
    return _lines(routine_dir, "show", "--name-only", "--format=", commit)


def _later_commits_on(routine_dir: Path, newest: str, paths: list[str]) -> list[dict]:
    """Commits AFTER `newest` that touched any of `paths` — what makes an undo unsafe."""
    if not paths:
        return []
    rows = _lines(routine_dir, "log", "--format=%h%x09%s", f"{newest}..HEAD", "--", *paths)
    return [{"hash": p[0], "subject": p[1]} for row in rows
            if len(p := row.split("\t", 1)) == 2]


def plan(routine_dir: Path, run_id: str) -> Plan:
    """What an undo of `run_id` would revert — and why it would be refused.

    The run page reads this to decide whether to offer the control, so every "no" carries a
    sentence a person can act on rather than a disabled button with no reason.
    """
    out = Plan(run_id=run_id, commits=[])
    if not (Path(routine_dir) / ".git").is_dir():
        out.why = ("this routine's directory is not a git repo, so its runs are not versioned "
                   "— there is no commit to revert")
        return out
    out.commits = run_commits(routine_dir, run_id)
    if not out.commits:
        out.why = (f"no commit of this repo names {run_id} — the run wrote nothing the "
                   "autocommit took, or its commit did not land (a `commit_failed` health "
                   "event would say so)")
        return out
    touched: list[str] = []
    for commit in out.commits:
        touched += [p for p in _touched(routine_dir, commit) if p not in touched]
    out.protected = sorted(p for p in touched if p.startswith(PROTECTED))
    out.paths = sorted(p for p in touched if not p.startswith(PROTECTED))
    if not out.paths:
        out.why = (f"this run changed only {PROTECTED} files, which an undo never touches: "
                   "that is what the NEXT run reads, so reverting it would make the next run "
                   "repeat the work or act on a world that no longer exists")
        return out
    out.blocked_by = _later_commits_on(routine_dir, out.commits[0], out.paths)
    if out.blocked_by:
        named = ", ".join(f"{c['hash']} ({c['subject']})" for c in out.blocked_by[:5])
        out.why = (f"a later commit has touched the same files: {named}. Reverting now would "
                   "silently drop that work — revert those commits first, or undo by hand")
        return out
    out.ok = True
    out.why = (f"would revert {len(out.paths)} file(s) across {len(out.commits)} commit(s), "
               f"leaving {len(out.protected)} {PROTECTED} file(s) as they are")
    return out


def undo(routine_dir: Path, run_id: str) -> dict:
    """Perform the undo: revert the run's commits, keeping `state/` exactly as it is.

    `revert --no-commit` newest-first, then `checkout` of every protected path from `HEAD`
    (git reverts whole commits, so protected paths are restored after the fact), then ONE
    commit. On any failure, `reset --hard` leaves the repo exactly as it was.
    """
    ready = plan(routine_dir, run_id)
    if not ready.ok:
        return {"ok": False, **ready.as_dict()}
    try:
        for commit in ready.commits:
            r = git(routine_dir, "revert", "--no-commit", commit, timeout=60)
            if r.returncode != 0:
                git(routine_dir, "revert", "--quit", timeout=20)
                git(routine_dir, "reset", "--hard", "HEAD", timeout=30)
                return {"ok": False, **ready.as_dict(),
                        "why": f"git could not revert {commit[:8]}: "
                               f"{(r.stderr or r.stdout).strip()[:300]}"}
        for rel in ready.protected:
            git(routine_dir, "checkout", "HEAD", "--", rel, timeout=20)
        from ..libgit import commit as commit_all

        landed = commit_all(
            routine_dir,
            f"undo {run_id}: revert its {len(ready.paths)} file(s), state/ untouched",
            routines_home=None, run_id=run_id)
    except (OSError, subprocess.TimeoutExpired) as exc:
        git(routine_dir, "revert", "--quit", timeout=20)
        git(routine_dir, "reset", "--hard", "HEAD", timeout=30)
        return {"ok": False, **ready.as_dict(), "why": f"the undo failed: {exc}"}
    # The STATUS, never the object: `libgit.Commit` is a dataclass, so `bool(landed)` is True
    # even for `failed` and `clean`.
    if landed.status != "committed":
        git(routine_dir, "reset", "--hard", "HEAD", timeout=30)
        return {"ok": False, **ready.as_dict(),
                "why": (landed.describe() or
                        f"the revert left nothing to commit (git said {landed.status!r}) — "
                        "the working tree is unchanged")}
    return {"ok": True, **ready.as_dict(), "why": ready.why, "committed": True}
