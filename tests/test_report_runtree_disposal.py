"""F859: a run-tree message has no ledger row, so its disposal fields are NAMED, not refused."""

from __future__ import annotations

import pytest

from rsched.config import ServerConfig
from rsched.engine import runtree
from rsched.engine.actions import validate_action
from rsched.engine.obs_admin import format_admin
from test_hooks_runtree import TS, _child_ctx, _loop, run_context

RUNTREE_TARGETS = ("parent", "child:ragapi-standup")


@pytest.mark.parametrize("target", RUNTREE_TARGETS)
@pytest.mark.parametrize("fields", [
    {"closes": True},
    {"answers": "R123"},
    {"settles": ["R1", "R2"]},
    {"supersedes": ["R7"]},
    {"closes": True, "answers": "R123"},
    {"answers": "not-an-id"},          # the grammar is not checked either: there is no row
])
def test_a_disposal_field_on_a_run_tree_message_costs_no_turn(target, fields):
    """None of them is refused — the turn happens and the hook goes out."""
    problems = validate_action({"say": "s", "kind": "report", "title": "answering the hand-back",
                                "target": target, "detail": "d", **fields})
    assert problems == [], problems


@pytest.mark.parametrize("fields", [
    {"closes": True},
    {"answers": "R9"},
    {"settles": ["R1"]},
    {"supersedes": ["R7"]},
])
def test_a_routine_target_refuses_no_disposal_field_on_its_own(fields):
    """A slug target has a real ledger row behind it, so the grammar bites where two fields
    contradict each other — but none of these single fields is such a pair. A bare `closes` is
    not refused at all any more: it is accepted and corrected where the disposal is built
    (tests/test_report_closes_correction.py), and `supersedes` still needs the target it hands
    rows to, which this action gives it.
    """
    action = {"say": "s", "kind": "report", "title": "t", "target": "scheduler-builder",
              "detail": "d", **fields}
    assert validate_action(action) == [], action


def test_a_bare_closes_with_no_target_is_not_refused_either():
    """Triage is a ledger row too, and the correction reaches it the same way: the grammar says
    nothing, and the drop happens when the disposal is constructed."""
    assert validate_action({"say": "s", "kind": "report", "title": "t", "detail": "d",
                            "closes": True}) == []


# -- the observation: what was ignored, and what to do instead -------------------------------


def _parent_loop(make_routine, tmp_path, slug: str):
    d = make_routine(slug)
    parent = run_context(d, TS)
    parent.server = ServerConfig()
    parent.server.routines_home = tmp_path
    child = _child_ctx(parent, n=1, label="F859")
    child.server = parent.server
    return _loop(child), parent, child


def test_the_observation_names_the_ledger_fields_it_ignored(make_routine, tmp_path):
    """The false belief is corrected at the moment it would have formed — in the observation of
    the very action that wrote the field, naming the ids so the run can act on them.
    """
    loop, _parent, _child = _parent_loop(make_routine, tmp_path, "rtdisp")

    obs = runtree.send(loop, target="parent", title="answering", detail="why",
                       disposal={"answers": "R123", "settles": ["R1", "R2"]})
    line = format_admin(obs, "report")

    assert obs["delivered"] is True              # the hook went out: nothing was refused
    assert "R123" in line and "R1" in line, line  # the rows it thought it was disposing of
    assert "answers" in line and "settles" in line, line
    assert "no R id" in line or "not a ledger row" in line, line
    # and it must say what WOULD dispose of them, or the run has a correction it cannot act on
    assert "target" in line, line


def test_a_closes_alone_is_not_worth_an_apology(make_routine, tmp_path):
    """`closes` names no row, so there is nothing to warn about: the ordinary note stands and
    the line stays short. A message that scolds every reply would teach runs to stop replying.
    """
    loop, _parent, _child = _parent_loop(make_routine, tmp_path, "rtdisp2")

    obs = runtree.send(loop, target="parent", title="answering", detail="why",
                       disposal={"closes": True})
    line = format_admin(obs, "report")

    assert obs["delivered"] is True
    assert "R" not in line.replace("HOOK", "").replace("OBSERVATION", "") or "hk-" in line, line
    assert "ignored" not in line.lower(), line


def test_a_hook_with_no_disposal_keeps_the_wording_it_had(make_routine, tmp_path):
    """The common case by far, and the fix must not touch it: a hook carrying no disposal field
    reads exactly as it did — the hook id, the addressee, and the note that it owes the ledger
    nothing. R2924 is the reason this is pinned: a changed observation shape on this path once
    made a child read a DELIVERED hook as a failed write and send it twice.
    """
    loop, _parent, _child = _parent_loop(make_routine, tmp_path, "rtdisp3")

    obs = runtree.send(loop, target="parent", title="brief is wrong", detail="why")
    line = format_admin(obs, "report")

    assert obs["delivered"] is True
    assert obs["hook"] in line and "parent" in line, line
    assert "I/O error" not in line and "not filed" not in line, line
    assert "ignored" not in line.lower(), line
