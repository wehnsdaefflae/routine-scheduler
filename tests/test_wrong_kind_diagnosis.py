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


class _FakeServer:
    def __init__(self, home):
        self.libraries_home = home


class _FakeCtx:
    def __init__(self, home):
        self.server = _FakeServer(home)


class _FakeLoop:
    def __init__(self, home):
        self.ctx = _FakeCtx(home)


def _catalog(tmp_path, *names):
    """A library home whose utils/ holds `names` — what `utils_lib.exists` reads."""
    for n in names:
        d = tmp_path / "utils" / n
        d.mkdir(parents=True)
        (d / "main.py").write_text('"""x — y.\n\nusage: gu x\n"""\n', encoding="utf-8")
    return _FakeLoop(tmp_path)


def test_a_one_word_util_name_is_diagnosed_from_the_catalog(tmp_path):
    """The measured hole: `websearch` has no hyphen, so the vocabulary heuristic saw nothing.

    `wrong_turn_detected` on this instance, all time to 2026-11-10: 14 invalid-kind rejections
    across 9 routines and 23 days, of which `websearch` is 8 — the most frequent single shape,
    and the one the hyphen rule could not catch. 34 of the 57 installed utils have no hyphen.
    """
    loop = _catalog(tmp_path, "websearch", "code-search")
    text = unkind_diagnosis({"kind": "websearch", "args": ["grant deadlines"]}, loop)
    assert text, "a util in the catalog used as a kind must be diagnosed"
    assert "websearch" in text
    assert "util" in text.lower()
    assert "name" in text                     # the correction carries the SHAPE
    assert "LOOKS LIKE" not in text           # the catalog knows; it does not guess


def test_the_catalog_branch_asserts_and_the_heuristic_only_suggests(tmp_path):
    """Two different claims, and the difference is the evidence behind them: a name the
    catalog confirms is a util; a hyphenated name it does not carry only looks like one."""
    loop = _catalog(tmp_path, "websearch")
    assert "IS A UTIL" in unkind_diagnosis({"kind": "websearch"}, loop)
    assert "LOOKS LIKE A UTIL" in unkind_diagnosis({"kind": "no-such-util"}, loop)


def test_without_a_loop_the_heuristic_still_answers():
    """Every off-engine caller (these tests, a diagnosis composed outside a run) keeps the
    behaviour it had — the catalog read is an addition, never a precondition."""
    assert "code-search" in unkind_diagnosis({"kind": "code-search"})
    assert unkind_diagnosis({"kind": "websearch"}) == ""


def test_a_broken_library_home_cannot_turn_a_rejection_into_a_crash(tmp_path):
    """This runs INSIDE a schema rejection: the one path that must never raise. A loop whose
    server attribute is missing, and a home that does not exist, both fall back silently."""

    class _Headless:
        pass

    assert "code-search" in unkind_diagnosis({"kind": "code-search"}, _Headless())
    gone = _FakeLoop(tmp_path / "nope")
    assert "code-search" in unkind_diagnosis({"kind": "code-search"}, gone)
    assert unkind_diagnosis({"kind": "websearch"}, gone) == ""


def test_a_real_kind_is_never_looked_up_even_if_a_util_shares_its_name(tmp_path):
    """`script` and `report` are action kinds. If a util were ever named one of them, the
    unknown-kind test above it is what keeps a correct action from being 'corrected'."""
    loop = _catalog(tmp_path, "script", "report")
    assert unkind_diagnosis({"kind": "script", "name": "outcome"}, loop) == ""
    assert unkind_diagnosis({"kind": "report", "title": "x"}, loop) == ""


def test_the_seam_carries_the_loop_so_the_live_catalog_reaches_the_retry(tmp_path):
    """`completion.diagnose` is the single composer every schema rejection reads. If it did
    not forward the loop, the catalog branch would be dead code in production."""
    loop = _catalog(tmp_path, "websearch")
    assert "websearch" in diagnose({"kind": "websearch"}, loop)
    assert diagnose({"kind": "websearch"}) == ""       # no loop, no catalog, no invention


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
