"""The hook MIRROR — a report, a reminder write and an injection, each as one hook row.

D152 campaign step 2 builds the mechanism BEHIND the existing surfaces, so these tests assert
two things over the LIVE paths (never over constructed rows):

1. each of the three mechanisms becomes ONE row whose trigger/addressee/delivery/disposition
   say what it is — that is what makes them configurations of one mechanism rather than three
   stores sharing a name;
2. the existing surfaces are UNCHANGED — the report ledger and its delivery, the reminder
   store, and the injected message all still read exactly as they did.

(2) is the half that matters at this step: a mirror that alters what a routine sees is not a
mirror. The record and the store itself are pinned in test_hooks_store.py.
"""

from __future__ import annotations

from conftest import finish
from helpers import reminder, server_for, set_capabilities
from rsched import hooks, reports
from rsched import reminders as remstore
from rsched.engine.runtime import run_routine
from rsched.hooks import store as hookstore

TS = "20260919-090000"


# --- the three mechanisms, as configurations of one record ----------------------------------


def test_a_filed_report_is_recorded_as_a_hook(tmp_path):
    """Mechanism a): trigger `send`, addressee the target routine, delivery `next_prompt`,
    disposition `open`. Taken from the live path — `reports.file_report` — so the row proves
    the mirror and not a reconstruction of it.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)
    filed = reports.file_report(home, routine="builder", run_id="builder:1",
                                title="the gate reds on main", detail="see run X",
                                target="self-audit")
    assert filed is not None
    _, rid, _ = filed

    rows = hookstore.read_hooks(home)

    assert [r.id for r in rows] == [rid]
    row = rows[0]
    assert row.source == "report"
    assert row.trigger == {"kind": "send"}
    assert row.addressee == "routine:self-audit"
    assert row.delivery == "next_prompt"
    assert row.disposition == "open"
    assert row.payload["title"] == "the gate reds on main"
    assert row.origin == {"routine": "builder", "run_id": "builder:1"}


def test_an_unaddressed_report_is_addressed_to_triage(tmp_path):
    """"I cannot name the owner" is an addressee, not the absence of one — which is what makes
    triage a routable state rather than a gap.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)
    reports.file_report(home, routine="builder", run_id="builder:1", title="odd log line")

    assert hookstore.read_hooks(home)[0].addressee == "triage"


def test_the_report_ledger_and_its_delivery_are_unchanged_by_the_mirror(tmp_path):
    """The half that matters at this step: the mirror is invisible. The ledger row and the
    delivered message must read exactly as they did before hooks existed.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)
    target = home / "self-audit"
    (target / "inbox").mkdir(parents=True)
    filed = reports.file_report(home, routine="builder", run_id="builder:1", title="t",
                                detail="d", target="self-audit", target_dir=target)
    assert filed is not None
    _, rid, _ = filed

    ledger = reports.read_reports(reports.reports_path(home))
    delivered = sorted((target / "inbox").glob("msg-rep-*.json"))

    assert [r["id"] for r in ledger] == [rid]
    assert ledger[0]["title"] == "t"
    assert ledger[0]["target"] == "self-audit"
    assert len(delivered) == 1
    assert f"REPORT {rid} from routine `builder`" in delivered[0].read_text()


def test_a_reminder_write_is_recorded_as_a_hook(make_routine, scripted):
    """Mechanism b): trigger `match`, addressee `self` (the routine's own future runs),
    delivery `hold_before_action` for the `action` kind. End to end through a real run, because
    the mirror hangs on the engine's write path and a unit call would not prove it is wired.
    """
    d = make_routine(slug="remr")
    set_capabilities(d, reminders="local")
    scripted([{"say": "Noting a consequence.", "kind": "read_file", "path": "LEDGER.md",
               "remind": {"op": "add", "regex": "^util:fs-ops mv ",
                          "description": "moving a served file breaks the live page"}},
              finish()])
    status, _ = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"

    rows = [r for r in hookstore.read_hooks(d.parent) if r.source == "reminder"]

    assert len(rows) == 1
    row = rows[0]
    assert row.trigger == {"kind": "match", "match": "action", "regex": "^util:fs-ops mv "}
    assert row.addressee == "self"
    assert row.delivery == "hold_before_action"
    assert row.payload["scope"] == "local"
    assert row.origin["routine"] == "remr"


def test_a_non_holding_reminder_kind_is_recorded_as_a_turn_boundary_delivery(
        make_routine, scripted):
    """Only the `action` kind holds (design rule 2). A `result` kind watches what already
    happened, so it rides the observation — and the row must say so, or the one store cannot
    answer which hooks in this instance can change control flow.
    """
    d = make_routine(slug="remr")
    set_capabilities(d, reminders="local")
    scripted([{"say": "Watching results.", "kind": "read_file", "path": "LEDGER.md",
               "remind": {"op": "add", "kind": "result", "regex": "exit 137",
                          "description": "the OOM killer took it; halve the batch"}},
              finish()])
    status, _ = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"

    row = next(r for r in hookstore.read_hooks(d.parent) if r.source == "reminder")

    assert row.trigger["match"] == "result"
    assert row.delivery == "turn_boundary"
    assert not row.holds()


def test_the_reminder_store_is_unchanged_by_the_mirror(make_routine, scripted):
    """As with the report ledger: the local store a future run reads must be exactly what it
    was. The mirror adds a row elsewhere; it does not touch this file.
    """
    d = make_routine(slug="remr")
    set_capabilities(d, reminders="local")
    scripted([{"say": "Noting.", "kind": "read_file", "path": "LEDGER.md",
               "remind": {"op": "add", "regex": "^shell: rm ", "description": "it deletes"}},
              finish()])
    run_routine(d, server_for(d), run_ts=TS)

    live, _ = remstore.load_local(d)

    assert [(r.regex, r.kind, r.scope) for r in live] == [("^shell: rm ", "action", "local")]


def test_a_failing_mirror_cannot_break_the_write_it_records(make_routine, scripted,
                                                            monkeypatch):
    """Best-effort, and asserted as behaviour rather than as a promise in prose: the reminder
    write and the report filing are the real work, and bookkeeping that can fail them is a
    defect dressed as a feature.
    """
    def boom(*_a, **_k):
        raise OSError("the control directory is read-only")

    monkeypatch.setattr(hookstore, "record_reminder", boom)
    d = make_routine(slug="remr")
    set_capabilities(d, reminders="local")
    scripted([{"say": "Noting.", "kind": "read_file", "path": "LEDGER.md",
               "remind": {"op": "add", "regex": "^shell: rm ", "description": "it deletes"}},
              finish()])

    status, _ = run_routine(d, server_for(d), run_ts=TS)

    assert status == "ok"
    live, _ = remstore.load_local(d)
    assert [r.regex for r in live] == ["^shell: rm "]


def test_a_mid_run_injection_is_recorded_as_a_hook(make_routine, scripted):
    """Mechanism c): trigger `send`, addressee `run:<run_id>`, delivery `turn_boundary`,
    disposition `settled` — consumed once. The row is written where the message actually
    ENTERS the conversation, so it records a delivery and not the web layer's intent: the
    inject route only queues a file, and whether it reached a live run is decided in the loop.
    """
    from rsched.engine.inbox import file_message

    d = make_routine(slug="injr")

    def racing_message():
        file_message(d, "the funder replied — switch to their numbers", via="web")
        return {"say": "Looking again.", "kind": "read_file", "path": "LEDGER.md"}

    scripted([{"say": "Working.", "kind": "read_file", "path": "LEDGER.md"},
              racing_message, finish()])
    status, _ = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"

    rows = [r for r in hookstore.read_hooks(d.parent) if r.source == "injection"]

    assert len(rows) == 1
    row = rows[0]
    assert row.trigger == {"kind": "send", "via": "web"}
    assert row.addressee == f"run:injr:{TS}"
    assert row.delivery == "turn_boundary"
    assert row.disposition == "settled"
    assert "switch to their numbers" in row.payload["text"]
    # the id is the run and TURN it was delivered at: an injection has no id of its own
    # anywhere in the system, and that pair is what identifies it — and makes a replayed leg
    # re-delivering the same message unable to double-count it.
    assert row.id == f"inj-injr:{TS}-t{row.origin['turn']}"


def test_the_injected_message_itself_is_unchanged_by_the_mirror(make_routine, scripted):
    """The third surface held still: the conversation the model reads must carry exactly the
    wording it carried before hooks existed.
    """
    from rsched.engine.inbox import file_message
    from rsched.engine.transcript import read_events

    d = make_routine(slug="injr")

    def racing_message():
        file_message(d, "one more thing", via="web")
        return {"say": "Looking again.", "kind": "read_file", "path": "LEDGER.md"}

    ep = scripted([{"say": "Working.", "kind": "read_file", "path": "LEDGER.md"},
                   racing_message, finish()])
    _, run_dir = run_routine(d, server_for(d), run_ts=TS)

    events, _ = read_events(run_dir / "transcript.jsonl")

    assert any(e["type"] == "user_injection" and e["payload"]["text"] == "one more thing"
               for e in events)
    assert any("USER MESSAGE (injected mid-run):\none more thing" in m["content"]
               for m in ep.calls[-1]["messages"] if isinstance(m.get("content"), str))


def test_a_cross_routine_hook_is_addressed_to_that_routine(make_routine, scripted):
    """The addressee is what distinguishes a hook a routine set on ITSELF from one a sibling
    set on it — spelling a self-hook as the routine's own slug would make the two
    indistinguishable in the one store.
    """
    d = make_routine(slug="remr")
    set_capabilities(d, reminders="local")
    other = d.parent / "other-routine"
    (other / "state").mkdir(parents=True)
    remstore.save_local(other, [reminder(rid="rem-x", regex="^existing")], {})
    scripted([{"say": "Setting a hook on a sibling.", "kind": "read_file", "path": "LEDGER.md",
               "remind": {"op": "add", "target": "other-routine", "regex": "^util:git push",
                          "description": "that repo is published; a push is public"}},
              finish()])
    status, _ = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"

    rows = [r for r in hookstore.read_hooks(d.parent) if r.source == "reminder"]

    assert len(rows) == 1
    assert rows[0].addressee == "routine:other-routine"
    assert rows[0].origin["routine"] == "remr"
