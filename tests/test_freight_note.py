"""Freight that lands MID-RUN is named to the RUNNING run — not only to the next leg's boot."""
from __future__ import annotations

from conftest import finish, write_file
from helpers import server_for
from rsched.engine import inbox
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20260708-070000"
NOTE_LEAD = "freight arrived in this routine's inbox while this run was working"


def _probe(say: str) -> dict:
    return write_file("state/probe.txt", content="probe", say=say)


def _prompt(ep, call: int) -> str:
    return " ".join(m["content"] for m in ep.calls[call]["messages"])


def test_an_answer_that_lands_mid_run_is_named_to_the_live_run(make_routine, scripted):
    """The incident. The run boots, works, and the operator answers a decision at turn 2 —
    after the only digest this run will ever compose. Before this, nothing in the run's
    context said an answer had landed, so the run kept calling the decision open and the
    report it wrote recorded it as awaiting him: the DOUBLE ASK the row is about.
    """
    d = make_routine(slug="freightr")
    (d / "inbox").mkdir(exist_ok=True)

    def answer_lands():
        # exactly what a click on the Decisions page files (web/api_audit.write_feedback):
        # his words, on the `web-audit` channel, which a live run may not consume
        inbox.file_message(d, "[AUDIT decision · D159] selected: B — ship the drain\nand "
                              "say so in the report",
                           via="web-audit", extra={"kind": "decision", "target": "D159"})
        return _probe("working on")

    ep = scripted([_probe("first"), answer_lands, _probe("third"),
                   finish(summary="done")])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"

    # turn 2's prompt was composed BEFORE the answer existed — nothing to see there
    assert "D159" not in _prompt(ep, 1)
    # …and the very next boundary names it
    turn3 = _prompt(ep, 2)
    assert NOTE_LEAD in turn3, "an answer that landed mid-run never reached the running run"
    assert "D159" in turn3 and "ship the drain" in turn3
    assert "web-audit" in turn3

    # NAMED, NEVER DELIVERED: the file is untouched, so the next FRESH run still gets it
    assert len(list((d / "inbox").glob("msg-*.json"))) == 1
    assert inbox.has_pending_messages(d)

    # once per file. The note rides the message list, so it is READ on every later turn —
    # but it is APPENDED once, or a long run accumulates one copy per boundary.
    assert _prompt(ep, 3).count(NOTE_LEAD) == 1

    # it is the ENGINE speaking, not the user: a `user_injection` whose source is `engine`,
    # recorded verbatim so `history.replay_messages` reproduces it on a resumed leg
    events, _ = read_events(run_dir / "transcript.jsonl")
    notes = [e["payload"] for e in events
             if e["type"] == "user_injection" and (e["payload"] or {}).get("source") == "engine"]
    assert any(NOTE_LEAD in str(p.get("text") or "") for p in notes)
    assert not any("D159" in str((e["payload"] or {}).get("text") or "")
                   for e in events
                   if e["type"] == "user_injection"
                   and (e["payload"] or {}).get("source") != "engine"), \
        "freight a run may not consume must never be injected as the user talking"


def test_freight_the_run_may_consume_is_delivered_and_not_named(make_routine, scripted):
    """The note must not double-report what the drain already delivers. A message on a LIVE
    via is the user talking to THIS run: it arrives as a user message and is consumed, so
    naming it as well would tell the run to go and read a file that is already gone.
    """
    d = make_routine(slug="livefr")
    (d / "inbox").mkdir(exist_ok=True)

    def user_speaks():
        inbox.file_message(d, "while you work: skip the second half", via="web")
        return _probe("working on")

    ep = scripted([_probe("first"), user_speaks, _probe("third"),
                   finish(summary="done")])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"

    turn3 = _prompt(ep, 2)
    assert "skip the second half" in turn3          # delivered…
    assert NOTE_LEAD not in turn3                   # …and not named as unreadable freight
    assert not list((d / "inbox").glob("msg-*.json"))   # it was consumed
