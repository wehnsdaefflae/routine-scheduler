"""F715 — what a run's SUMMARY says about a background call NOBODY READ (authorized by D195).

`engine/background.settle` has always distinguished two losses in the TRANSCRIPT: a call that
LANDED with no turn left to read it (`unread: true`, its real result recorded) and one still
RUNNING whose result is gone (`abandoned: true`). Only the second reached `result.md` — so a run
that backgrounded a call, finished before reading it, and drew its conclusions from evidence it
never saw said NOTHING about that on the one surface the operator and the next run actually read.

D195's mechanism half: an honesty instruction lifts flaw-flagging where the model CAN see the
flaw, and its one non-improving scenario is the hidden pending tool call — exactly this. A run
cannot flag what it was never shown, so the ENGINE says it.

These tests sit beside `test_background_actions.py` rather than inside it because that module is
AT its size-ratchet ceiling (1063 lines in `tests/size_ratchet.json`) and a file over the house
bar may not grow — the same rule that put the wording in `engine/background_summary.py`.

DETERMINISM, not sleeps. "Landed but never read" is a race if it is built out of timings: the
call must finish AFTER the last turn boundary (so `collect` cannot deliver it) and BEFORE
`settle` gives up (so it is not `abandoned`). Here the backgrounded handler blocks on an Event
that the FINISH turn's own scripted reply sets — the scripted endpoint runs a callable reply —
which forces the order: boundary (nothing to collect) → model call releases the work and waits
for it → finish → `settle` sees it landed. No sleep decides anything.
"""

import threading
from types import SimpleNamespace

import pytest

from conftest import finish, util
from helpers import server_for, set_capabilities
from rsched.engine import background_summary
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20260708-090000"

#: The two sentences, as the tests address them. Distinct by design: the abandoned call LOST its
#: result, the landed one HAS a result nobody read.
UNREAD_PHRASE = "LANDED after this run's last turn and were never read"
ABANDONED_PHRASE = "still running at run end"


def _server(routine_dir):
    s = server_for(routine_dir)
    set_capabilities(routine_dir, actions=["memory_read", "memory_write"])
    return s


def _call(handle, kind, brief):
    """A settled background call, as `settle_note` reads one (duck-typed on three fields)."""
    return SimpleNamespace(handle=handle, kind=kind, brief=brief)


# ---- the renderer, as a pure function ----------------------------------------------------------

def test_nothing_unread_and_nothing_abandoned_is_no_line_at_all():
    """The ordinary run. A summary that carried a bracket about background calls on every run
    would teach every reader to skip the brackets."""
    assert background_summary.settle_note(unread=[], abandoned=[]) == ""


def test_the_two_losses_get_two_different_sentences():
    """The abandoned call's result is GONE; the landed one's result EXISTS and nobody read it.
    A surface folding them could not tell an operator which of the two happened."""
    unread_only = background_summary.settle_note(
        unread=[_call("bg1", "util", "websearch")], abandoned=[])
    abandoned_only = background_summary.settle_note(
        unread=[], abandoned=[_call("bg2", "llm", "summarise")])

    assert UNREAD_PHRASE in unread_only
    assert ABANDONED_PHRASE not in unread_only
    assert "Their results are NOT lost" in unread_only, unread_only
    assert "`unread`" in unread_only, (
        "the landed line must point at the transcript row that HAS the result — otherwise the "
        "operator is told something was missed and not where to find it")

    assert ABANDONED_PHRASE in abandoned_only
    assert UNREAD_PHRASE not in abandoned_only
    assert "Their results are lost" in abandoned_only, abandoned_only


def test_each_line_names_how_many_and_which_handles():
    note = background_summary.settle_note(
        unread=[_call("bg1", "util", "websearch"), _call("bg3", "read_file", "docs/x.md")],
        abandoned=[_call("bg2", "script", "crunch")])
    assert "2 background action(s)" in note, note
    assert "1 background action(s)" in note, note
    for needle in ("`bg1` (util: websearch)", "`bg3` (read_file: docs/x.md)",
                   "`bg2` (script: crunch)"):
        assert needle in note, (needle, note)


def test_both_losses_in_one_run_are_two_lines_not_one():
    note = background_summary.settle_note(
        unread=[_call("bg1", "util", "a")], abandoned=[_call("bg2", "util", "b")])
    lines = note.splitlines()
    assert len(lines) == 2, note
    assert all(line.startswith("[") and line.endswith("]") for line in lines), note


# ---- through a real run: both directions reach result.md ---------------------------------------

@pytest.fixture
def gated_util(monkeypatch):
    """A `util` dispatch whose calls are driven by EVENTS, never by sleeps.

    `args=["gate"]` blocks until `release` is set and then announces it has landed;
    `args=["hang"]` blocks until the test's teardown releases it, so it is still running when
    `settle` looks. Everything else returns at once.
    """
    import rsched.engine.executor as executor_mod

    gate = SimpleNamespace(release=threading.Event(), landed=threading.Event(),
                           hang=threading.Event())

    def dispatch(action, ctx):
        args = action.get("args") or []
        if args[:1] == ["gate"]:
            gate.release.wait(timeout=20)
            out = "the gated result"
            gate.landed.set()
        elif args[:1] == ["hang"]:
            gate.hang.wait(timeout=20)
            out = "never seen"
        else:
            out = "immediate"
        return {"kind": "util", "name": action.get("name"), "exit": 0, "stdout": out,
                "stderr": ""}

    monkeypatch.setitem(executor_mod.DISPATCH, "util", dispatch)
    yield gate
    gate.release.set()
    gate.hang.set()


def _finish_releasing(gate, *, wait_for_landing=True):
    """The finish turn's scripted reply, as a CALLABLE: it releases the gated background call
    and (by default) waits for it to land before the finish action is even returned. That is
    what makes "landed after the last turn boundary" a fact rather than a hope."""
    def reply():
        gate.release.set()
        if wait_for_landing:
            assert gate.landed.wait(timeout=20), "the gated background call never finished"
        return finish(summary="done" + " ." * 20)

    return reply


def test_a_call_that_landed_unread_is_named_in_the_summary(make_routine, scripted, gated_util):
    """The F715 case itself: the result EXISTS, the run never saw it, and until now result.md
    said nothing — the run's conclusions rested on evidence it was never shown."""
    d = make_routine(slug="bgunread")
    scripted([
        {**util("websearch", args=["gate"], say="Backgrounding a fetch."), "background": True},
        _finish_releasing(gated_util),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status

    result = (run_dir / "result.md").read_text(encoding="utf-8")
    assert UNREAD_PHRASE in result, result[-800:]
    assert "`bg1` (util: websearch)" in result, result[-800:]
    assert ABANDONED_PHRASE not in result, (
        "a call that LANDED was reported as a lost result — the two losses are different and "
        f"the summary must not fold them: {result[-800:]}")

    events, _ = read_events(run_dir / "transcript.jsonl")
    unread = [e["payload"] for e in events
              if e["type"] == "observation" and e["payload"].get("unread")]
    assert len(unread) == 1, unread
    assert "the gated result" in str(unread[0]), (
        "the summary points the reader at a transcript row that must hold the real result")


def test_a_run_that_read_everything_gets_no_background_line(make_routine, scripted, gated_util):
    """The silence has to stay silent on the ordinary run: the call lands at a turn boundary,
    the run READS it, and the summary says nothing about background work at all."""
    d = make_routine(slug="bgread")

    def release_then_work():
        gated_util.release.set()
        assert gated_util.landed.wait(timeout=20)
        return util("list", say="It has landed; the next boundary delivers it.")

    scripted([
        {**util("websearch", args=["gate"], say="Backgrounding a fetch."), "background": True},
        release_then_work,
        util("list", say="Reading on."),
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status

    result = (run_dir / "result.md").read_text(encoding="utf-8")
    assert UNREAD_PHRASE not in result, result[-800:]
    assert ABANDONED_PHRASE not in result, result[-800:]
    assert "background action(s)" not in result, result[-800:]

    events, _ = read_events(run_dir / "transcript.jsonl")
    delivered = [e["payload"] for e in events if e["type"] == "observation"
                 and e["payload"].get("background") and not e["payload"].get("started")]
    assert len(delivered) == 1 and not delivered[0].get("unread"), delivered


def test_a_landed_call_and_a_running_one_are_reported_as_two_separate_losses(
        make_routine, scripted, gated_util, monkeypatch):
    """Both can happen in one run, and the summary must say both — one result exists unread,
    the other is gone. `SETTLE_SECONDS` is shortened so the still-running call is abandoned
    promptly; nothing about the ORDER depends on that number."""
    import rsched.engine.background as background_mod

    monkeypatch.setattr(background_mod, "SETTLE_SECONDS", 0.2)
    d = make_routine(slug="bgboth")
    scripted([
        {**util("websearch", args=["gate"], say="Backgrounding a fetch."), "background": True},
        {**util("llm-complete", args=["hang"], say="Backgrounding a slow one."),
         "background": True},
        _finish_releasing(gated_util),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status

    result = (run_dir / "result.md").read_text(encoding="utf-8")
    assert UNREAD_PHRASE in result, result[-800:]
    assert ABANDONED_PHRASE in result, result[-800:]
    assert "`bg1` (util: websearch)" in result, result[-800:]
    assert "`bg2` (util: llm-complete)" in result, result[-800:]

    events, _ = read_events(run_dir / "transcript.jsonl")
    payloads = [e["payload"] for e in events if e["type"] == "observation"]
    assert [p["handle"] for p in payloads if p.get("unread")] == ["bg1"], payloads
    assert [p["handle"] for p in payloads if p.get("abandoned")] == ["bg2"], payloads
