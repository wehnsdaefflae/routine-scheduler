"""`websearch` and `code_search` as action kinds — a SPELLING of the util call, not a port.

D290, answered by the operator 2026-11-14: *"Make `websearch` and `code-search` action kinds,
matching what callers already attempt — two more kind bullets in every routine's prompt, and the
util catalog loses its two most-used entries to a second front door"*. He chose it over three
alternatives that all left the schema alone.

WHAT THE MEASUREMENT SAID, because it shapes the whole design. `kind: websearch` /
`kind: code-search` account for 23 rejected turns in 30 days across 12 routines and 3
conversations, the third-largest action-schema rejection class, and both `claude-sonnet-5` and
`claude-opus-5` do it. Every one recovered inside its own turn — `global-utils-review:
20261113-031130` turns 77/89/90 read *"Correcting the call shape — websearch is a util, not a
kind"* with `kind="util", name="websearch", args=[query]` succeeding immediately after. So the
INTENT behind the rejected shape is known exactly: a util call whose payload is the query.

THEREFORE THE KIND IS A SPELLING, REWRITTEN BEFORE ANY GATE. `normalize_action` turns the alias
into the ordinary util action the run meant, which is the only form that cannot drift:

* ONE implementation. A second code path to the same util would be a second place for its
  arguments, its timeout and its failure shape to diverge.
* ONE permission. The util's secret gate, its reservation and its grant checks are the util
  kind's, and they run on the rewritten action exactly as for a hand-written util call. A new
  kind with its own executor entry would route around every one of them.
* ONE telemetry row. `util_rejection_outcome` and `count_util` see `name="websearch"`, so the
  Stats tab keeps counting the util it has always counted instead of growing a second row.

`code_search` IS THE KIND AND `code-search` IS ACCEPTED ANYWAY. No action kind may contain a
hyphen — `tests/test_wrong_kind_diagnosis.py` pins that property, because it is the
discriminator that diagnoses every OTHER util-name-as-kind mistake, and a hyphenated kind would
turn those diagnoses into misdiagnoses. The rewrite runs before the schema validator, so the
hyphenated spelling callers actually send never has to be a kind to be understood.

AND IT DECLINES RATHER THAN GUESS. An alias action carrying no derivable payload is left exactly
as it came, so the validator refuses it inside the schema-retry cycle (a correction that costs
no turn) instead of running a util call with an empty query (a usage error that costs a real
turn). Tolerance here is strictly cheaper than optimism.
"""

from __future__ import annotations

import jsonschema
import pytest

from rsched.engine.actions import (
    BACKGROUNDABLE_KINDS,
    KIND_EXAMPLES,
    KIND_FIELDS,
    normalize_action,
    validate_action,
)
from rsched.engine.actionschema import ACTION_SCHEMA, KINDS
from rsched.engine.kindaliases import ALIAS_KINDS, SPELLINGS, rewrite
from rsched.engine.kindsurface import effective_kinds, schema_for_kinds

# ---- the contract the operator's answer names ---------------------------------------------

def test_both_kinds_are_in_the_schemas_enum_which_is_what_he_chose():
    """Option 2 and 3 left the schema alone; he chose the one that does not. The enum is what
    a wire-enforcing provider's grammar is built from, so this is the part a caller can SEE."""
    assert "websearch" in KINDS
    assert "code_search" in KINDS
    enum = ACTION_SCHEMA["properties"]["kind"]["enum"]
    assert "websearch" in enum and "code_search" in enum


def test_no_action_kind_contains_a_hyphen_even_now():
    """The property `test_wrong_kind_diagnosis` rests on, re-asserted from this side: the kind
    is `code_search`, and `code-search` is handled by the rewrite instead of by the enum."""
    assert [k for k in KINDS if "-" in k] == []


@pytest.mark.parametrize("kind", sorted(ALIAS_KINDS))
def test_an_alias_kind_is_rewritten_into_the_util_call_the_run_meant(kind):
    action = {"say": "x", "kind": kind, "args": ["three distinctive terms"]}
    out = normalize_action(action)
    assert out["kind"] == "util", out
    assert out["name"] == ALIAS_KINDS[kind]
    assert out["args"] == ["three distinctive terms"]
    assert validate_action(out) == [], "the rewritten action must be an ordinary valid util call"


def test_the_hyphenated_spelling_callers_actually_send_is_accepted():
    """`code-search` is what 11 of the 23 measured rejections wrote. It cannot be a kind (the
    hyphen property above), and it does not need to be: the rewrite runs first."""
    out = normalize_action({"say": "x", "kind": "code-search", "args": ["needs_you", "src/"]})
    assert out["kind"] == "util" and out["name"] == "code-search"
    assert out["args"] == ["needs_you", "src/"]


def test_a_single_string_payload_is_read_as_one_argument():
    """A query is one string and a model writing a one-field action writes it as one. Refusing
    the shape would cost the turn the whole change exists to save."""
    out = normalize_action({"say": "x", "kind": "websearch", "args": "one whole query"})
    assert out["args"] == ["one whole query"]


@pytest.mark.parametrize("field", ["query", "pattern", "prompt"])
def test_the_obvious_payload_field_names_are_tolerated(field):
    """Undeclared by the schema, so they would be stripped and the call would run EMPTY. The
    rewrite happens before the stripping, which is the only reason tolerance is possible."""
    out = normalize_action({"say": "x", "kind": "websearch", field: "the actual query"})
    assert out["kind"] == "util" and out["args"] == ["the actual query"]
    assert field not in out


def test_an_alias_with_no_payload_is_left_alone_so_the_retry_cycle_corrects_it():
    """The decline that makes tolerance safe: a rewrite here would run `gu websearch` with no
    query — exit 2, one real turn. Left alone, the validator names the missing field inside the
    schema-retry cycle, which costs none."""
    out = normalize_action({"say": "x", "kind": "websearch"})
    assert out["kind"] == "websearch", "an unrewritable alias must not become a util call"
    problems = validate_action(out)
    assert problems and any("args" in str(p) for p in problems), problems


def test_the_timeout_and_background_flags_survive_the_rewrite():
    out = normalize_action({"say": "x", "kind": "websearch", "args": ["q"],
                            "timeout_s": 60, "background": True})
    assert out["timeout_s"] == 60 and out["background"] is True
    assert validate_action(out) == []


def test_a_side_field_survives_the_rewrite():
    """`note`, `remind` and `role_swap` ride EVERY kind and are filed by the engine, not by the
    handler — losing one to a rewrite would silently drop a run's own record."""
    out = normalize_action({"say": "x", "kind": "websearch", "args": ["q"],
                            "note": "what this search was for"})
    assert out["note"] == "what this search was for"


# ---- the two existing invariants this kind category collides with --------------------------

@pytest.mark.parametrize("kind", sorted(ALIAS_KINDS))
def test_an_alias_kind_may_defer_because_the_util_call_it_becomes_may(kind):
    """A search is exactly the fetch a run should background, so the flag must be allowed.

    AND IT COLLIDES WITH AN EXISTING INVARIANT, which is why this is asserted here rather than
    left to that file: `test_background_actions.py::test_the_background_runner_matches_the_
    foreground_routing_table` requires every backgroundable kind to be in `executor.DISPATCH`,
    because a backgrounded kind with no runner raised `KeyError` in a live run (F633, R2255).
    An alias reaches no runner under its own name — `normalize_action` rewrites it inside
    `completion.judge`, which runs for a chained element too (`chain._next_unfit` puts the
    JUDGED action back in the queue), so there is no path on which an alias arrives at a
    runner. What must be in DISPATCH is therefore the kind it BECOMES; an executor entry for
    the alias itself could only ever be dead code, and dead code at a dispatch seam is exactly
    how 0.535.2's F888 fix came to sit in an adapter this instance never executes.
    """
    assert kind in BACKGROUNDABLE_KINDS
    assert "background" in KIND_FIELDS[kind][1]
    from rsched.engine import executor
    assert normalize_action(KIND_EXAMPLES[kind])["kind"] in executor.DISPATCH


@pytest.mark.parametrize("kind", sorted(ALIAS_KINDS))
def test_the_kinds_own_example_is_a_valid_action_and_rewrites(kind):
    """`KIND_EXAMPLES` is what a weak model is shown when its reply fails validation, so the
    example must be valid BEFORE the rewrite as well as after it."""
    jsonschema.Draft202012Validator(ACTION_SCHEMA).validate(KIND_EXAMPLES[kind])
    assert validate_action(normalize_action(KIND_EXAMPLES[kind])) == []


# ---- availability: an alias rides the util kind, never its own grant -----------------------

def test_a_workflow_that_may_call_utils_may_spell_it_either_way():
    """The alias is a spelling of a call the workflow already permits. Making it a separate
    entry in every workflow's `tools:` would mean editing 31 routines to keep what they have."""
    kinds = effective_kinds({"util", "read_file", "finish"})
    assert "websearch" in kinds and "code_search" in kinds


def test_a_workflow_that_may_not_call_utils_may_not_spell_it_at_all():
    """The other direction is the one that must not fail open: the alias is a util call, so a
    run that may not make one may not make it under another name either."""
    kinds = effective_kinds({"read_file", "write_file", "finish"})
    assert "websearch" not in kinds and "code_search" not in kinds


def test_the_projection_carries_the_alias_kinds_fields():
    """A restricted run's schema must still accept the alias's own example."""
    schema = schema_for_kinds({"util", "websearch", "finish"})
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(KIND_EXAMPLES["websearch"])


# ---- the map itself ------------------------------------------------------------------------

def test_every_alias_names_a_real_kind_and_every_spelling_resolves():
    assert set(ALIAS_KINDS) <= set(KINDS), "an alias kind the enum does not carry is unreachable"
    assert set(ALIAS_KINDS) <= set(SPELLINGS)
    for util in SPELLINGS.values():
        assert util and " " not in util


def test_rewrite_leaves_every_other_kind_untouched():
    """A diagnosis or a rewrite that fires on ordinary actions teaches the model to ignore it —
    `shiftedfields.field_shift_diagnosis` says the same of its own signature."""
    for kind in ("util", "script", "shell", "read_file", "finish", "report", "llm"):
        assert rewrite({"say": "x", "kind": kind, "name": "n", "args": ["a"]}) is None
