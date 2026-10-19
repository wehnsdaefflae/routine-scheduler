"""HOOKS as one mechanism (D152, campaign step 2) — the RECORD and the STORE.

The operator's order: implement hooks as a single mechanism unifying reports/messages,
consequence reminders and run injections, with run↔sub-run communication as a case of it.
Step 2 builds the mechanism BEHIND the existing surfaces: nothing a routine sees changes, and
every current path writes through the one store as well as its own.

What is pinned here is the mechanism's own shape — the five fields and nothing beside them,
one spelling for an addressee, which delivery moment can change control flow, a vocabulary no
mechanism may invent a value outside of, and a stream that reads back in order and forgives a
row from a newer build. That the three live paths actually write through it, and that their
own surfaces are unchanged by doing so, is test_hooks_mirror.py.
"""

from __future__ import annotations

import pytest

from conftest import finish
from helpers import reminder, server_for, set_capabilities
from rsched import hooks, reports
from rsched import reminders as remstore
from rsched.engine.runtime import run_routine
from rsched.hooks import store as hookstore

TS = "20260919-090000"


# --- the record -----------------------------------------------------------------------------


def test_the_five_fields_are_the_whole_record():
    """No mechanism may add a field — that is the test that this is ONE mechanism and not three
    stores sharing a name. The five are trigger, addressee, payload, delivery, disposition;
    everything else on the row is provenance (id, ts, source, origin).
    """
    row = hooks.Hook(id="h1").as_record()
    assert set(row) == {"id", "ts", "source", "trigger", "addressee", "payload", "delivery",
                        "disposition", "origin"}


@pytest.mark.parametrize(("kind", "name", "rendered"), [
    ("routine", "self-audit", "routine:self-audit"),
    ("run", "r:20260919-090000", "run:r:20260919-090000"),
    ("child", "F677", "child:F677"),
    ("parent", "", "parent"),
    ("triage", "", "triage"),
])
def test_an_addressee_has_one_spelling(kind, name, rendered):
    """The addressee is the only part of a hook another RUN has to parse, so a second spelling
    of it is how an addressing scheme gains a special case (design rule 1: the run tree is the
    only addressing scheme).
    """
    assert hooks.addressee(kind, name) == rendered


def test_an_unknown_addressee_kind_is_refused():
    with pytest.raises(ValueError, match="unknown hook addressee kind"):
        hooks.addressee("everyone")


def test_only_a_hold_can_change_what_runs():
    """Design rule 2. Every other delivery moment rides an observation or a prompt section, so
    adding addressees can never alter control flow — which is why the reminder half is the
    risky one and reports and injections are not.
    """
    assert hooks.Hook(id="h1", delivery="hold_before_action").holds()
    for delivery in ("next_prompt", "turn_boundary", "decisions_page"):
        assert not hooks.Hook(id="h1", delivery=delivery).holds()


def test_a_row_from_a_newer_build_still_reads():
    """The store is append-only and shared by three mechanisms being migrated one at a time, so
    a reader that raised on an unexpected value would turn a forward-compatible store into an
    outage.
    """
    hook = hooks.from_record({"id": "h9", "trigger": {"kind": "teleport"},
                              "delivery": "carrier_pigeon", "disposition": "quantum",
                              "unknown_field": 42})
    assert hook.id == "h9"
    assert hook.trigger == {"kind": "teleport"}
    assert not hook.holds()


def test_no_mechanism_invents_a_vocabulary_value(tmp_path, make_routine, scripted):
    """The companion to "no mechanism adds a field": no mechanism adds a VALUE either.

    Every row the three live paths write must spell its trigger, its delivery and its source
    with one of the declared vocabularies — otherwise the one store answers "what hooks exist"
    in three dialects and the question is no better off than with three stores. Asserted over
    rows the real paths produced, not over constructed ones.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)
    reports.file_report(home, routine="builder", run_id="builder:1", title="t",
                        target="self-audit")
    d = make_routine(slug="vocr")
    set_capabilities(d, reminders="local")
    scripted([{"say": "Noting.", "kind": "read_file", "path": "LEDGER.md",
               "remind": {"op": "add", "regex": "^shell: rm ", "description": "it deletes"}},
              finish()])
    run_routine(d, server_for(d), run_ts=TS)

    rows = hookstore.read_hooks(home) + hookstore.read_hooks(d.parent)

    assert rows
    for row in rows:
        assert row.source in hooks.SOURCES, row
        assert row.trigger.get("kind") in hooks.TRIGGERS, row
        assert row.delivery in hooks.DELIVERIES, row
        if row.trigger.get("kind") == "match":
            assert row.trigger.get("match") in hooks.TRIGGER_KINDS, row


def test_the_declared_trigger_kinds_are_the_reminder_store_s_own(tmp_path):
    """One vocabulary, not two spellings of one. The reminder store's KINDS and the hook
    package's TRIGGER_KINDS are the same tuple — if they drift, a reminder written through its
    own path records a trigger the hook store calls unknown, and the unification is nominal.
    """
    assert hooks.TRIGGER_KINDS == remstore.KINDS
    assert hooks.HOLDING_KINDS == remstore.HOLDING_KINDS
    assert hooks.TURN_MOMENTS == remstore.TURN_MOMENTS


# --- the store ------------------------------------------------------------------------------


def test_the_stream_is_append_only_and_reads_back_in_order(tmp_path):
    home = tmp_path / "routines"
    for n in (1, 2, 3):
        hookstore.append(home, hooks.Hook(id=f"h{n}", source="report"))

    rows = hookstore.read_hooks(home)

    assert [r.id for r in rows] == ["h1", "h2", "h3"]
    assert hookstore.hooks_path(home).name == "hooks.jsonl"
    assert hookstore.hooks_path(home).parent.name == ".control"


def test_a_missing_stream_reads_as_empty(tmp_path):
    assert hookstore.read_hooks(tmp_path / "nothing") == []


def test_every_row_carries_a_timestamp_even_when_the_caller_forgot_one(tmp_path):
    home = tmp_path / "routines"
    hookstore.append(home, hooks.Hook(id="h1"))

    assert hookstore.read_hooks(home)[0].ts
