"""A `kind` that is not a kind at all: what the refusal tells the run.

The validator's sentence is accurate and useless — `kind: 'note' is not one of [...]` followed
by all 34 current kinds — and it is the ONE field where the right answer cannot be read off the
list, because the thing the run actually wanted is not in it.

MEASURED on this instance's health stream, 2026-11-07 (`wrong_turn_detected`, reason
`schema_rejected`), two runs in one evening, each losing a turn:

* `tv-show-tracker-seedbox-manager:20261107-170000` turn 27 — `kind: 'note' is not one of [...]`.
  `note` is a FIELD that rides any action (and costs no turn); the run wanted to leave one and
  sent it as an action instead.
* `deficit-coach:20261107-230003` turn 10 — `kind: 'code-search' is not one of [...]`.
  `code-search` is a UTIL; the call shape is `kind="util", name="code-search"`.

Both are exactly diagnosable: no action kind contains a hyphen (every one uses underscores), and
the contract knows its own field names. `engine/renamedkinds.py` already does this for a kind
that was RENAMED — this is the same courtesy for a kind that never existed.
"""

from __future__ import annotations

import pytest

from rsched.engine.actionschema import ACTION_SCHEMA, KINDS
from rsched.engine.completion import diagnose
from rsched.engine.shiftedfields import unkind_diagnosis


def test_a_field_name_used_as_a_kind_is_named_as_a_field():
    """`note` is the specimen: a field on EVERY action, so the run's intent is unambiguous."""
    text = unkind_diagnosis({"kind": "note", "note": "something worth keeping"})
    assert text, "a field name used as a kind must be diagnosed"
    assert "note" in text
    assert "field" in text.lower()
    # it quotes the contract's own words about that field rather than re-describing it
    assert "worth keeping" in text or "state/notes.md" in text


def test_a_util_name_used_as_a_kind_gives_the_util_call_shape():
    text = unkind_diagnosis({"kind": "code-search", "args": ["foo"]})
    assert text, "a util name used as a kind must be diagnosed"
    assert "code-search" in text
    assert "util" in text
    # the correction has to carry the SHAPE, not just the word
    assert "name" in text


def test_no_action_kind_contains_a_hyphen_which_is_what_makes_the_util_read_safe():
    """The discriminator the util branch rests on — asserted, so a future kind with a hyphen
    cannot silently turn every such action into a misdiagnosis."""
    assert [k for k in KINDS if "-" in k] == []


@pytest.mark.parametrize("kind", ["delete", "report", "script", "util", "finish"])
def test_a_real_kind_is_never_diagnosed_even_when_it_is_also_a_field_name(kind):
    """`delete` is BOTH a schema field (recursive delete on `read_file`… no: on `memory_write`)
    and a valid action kind. A diagnosis that fired on it would explain a field the run used
    correctly — the mistake `field_shift_diagnosis`' own docstring warns teaches the model to
    ignore the diagnosis."""
    assert unkind_diagnosis({"kind": kind}) == ""


@pytest.mark.parametrize("kind", ["", "Something Entirely Made Up", "x", "do_the_thing"])
def test_nothing_is_said_when_there_is_nothing_specific_to_say(kind):
    """A kind that is neither a field nor util-shaped gets the enum and nothing invented."""
    assert unkind_diagnosis({"kind": kind}) == ""


def test_the_one_seam_carries_it_so_every_rejection_path_gets_it():
    """`completion.diagnose` is the single composer every schema rejection reads (the renamed
    kind, the field shift). A diagnosis wired anywhere else would reach one path only."""
    assert "code-search" in diagnose({"kind": "code-search"})
    assert "field" in diagnose({"kind": "note", "note": "x"}).lower()


def test_the_field_branch_reads_the_contract_rather_than_a_copy_of_it():
    """One source: every field it will diagnose is a real property of the live schema, so a
    field renamed in the contract cannot leave a correction pointing at a field that is gone."""
    for field in ("note", "say", "summary", "evidence"):
        assert field in ACTION_SCHEMA["properties"], field
