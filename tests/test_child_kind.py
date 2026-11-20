"""One name for a child run: the `child` kind with a `mode`, and the three names it retires
(D241, operator decision 2026-10-17: *"sub-runs / subtasks (why two terms for the same thing?
fix that)"*).

The decision is about VOCABULARY, so the tests are about vocabulary: that the retired names are
GONE rather than aliased (an alias is how two names for one thing survive indefinitely), that
the prompt a model actually reads says one noun, and that the instructions already on this
instance are carried over — because removing a kind while a live recipe still names it tells a
run to emit something the schema refuses.
"""

from __future__ import annotations

import json

import pytest

from conftest import make_test_server
from rsched import migrate_child_kind
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

def test_the_migration_rewrites_an_instruction_but_never_a_record(tmp_path):
    """A recipe is an INSTRUCTION a run acts on and must be carried over; a ledger is a RECORD
    of what happened, and a run really did call `subtask` in October. Rewriting the record
    would make it lie about the past."""
    server = make_test_server(tmp_path)
    routine = tmp_path / "routines" / "demo"
    (routine / "stages").mkdir(parents=True)
    (routine / "main.md").write_text("Use `subtask` for an ordered step, `spawn` to parallelise.",
                                     encoding="utf-8")
    (routine / "stages" / "build.md").write_text("Check `subruns` for the status table.",
                                                 encoding="utf-8")
    (routine / "LEDGER.md").write_text("2026-10-02: spawned 3 subtasks, all green.",
                                       encoding="utf-8")

    record = migrate_child_kind.run_migration(server)

    assert record["files"] == 2
    main = (routine / "main.md").read_text(encoding="utf-8")
    assert "subtask" not in main and "spawn" not in main
    assert "`child` (mode sequential)" in main and "`child`" in main
    assert "children" in (routine / "stages" / "build.md").read_text(encoding="utf-8")
    assert "subtasks" in (routine / "LEDGER.md").read_text(encoding="utf-8")


def test_the_migration_leaves_the_spawn_hook_point_alone(tmp_path):
    """`spawn` is also the name of a hook POINT — a moment in a run's life that rules match on.
    Renaming it here would silently stop those rules firing, which is a failure with no error.
    """
    server = make_test_server(tmp_path)
    routine = tmp_path / "routines" / "demo"
    routine.mkdir(parents=True)
    (routine / "main.md").write_text(
        "A rule may fire at the `spawn` hook point.\nUse `subtask` for an ordered step.",
        encoding="utf-8")

    migrate_child_kind.run_migration(server)

    text = (routine / "main.md").read_text(encoding="utf-8")
    # BACKTICKED, because that is the spelling the rewrite table actually matches: a plain-prose
    # "spawn" is never touched, so a test using one would pass whether the guard existed or not.
    assert "`spawn` hook point" in text          # the moment keeps its name
    assert "subtask" not in text                 # the kind does not


def test_the_migration_leaves_a_rules_bare_yaml_moment_alone(tmp_path):
    """The shape the quoted KEEP forms miss, and the one that actually bit: a rule's
    `assists:` names its hook moment on a bare YAML key. Rewriting `moment: spawn` to
    `moment: child` makes the rule fail its OWN linter — `spawn` is a moment in a run's life
    and `child` is not one — and the failure is silent until the library is linted.
    """
    server = make_test_server(tmp_path)
    rules = tmp_path / "library" / "rules"
    rules.mkdir(parents=True)
    (rules / "evidence-discipline.md").write_text(
        "assists:\n  - id: on-a-thin-brief\n    moment: spawn\n"
        "    line: >-\n      Brief it with `subtask` in mind.\n", encoding="utf-8")

    migrate_child_kind.run_migration(server)

    text = (rules / "evidence-discipline.md").read_text(encoding="utf-8")
    assert "moment: spawn" in text, "the hook MOMENT must keep its name"
    assert "subtask" not in text, "the kind must not"


def test_the_migration_runs_once(tmp_path):
    server = make_test_server(tmp_path)
    routine = tmp_path / "routines" / "demo"
    routine.mkdir(parents=True)
    (routine / "main.md").write_text("`subtask` it.", encoding="utf-8")

    first = migrate_child_kind.run_migration(server)
    assert first["files"] == 1
    assert migrate_child_kind.run_migration(server) == {}


def test_the_migration_reaches_the_library_a_run_actually_reads(tmp_path):
    server = make_test_server(tmp_path)
    rules = tmp_path / "library" / "rules"
    rules.mkdir(parents=True)
    (rules / "work-order.md").write_text("Decompose with `subtask`.", encoding="utf-8")

    migrate_child_kind.run_migration(server)

    assert "subtask" not in (rules / "work-order.md").read_text(encoding="utf-8")
