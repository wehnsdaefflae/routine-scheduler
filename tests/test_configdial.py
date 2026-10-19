"""The `config_confirm` dial's FIELD POLICY (rsched/configdial.py) — D191.

The dial decides whether a routine's own `config_patch` needs the operator's click. This file
pins the vocabulary and the three sets, because every other half of the feature (the apply
gate, the UI ladder, the docs) is derived from them: a set that drifts here is an autonomous
write nobody authorized.

Two properties are load-bearing and are asserted at every level:

* a patch naming ANOTHER routine is a decision whatever the dial says, and
* the SEALED keys are refused whatever the dial says — the exclusion is absolute, enforced
  where the apply happens and never by the calling run's restraint.
"""

from __future__ import annotations

import pytest

from rsched import configdial
from rsched.configflow import ROUTINE_PATCH_FIELDS


def test_the_ladder_is_the_dials_three_levels():
    assert configdial.LEVELS == ("always", "own_fields", "never")
    assert configdial.DEFAULT_LEVEL == "always"


def test_every_declared_field_is_a_real_patch_field():
    """The allow-list is DERIVED from the apply surface's vocabulary. A name that is not a
    `RoutinePatch` field would be an allowance for a key the apply route refuses — autonomy
    that reads as granted and 422s."""
    assert configdial.OWN_FIELDS <= ROUTINE_PATCH_FIELDS
    assert configdial.SEALED <= ROUTINE_PATCH_FIELDS
    assert not configdial.OWN_FIELDS & configdial.SEALED


def test_the_operators_five_exclusions_are_in_the_sealed_set():
    """His words, 2026-10-18: keep `permissions`, `capabilities`, `fs_read_roots`,
    `fs_write_roots` and `models.*` out of any autonomous set."""
    for key in ("permissions", "capabilities", "fs_read_roots", "fs_write_roots", "models"):
        assert key in configdial.SEALED, key


def test_allowed_fields_widens_from_nothing_to_everything_unsealed():
    assert configdial.allowed_fields("always") == frozenset()
    assert configdial.allowed_fields("own_fields") == configdial.OWN_FIELDS
    assert configdial.allowed_fields("never") == ROUTINE_PATCH_FIELDS - configdial.SEALED
    # an unknown dial value is read as the default, never as the widest
    assert configdial.allowed_fields("banana") == frozenset()


def test_always_is_the_default_and_applies_nothing():
    assert not configdial.may_apply({"budgets": {"max_turns": 50}}, "always")
    assert configdial.refusals({"budgets": {"max_turns": 50}}, "always") == [
        ("config_confirm is 'always' for this routine: every config change it proposes is the "
         "operator's decision.")]


def test_own_fields_applies_an_in_list_self_patch():
    patch = {"budgets": {"max_turns": 50}, "deliberation": "brief"}
    assert configdial.refusals(patch, "own_fields") == []
    assert configdial.may_apply(patch, "own_fields")


def test_own_fields_refuses_a_field_outside_the_allow_list():
    """`schedule` is a legitimate patch field and deliberately NOT autonomous at own_fields:
    a friendly cadence of "disabled" writes `enabled: false`, so the dial would let a routine
    take itself off the schedule."""
    assert "schedule" in ROUTINE_PATCH_FIELDS and "schedule" not in configdial.OWN_FIELDS
    why = configdial.refusals({"budgets": {}, "schedule": {"cron": "0 3 * * *"}}, "own_fields")
    assert why and "schedule" in why[0]
    assert not configdial.may_apply({"schedule": {"cron": "0 3 * * *"}}, "own_fields")
    # and `never` does reach it — that is the documented difference between the two levels
    assert configdial.may_apply({"schedule": {"cron": "0 3 * * *"}}, "never")


@pytest.mark.parametrize("level", ["always", "own_fields", "never"])
@pytest.mark.parametrize("key", ["permissions", "capabilities", "fs_read_roots",
                                 "fs_write_roots", "models", "grants", "connections",
                                 "machines", "trial"])
def test_a_sealed_key_is_refused_under_every_dial_value(level, key):
    """`trial` is sealed for the same reason `models` is: a TrialConfig CARRIES a models
    mapping (config/trialconf.py), so an autonomous trial is an autonomous role binding, and
    a role naming an absent model takes the routine dark (F643)."""
    why = configdial.refusals({key: {}}, level)
    assert why, (level, key)
    assert key in why[0] and not configdial.may_apply({key: {}}, level)


@pytest.mark.parametrize("level", ["always", "own_fields", "never"])
def test_a_dotted_models_key_is_sealed_too(level):
    """The operator sealed "anything under `models.`". The patch surface is flat, but a
    proposal spelling the role out must not slip past a membership test."""
    assert not configdial.may_apply({"models.main": "gpt"}, level)
    assert "models.main" in configdial.refusals({"models.main": "gpt"}, level)[0]


@pytest.mark.parametrize("level", ["always", "own_fields", "never"])
def test_a_cross_routine_patch_is_a_decision_under_every_dial_value(level):
    """D123/F458 keeps the target INSIDE the patch as `routine`; whatever the dial says, a
    patch for someone else's config is the operator's."""
    why = configdial.refusals({"budgets": {"max_turns": 50}}, level, cross_routine=True)
    assert why and "another routine" in why[0]
    assert not configdial.may_apply({"budgets": {}}, level, cross_routine=True)


def test_an_empty_patch_never_applies():
    """Nothing to apply is not an autonomous apply: it must not settle a record as applied."""
    assert not configdial.may_apply({}, "never")
    assert configdial.refusals({}, "never") == ["the config_patch names no field to apply."]


def test_the_sealed_set_is_refused_even_when_the_rest_is_allowed():
    """ALL-OR-NOTHING: one sealed key refuses the whole patch rather than applying the half
    that was allowed — a partial autonomous apply would leave the record settled with the
    operator's half silently dropped."""
    why = configdial.refusals({"budgets": {"max_turns": 9}, "capabilities": {"actions": []}},
                              "never")
    assert why and "capabilities" in why[0]
