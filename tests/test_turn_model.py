"""D188 — which model answered THIS turn, recorded on the turn itself."""

from __future__ import annotations

from conftest import finish, write_file
from helpers import server_for
from rsched.engine.completion import next_action
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from test_loop_referral import CLASSIFIER_REFUSAL, _FakeEndpoint, _FakeRegistry, _loop

TS = "20261022-090000"


def test_every_turn_records_the_role_and_the_model_that_served_it(make_routine, scripted):
    """Both halves on every turn: the concrete catalog model that was called, and the role
    the run asked for. A reader scanning the transcript can tell them apart without holding
    the config in their head."""
    d = make_routine("turnmodel")
    scripted([write_file("state/probe.txt", say="grounding work"), finish()])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    events, _ = read_events(run_dir / "transcript.jsonl")
    turns = [e for e in events if e["type"] == "assistant_action"]
    assert len(turns) == 2
    assert [t["usage"]["model"] for t in turns] == ["scripted/test-model"] * 2
    assert [t["usage"]["role"] for t in turns] == ["main"] * 2


def test_a_turn_the_uncensored_model_served_is_recorded_under_that_role(make_routine):
    """The role is what actually served the turn, never a constant. A classifier refusal hands
    the refused TURN to the uncensored model (engine/turntakeover.py) and its reply IS the
    action — so the turn is attributed to `uncensored` and to the uncensored model, not to the
    `main` slot the run started the turn in."""
    main = _FakeEndpoint([CLASSIFIER_REFUSAL])
    unc = _FakeEndpoint([finish(summary="served by the uncensored model")])
    loop = _loop(make_routine, _FakeRegistry(main, unc, main, main_name="d188-unc"),
                 slug="d188unc")
    action, usage = next_action(loop)
    assert action["kind"] == "finish"
    assert usage["model"] == "unc-ep/unc-model"
    assert usage["role"] == "uncensored"
