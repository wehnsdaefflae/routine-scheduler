"""Recipe-version identity + rollback (rsched.recipes) against REAL git repos: the
version is the last recipe-touching commit (never the state-noise HEAD), dirty recipe
edits are snapshotted recipe-only at run start, and a revert restores exactly the recipe
set — config and state are never touched.
"""


import pytest

from conftest import git_in
from rsched.recipes import (
    RecipeError,
    current_recipe_commit,
    recipe_log,
    revert_recipe,
)


def _git(d, *args, date: str = ""):
    return git_in(d, *args, date=date).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "routines" / "gitr"
    (d / "stages").mkdir(parents=True)
    (d / "state").mkdir()
    (d / "main.md").write_text("# main v1\n", encoding="utf-8")
    (d / "stages" / "scan.md").write_text("# scan v1\n", encoding="utf-8")
    (d / "state" / "notes.md").write_text("notes\n", encoding="utf-8")
    (d / "routine.yaml").write_text("slug: gitr\n", encoding="utf-8")
    _git(d, "init", "-q")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "scaffold", date="2026-07-01T10:00:00+00:00")
    return d


def test_unversioned_dir_degrades(tmp_path):
    d = tmp_path / "conv"
    d.mkdir()
    assert current_recipe_commit(d) is None
    assert recipe_log(d) == []
    with pytest.raises(RecipeError, match="no git history"):
        revert_recipe(d, "abc123")


def test_recipe_commit_ignores_state_noise(repo):
    """The version key is the last RECIPE-touching commit: the engine's run-end state
    autocommits move HEAD every run but must not open a new health bucket."""
    v1 = current_recipe_commit(repo)
    assert v1 == _git(repo, "rev-parse", "HEAD")
    (repo / "state" / "notes.md").write_text("more notes\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "gitr:20260702-070000: ok")   # a run's autocommit
    assert _git(repo, "rev-parse", "HEAD") != v1
    assert current_recipe_commit(repo) == v1                  # bucket unchanged


def test_dirty_recipe_snapshotted_recipe_only(repo):
    """The improver's uncommitted recipe edit is committed at the NEXT run's start as a
    recipe-only snapshot — state dirt stays out of it (and stays dirty)."""
    v1 = current_recipe_commit(repo)
    (repo / "stages" / "scan.md").write_text("# scan v2\n", encoding="utf-8")
    (repo / "state" / "notes.md").write_text("dirty state\n", encoding="utf-8")
    v2 = current_recipe_commit(repo)
    assert v2 != v1
    shown = _git(repo, "show", "--name-only", "--format=%s", v2)
    assert "recipe: pre-run snapshot" in shown
    assert "stages/scan.md" in shown
    assert "state/notes.md" not in shown
    status = _git(repo, "status", "--porcelain")
    assert "state/notes.md" in status                         # left for the run-end commit


def test_recipe_log_series(repo):
    (repo / "main.md").write_text("# main v2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "recipe: sharpen the scan stage",
         date="2026-07-05T10:00:00+00:00")
    log = recipe_log(repo)
    assert [e["subject"] for e in log] == ["recipe: sharpen the scan stage", "scaffold"]
    assert log[0]["commit"] == current_recipe_commit(repo)
    assert log[0]["date"].startswith("2026-07-05")
    assert log[0]["short"] and log[0]["commit"].startswith(log[0]["short"])


def test_revert_restores_pre_change_recipe_only(repo):
    """Reverting a recipe change restores the whole recipe set as of just before it —
    including deleting a stage the change ADDED — while config and state keep their
    post-change content."""
    (repo / "main.md").write_text("# main v2\n", encoding="utf-8")
    (repo / "stages" / "extra.md").write_text("# extra\n", encoding="utf-8")
    (repo / "routine.yaml").write_text("slug: gitr\nenabled: true\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "recipe: v2 + config edit")
    bad = current_recipe_commit(repo)

    result = revert_recipe(repo, bad)
    assert result["reverted"] == bad
    assert (repo / "main.md").read_text(encoding="utf-8") == "# main v1\n"
    assert not (repo / "stages" / "extra.md").exists()
    # NOT recipe: the config edit survives the revert untouched
    assert "enabled: true" in (repo / "routine.yaml").read_text(encoding="utf-8")
    assert (repo / "state" / "notes.md").read_text(encoding="utf-8") == "notes\n"
    # the revert IS the new recipe version — health tracking continues from it
    assert current_recipe_commit(repo) == result["new_commit"]
    assert recipe_log(repo)[0]["subject"].startswith("recipe: revert to pre-")


def test_revert_guards(repo):
    root = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(RecipeError, match="unknown commit"):
        revert_recipe(repo, "0000000000000000000000000000000000000000")
    with pytest.raises(RecipeError, match="not a commit hash"):
        revert_recipe(repo, "HEAD^{}")
    with pytest.raises(RecipeError, match="first commit"):
        revert_recipe(repo, root)
    (repo / "state" / "notes.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "state only")
    with pytest.raises(RecipeError, match="touched no recipe file"):
        revert_recipe(repo, _git(repo, "rev-parse", "HEAD"))


def test_revert_when_already_matching_is_refused(repo):
    (repo / "main.md").write_text("# main v2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "recipe: v2")
    bad = current_recipe_commit(repo)
    revert_recipe(repo, bad)
    # reverting the ORIGINAL change again: the recipe already matches its pre-state
    with pytest.raises(RecipeError, match="already matches"):
        revert_recipe(repo, bad)
    assert (repo / "main.md").read_text(encoding="utf-8") == "# main v1\n"
