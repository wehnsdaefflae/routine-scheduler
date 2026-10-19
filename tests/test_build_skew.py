"""F677: a release landing mid-run must not take an in-flight run's finish gate with it.

The specimen is 2026-10-12, 07:34-08:26: three runs (scheduler-builder, llmsectest-weekday,
global-utils-review) filed the instance's first three `finish_gate_failed` events, all
`AttributeError: 'GrantPolicy' object has no attribute 'jobs_on'` at engine/jobops.py:43.
0.416.0 (committed 07:23) renamed taskops.py -> jobops.py and tasks_on -> jobs_on while those
runs already HELD a GrantPolicy made by the build they booted with — and the finish path is
reached through one deliberately lazy import (engine/finishstands.py), so the gate and its whole
module subtree were loaded FRESH FROM DISK at the finish turn. It failed open: all three runs
finished, and the jobs gate silently did not run.

Both halves are tested here: the finish path is PINNED at run start, so a held object meets the
build that made it; and a held object that still cannot answer is answered from the routine's own
capabilities — DATA, not a held object — with the mismatch NAMED on the record rather than
reaching the operator as a bare AttributeError.
"""

import sys
from types import ModuleType

from helpers import health_events, run_context, server_for, set_capabilities
from rsched import jobs
from rsched.engine import buildskew, finishgate, finishstands, jobops
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20261012-073400"


class PolicyFromTheOldBuild:
    """What those three runs were holding: the policy object as the PREVIOUS build left it.

    0.416.0 renamed the property `tasks_on` to `jobs_on`; an instance made before the release
    answers the old name and has never heard of the new one. The `jobs` SETTING the policy
    carries is untouched by the rename — but reading it off this object is reading the held
    object again, which is the thing that cannot be trusted here.
    """

    tasks_on = True
    jobs = "on"


def _held_policy_run(make_routine, slug, *, jobs_setting="on"):
    """A run whose HELD policy is that old-build object, with the routine's own config saying
    whether it keeps jobs."""
    d = make_routine(slug=slug)
    set_capabilities(d, jobs=jobs_setting)
    ctx = run_context(d, TS, grants=PolicyFromTheOldBuild(), server=server_for(d))
    return d, ctx


class _Loop:
    """Only what `jobops.finish_owed` reads of a loop."""

    def __init__(self, ctx):
        self.ctx = ctx
        self._finish_reserved = False


# ---- the gate keeps WORKING under the mismatch -------------------------------------------

def test_a_policy_held_from_the_old_build_does_not_crash_the_jobs_gate(make_routine):
    """jobops.py:43 read `ctx.grants.jobs_on` off the held object and raised — at the finish
    turn, inside the gate, where it cost three runs their judging."""
    _d, ctx = _held_policy_run(make_routine, "skewon")

    assert ctx.grants.tasks_on is True          # the stand-in answers the name 0.415.x knew
    assert jobops.enabled(ctx) is True          # answered from config, not from the stale object


def test_the_jobs_gate_still_reads_off_when_the_routine_keeps_no_jobs(make_routine):
    """Not weakened into a yes: the fallback asks the routine's OWN capabilities, so a routine
    without the job layer still has no job gate."""
    _d, ctx = _held_policy_run(make_routine, "skewoff", jobs_setting="off")

    assert jobops.enabled(ctx) is False


def test_the_jobs_gate_still_holds_a_run_to_its_due_jobs_under_the_mismatch(make_routine):
    """The gate must really RUN, not merely not-crash: a job due this run and not checkpointed
    still sets the finish aside, which is exactly what the three runs of 2026-10-12 lost."""
    d, ctx = _held_policy_run(make_routine, "skewdue")
    jobs.save(d, {"version": 1, "deleted": [], "jobs": [
        {"id": "alpha", "title": "Alpha project", "brief": "steward alpha", "state": "active"}],
        "run": {"run_id": ctx.run_id, "due": [{"id": "alpha", "why": ["due by its clock"]}],
                "done": {}, "open": ""}})

    owed = jobops.finish_owed(_Loop(ctx))

    assert owed is not None
    assert "GATED PROCESSING" in owed and "alpha" in owed


def test_the_mismatch_is_recorded_once_naming_the_object_and_the_attribute(make_routine):
    """A skew the engine worked around is still a mid-run release nobody announced — it goes on
    the run's own record, naming WHAT disagreed, not just that something did."""
    _d, ctx = _held_policy_run(make_routine, "skewrec")

    jobops.boot(ctx)

    events, _ = read_events(ctx.run_dir / "transcript.jsonl")
    skews = [e for e in events if e["type"] == "error" and e["payload"].get("build_skew")]
    assert len(skews) == 1
    message = skews[0]["payload"]["message"]
    assert "PolicyFromTheOldBuild" in message and "jobs_on" in message
    assert "mismatch" in message


# ---- the SENTENCE: a reader can act on it -------------------------------------------------

def test_the_mismatch_sentence_names_both_sides_and_the_cause():
    line = buildskew.describe(PolicyFromTheOldBuild(), "jobs_on")

    assert "PolicyFromTheOldBuild" in line and "jobs_on" in line
    assert "build" in line and "F677" in line
    assert len(line) <= 260          # it rides a health detail capped at 500 with the frame


def _attribute_error(holder: object, name: str) -> AttributeError:
    """The AttributeError the INTERPRETER raises for a missing attribute — `name` and `obj`
    filled in by Python itself, which is what makes the diagnosis possible at all."""
    try:
        getattr(holder, name)
    except AttributeError as exc:
        return exc
    raise AssertionError(f"{name} must be missing from {holder!r}")


def test_an_attribute_error_off_a_held_object_is_recognised():
    exc = _attribute_error(PolicyFromTheOldBuild(), "jobs_on")

    assert "jobs_on" in buildskew.from_error(exc)


def test_an_ordinary_attribute_error_is_not_dressed_up_as_a_release():
    """False positives would make the sentence worthless: a typo on a builtin is a bug in the
    code, not two builds disagreeing."""
    exc = _attribute_error({}, "jobs_on")

    assert buildskew.from_error(exc) == ""


# ---- the finish path is PINNED at run start ----------------------------------------------

def test_the_pinned_gate_is_used_even_when_a_new_build_lands_mid_run(monkeypatch):
    """The 2026-10-12 mechanism: `from . import finishgate` resolves at the FINISH turn, so a
    release that landed since boot is what the run's held objects meet. Pinned at run start, the
    finish uses the gate this process booted with.
    """
    finishstands.pin()
    impostor = ModuleType("rsched.engine.finishgate")
    impostor.check_finish = lambda loop, action, ctx: "from the new build on disk"
    monkeypatch.setitem(sys.modules, "rsched.engine.finishgate", impostor)
    monkeypatch.setattr(sys.modules["rsched.engine"], "finishgate", impostor)
    monkeypatch.setattr(finishgate, "check_finish", lambda loop, action, ctx: "the booted gate")

    assert finishstands.check_finish_or_stand(None, {"status": "ok", "summary": "x"},
                                              None) == "the booted gate"


def test_a_caller_that_never_pinned_still_resolves_the_gate(monkeypatch):
    """A unit test or a tool calls straight in. The guard must not depend on a pin it never got
    — it resolves the gate itself, exactly as it always did."""
    monkeypatch.delitem(finishstands._PINNED, "gate", raising=False)
    monkeypatch.setattr(finishgate, "check_finish", lambda loop, action, ctx: "resolved")

    assert finishstands.check_finish_or_stand(None, {"status": "ok", "summary": "x"},
                                              None) == "resolved"


def test_a_real_run_pins_the_finish_path_before_its_first_turn(make_routine, scripted):
    finishstands._PINNED.pop("gate", None)
    d = make_routine(slug="pinned")
    # a probe before the finish: an ok-finish that executed nothing is a fabrication the
    # finish gate rejects, which would exhaust the scripted replies
    scripted([{"say": "Doing work.", "kind": "util", "name": "nope", "args": []},
              {"say": "done", "kind": "finish", "status": "ok", "summary": "pinned"}])

    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)

    assert status == "ok"
    assert finishstands._PINNED.get("gate") is finishgate


# ---- and when the gate genuinely cannot evaluate, the event SAYS so ----------------------

def test_the_health_event_names_the_mismatch_not_just_the_exception(make_routine, scripted,
                                                                    monkeypatch):
    """`finish_gate_failed` already existed and already carried the exception. What a reader
    could not act on was WHY: the three rows of 2026-10-12 said only
    `AttributeError: 'GrantPolicy' object has no attribute 'jobs_on'`, which reads as an engine
    bug rather than as a release that landed under a running run.
    """
    held = PolicyFromTheOldBuild()

    def exploding_on_a_held_object(loop, action, ctx):
        raise AttributeError("'GrantPolicy' object has no attribute 'jobs_on'",
                             name="jobs_on", obj=held)

    monkeypatch.setattr(finishgate, "check_finish", exploding_on_a_held_object)
    finishstands._PINNED["gate"] = finishgate
    d = make_routine(slug="skewgate")
    scripted([{"say": "Doing work.", "kind": "util", "name": "nope", "args": []},
              {"say": "done", "kind": "finish", "status": "partial", "summary": "one release"}])

    status, run_dir = run_routine(d, server_for(d), run_ts=TS)

    assert status == "partial"                  # the run's own status, carried through
    rows = health_events(d.parent, event=finishstands.EVENT, routine="skewgate")
    assert len(rows) == 1
    detail = rows[0]["detail"]
    assert "jobs_on" in detail and "mismatch" in detail and "STOOD" in detail

    events, _ = read_events(run_dir / "transcript.jsonl")
    errors = [e for e in events if e["type"] == "error"
              and e["payload"].get("where") == "finishgate"]
    assert len(errors) == 1 and "jobs_on" in errors[0]["payload"]["build_skew"]
