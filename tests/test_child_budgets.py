"""The child-run budget keys: `max_child_runs` / `max_child_depth`, and the migration that
carries every existing config over (D241 increment 2).

The keys are RENAMED, not aliased — an alias is how two names for one thing survive
indefinitely, which is the whole subject of D241. That makes the migration load-bearing rather
than tidy, and the test below that matters most is the one which shows WHY: an unmigrated
`max_subruns` does not fail. `config/routine_scrub` drops an unknown budget key with a problem
line, so a ceiling the operator deliberately lowered would silently return to the default of 8,
with nothing but a loader warning to show for it.
"""

from __future__ import annotations

import pytest
import yaml

from conftest import make_test_server
from rsched import migrate_child_budgets
from rsched.config import DEFAULT_BUDGETS, load_routine
from rsched.engine.budgets_config import Budgets

OLD = ("max_subruns", "max_subrun_depth")
NEW = ("max_child_runs", "max_child_depth")


# ------------------------------------------------------------------------------- the keys

@pytest.mark.parametrize("key", NEW)
def test_the_new_keys_are_the_defaults(key):
    assert key in DEFAULT_BUDGETS


@pytest.mark.parametrize("key", OLD)
def test_no_old_key_survives_as_a_default_or_an_alias(key):
    assert key not in DEFAULT_BUDGETS


def test_the_budgets_dataclass_carries_the_new_names():
    b = Budgets.from_config(dict(DEFAULT_BUDGETS))
    assert b.max_child_runs == DEFAULT_BUDGETS["max_child_runs"]
    assert b.max_child_depth == DEFAULT_BUDGETS["max_child_depth"]
    assert not hasattr(b, "max_subruns")


# --------------------------------------------------------------------------- the migration

def _write_routine(tmp_path, slug, budgets_yaml):
    d = tmp_path / "routines" / slug
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(
        "name: Demo\nschedule: {}\nbudgets:\n" + budgets_yaml, encoding="utf-8")
    return d / "routine.yaml"


def test_an_unmigrated_ceiling_would_silently_revert_to_the_default(tmp_path):
    """WHY the migration exists, stated as a test rather than as a comment: the old key does
    not fail. It is DROPPED, and the run takes the default — so an operator who lowered the
    ceiling to 2 would get 8 and nothing would say so."""
    path = _write_routine(tmp_path, "unmigrated", "  max_subruns: 2\n")
    cfg, problems = load_routine(path.parent)
    assert cfg.budgets["max_child_runs"] == DEFAULT_BUDGETS["max_child_runs"] == 8
    assert "max_subruns" not in cfg.budgets
    assert any("max_subruns" in p for p in problems), problems


def test_the_migration_carries_both_keys_and_keeps_the_values(tmp_path):
    server = make_test_server(tmp_path)
    path = _write_routine(tmp_path, "demo", "  max_subruns: 2\n  max_subrun_depth: 1\n")

    record = migrate_child_budgets.run_migration(server)

    assert record["files"] == 1
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["budgets"] == {"max_child_runs": 2, "max_child_depth": 1}
    cfg, problems = load_routine(path.parent)
    assert cfg.budgets["max_child_runs"] == 2 and cfg.budgets["max_child_depth"] == 1
    assert not [p for p in problems if "max_subrun" in p]


def test_the_migration_keeps_the_operators_comments_and_ordering(tmp_path):
    """A routine.yaml is the operator's own document. A load/dump round trip would discard
    every comment in it, so the rewrite is textual."""
    server = make_test_server(tmp_path)
    d = tmp_path / "routines" / "commented"
    (d / "state").mkdir(parents=True)
    (d / "routine.yaml").write_text(
        "name: Demo\nschedule: {}\n"
        "budgets:\n"
        "  # two children is deliberate: this box has 8 GB\n"
        "  max_subruns: 2\n"
        "  max_turns: 40\n", encoding="utf-8")

    migrate_child_budgets.run_migration(server)

    text = (d / "routine.yaml").read_text(encoding="utf-8")
    assert "# two children is deliberate: this box has 8 GB" in text
    assert "  max_child_runs: 2\n  max_turns: 40\n" in text, text


def test_the_migration_runs_once(tmp_path):
    server = make_test_server(tmp_path)
    _write_routine(tmp_path, "demo", "  max_subruns: 2\n")
    assert migrate_child_budgets.run_migration(server)["files"] == 1
    assert migrate_child_budgets.run_migration(server) == {}


def test_a_config_without_the_keys_is_left_alone(tmp_path):
    server = make_test_server(tmp_path)
    path = _write_routine(tmp_path, "plain", "  max_turns: 40\n")
    before = path.read_text(encoding="utf-8")

    record = migrate_child_budgets.run_migration(server)

    assert record["files"] == 0
    assert path.read_text(encoding="utf-8") == before


def test_a_mention_in_a_comment_is_not_a_key(tmp_path):
    """The rewrite matches a `key:` line, so prose is not rewritten — a comment recalling the
    old name stays readable, and the record names every file that changed so a stale mention
    is findable by hand."""
    server = make_test_server(tmp_path)
    d = tmp_path / "routines" / "mentions"
    (d / "state").mkdir(parents=True)
    (d / "routine.yaml").write_text(
        "name: Demo\nschedule: {}\nbudgets:\n"
        "  # raised from max_subruns 8 after the 2026-10 review\n"
        "  max_subruns: 3\n", encoding="utf-8")

    migrate_child_budgets.run_migration(server)

    text = (d / "routine.yaml").read_text(encoding="utf-8")
    assert "# raised from max_subruns 8 after the 2026-10 review" in text
    assert "  max_child_runs: 3" in text
