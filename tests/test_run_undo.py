"""UNDO A RUN — D269's second increment, over a real git repo.

The order (`docs/designs.md`, decided 2026-10-08): *"'Undo this run' on the RUN PAGE, for a
versioned routine: revert that run's autocommit limited to its deliverables and the recipe
(never `state/`), refused when a later commit touched the same paths."*

Each of those clauses is a test here, because each one is what makes the control safe to offer:

1. the run's deliverables and recipe go back;
2. **`state/` does not** — that is what the NEXT run reads, and reverting it would make the
   next run repeat the work or act on a world that no longer exists;
3. a LATER commit on the same paths REFUSES the undo, naming what is in the way — a revert that
   silently drops someone else's later edit is worse than no undo;
4. a RESUMED run is several commits and all of them go, newest first;
5. it REVERTS rather than rewrites, so the undo is itself a commit (and itself undoable).

The repo is real, not a stand-in: the whole subject is what git does with a revert, and a
mocked `git` would prove only that this module calls the function I wrote.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rsched.engine import rundo
from rsched.engine.autocommit import autocommit

RUN = "r:20261111-140003"
LATER = "r:20261111-150000"


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                         check=True, timeout=30)
    return out.stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "routine"
    (repo / "state").mkdir(parents=True)
    (repo / "artifacts").mkdir()
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "main.md").write_text("# recipe v1\n", encoding="utf-8")
    (repo / "state" / "plan.md").write_text("step 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def _run_wrote(repo: Path, run_id: str, *, deliverable: str = "", recipe: str = "",
               state: str = "") -> None:
    """One run's work, committed exactly as the engine's own autocommit commits it."""
    if deliverable:
        (repo / "artifacts" / "report.md").write_text(deliverable, encoding="utf-8")
    if recipe:
        (repo / "main.md").write_text(recipe, encoding="utf-8")
    if state:
        (repo / "state" / "plan.md").write_text(state, encoding="utf-8")
    autocommit(repo, f"{run_id}: ok", routines_home=None, run_id=run_id)


def test_the_runs_deliverable_and_recipe_go_back(tmp_path):
    repo = _repo(tmp_path)
    _run_wrote(repo, RUN, deliverable="the run's output\n", recipe="# recipe v2\n")

    ready = rundo.plan(repo, RUN)
    assert ready.ok is True, ready.why
    assert ready.paths == ["artifacts/report.md", "main.md"], ready.paths
    out = rundo.undo(repo, RUN)

    assert out["ok"] is True, out["why"]
    assert (repo / "main.md").read_text(encoding="utf-8") == "# recipe v1\n"
    assert not (repo / "artifacts" / "report.md").exists(), "a file the run CREATED must go"


def test_state_is_never_reverted(tmp_path):
    """The bound that matters most: `state/` is the next run's input, not this run's output."""
    repo = _repo(tmp_path)
    _run_wrote(repo, RUN, deliverable="out\n", state="step 2 — done by this run\n")

    ready = rundo.plan(repo, RUN)
    assert ready.protected == ["state/plan.md"], ready.protected
    assert "state/plan.md" not in ready.paths
    assert rundo.undo(repo, RUN)["ok"] is True

    assert (repo / "state" / "plan.md").read_text(encoding="utf-8") == \
        "step 2 — done by this run\n", "the run's forward state must survive its own undo"
    assert not (repo / "artifacts" / "report.md").exists()


def test_a_run_that_only_touched_state_has_nothing_to_undo(tmp_path):
    """Said as such, rather than performed as an empty revert that reports success."""
    repo = _repo(tmp_path)
    _run_wrote(repo, RUN, state="only state\n")
    ready = rundo.plan(repo, RUN)
    assert ready.ok is False
    assert "changed only state/ files" in ready.why and "the NEXT run reads" in ready.why
    assert rundo.undo(repo, RUN)["ok"] is False


def test_a_later_commit_on_the_same_paths_refuses_the_undo(tmp_path):
    """A revert that silently drops a later edit is worse than no undo. The refusal NAMES it."""
    repo = _repo(tmp_path)
    _run_wrote(repo, RUN, recipe="# recipe v2\n")
    (repo / "main.md").write_text("# recipe v3, edited by hand\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "operator: fix the recipe by hand")

    ready = rundo.plan(repo, RUN)
    assert ready.ok is False
    assert "a later commit has touched the same files" in ready.why
    assert "operator: fix the recipe by hand" in ready.why
    assert rundo.undo(repo, RUN)["ok"] is False
    assert (repo / "main.md").read_text(encoding="utf-8") == "# recipe v3, edited by hand\n"


def test_a_later_commit_on_other_paths_does_not_block(tmp_path):
    """The check is per PATH, not per time: an unrelated later commit is not in the way."""
    repo = _repo(tmp_path)
    _run_wrote(repo, RUN, recipe="# recipe v2\n")
    (repo / "artifacts" / "unrelated.md").write_text("someone else's file\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"{LATER}: ok")

    assert rundo.plan(repo, RUN).ok is True
    assert rundo.undo(repo, RUN)["ok"] is True
    assert (repo / "main.md").read_text(encoding="utf-8") == "# recipe v1\n"
    assert (repo / "artifacts" / "unrelated.md").exists(), "the later work must survive"


def test_every_leg_of_a_resumed_run_is_undone(tmp_path):
    """A resumed run autocommits per leg, so "the run" is several commits. Undoing only the
    last would leave it half-undone — the state nobody can reason about.
    """
    repo = _repo(tmp_path)
    _run_wrote(repo, RUN, deliverable="leg 1\n")
    _run_wrote(repo, RUN, deliverable="leg 2\n", recipe="# recipe v2\n")

    ready = rundo.plan(repo, RUN)
    assert len(ready.commits) == 2, ready.commits
    assert rundo.undo(repo, RUN)["ok"] is True
    assert not (repo / "artifacts" / "report.md").exists()
    assert (repo / "main.md").read_text(encoding="utf-8") == "# recipe v1\n"


def test_the_undo_is_a_commit_and_leaves_the_runs_own_history(tmp_path):
    """It reverts, never rewrites: the run's work stays in the history and the undo is itself
    undoable — and nothing already pushed has to be force-updated.
    """
    repo = _repo(tmp_path)
    _run_wrote(repo, RUN, deliverable="out\n")
    before = _git(repo, "rev-parse", "HEAD").strip()

    assert rundo.undo(repo, RUN)["ok"] is True
    log = _git(repo, "log", "--format=%s", "-n", "3").splitlines()
    assert log[0].startswith(f"undo {RUN}:"), log
    assert f"{RUN}: ok" in log[1], "the run's own commit is still in the history"
    assert _git(repo, "rev-parse", f"{before}^{{commit}}").strip() == before


def test_an_unversioned_routine_says_so_instead_of_failing(tmp_path):
    plain = tmp_path / "plain"
    (plain / "state").mkdir(parents=True)
    ready = rundo.plan(plain, RUN)
    assert ready.ok is False and "not a git repo" in ready.why


def test_a_run_with_no_commit_of_its_own_says_so(tmp_path):
    repo = _repo(tmp_path)
    ready = rundo.plan(repo, "r:19990101-000000")
    assert ready.ok is False and "no commit of this repo names" in ready.why


@pytest.mark.parametrize("subject", [f"{RUN}: ok", f"{RUN}: recipe revision (ok)"])
def test_both_autocommit_subjects_are_recognised(tmp_path, subject):
    """`engine/loopend` writes one of two forms; a run id reaches its repo nowhere else."""
    repo = _repo(tmp_path)
    (repo / "main.md").write_text("# v2\n", encoding="utf-8")
    autocommit(repo, subject, routines_home=None, run_id=RUN)
    assert rundo.run_commits(repo, RUN), subject
