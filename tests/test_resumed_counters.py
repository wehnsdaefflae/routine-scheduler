"""D245 (a) — the two counters a RESUMED LEG must keep, rebuilt from the transcript.

The designs entry: *"`loop.failures` (the "failed twice this run" count behind the repair hint)
and `_schema_off` ("schema off for the rest of the run") reset on every leg, so after a resume
a call's second failure reads as its first."*

`tests/test_guard_scope.py` already pins the OTHER half of this — an assist that fired may not
fire again after a resume. This is the complement, and the harder direction: a call that failed
ONCE before the restart and once after it has failed TWICE in this run, so fix-the-cause's
moment is NOW. With the tally reset, that moment never arrives at all: the run that most needs
"stop tuning this and change route" is exactly the one whose legs keep splitting the evidence.

Both facts are already READABLE from the transcript the resumed leg boots with — no side file,
no new event type:

* a failure is `exitgate.failure_tally_key(ctx, action, obs)` over an action and its own
  observation, recorded as a paired `assistant_action` + `observation`;
* `_schema_off` already writes an `error` event saying so ("provider response-format disabled
  for the rest of the run"), so the fact was on the record and simply never read back.
"""

from __future__ import annotations

import json

from conftest import finish, util, write_file
from helpers import server_for, transcript_events
from rsched import assists as lib
from rsched.endpoints.base import EndpointError
from rsched.engine.runtime import run_routine
from test_assists import TS, _hold_rule, _rule


def _down() -> EndpointError:
    """The leg ends here — the shape a crash or a restart leaves: an unauthored finish."""
    return EndpointError("provider down")


def _fires(d) -> int:
    state = lib.state_path(d)
    return json.loads(state.read_text())["fix-the-cause/m"]["fires"] if state.is_file() else 0


def test_a_call_that_failed_before_a_resume_is_on_its_second_failure_after_it(
        make_routine, scripted):
    """The entry's own sentence, as behaviour. One failure per leg is two failures of one
    call in one run, and that is where fix-the-cause has something to say."""
    d = make_routine(slug="counters")
    server = server_for(d)
    _rule(server, "fix-the-cause", "observation", "repeated-failure", "change route now")
    _hold_rule(d, ["fix-the-cause"])

    scripted([util("nonexistent-util"), _down()])
    assert run_routine(d, server, run_ts=TS)[0] == "failed"
    assert _fires(d) == 0               # one failure is information, not a pattern

    scripted([util("nonexistent-util"), write_file("state/a.txt"), finish()])
    status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert status == "ok"

    assert _fires(d) == 1               # the SECOND failure of that call, across the restart
    fired = [e for e in transcript_events(run_dir) if e["type"] == "observation"
             and "fix-the-cause/m" in (e["payload"].get("assists") or [])]
    assert len(fired) == 1
    assert fired[0]["payload"]["kind"] == "util"


def test_a_different_call_failing_after_the_resume_is_still_its_first_failure(
        make_routine, scripted):
    """The counter is per CALL, and the rebuild must not flatten that: carrying a tally that
    fires on any second failure anywhere would make the line meaningless."""
    d = make_routine(slug="percall")
    server = server_for(d)
    _rule(server, "fix-the-cause", "observation", "repeated-failure", "change route now")
    _hold_rule(d, ["fix-the-cause"])

    scripted([util("nonexistent-util"), _down()])
    assert run_routine(d, server, run_ts=TS)[0] == "failed"

    scripted([util("another-missing-util"), write_file("state/a.txt"), finish()])
    assert run_routine(d, server, run_ts=TS, resume_from=TS)[0] == "ok"
    assert _fires(d) == 0


def test_a_held_action_is_not_counted_as_a_failure_by_the_rebuild():
    """D243's defect, one level down: an action the engine never RAN must not be tallied as a
    failed call. The live loop gates its tally on `donothing.grounds_a_finish`, so a rebuild
    that forgot the gate would count holds the live run never counted."""
    from rsched.engine import exitgate, resumedcounters

    held = {"kind": "reminder_hold", "reminders": [{"id": "rem-1"}]}
    events = [{"type": "assistant_action", "turn": 3, "payload": util("nope")},
              {"type": "observation", "turn": 3, "payload": held}]
    assert resumedcounters.failures_from(None, events) == {}
    # and the live seam agrees about what a failure is
    assert exitgate.failure_tally_key(None, util("nope"), held) == ""


def test_the_rebuilt_tally_counts_each_call_by_the_live_key():
    """Directly, over a transcript: the same key the live loop tallies under, so the predicate
    reading it cannot tell a rebuilt leg from a fresh one."""
    from rsched.engine import resumedcounters

    def pair(turn, action, obs):
        return [{"type": "assistant_action", "turn": turn, "payload": action},
                {"type": "observation", "turn": turn, "payload": obs}]

    bad = {"kind": "util", "exit": 2, "error": "no such util"}
    ok = {"kind": "util", "exit": 0}
    events = (pair(1, util("nope"), bad) + pair(2, util("nope"), bad)
              + pair(3, util("fine"), ok) + pair(4, util("other"), bad))
    assert resumedcounters.failures_from(None, events) == {"util:nope": 2, "util:other": 1}


def test_an_observation_with_no_action_before_it_is_skipped():
    """A transcript is not always well-formed: a leg killed between the two events leaves an
    observation with no action, and the rebuild may not guess which call it was."""
    from rsched.engine import resumedcounters

    events = [{"type": "observation", "turn": 9,
               "payload": {"kind": "util", "exit": 2, "error": "boom"}}]
    assert resumedcounters.failures_from(None, events) == {}


def test_schema_off_stays_off_across_a_resume():
    """The second half. Once shedding has rescued a run twice the provider schema is off for
    the REST of the run — and a restart is not the end of the run. The fact is already on the
    transcript as an `error` event; this reads it back."""
    from rsched.engine import resumedcounters

    events = [{"type": "error", "turn": 7, "payload": {
        "where": "schema", "attempt": 0,
        "message": "provider response-format disabled for the rest of the "
                   "run: repeat-streak shedding rescued it twice — the "
                   "grammar is suppressing fields for this model"}}]
    assert resumedcounters.schema_off_from(events) is True
    assert resumedcounters.schema_off_from([]) is False
    # an unrelated schema error is NOT the switch
    assert resumedcounters.schema_off_from([{"type": "error", "payload": {
        "where": "schema", "attempt": 1, "message": "invalid JSON in the reply"}}]) is False


def test_the_rebuild_is_registered_in_the_guard_scope():
    """A rebuild nobody calls is a description. `guardscope.rebuild` is the one seam a resumed
    leg's boot goes through, so the layer has to be in its list."""
    import inspect

    from rsched.engine import guardscope

    assert "resumedcounters" in inspect.getsource(guardscope.rebuild)
