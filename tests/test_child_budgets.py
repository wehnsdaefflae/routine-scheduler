"""The child-run budget keys: `max_child_runs` / `max_child_depth`, and the migration that
carries every existing config over (D241 increment 2).
"""

from __future__ import annotations

import pytest

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


