"""D278: a resumed leg is TOLD when its last action's outcome is unknown.

The engine records `assistant_action` BEFORE dispatch and the `observation` after it. A process
killed between the two — a deploy, the D99 SIGKILL auto-resume, `daemon/reap_resume.py` after a
restart — leaves the transcript ending in an action with no result, and `replay_messages` hands
that to the model as the last assistant message. The only framing the resumed leg used to get was
`rewind._RESUMED`, "continue from the last observation", which says nothing about the last action
maybe having happened. For an outward action that is the costly case: freelance-hunter sending
mail, vodafone-outage-claims filing a claim, do-my-taxes submitting a return — a model that sees
no result is likely to issue the action again.

Reviewed from getpaseo/paseo, whose daemon journals every agent message `pending` before sending
and `completed` after, and whose retry reports `agent_request_outcome_unknown` rather than
sending twice.

The kill here is a `KeyboardInterrupt` raised inside the dispatcher: the `assistant_action` event
is already on disk, the `observation` never is — byte for byte the shape a SIGKILL leaves.
"""

from __future__ import annotations

import pytest
from conftest import finish, util, write_file
from helpers import server_for, transcript_events
from rsched.engine.donothing import grounding_from
from rsched.engine.history import replay_messages, seen_paths
from rsched.engine.runtime import run_routine

TS = "20260701-070000"

#: The payload kind the engine writes for an action whose result was never recorded.
KIND = "outcome_unknown"


def _die(*_args, **_kwargs):
    """The process ending mid-dispatch. A BaseException, so no `except Exception` in the
    engine turns it into an orderly failure with an observation attached.
    """
    raise KeyboardInterrupt("killed between the two events")


def _unknowns(run_dir) -> list[dict]:
    return [e for e in transcript_events(run_dir) if e["type"] == "observation"
            and (e.get("payload") or {}).get("kind") == KIND]


def _user_text(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages if m["role"] == "user")


def _interrupted(d, server, scripted, monkeypatch, action: dict):
    """Leg one: `action` lands, the process dies before its observation is written."""
    import rsched.engine.actionroute as actionroute_mod

    scripted([action, finish()])
    monkeypatch.setattr(actionroute_mod, "dispatch_action", _die)
    with pytest.raises(KeyboardInterrupt):
        run_routine(d, server, run_ts=TS)
    monkeypatch.undo()
    events = transcript_events(d / "runs" / TS)
    assert events[-1]["type"] == "assistant_action", "the kill must leave a dangling action"
    assert events[-1]["payload"]["kind"] == action["kind"]


# --- the boot-time detection and the unknown-outcome observation ---------------------------

def test_a_resumed_leg_is_told_its_last_action_may_already_have_happened(
        make_routine, scripted, monkeypatch):
    """The whole order in one test: the leg died between the two events, and the leg that
    takes over reads that the outcome is UNKNOWN — not an action with no result at all."""
    d = make_routine(slug="unknown")
    server = server_for(d)
    _interrupted(d, server, scripted, monkeypatch, util("web-request", ["--post"]))

    ep = scripted([finish()])
    status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert status == "ok"
    assert len(_unknowns(run_dir)) == 1, "exactly one observation answers the dangling action"

    told = _user_text(ep.calls[0]["messages"])
    assert "UNKNOWN" in told
    assert "may or may not have taken effect" in told
    assert "check its effect" in told
    assert "util" in told                       # it names WHICH action is in doubt


def test_the_observation_is_a_real_transcript_event_on_the_dead_action_s_turn(
        make_routine, scripted, monkeypatch):
    """Written as a transcript event, not an engine note: the prompt stays append-only and a
    later resume replays it verbatim instead of re-authoring it."""
    d = make_routine(slug="unknown")
    server = server_for(d)
    _interrupted(d, server, scripted, monkeypatch, write_file("state/x.txt"))

    scripted([finish()])
    _status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    events = transcript_events(run_dir)
    action = next(e for e in events if e["type"] == "assistant_action")
    unknown = _unknowns(run_dir)[0]
    assert unknown["turn"] == action["turn"]
    assert events.index(unknown) == events.index(action) + 1   # it ANSWERS that action
    # replayed verbatim from the record — the same words a second leg would read
    replayed = _user_text(replay_messages(events)[0])
    assert "may or may not have taken effect" in replayed


def test_a_second_resume_does_not_add_a_second_observation(
        make_routine, scripted, monkeypatch):
    """Derived from the transcript, never from a flag: after the first resume the transcript no
    longer ends in an unanswered action, so the detection has nothing to fire on."""
    d = make_routine(slug="unknown")
    server = server_for(d)
    _interrupted(d, server, scripted, monkeypatch, write_file("state/x.txt"))

    scripted([finish()])
    run_routine(d, server, run_ts=TS, resume_from=TS)
    scripted([finish()])
    _status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert len(_unknowns(run_dir)) == 1


def test_a_run_that_finished_normally_gets_no_such_observation(
        make_routine, scripted, monkeypatch):
    """The negative that matters most: a `finish` writes no observation and needs none, so
    every ordinary run — and every conversation follow-up leg — ends in an unanswered action
    by construction. A detection that fired there would tell every resumed conversation its
    finish may not have happened."""
    d = make_routine(slug="unknown")
    server = server_for(d)
    scripted([write_file("state/x.txt"), finish()])
    assert run_routine(d, server, run_ts=TS)[0] == "ok"

    scripted([finish()])
    _status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert _unknowns(run_dir) == []


# --- the guards the resume rebuilds treat it as executing nothing -------------------------

def test_the_unknown_outcome_grounds_nothing_and_shows_no_path(
        make_routine, scripted, monkeypatch):
    """It records the ABSENCE of a result. Counting it would let a resumed leg pass the
    fabrication guard on an action nobody knows ran, and grounding its path would make
    `write_file` believe a file had been read."""
    d = make_routine(slug="unknown")
    server = server_for(d)
    _interrupted(d, server, scripted, monkeypatch, write_file("state/x.txt"))

    scripted([finish()])
    _status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    upto = transcript_events(run_dir)
    upto = upto[:upto.index(_unknowns(run_dir)[0]) + 1]      # the first leg plus the note
    count, kinds = grounding_from(upto)
    assert (count, kinds) == (0, set())
    assert seen_paths(upto) == []
