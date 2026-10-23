"""An aborted run NAMES the actor that stopped it (F751).

The defect: the engine caught `RunAborted` and finished with "Run aborted by the user/daemon." —
a disjunction, so the record attributed the stop to nobody. The specimen, fau:20261016-125013,
was launched, aborted at turn 0 while carrying six jobs forward, and its whole account of who
did it is those two words.

Pinned here: each actor's finish text and its machine-readable pair on the finish event (`actor`
+ `cause`, the latter in `runner_reap.classify_cause`'s vocabulary so the health stream stays
countable by one filter); the honest fallback when nothing was recorded; the breadcrumb's pid
stamp (a resume reuses the run dir, so a foreign mark must never speak for a later leg); and
that every door writes the breadcrumb BEFORE it signals, since after the signal there is no
later moment in which to say who asked.
"""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace

import pytest

from conftest import finish, spawn
from helpers import health_events, server_for
from rsched import abortmark
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.paths import atomic_write_json, read_json

TS = "20261023-120000"


@pytest.fixture(autouse=True)
def _no_stale_signal():
    """The signal record is process-wide, like the abort flag it accompanies — and one test
    process serves many runs, so each test starts with nothing recorded.
    """
    abortmark.forget()
    yield
    abortmark.forget()


def _server(routine_dir):
    return server_for(routine_dir)


# -- the vocabulary and its sentences ------------------------------------------------------


@pytest.mark.parametrize(("actor", "reason", "expected", "cause"), [
    ("user", "aborted from the run page",
     "Run aborted by the user — aborted from the run page.", "user_abort"),
    ("parent", "the parent run killed this child-task",
     "Run aborted by the parent run — the parent run killed this child-task.", "parent_kill"),
    ("signal", "SIGTERM to the engine process",
     "Run aborted by a signal to the engine process — SIGTERM to the engine process.",
     "signal_kill"),
    ("user", "", "Run aborted by the user.", "user_abort"),
])
def test_each_actor_has_its_own_sentence_and_cause(actor, reason, expected, cause):
    mark = abortmark.Abort(actor, reason)
    assert mark.sentence == expected
    assert mark.cause == cause


def test_an_unrecorded_abort_says_so_instead_of_guessing():
    """The honest residue: no door wrote, no handler caught, no parent killed. It must read as
    "nobody established this", never as the likelier story — that is the misattribution F480
    exists to end, and `unknown` only discriminates while it keeps meaning exactly this.
    """
    mark = abortmark.Abort("unknown", "")
    assert mark.sentence == "Run aborted by an unrecorded signal (no actor left a record)."
    assert mark.cause == "unknown"
    assert "user" not in mark.sentence and "daemon" not in mark.sentence


# -- the breadcrumb ------------------------------------------------------------------------


def test_the_mark_is_read_back_for_this_process(tmp_path):
    abortmark.write(tmp_path, actor="user", reason="from the console", pid=os.getpid())
    mark = read_json(tmp_path / abortmark.MARK_FILE)
    assert mark["actor"] == "user" and mark["pid"] == os.getpid() and mark["ts"]
    assert abortmark.resolve(tmp_path) == abortmark.Abort("user", "from the console")


def test_a_mark_naming_another_process_is_not_about_this_leg(tmp_path):
    """A resume reuses the run dir and the run id: a mark left by an abort that never landed
    must not attribute a LATER leg's ending. A new leg is a new process."""
    abortmark.write(tmp_path, actor="user", reason="an earlier leg", pid=os.getpid() + 1)
    assert abortmark.resolve(tmp_path) == abortmark.Abort("unknown", "")


def test_a_mark_that_was_never_written_reads_unknown(tmp_path):
    """Two ways a breadcrumb fails to exist, one contract. A door that cannot name the process
    writes nothing (an unstamped mark is indistinguishable from a stale one, and a wrong
    attribution is worse than `unknown`), and a write that RAISES never blocks the abort it
    describes — best-effort, exactly like the shutdown mark."""
    abortmark.write(tmp_path, actor="user", reason="no pid known", pid=None)
    assert not (tmp_path / abortmark.MARK_FILE).exists()
    abortmark.write(tmp_path / "nope" / "deeper", actor="user", reason="x", pid=os.getpid())
    assert abortmark.resolve(tmp_path) == abortmark.Abort("unknown", "")


def test_the_parents_kill_outranks_a_mark_and_the_process_flag(tmp_path):
    """A signal aborts every loop in the process; a child's own event is the one fact only its
    parent set, so for that child the parent is the actor whatever else was recorded.
    """
    abortmark.write(tmp_path, actor="user", reason="the root run's cancel", pid=os.getpid())
    event = abortmark.AbortEvent()
    event.set("the parent run killed this child-task")
    assert abortmark.resolve(tmp_path, event) == abortmark.Abort(
        "parent", "the parent run killed this child-task")


def test_a_bare_event_still_names_the_parent(tmp_path):
    """A plain `threading.Event` (a hand-built loop, a test) carries no reason — the actor is
    still knowable from the fact that only a parent holds that event.
    """
    event = threading.Event()
    event.set()
    assert abortmark.resolve(tmp_path, event).actor == "parent"


def test_the_first_reason_set_on_an_event_wins():
    """A child killed deliberately must not be relabelled by the parent's exit sweep moments
    later (`kill` then `kill_all` both set the same event).
    """
    event = abortmark.AbortEvent()
    event.set("the parent run killed this child-task")
    event.set("parent run finished (ok)")
    assert event.reason == "the parent run killed this child-task"
    assert event.is_set()


def test_a_signal_handler_records_itself_then_raises_the_flag(tmp_path, monkeypatch):
    """What `run-once`'s Ctrl-C installs: record WHO, then raise the flag — in that order,
    because the flag ends the run and the record is its only witness."""
    from rsched.engine import control

    monkeypatch.setitem(control._ABORT, "flag", False)
    abortmark.signalled("user", "interrupted at the terminal (Ctrl-C)")
    assert control._ABORT["flag"] is True
    assert abortmark.resolve(tmp_path) == abortmark.Abort(
        "user", "interrupted at the terminal (Ctrl-C)")


def test_the_first_signal_wins_over_a_later_one(tmp_path, monkeypatch):
    """A second signal arriving while the run closes out must not rewrite the attribution."""
    from rsched.engine import control

    monkeypatch.setitem(control._ABORT, "flag", False)
    abortmark.signalled("user", "interrupted at the terminal (Ctrl-C)")
    abortmark.signalled("signal", "SIGTERM to the engine process")
    assert abortmark.resolve(tmp_path).reason == "interrupted at the terminal (Ctrl-C)"


def test_a_door_that_wrote_a_mark_outranks_the_signal_it_sent(tmp_path, monkeypatch):
    """Both exist on a console cancel: the daemon's SIGTERM reaches the handler, and the door
    wrote first. The door knows WHO asked; the handler only knows a signal arrived.
    """
    from rsched.engine import control

    monkeypatch.setitem(control._ABORT, "flag", False)
    abortmark.write(tmp_path, actor="user", reason="aborted from the run page",
                    pid=os.getpid())
    abortmark.signalled("signal", "SIGTERM to the engine process")
    assert abortmark.resolve(tmp_path) == abortmark.Abort("user", "aborted from the run page")


# -- the engine's close-out ----------------------------------------------------------------


def test_the_finish_names_the_actor_the_door_recorded(make_routine, scripted):
    """End to end: a door marks the run, the abort lands, and the finish text, the finish
    payload and result.md all name the user — no disjunction anywhere."""
    import rsched.engine.loop as loop_mod

    d = make_routine(slug="actor-user")
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True)
    abortmark.write(run_dir, actor="user", reason="aborted from the run page", pid=os.getpid())

    def act_then_abort():
        loop_mod._ABORT["flag"] = True
        return finish(summary="never reached")

    scripted([act_then_abort])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "aborted"
    events, _ = read_events(run_dir / "transcript.jsonl")
    payload = events[-1]["payload"]
    assert payload["status"] == "aborted"
    assert payload["summary"].startswith(
        "Run aborted by the user — aborted from the run page.")
    assert (payload["actor"], payload["cause"]) == ("user", "user_abort")
    assert "user/daemon" not in payload["summary"]
    assert (run_dir / "result.md").read_text(encoding="utf-8").startswith(
        "Run aborted by the user — aborted from the run page.")
    # countable by actor on the health stream too, in the reap's own vocabulary
    rows = health_events(d.parent, event="run_failed", run_id=f"actor-user:{TS}")
    assert rows and rows[-1]["cause"] == "user_abort"


def test_an_abort_nobody_recorded_finishes_honestly(make_routine, scripted):
    """No breadcrumb, no handler, no parent: the turn-0 specimen's own case. It says that
    plainly, keeps the `aborted` status, and files `unknown` rather than naming a suspect.
    """
    import rsched.engine.loop as loop_mod

    def abort_before_the_first_turn():
        loop_mod._ABORT["flag"] = True
        return finish(summary="never reached")

    d = make_routine(slug="actor-none")
    scripted([abort_before_the_first_turn])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "aborted"
    events, _ = read_events(run_dir / "transcript.jsonl")
    payload = events[-1]["payload"]
    assert payload["summary"].startswith(
        "Run aborted by an unrecorded signal (no actor left a record).")
    assert (payload["actor"], payload["cause"]) == ("unknown", "unknown")
    assert read_json(run_dir / "status.json")["state"] == "aborted"
    rows = health_events(d.parent, event="run_failed", run_id=f"actor-none:{TS}")
    assert rows and rows[-1]["cause"] == "unknown"


def test_a_signalled_abort_names_the_signal(make_routine, scripted):
    """What `rsched engine-run`'s SIGTERM handler leaves behind when no door wrote a mark — a
    supervisor stop or a deploy. The handler is the only witness a signal has.
    """
    d = make_routine(slug="actor-signal")
    scripted([lambda: (abortmark.signalled("signal", "SIGTERM to the engine process"),
                       finish(summary="never reached"))[1]])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "aborted"
    events, _ = read_events(run_dir / "transcript.jsonl")
    payload = events[-1]["payload"]
    assert payload["summary"].startswith("Run aborted by a signal to the engine process")
    assert (payload["actor"], payload["cause"]) == ("signal", "signal_kill")


def test_a_killed_child_names_its_parent(make_routine, scripted, monkeypatch):
    """The actor that is knowable in-process: the parent's `kill` sets the child's own event
    with the reason on it, and the child's close-out reads it from there.
    """
    from rsched.engine import subruns

    monkeypatch.setattr(subruns, "KILL_JOIN_S", 4.0)
    released = threading.Event()

    def sleepy():
        released.wait(20)
        return finish(summary="should never land")

    def release_and_finish():
        released.set()
        return finish(summary="killed the slowpoke")

    d = make_routine(slug="actor-parent")
    scripted([
        ("(no previous runs)", spawn("CHILD-K: sleep.", label="slow")),
        ("CHILD-K", sleepy),
        ("(no previous runs)", {"say": "Killing it.", "kind": "kill", "n": 1}),
        ("(no previous runs)", release_and_finish),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"
    events, _ = read_events(run_dir / "transcript.jsonl")
    end = next(e for e in events if e["type"] == "subrun_end")
    assert end["payload"]["status"] == "aborted"
    child, _ = read_events(run_dir / "sub" / "1" / "transcript.jsonl")
    payload = child[-1]["payload"]
    assert payload["summary"].startswith(
        "Run aborted by the parent run — the parent run killed this child-task.")
    assert (payload["actor"], payload["cause"]) == ("parent", "parent_kill")


# -- the doors -----------------------------------------------------------------------------


def test_the_cli_door_marks_the_run_before_signalling(cli_server, make_routine, capsys):
    """`rsched abort`: the mark is written for the pid it is about to signal, whatever the
    signal then achieves (here the pid is dead, so the abort reports failure — and the
    breadcrumb is still the record of who asked).
    """
    from rsched import cli

    d = make_routine(slug="cli-abortee")
    run_dir = d / "runs" / "20260716-200000"
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "status.json", {
        "run_id": "cli-abortee:20260716-200000", "state": "running", "pid": 999_999_999,
        "started": "20260716-200000", "updated": "2026-07-16T20:00:00+00:00",
        "turn": 1, "usage": {}, "elapsed_s": 1})
    assert cli.cmd_abort(SimpleNamespace(run_id="cli-abortee")) == 1
    capsys.readouterr()
    mark = read_json(run_dir / abortmark.MARK_FILE)
    assert mark["actor"] == "user" and mark["pid"] == 999_999_999
    assert "rsched abort" in mark["reason"]


@pytest.mark.asyncio
async def test_the_web_door_marks_the_run_before_signalling(tmp_path):
    """`abort_with_fallback` is the ONE sequence the run page, a conversation's stop and a
    background task's cancel share, so the breadcrumb is written there — before either kill
    path, with the reason naming the door."""
    from rsched.web import api_run_control

    run_dir = tmp_path / "runs" / TS
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "status.json", {"run_id": f"webbed:{TS}", "state": "running",
                                                "pid": 4242})
    order: list[str] = []

    class _Runner:
        async def abort(self, slug: str) -> bool:
            order.append(f"abort:{slug}")
            # the mark must already be on disk when the signal goes out
            order.append("marked" if (run_dir / abortmark.MARK_FILE).exists() else "unmarked")
            return True

    assert await api_run_control.abort_with_fallback(
        _Runner(), "webbed", run_dir, "aborted from the run page") is True
    assert order == ["abort:webbed", "marked"]
    mark = read_json(run_dir / abortmark.MARK_FILE)
    assert (mark["actor"], mark["reason"], mark["pid"]) == (
        "user", "aborted from the run page", 4242)


@pytest.mark.asyncio
async def test_a_terminal_run_is_neither_marked_nor_signalled(tmp_path):
    """A finished run's status.json keeps its last pid, which the kernel may have handed on.
    The existing guard refuses the abort; the mark must not be written either.
    """
    from rsched.web import api_run_control

    run_dir = tmp_path / "runs" / TS
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "status.json", {"run_id": f"donezo:{TS}", "state": "finished",
                                                "outcome": "ok", "pid": 4242})

    class _Runner:
        async def abort(self, slug: str) -> bool:
            raise AssertionError("a terminal run must never be signalled")

    assert await api_run_control.abort_with_fallback(_Runner(), "donezo", run_dir) is False
    assert not (run_dir / abortmark.MARK_FILE).exists()
