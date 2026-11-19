"""Evidently-intended field shapes put into their canonical one at the normalize seam
(engine/fieldshapes.py). Every case is a shape the wrong-turn stream recorded being REFUSED,
2026-11-10..18 — a schema retry each, and a stopped chain when it was not the reply's last
action. Each repair is lossless; a shape that cannot be made canonical without a guess (several
labels in one `remind_feedback`) stays refused."""

from __future__ import annotations

import pytest

from rsched.engine import notemeta
from rsched.engine.actions import normalize_action, validate_action
from rsched.engine.actionschema import ACTION_SCHEMA
from rsched.schema_guard import validate

RID = "rem-20261115-005415-1"


def _accepted(action: dict) -> dict:
    """The action as the turn would run it — refused here exactly as the turn would refuse it."""
    out = normalize_action({"say": "x", **action})
    assert validate(out, ACTION_SCHEMA) == []
    assert validate_action(out) == []
    return out


def test_a_memory_note_named_as_its_index_line_names_it():
    """INDEX.md lists `- carriers-and-sources.md: …`, and runs copied that name."""
    for kind in ("memory_read", "memory_write"):
        out = _accepted({"kind": kind, "name": "carriers-and-sources.md",
                         **({"content": "# c", "about": "a"} if kind == "memory_write" else {})})
        assert out["name"] == "carriers-and-sources"
    # the INDEX keeps its own names (engine/notemeta.INDEX_NAMES)
    assert _accepted({"kind": "memory_read", "name": "INDEX.md"})["name"] == "INDEX.md"


def test_a_numeric_string_is_the_integer_it_spells():
    assert _accepted({"kind": "kill", "n": "4"})["n"] == 4
    assert _accepted({"kind": "read_file", "path": "a.txt", "start_line": "-5"})["start_line"] == -5
    # a string that is not a number is left for the schema to refuse
    assert normalize_action({"say": "x", "kind": "kill", "n": "four"})["n"] == "four"


def test_one_report_id_is_the_one_element_list():
    out = _accepted({"kind": "report", "title": "t", "target": "self-audit",
                     "supersedes": "R3463", "settles": "R3464"})
    assert (out["supersedes"], out["settles"]) == (["R3463"], ["R3464"])


def test_a_bare_verified_date_is_this_runs_own_check():
    out = _accepted({"kind": "memory_write", "name": "n", "content": "# n", "about": "a",
                     "verified": "2026-11-17"})
    assert out["verified"] == {"by": "run", "at": "2026-11-17"}
    assert notemeta.from_action(out)["verified"] == {"by": "run", "at": "2026-11-17"}


def test_a_remind_with_no_op_and_no_id_can_only_be_an_add():
    out = _accepted({"kind": "read_file", "path": "a.txt",
                     "remind": {"regex": "^script:review_reduce ", "description": "check it"}})
    assert out["remind"]["op"] == "add"
    # with an id the verb is ambiguous (revise? delete?) — still refused, never guessed
    bare = normalize_action({"say": "x", "kind": "read_file", "path": "a.txt",
                             "remind": {"id": RID, "description": "d"}})
    assert "op" not in bare["remind"]


@pytest.mark.parametrize("written", [
    [{"id": RID, "label": "didnt"}],                       # a one-element list
    {RID: "didnt"},                                        # {<id>: <label>}
    {"id": RID, "verdict": "didnt"},                       # the label under another key
    {"id": RID, "outcome": "didnt"},
    {"id": RID, "feedback": "didnt"},
    {"id": RID, "disposition": "didnt"},                   # a LABEL value in `disposition`
    [{"id": RID, "verdict": "didnt", "reason": "the repaired search held"}],
])
def test_one_label_in_any_written_shape_is_the_one_object(written):
    out = _accepted({"kind": "read_file", "path": "a.txt", "remind_feedback": written})
    assert out["remind_feedback"] == {"id": RID, "label": "didnt"}


def test_a_foreign_hooks_disposition_stays_beside_its_label():
    fb = {"id": RID, "label": "didnt", "disposition": "keep"}
    assert _accepted({"kind": "read_file", "path": "a.txt",
                      "remind_feedback": [fb]})["remind_feedback"] == fb


@pytest.mark.parametrize("written", [
    [{"id": RID, "label": "would_have"}, {"id": "rem-20261115-005415-2", "label": "didnt"}],
    {RID: "didnt", "rem-20261115-005415-2": "did"},
])
def test_several_labels_in_one_field_stay_refused(written):
    out = normalize_action({"say": "x", "kind": "read_file", "path": "a.txt",
                            "remind_feedback": written})
    assert validate(out, ACTION_SCHEMA)
