"""`subruns` covers EVERY child the run started — a backgrounded call too (R3600).

The operator pointed this out mid-run in tv-show-tracker-seedbox-manager: a `background: true`
script was in flight as `bg1`, and `subruns` answered *"no record of a child in THIS leg — and
none on disk either, so none was started"*. Both halves of that sentence were false in the way
that matters: work the run had started was running, and the one action whose job is to say what
is running denied it. The two share the notification mechanism (a result appended at a later
turn boundary, tagged with its handle) and `kill` already addresses both namespaces, so the
status table was the only surface that knew about one and not the other.
"""

from __future__ import annotations

import threading

import pytest

from conftest import finish, util
from helpers import server_for, set_capabilities
from rsched.engine import background_status, obs_children
from rsched.engine.background import Pending
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20260708-090000"


class _Loop:
    """Only what the status rows read: the live pending list."""

    def __init__(self, pendings):
        self._background = list(pendings)


def _pending(handle: str, kind: str, alive: bool, *, turn: int = 3) -> tuple[Pending, threading.Event]:
    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    if alive:
        thread.start()
    return Pending(handle=handle, kind=kind, brief=f"{kind} brief", turn=turn,
                   thread=thread), release


def test_the_rows_name_every_in_flight_background_call():
    running, release = _pending("bg1", "script", alive=True)
    try:
        rows = background_status.status_rows(_Loop([running]))
        assert [r["handle"] for r in rows] == ["bg1"], rows
        row = rows[0]
        assert row["kind"] == "script"
        assert row["started_turn"] == 3
        assert row["state"] == "running"
        assert row["stoppable"] is True, "a script's process group CAN be terminated by kill"
    finally:
        release.set()


def test_a_landed_call_is_reported_as_landed_not_as_running():
    """`collect` drops a delivered Pending at the next boundary, but a call that FINISHED
    mid-turn is still in the list when `subruns` runs — reporting it as running would be a
    second false claim in place of the first."""
    landed, _ = _pending("bg2", "llm", alive=False)
    rows = background_status.status_rows(_Loop([landed]))
    assert rows[0]["state"] == "landed", rows
    assert rows[0]["stoppable"] is False, "an llm thread has no interruption point"


def test_no_background_work_means_no_rows():
    assert background_status.status_rows(_Loop([])) == []


def test_the_observation_does_not_claim_nothing_was_started():
    """The exact sentence the operator met. With a background call in flight it is false, and
    the branch that printed it ran whenever there was no spawn/subtask child."""
    obs = {"kind": "subruns", "count": 0, "rows": [],
           "background": [{"handle": "bg1", "kind": "script", "brief": "script: seedbox-drain",
                           "started_turn": 4, "state": "running", "stoppable": True}]}
    text = obs_children.format_children(obs, "subruns")
    assert "none was started" not in text, text
    assert "bg1" in text and "seedbox-drain" in text, text


def test_the_observation_still_says_nothing_was_started_when_nothing_was():
    obs = {"kind": "subruns", "count": 0, "rows": [], "background": []}
    text = obs_children.format_children(obs, "subruns")
    assert "none was started" in text, text


@pytest.mark.timeout(120)
def test_subruns_surfaces_a_live_background_call_in_a_real_run(make_routine, scripted,
                                                               monkeypatch):
    """Through the real loop: turn 1 backgrounds a slow util, turn 2 asks `subruns`, and the
    answer names the handle instead of denying the work exists."""
    import rsched.engine.executor as executor_mod

    gate = threading.Event()

    def slow(action, ctx):
        gate.wait(timeout=30)
        return {"kind": "util", "name": action.get("name"), "exit": 0,
                "stdout": "slow result", "stderr": ""}

    monkeypatch.setitem(executor_mod.DISPATCH, "util", slow)

    d = make_routine(slug="bgsub")
    server = server_for(d)
    set_capabilities(d, actions=["memory_read", "memory_write"])

    def release_then_finish():
        gate.set()
        return finish(summary="done" + " ." * 20)

    scripted([
        {**util("websearch", args=["q"], say="Backgrounding a slow fetch."), "background": True},
        {"say": "What is running?", "kind": "subruns"},
        release_then_finish,
    ])
    try:
        status, run_dir = run_routine(d, server, run_ts=TS)
    finally:
        gate.set()
    assert status == "ok", status

    events, _ = read_events(run_dir / "transcript.jsonl")
    table = next(e for e in events
                 if e["type"] == "observation" and e["payload"].get("kind") == "subruns")
    handles = [r["handle"] for r in table["payload"].get("background") or []]
    assert handles == ["bg1"], (
        "subruns ran while a backgrounded util was in flight and did not name it: "
        f"{table['payload']}")
