"""`do_nothing` (D186): a run fired on a schedule states, as a first-class action, that
nothing was due — and WHY.

Before this kind existed a run that found nothing due had two options: `finish`, which is
right and is still right, or an invented action — a `read_file` of something it did not need,
a `util list` it had already seen — to have done SOMETHING. The operator decided the run
should be able to say it plainly instead, with the reasoning attached.

THE REASONING IS `say`, not a new field. `say` already rides every kind, the transcript
already renders it for every kind (static/components/transcript.js), and the console already
publishes it. What `say` does NOT have anywhere else is a non-emptiness check — the schema
sets no minLength and `KIND_FIELDS` never lists it among a kind's required fields — so this
kind, whose entire content is its reasoning, enforces one of its own.

THE FABRICATION GUARD IS THE DESIGN DECISION (the brief's open question). A run that does
nothing but `do_nothing` has established nothing, so it must not be able to ground a top-level
`finish(ok)` that the guard would otherwise refuse. `do_nothing` is therefore a NON-GROUNDING
action: it dispatches, it is observed, it is in the transcript — and `loop.executed_actions`,
the counter the guard reads, does not count it. The opposite choice would have turned the
guard off with one word: `do_nothing` then `finish(ok)` would pass with no work behind it,
which is exactly the hallucinated-completion shape the guard exists to refuse. An IDLE run
still finishes — `finish(ok)` is unaffected after any real action, and the guard was never a
bar against ending a run, only against claiming an unobserved one.
"""

from conftest import finish, write_file
from helpers import server_for, set_capabilities
from rsched.engine.actionschema import KINDS
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20260708-070000"


def do_nothing(say="Nothing is due: the three sources were read and none has changed."):
    return {"say": say, "kind": "do_nothing"}


def _run(make_routine, scripted, replies, *, slug="testr"):
    d = make_routine(slug=slug)
    set_capabilities(d, actions=["memory_read", "memory_write"])
    scripted(replies)
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    return d, status, events


def _observations(events, kind):
    return [e["payload"] for e in events
            if e["type"] == "observation" and e["payload"].get("kind") == kind]


def test_do_nothing_is_an_action_kind():
    assert "do_nothing" in KINDS


def test_do_nothing_validates_with_only_a_say():
    """No fields of its own: the reasoning IS `say`, which rides every kind already."""
    from rsched.engine.actions import validate_action

    assert validate_action(do_nothing()) == []


def test_do_nothing_requires_a_non_empty_reasoning():
    """The whole point of the kind is the reasoning. `say` carries no non-emptiness check
    anywhere else in the contract (no minLength, never in a kind's required fields), so the
    kind whose entire content is its `say` enforces one — inside the schema-retry cycle, where
    it costs no turn."""
    from rsched.engine.actions import validate_action

    for blank in ("", "   ", "\n"):
        problems = validate_action({"say": blank, "kind": "do_nothing"})
        assert any("say" in p for p in problems), f"{blank!r} was accepted"
    # …and a different kind with a blank `say` is NOT newly rejected: this check is this
    # kind's, not a change to the shared field.
    assert validate_action({"say": "", "kind": "subruns"}) == []


def test_do_nothing_takes_no_other_fields():
    """A run reaching for a `reason`/`summary` field is told the reasoning goes in `say`."""
    from rsched.engine.actions import validate_action

    problems = validate_action({"say": "s", "kind": "do_nothing", "reason": "nothing due"})
    assert any("reason" in p for p in problems)


def test_the_observation_reflects_the_reasoning_back():
    """The run must be able to READ what it just recorded — an observation that said nothing
    would make the kind indistinguishable from a no-op."""
    from rsched.engine.observations import format_observation

    text = format_observation({"kind": "do_nothing", "reasoning": "no new mail since Friday"})
    assert "do_nothing" in text
    assert "no new mail since Friday" in text


def test_a_do_nothing_turn_is_dispatched_and_recorded(make_routine, scripted):
    """End to end: the action runs, is observed, and its reasoning is in the transcript."""
    _d, status, events = _run(
        make_routine, scripted,
        [do_nothing("Nothing is due: the feed has no entries newer than the marker."),
         write_file("state/real.txt"), finish()])
    assert status == "ok"
    obs = _observations(events, "do_nothing")
    assert len(obs) == 1
    assert "no entries newer than the marker" in obs[0]["reasoning"]
    actions = [e["payload"] for e in events if e["type"] == "assistant_action"]
    assert any(a["kind"] == "do_nothing" for a in actions)


def test_do_nothing_alone_does_not_ground_an_ok_finish(make_routine, scripted):
    """THE decision this kind turns on. `do_nothing` establishes nothing, so a top-level
    `finish(ok)` behind nothing else is still the hallucinated completion the fabrication
    guard refuses (engine/finishgate.py). Were it to count, the guard would be switchable off
    by one word."""
    d, status, events = _run(
        make_routine, scripted,
        [do_nothing(), finish(), write_file("state/real.txt"), finish()])
    rejected = [e for e in events if e["type"] == "observation"
                and e["payload"].get("rejected")]
    assert rejected, "finish(ok) after nothing but a do_nothing must be refused"
    assert (d / "state" / "real.txt").exists()   # the run went on and did real work
    assert status == "ok"


def test_a_run_that_did_real_work_may_do_nothing_and_still_finish(make_routine, scripted):
    """The other half: `do_nothing` never WITHDRAWS grounding a real action established.
    A run that worked, then found nothing further due, finishes normally."""
    _d, status, events = _run(
        make_routine, scripted,
        [write_file("state/real.txt"),
         do_nothing("The remaining two sources are unchanged; nothing further is due."),
         finish()])
    assert status == "ok"
    rejected = [e for e in events if e["type"] == "observation"
                and e["payload"].get("rejected")]
    assert not rejected, "a finish after real work must not be refused"


def test_do_nothing_is_available_without_a_workflow_allowlist():
    """It is NOT an ALWAYS_KIND: a workflow that narrows `tools:` is narrowing deliberately,
    and `finish` already covers "this run must be able to end". But a workflow that names it
    gets it, and an unrestricted one has it like any other kind."""
    from rsched.engine.actions import ALWAYS_KINDS, validate_action
    from rsched.engine.kindsurface import effective_kinds

    assert "do_nothing" not in ALWAYS_KINDS
    assert "do_nothing" in effective_kinds(None, None)
    assert validate_action(do_nothing(), {"do_nothing", "finish"}) == []
    problems = validate_action(do_nothing(), {"read_file", "finish"})
    assert problems and "not available in this workflow" in problems[0]


def test_do_nothing_may_not_be_backgrounded():
    """Nothing to defer: the action's whole effect is the statement it records."""
    from rsched.engine.actions import BACKGROUNDABLE_KINDS, validate_action

    assert "do_nothing" not in BACKGROUNDABLE_KINDS
    problems = validate_action({**do_nothing(), "background": True})
    assert any("background" in p for p in problems)
