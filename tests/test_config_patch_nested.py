"""A `config_patch`'s INNER keys, judged where the record is written (F737).

`patch_shape` judged the patch's own keys, so `{"budgets": {…}}` passed it whatever the mapping
held. Measured 2026-10-20: `llmsectest-weekday` filed
`{"budgets": {"max_turns": 600, "max_minutes": 900}}` — a budget name this repo has never had —
the apply answered `budgets: Value error, unknown budget max_minutes`, and the operator pressed
`approve & apply` FOUR times (`.ui-traces/20261020.jsonl`, 04:39:18-04:39:39) before typing the
toast text back to the run by hand. That is the dead apply button F537 closed, one level down.

The anti-drift guard is `test_every_budget_the_loader_keeps_is_accepted_at_filing`: the filing
check reads the loader's own vocabulary, so a new budget cannot be added and then refused here.
"""

from __future__ import annotations

import pytest

from conftest import finish
from helpers import server_for, transcript_events
from rsched.config.base import DEFAULT_BUDGETS, EFFORT_LEVELS, MODEL_KINDS, ROUTINE_MODEL_ROLES
from rsched.engine.config_bridge import patch_nested
from rsched.engine.runtime import run_routine

TS = "20261020-070000"


def test_the_measured_case_is_refused_and_names_the_key_and_the_vocabulary():
    """The exact patch of 2026-10-20, and the refusal has to teach the right name."""
    why = patch_nested({"budgets": {"max_turns": 600, "max_minutes": 900}})
    assert "max_minutes" in why
    assert "max_wall_clock_min" in why          # the name he actually wanted, offered
    assert "read and not take" in why           # the standard it is enforcing


def test_a_well_formed_patch_passes_untouched():
    assert patch_nested({"budgets": {"max_turns": 600, "max_wall_clock_min": 900}}) == ""
    assert patch_nested({"routine": "suedlink-wlf", "budgets": {"max_cost": -1}}) == ""
    assert patch_nested({"models": {"main": "some-catalog-name"}}) == ""
    assert patch_nested({"efforts": {"main": "high"}}) == ""
    assert patch_nested({"efforts": {"main": None}}) == ""      # null = provider's default
    assert patch_nested({}) == ""
    assert patch_nested(None) == ""


def test_a_mapping_field_that_is_not_a_mapping_is_left_to_the_apply():
    """Shape errors are the patch models' job; this check is about VOCABULARY, and must not
    crash on a value it was not asked about."""
    assert patch_nested({"budgets": 900}) == ""
    assert patch_nested({"budgets": None}) == ""
    assert patch_nested({"efforts": ["main"]}) == ""


def test_model_roles_and_effort_levels_are_judged_too():
    assert "decision_media" in patch_nested({"models": {"main_model": "x"}})
    assert "mian" in patch_nested({"efforts": {"mian": "high"}})
    bad_level = patch_nested({"efforts": {"main": "highest"}})
    assert "highest" in bad_level and "xhigh" in bad_level
    # a DECISION role is a models: role but not an efforts: one — efforts takes chat roles only
    assert patch_nested({"models": {"decision": "jev"}}) == ""
    assert "decision" in patch_nested({"efforts": {"decision": "high"}})


@pytest.mark.parametrize("name", sorted(DEFAULT_BUDGETS))
def test_every_budget_the_loader_keeps_is_accepted_at_filing(name):
    """THE anti-drift pin: the filing check reads `DEFAULT_BUDGETS`, never a copy of it, so a
    budget added to the loader is proposable the same day."""
    assert patch_nested({"budgets": {name: 1}}) == ""


@pytest.mark.parametrize("role", sorted(ROUTINE_MODEL_ROLES))
def test_every_model_role_the_config_declares_is_accepted_at_filing(role):
    assert patch_nested({"models": {role: "a-name"}}) == ""


@pytest.mark.parametrize("level", sorted(EFFORT_LEVELS))
@pytest.mark.parametrize("role", sorted(MODEL_KINDS))
def test_every_effort_role_and_level_pair_is_accepted_at_filing(role, level):
    assert patch_nested({"efforts": {role: level}}) == ""


def test_the_check_is_wired_into_the_ask_so_no_record_is_filed(make_routine, scripted):
    """End to end through `ask_user`: the run is told at the turn that files the ask, and the
    Decisions page never gets a button that 422s. Unwired, every assertion above still passes.
    """
    d = make_routine(slug="budgeter")
    scripted([
        {"say": "raise my turns", "kind": "ask_user", "mode": "deferred",
         "question": "Raise the budget?",
         "config_patch": {"budgets": {"max_turns": 600, "max_minutes": 900}}},
        finish(),
    ])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    assert not list((d / "questions" / "pending").glob("*.json"))
    obs = next(e for e in transcript_events(run_dir)
               if e["type"] == "observation" and e["payload"].get("error"))
    assert "max_minutes" in obs["payload"]["error"]
    assert "max_wall_clock_min" in obs["payload"]["error"]
