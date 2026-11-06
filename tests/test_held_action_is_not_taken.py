"""A HELD or REFUSED action is not one the run TOOK — D243 (review item E4.3).

`finishgate` handed the claim guard `{r["kind"] for r in loop.turn_records}` as the set of
actions this run took. But `turn_records` is appended at the ASSISTANT_ACTION stage
(`engine/loop.py:269`), before dispatch — so it holds every action the model EMITTED, including

* one a consequence reminder or a rule assist HELD (`engine/hold.py`: it did not run), and
* one the reserved finish turn refused.

So a summary saying "reported the outage" passed the guard on a `report` that was held and never
re-emitted: the one guard whose whole job is to catch narrated-but-unperformed work was itself
reading an unperformed action as performed. The engine already has the right predicate and uses
it for the OTHER half of the same question — `donothing.grounds_a_finish`, which `loop` and the
resume rebuild both ask to count `executed_actions` (a hold grounds no finish). "Taken" now means
what EXECUTED, by that same predicate, so the two cannot drift.

Also here, from the same review item: `loop.assist_undo_points` and `loop.reminder_owed` are set
by their layers' `configure` but were not declared on `EngineLoop`, so the class contract did not
carry them.
"""

from __future__ import annotations

from conftest import finish, write_file
from helpers import prompt_text, reminder, server_for, set_capabilities
from rsched import reminders as store
from rsched.engine.loop import EngineLoop
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20260906-090000"


def report(title: str, detail: str, target: str = "self-audit") -> dict:
    """A `report` action as the model emits one (conftest has no helper for this kind)."""
    return {"say": "Raising it.", "kind": "report", "title": title, "detail": detail,
            "target": target}


def _run(make_routine, scripted, replies, *, local=(), **caps):
    d = make_routine(slug="heldr")
    set_capabilities(d, reminders="local", **caps)
    if local:
        store.save_local(d, list(local), {})
    ep = scripted(replies)
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    return d, ep, status, events


def test_a_summary_claiming_a_held_report_is_rejected(make_routine, scripted):
    """THE DEFECT. The run emits a `report` that a reminder holds — so nothing is filed — and
    then finishes `ok` claiming it. The claim guard must reject that finish; before the fix the
    held action was in `turn_records`, so `report` read as taken and the claim sailed through.

    The second finish, with the claim dropped, must then be accepted — the guard's job is to
    make the run either take the action or stop saying it did.
    """
    caution = "a report to that routine is read by nobody until Monday"
    _d, ep, status, events = _run(
        make_routine, scripted,
        [write_file("state/probe.txt", content="x"),      # something really executed
         report(title="the outage", detail="the key gap", target="self-audit"),
         finish(summary="Steady run. Filed report to self-audit about the key gap."),
         finish(summary="Steady run. Nothing to escalate this run.")],
        local=[reminder(rid="rem-r", regex=r"^report ", desc=caution)])

    holds = [e for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "reminder_hold"]
    assert len(holds) == 1, "the report was not held — the test proves nothing"
    assert "ACTION HELD" in prompt_text(ep)

    rejected = [e for e in events if e["type"] == "observation"
                and e["payload"].get("kind") == "finish" and e["payload"].get("rejected")]
    assert len(rejected) == 1, "a summary claiming a HELD report was accepted"
    assert rejected[0]["payload"].get("unbacked_claims") == ["report"]
    assert status == "ok"            # the second finish, without the claim, stands


def test_an_executed_report_still_backs_its_claim(make_routine, scripted):
    """The other half of the contract, which must NOT change: a report that really ran backs a
    summary that says so. Without this the fix could pass by rejecting everything.
    """
    _d, _ep, status, events = _run(
        make_routine, scripted,
        [report(title="the outage", detail="the key gap", target="self-audit"),
         finish(summary="Steady run. Filed report to self-audit about the key gap.")])

    rejected = [e for e in events if e["type"] == "observation"
                and e["payload"].get("kind") == "finish" and e["payload"].get("rejected")]
    assert rejected == [], "an executed report did not back its own claim"
    assert status == "ok"


def test_a_resumed_leg_rebuilds_the_kinds_behind_its_count(make_routine, scripted):
    """The counter and the kind set are rebuilt from the SAME predicate on resume, so an
    earlier leg's report still backs a later leg's summary.

    This is the half that would have broken quietly: rebuilding `executed_actions` without
    `executed_kinds` leaves the guard an empty set, and every legitimate claim in the resumed
    leg's summary is rejected — a run that did the work being told it did not.
    """
    d = make_routine(slug="resumer")
    set_capabilities(d, reminders="local")
    # The first leg files the report and ends WITHOUT claiming it, so what the resumed leg's
    # claim rests on is the rebuild and nothing else.
    scripted([report(title="the outage", detail="the key gap", target="self-audit"),
              finish(summary="Raised it; wrapping up here.")])
    run_routine(d, server_for(d), run_ts=TS)

    # the resumed leg claims the report the FIRST leg filed
    scripted([finish(summary="Filed report to self-audit about the key gap.")])
    status2, run_dir2 = run_routine(d, server_for(d), run_ts=TS, resume_from=TS)
    events2, _ = read_events(run_dir2 / "transcript.jsonl")

    rejected = [e for e in events2 if e["type"] == "observation"
                and e["payload"].get("kind") == "finish" and e["payload"].get("rejected")]
    assert rejected == [], "a resumed leg rejected a claim its first leg's report backs"
    assert status2 == "ok"


def test_the_two_attributes_their_layers_set_are_declared_on_the_loop():
    """`assist.configure` sets `assist_undo_points` and `remind.configure` sets
    `reminder_owed`; neither was in the class's annotations, so the contract the other
    attributes keep did not cover them.
    """
    for name in ("assist_undo_points", "reminder_owed"):
        assert name in EngineLoop.__annotations__, f"{name} is set by a layer but undeclared"
