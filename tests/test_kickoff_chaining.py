"""The kickoff must not teach one-action-per-turn."""

from __future__ import annotations

from types import SimpleNamespace

from conftest import finish, write_file
from helpers import server_for
from rsched.engine.composer import kickoff_message
from rsched.engine.runtime import run_routine


def test_the_kickoff_does_not_instruct_one_action_per_turn():
    """The sentence that taught 0.01% chaining is gone, and the first turn is still bounded."""
    text = kickoff_message(SimpleNamespace(run_id="job-radar:20260712-070000"))
    assert "ONE JSON action object" not in text
    assert "one action" not in text.lower()
    # what it must still say: this is a STEP, not a plan and not a finish
    assert "first actual step" in text
    for forbidden in ("not a plan", "not a summary", "not a finish"):
        assert forbidden in text, f"the kickoff no longer rules out {forbidden!r}"
    # …and it now names the chaining option the engine has supported since 0.446.0
    assert "chain them" in text


def test_a_fresh_run_reads_the_new_kickoff_as_its_first_user_message(make_routine, scripted):
    """Read off what the endpoint was SENT, not the function: the kickoff only matters as the
    message a run's first completion actually receives (`engine/boot.py`)."""
    d = make_routine(slug="kickoffchain")
    ep = scripted([write_file("state/a.txt"), finish()])
    status, _ = run_routine(d, server_for(d), run_ts="20261105-020000")
    assert status == "ok"
    first_user = next(m["content"] for m in ep.calls[0]["messages"] if m["role"] == "user")
    assert first_user.startswith("Begin run kickoffchain:20261105-020000.")
    assert "ONE JSON action object" not in first_user
    assert "chain them in that one reply" in first_user
