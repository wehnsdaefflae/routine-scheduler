"""The kickoff must not teach one-action-per-turn.

F836, measured 2026-11-05 over the 60 most recent fleet transcripts: **1 turn of 8,084 carried
more than one action (0.01%)**, and 16 of 17 routines had never chained once — while
`engine/chain.py` has been on for every routine since 0.446.0. The mechanism was never the
problem (`replyactions.reply_actions` reads actions from a reply's text AND from
`completion.parsed_rest`); the PROSE was: `composer.kickoff_message` said "Reply with ONE JSON
action object" — the last instruction a run reads before its first action, and a flat
contradiction of the harness paragraph that says a reply may carry several.

D236 (operator, 2026-11-05, "Free fix, re-measure") split the candidate: the KICKOFF is a
one-shot boot message outside the ratcheted engine contract, so it is fixed here at no
always-on cost; the +297-character when-to-chain paragraph the candidate added to the harness
was declined, so `tests/test_prompt_budget.py` keeps its ceiling and the chaining rate is
re-measured on the kickoff fix alone. `tests/test_prompt_anatomy.py` separately pins the kickoff
into `docs/prompt-anatomy.md`, so the doc cannot go stale either.
"""

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
