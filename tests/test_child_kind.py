"""One name for a child run: the `child` kind with a `mode`, and the three names it retires
(D241, operator decision 2026-10-17: *"sub-runs / subtasks (why two terms for the same thing?
fix that)"*).
"""

from __future__ import annotations

import json

import pytest

from rsched.engine import childfields, kindprose
from rsched.engine.actions import KIND_FIELDS
from rsched.engine.actionschema import ACTION_SCHEMA, KINDS
from test_actions import _parse_both_layers

RETIRED = ("spawn", "subtask", "subruns")


# ------------------------------------------------------------------- the kinds themselves

@pytest.mark.parametrize("retired", RETIRED)
def test_no_retired_kind_is_offered_by_the_schema(retired):
    """Removed, not aliased. A kind that still validates is a second name for the same thing,
    which is exactly what this decision ends."""
    assert retired not in KINDS
    assert retired not in ACTION_SCHEMA["properties"]["kind"]["enum"]
    assert retired not in KIND_FIELDS


def test_the_child_kind_exists_with_its_mode_and_the_status_table_is_children():
    assert "child" in KINDS
    assert "children" in KINDS
    required, optional = KIND_FIELDS["child"]
    assert required == ("prompt",)
    assert "mode" in optional


def test_the_mode_enum_carries_both_schedulings_and_ask_users_two_values():
    """One JSON-schema property cannot hold two enums, so `mode` is shared — and a shared field
    must still offer every value its kinds take."""
    enum = ACTION_SCHEMA["properties"]["mode"]["enum"]
    assert set(childfields.MODES) <= set(enum)
    assert {"blocking", "deferred"} <= set(enum)


def test_a_parallel_child_is_the_default():
    assert childfields.mode_of({"kind": "child", "prompt": "x"}) == "parallel"
    assert childfields.mode_of({"kind": "child", "mode": "sequential"}) == "sequential"


def test_an_unknown_mode_is_refused_with_both_values_named():
    problems = childfields.mode_problems({"kind": "child", "mode": "background"})
    assert problems and "parallel" in problems[0] and "sequential" in problems[0]


def test_turns_on_a_parallel_child_is_corrected_rather_than_ignored():
    """A `turns` the engine ignores reads to the caller as a budget it set. Saying so is what
    turns a silent misunderstanding into one correction."""
    problems = childfields.mode_problems({"kind": "child", "turns": 8})
    assert problems and "sequential" in problems[0]
    assert not childfields.mode_problems({"kind": "child", "mode": "sequential", "turns": 8})


# ------------------------------------------------------------- the call, through both layers

@pytest.mark.parametrize("mode", ["parallel", "sequential"])
def test_a_child_run_validates_in_either_mode(mode):
    """D241: ONE kind for a child run, its scheduling in `mode`. The parametrized list above
    carries one case per KIND, so the second mode is exercised here rather than by a second
    entry whose id would not match its kind.
    """
    action = {"say": "s", "kind": "child", "mode": mode, "prompt": "do x", "label": "step-1",
              "workflow": "general-task"}
    assert _parse_both_layers(json.dumps(action)) == action


def test_turns_budgets_a_sequential_child():
    action = {"say": "s", "kind": "child", "mode": "sequential", "prompt": "do step x",
              "label": "step-1", "workflow": "general-task", "turns": 8}
    assert _parse_both_layers(json.dumps(action)) == action


# ------------------------------------------------------------------------ the prompt surface

@pytest.mark.parametrize("retired", RETIRED)
def test_the_rendered_kind_prose_never_says_a_retired_name(retired):
    """THE test the decision asks for: what a model reads must carry one noun. Rendered for
    every kind, because a bullet that only appears for some capability is still a prompt."""
    text = kindprose.kind_bullets(list(KINDS))
    assert retired not in text, f"the prompt still says {retired!r}"


def test_the_mode_is_taught_where_the_wire_carries_it():
    """The two schedulings are taught by the FIELD's own description, which every transport
    sends with the request — not by the kind bullet, which carries identity and conduct only
    (0.536.0, and `test_kind_bullets_carry_conduct.py` enforces it). So the bullet names the
    child run and the field says when it runs; neither repeats the other.
    """
    bullet = kindprose.kind_bullets(["child"])
    assert "child" in bullet
    described = str(ACTION_SCHEMA["properties"]["mode"]["description"])
    for word in ("parallel", "sequential"):
        assert word in described, f"the mode field does not teach {word!r}"
    assert "parallel" not in bullet, "the bullet recites what the wire already carries"


# ----------------------------------------------------------------------------- the migration

