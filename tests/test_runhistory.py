"""The instance's history read back from git (rsched/runhistory.py) — what the run-record backfill
rebuilds a past run's fingerprint from: AS OF an instant, which release `main` held, which
routine.yaml and recipe version were committed, which text a library rule had."""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import git_in
from rsched import runhistory

D1, D2, D3 = "2026-08-01T10:00:00+00:00", "2026-08-02T10:00:00+00:00", "2026-08-03T10:00:00+00:00"


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _version_commit(repo, version: str, date: str) -> None:
    f = repo / runhistory.VERSION_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    git_in(repo, "add", "-A", date=date)
    git_in(repo, "commit", "-qm", version, date=date)


def test_a_release_reached_the_instance_when_main_moved_not_when_it_was_written(tmp_path):
    """A release written on a branch on day 2 and fast-forwarded on day 3 ran from day 3."""
    repo = tmp_path / "src"
    repo.mkdir()
    git_in(repo, "init", "-q", "-b", "main")
    _version_commit(repo, "0.1.0", D1)
    git_in(repo, "checkout", "-qb", "feature")
    _version_commit(repo, "0.2.0", D2)
    git_in(repo, "checkout", "-q", "main")
    git_in(repo, "merge", "-q", "--ff-only", "feature", date=D3)
    engine = runhistory.engine_timeline(repo)
    assert engine.at(_at("2026-07-31T00:00:00+00:00")) is None          # before any: unknown
    assert engine.at(_at("2026-08-02T12:00:00+00:00")) == "0.1.0"       # written, not yet merged
    assert engine.at(_at("2026-08-03T12:00:00+00:00")) == "0.2.0"


def test_without_a_reflog_the_version_commits_dates_stand_in(tmp_path):
    repo = tmp_path / "src"
    repo.mkdir()
    git_in(repo, "init", "-q", "-b", "main")
    _version_commit(repo, "0.1.0", D1)
    _version_commit(repo, "0.2.0", D2)
    for log in (repo / ".git" / "logs").rglob("*"):
        if log.is_file():
            log.unlink()
    engine = runhistory.engine_timeline(repo)
    assert engine.at(_at("2026-08-01T12:00:00+00:00")) == "0.1.0"
    assert engine.at(_at("2026-08-02T12:00:00+00:00")) == "0.2.0"


def test_a_routines_config_and_recipe_are_read_as_committed_then(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    git_in(d, "init", "-q")
    (d / "routine.yaml").write_text("slug: r\nbudgets: {max_turns: 10}\n", encoding="utf-8")
    (d / "main.md").write_text("# v1\n", encoding="utf-8")
    git_in(d, "add", "-A", date=D1)
    git_in(d, "commit", "-qm", "one", date=D1)
    v1 = git_in(d, "rev-parse", "HEAD").stdout.strip()
    (d / "tuning.yaml").write_text("deliberation: deliberate\n", encoding="utf-8")
    (d / "routine.yaml").write_text("slug: r\nbudgets: {max_turns: 20}\n", encoding="utf-8")
    git_in(d, "add", "-A", date=D2)
    git_in(d, "commit", "-qm", "two", date=D2)
    (d / "state.txt").write_text("run output\n", encoding="utf-8")         # neither config nor recipe
    git_in(d, "add", "-A", date=D3)
    git_in(d, "commit", "-qm", "run", date=D3)
    config, recipe = runhistory.routine_timeline(d), runhistory.recipe_timeline(d)
    first = config.at(_at("2026-08-01T12:00:00+00:00"))
    assert isinstance(first, runhistory.RoutineState) and "10" in first.routine_yaml
    assert first.tuning_yaml is None
    later = config.at(_at("2026-08-04T00:00:00+00:00"))
    assert isinstance(later, runhistory.RoutineState) and later.tuning_yaml
    assert recipe.at(_at("2026-08-01T12:00:00+00:00")) == v1
    assert recipe.at(_at("2026-08-04T00:00:00+00:00")) != v1             # tuning.yaml is recipe
    assert len(recipe.points) == 2                                      # the run commit is not


def test_a_rules_text_is_hashed_as_a_live_run_hashes_it_and_a_deleted_rule_is_empty(tmp_path):
    lib = tmp_path / "lib"
    (lib / "rules").mkdir(parents=True)
    git_in(lib, "init", "-q")
    (lib / "rules" / "x.md").write_text("# rule: x — one\n", encoding="utf-8")
    git_in(lib, "add", "-A", date=D1)
    git_in(lib, "commit", "-qm", "x", date=D1)
    git_in(lib, "rm", "-q", "rules/x.md", date=D2)
    git_in(lib, "commit", "-qm", "drop x", date=D2)
    rule = runhistory.rule_timeline(lib, "x", lambda text: f"h:{text.strip()}")
    assert rule.at(_at("2026-08-01T12:00:00+00:00")) == "h:# rule: x — one"
    assert rule.at(_at("2026-08-02T12:00:00+00:00")) == ""
    assert runhistory.rule_timeline(lib, "never", str).at(datetime.now(UTC)) is None
