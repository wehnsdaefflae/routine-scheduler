"""UNDO A RUN: revert what one run wrote to a versioned routine — D269's second increment.

The order (decided 2026-10-08, `docs/designs.md`): *"'Undo this run' on the RUN PAGE, for a
versioned routine: revert that run's autocommit limited to its deliverables and the recipe
(never `state/`), refused when a later commit touched the same paths."* The first increment
shipped in 0.530.0 for the unversioned case — a conversation's per-reply artifact snapshots and
the ⟲ rewind that restores them (`engine/artsnap.py`). This is the versioned half, and it is a
different mechanism for a reason: a routine's working directory IS a git repo, autocommitted at
every run end (`engine/autocommit.py`), so the material to undo is a commit rather than a
snapshot.

**Three bounds, each from the order and each load-bearing:**

1. **Deliverables and the recipe — NEVER `state/`.** `state/` is where a run writes what the
   NEXT run reads: its plan, its notes, its ledger of what it already did. Reverting that does
   not undo work, it makes the next run repeat it or act on a world that no longer exists. So
   the revert is restricted to paths outside `state/`, and a run whose commit touched only
   `state/` has nothing to undo — said as such, rather than performed as an empty revert.
2. **Refused when a LATER commit touched the same paths.** A revert that silently loses someone
   else's later edit is worse than no undo at all, and a routine's repo has several writers (its
   own runs, `routine-improver`'s recipe edits, the operator by hand). The refusal NAMES the
   commits in the way, so the reader can decide instead of guessing.
3. **A RESUMED run is several commits.** Every leg autocommits, so a run id can own two, three
   or ten commits (`<run id>: ok`, and `<run id>: recipe revision (ok)` for a revision leg).
   Undoing "the run" means undoing all of them, newest first — a revert of only the last leg
   would leave the run half-undone, which is the state nobody can reason about.

**It reverts, it never rewrites.** `git revert --no-commit` plus one new commit: the history
keeps both the run's work and its undo, so the undo itself is undoable and nothing that was
pushed has to be force-updated. A rewrite would also break the library mirror, which pushes
these repos.

Read-only by default: `plan()` answers what an undo WOULD do and why it would be refused, which
is what the run page's control reads before it offers the button at all.
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

    `commits` has NO DEFAULT on purpose, and the reason is a repo policy rather than a design
    preference: `tests/test_double_conformance.py` refuses a `SimpleNamespace` stand-in for a
    dataclass whose required fields the namespace already supplies (F766 — a hand-built surface
    silently lacks the field production adds tomorrow). With `run_id` as the ONLY required
    field, this class made every pre-existing `SimpleNamespace(run_id=…)` in the suite read as a
    stand-in FOR IT — five of them, in test files with nothing to do with undoing a run. A
    second required field removes the collision, and asking every caller to say which commits it
    is talking about is no loss: a plan with no commit list is not a plan.
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
    """Commits AFTER `newest` that touched any of `paths` — what makes an undo unsafe.

    Asked per path set rather than per path, because the question is "has anything landed on
    this material since", and one answer is what the refusal needs to name.
    """
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

    The sequence is `revert --no-commit` newest-first, then `checkout` of every protected path
    from `HEAD` (undoing the revert's effect on it), then ONE commit. Done in that order because
    git reverts a whole commit: there is no "revert these paths of it", so the protected paths
    are restored after the fact rather than excluded up front — and if anything goes wrong in
    between, the final `reset --hard` leaves the repo exactly as it was.
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
    # even for `failed` and for `clean` — a truthiness test here would have reported a commit
    # that never landed as a completed undo, which is the one lie this control may not tell.
    if landed.status != "committed":
        git(routine_dir, "reset", "--hard", "HEAD", timeout=30)
        return {"ok": False, **ready.as_dict(),
                "why": (landed.describe() or
                        f"the revert left nothing to commit (git said {landed.status!r}) — "
                        "the working tree is unchanged")}
    return {"ok": True, **ready.as_dict(), "why": ready.why, "committed": True}
