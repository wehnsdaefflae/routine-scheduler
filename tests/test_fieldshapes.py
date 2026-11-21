"""Evidently-intended field shapes put into their canonical one at the normalize seam
(engine/fieldshapes.py). Every case is a shape the wrong-turn stream recorded being REFUSED,
2026-11-10..18 — a schema retry each, and a stopped chain when it was not the reply's last
action. Each repair is lossless; a shape that cannot be made canonical without a guess (several
labels in one `remind_feedback`) stays refused."""

from __future__ import annotations

import pytest

from rsched.engine import accounting, notemeta
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


#: The finish `accounting` shapes the wrong-turn stream recorded being REFUSED, 2026-10-25..11-17
#: — 19 turns across 12 routines, each one a schema retry at the one turn a run cannot repeat.
@pytest.mark.parametrize(("written", "entries"), [
    # newline-separated entries (9 of the 19) — tv-show-tracker-seedbox-manager, 2026-11-16
    (("b2 met: Rick and Morty is tracked at Starting Season 10\n"
      "b3 unmet: Wicker was searched across TPB, Torlock and NZBGeek"),
     ["b2 met: Rick and Morty is tracked at Starting Season 10",
      "b3 unmet: Wicker was searched across TPB, Torlock and NZBGeek"]),
    # ONE entry as a bare string (6 of the 19) — deficit-coach, 2026-11-17
    ("d1 met: read the stale 4 Nov weigh-in and yesterday's close",
     ["d1 met: read the stale 4 Nov weigh-in and yesterday's close"]),
    # the list's own JSON, as a string (2 of the 19) — folder-reorg, 2026-10-25
    ('["d1 met: phase.json matched the evidence", "d2 met: the unit exit was diagnosed"]',
     ["d1 met: phase.json matched the evidence", "d2 met: the unit exit was diagnosed"]),
    # a `waits on` entry parses as an entry too (accounting._WAITS_RE)
    (("b7 waits on scheduler-builder: the endpoint parameter\n"
      "b4 unmet: 157 rows carry no proof"),
     ["b7 waits on scheduler-builder: the endpoint parameter",
      "b4 unmet: 157 rows carry no proof"]),
])
def test_an_accounting_written_as_one_string_is_the_list_it_spells(written, entries):
    out = _accepted({"kind": "finish", "status": "ok", "summary": "s", "accounting": written})
    assert out["accounting"] == entries
    # lossless: the parser reads exactly the verdicts the written text carried
    assert len(accounting.parse(out["accounting"])) == len(entries)


@pytest.mark.parametrize("written", [
    "d1 met: the first half of the account\nand the sentence continues on this line",
    "Here is my accounting:\nd1 met: the thing was done",
    "everything went fine this run",
])
def test_a_string_whose_parts_are_not_whole_entries_stays_refused(written):
    """Splitting these would INVENT a boundary — where one verdict's account ends. The field
    is left as written, so the run is told the shape rather than given a guess."""
    out = normalize_action({"say": "x", "kind": "finish", "status": "ok", "summary": "s",
                            "accounting": written})
    assert out["accounting"] == written


def test_a_real_list_is_untouched_and_an_empty_field_never_reaches_the_repair():
    """The repair reads only a NON-EMPTY string. An empty one, a null and an empty list are
    dropped by the seam's own empty-field stripping before `canonical` runs (actions.py), so
    `accounting.arrived` still distinguishes "no field" from "a field saying nothing" — the
    distinction F805 exists for. A whitespace-only string survives the strip and is returned as
    written, which the schema refuses as the non-list it is."""
    assert _accepted({"kind": "finish", "status": "ok", "summary": "s",
                      "accounting": ["d1 met: x y"]})["accounting"] == ["d1 met: x y"]
    for value in ("", None, []):
        out = normalize_action({"say": "x", "kind": "finish", "status": "ok", "summary": "s",
                                "accounting": value})
        assert "accounting" not in out
    blank = normalize_action({"say": "x", "kind": "finish", "status": "ok", "summary": "s",
                              "accounting": "   "})
    assert blank["accounting"] == "   "
    assert validate(blank, ACTION_SCHEMA)
