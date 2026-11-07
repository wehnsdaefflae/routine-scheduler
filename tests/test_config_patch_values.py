"""A `config_patch`'s VALUES, judged where the record is written (`engine/config_values.py`).

`patch_shape` judges the patch's keys and `patch_nested` the inner keys of its mapping fields,
so a patch whose names are all real reached the Decisions page unjudged — and the operator found
out it could not be applied by pressing the button. Measured 2026-11-06 on the Questions page
(`.ui-traces/20261106.jsonl`, `view=questions`), eleven error toasts in nine minutes:

* `measures: List should have at most 6 items after validation, not 7` ×5, 02:25-02:34 — an
  `outcome` contract with SEVEN measures. `outcome` is a real `RoutinePatch` field, so
  `patch_shape` passed it, and nothing looked inside.
* `models.tool_call: must be a catalog model name` ×4, 02:33-02:34 — a role bound to a model
  this instance does not serve. `patch_nested` judged the role NAME and stopped there.

Nine wasted *approve & apply* clicks. The checks here mirror `web/config_fields.validate_models`
and `config/outcome.OutcomeConfig` and are deliberately NO STRICTER than the apply: a filing
check that refuses a proposal the click would have taken loses a change nobody can see was lost.
The three properties that encode that are `test_what_the_apply_accepts_is_never_refused_here`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import finish
from helpers import server_for, transcript_events
from rsched.config.base import DECISION_ROLES, MODEL_KINDS, ROUTINE_MODEL_ROLES
from rsched.config.outcome import MAX_MEASURES
from rsched.engine.config_bridge import outcome_value, role_value
from rsched.engine.runtime import run_routine

TS = "20261106-020000"

#: An instance that serves one chat model and one decision model — the two catalogs a `models:`
#: role resolves through, kept apart because a chat name is not a decision name.
CATALOG = SimpleNamespace(models={"Sonnet": object()}, decision_models={"jev": object()})

MEASURE = {"id": "fixes_shipped", "label": "fixes shipped", "kind": "count", "better": "higher"}


def _outcome(n: int) -> dict:
    return {"principal": "Mark",
            "measures": [{**MEASURE, "id": f"m{i}"} for i in range(n)]}


# ---------------------------------------------------------------- the measured cases


def test_seven_measures_is_refused_at_filing_and_names_the_cap():
    """The 2026-11-06 case: five clicks on a contract one measure over the cap."""
    why = outcome_value({"outcome": _outcome(MAX_MEASURES + 1)})
    assert "measures" in why
    assert str(MAX_MEASURES) in why                  # the cap, offered rather than hidden
    assert "read and not take" in why or "clears one" in why
    assert '"principal"' in why                      # the shape, so the next filing is right


def test_a_role_naming_a_model_the_instance_cannot_serve_is_refused_at_filing():
    """The 2026-11-06 case: four clicks on `models.tool_call`."""
    why = role_value(CATALOG, {"models": {"tool_call": "claude-haiku-9"}})
    assert "models.tool_call" in why and "claude-haiku-9" in why
    assert "Sonnet" in why                           # what this instance does serve
    assert "read and not take" in why


def test_the_refusal_distinguishes_the_two_catalogs():
    """A DECISION role names a decision model, so a chat name there is refused — and the
    refusal must send the reader to the right settings page, not the chat catalog."""
    why = role_value(CATALOG, {"models": {"decision": "Sonnet"}})
    assert "decision model" in why and "jev" in why
    chat = role_value(CATALOG, {"models": {"main": "jev"}})
    assert "catalog model" in chat and "Sonnet" in chat


# ---------------------------------------------------------------- never stricter than the apply


def test_what_the_apply_accepts_is_never_refused_here():
    """THE standing property. Each line is something `api_routine_patch` would have applied."""
    assert outcome_value({"outcome": _outcome(MAX_MEASURES)}) == ""
    assert outcome_value({"outcome": None}) == ""        # the one spelling of "clear it"
    assert outcome_value({"budgets": {"max_turns": 9}}) == ""
    assert outcome_value({}) == "" and outcome_value(None) == ""
    assert role_value(CATALOG, {"models": {"main": "Sonnet"}}) == ""
    assert role_value(CATALOG, {"models": {"decision": "jev"}}) == ""
    # blanking a role is how the apply clears it back to the instance default
    assert role_value(CATALOG, {"models": {"main": ""}}) == ""
    assert role_value(CATALOG, {"models": {"uncensored": None}}) == ""
    assert role_value(CATALOG, {}) == "" and role_value(CATALOG, None) == ""
    # shape errors belong to the patch models; a non-mapping must not crash this check
    assert role_value(CATALOG, {"models": "Sonnet"}) == ""
    assert outcome_value({"outcome": "Mark"}) != ""      # judged, not crashed


@pytest.mark.parametrize("role", sorted(ROUTINE_MODEL_ROLES))
def test_every_declared_role_is_proposable_with_a_name_the_instance_serves(role):
    """Anti-drift: the check reads the config's own role lists, so a role added to
    `ROUTINE_MODEL_ROLES` is proposable the same day — through whichever catalog it resolves."""
    name = "jev" if role in DECISION_ROLES else "Sonnet"
    assert role_value(CATALOG, {"models": {role: name}}) == ""


def test_a_chat_role_is_never_judged_against_the_decision_catalog():
    """The reason `trials.roles_problem` is not reused: it checks every role against the CHAT
    catalog, which would refuse a valid `decision` proposal."""
    for role in MODEL_KINDS:
        assert role_value(CATALOG, {"models": {role: "jev"}}) != ""
    for role in DECISION_ROLES:
        assert role_value(CATALOG, {"models": {role: "Sonnet"}}) != ""


# ---------------------------------------------------------------- wired into the ask


def test_the_checks_are_wired_into_the_ask_so_no_record_is_filed(make_routine, scripted):
    """End to end through `ask_user`: the run is told on the turn that files the ask, and the
    Decisions page never gets a button that 422s. Unwired, every assertion above still passes.
    """
    d = make_routine(slug="contractor")
    scripted([
        {"say": "propose my contract", "kind": "ask_user", "mode": "deferred",
         "question": "Adopt this outcome contract?",
         "config_patch": {"outcome": _outcome(MAX_MEASURES + 1)}},
        finish(),
    ])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    assert not list((d / "questions" / "pending").glob("*.json"))
    obs = next(e for e in transcript_events(run_dir)
               if e["type"] == "observation" and e["payload"].get("error"))
    assert "measures" in obs["payload"]["error"]
    assert str(MAX_MEASURES) in obs["payload"]["error"]
