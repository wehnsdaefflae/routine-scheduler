"""D245(b): a REPLAYED observation keeps the tails that rode it.

`EngineLoop._tails` appends everything that rides an observation message — the reminder note,
the rule-assist line, the history pointer, the spent once-grant, the repeat warning, the budget
remainder (docs/prompt-anatomy.md §3b) — after the observation was already recorded. None of it
reached the transcript, so `history.replay_messages` rebuilt the observation WITHOUT its tails:
from the first such message a resumed leg's prompt differed from what the model actually read.
Two costs, both measured elsewhere in this codebase (`engine/enginenote.py`'s docstring): the
provider cache misses from that message on — re-WRITTEN at 1.25x instead of re-read at 0.1x —
and cautions the model had been shown are simply gone from the leg that continues its work.

The tails are kept as the rendered TEXT, never as the facts behind them: re-rendering on a
resume would recompute a budget remainder against the new leg's window and re-apply reminder
ops that already ran. Verbatim is the only thing that can be byte-identical.
"""

from __future__ import annotations

from conftest import finish, write_file
from helpers import server_for, set_capabilities
from rsched.engine.history import replay_messages
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20260708-070000"


def _user(messages: list[dict]) -> list[str]:
    return [str(m["content"]) for m in messages if m.get("role") == "user"]


def _wrote() -> dict:
    """A COMPLETE write_file observation — every key its renderer reads (tests/render_guard.py
    turns a renderer's floor into a failure, so a half-built payload reds on the fixture)."""
    return {"kind": "write_file", "path": "state/a.txt", "bytes": 1}


def test_an_observation_that_carried_a_reminder_tail_replays_with_it(make_routine, scripted):
    """The incident, end to end: an action carrying a `remind` op is answered with a
    `[REMINDERS: …]` note on its observation. The live leg reads it; a resume must read the
    same bytes.
    """
    d = make_routine(slug="tailr")
    set_capabilities(d, reminders="local")
    ep = scripted([{**write_file("state/probe.txt", content="x"),
                    "remind": {"op": "add", "regex": "^util:ghost",
                               "description": "it wipes the shared workdir"}},
                   finish()])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"

    live = _user(ep.calls[-1]["messages"])[-1]
    assert "[REMINDERS: added rem-" in live, "the live leg never read a reminder tail"

    events, _ = read_events(run_dir / "transcript.jsonl")
    replayed = _user(replay_messages(events)[0])[-1]
    assert "[REMINDERS: added rem-" in replayed, \
        "the replayed observation lost the reminder tail the model was shown"
    # …and not merely present: byte-identical, which is what the provider cache compares.
    # It pins the ORDER of several tails on one message too — this observation carries the
    # reminder note AND the budget remainder, in the order `_tails` appended them.
    assert replayed == live


def test_two_tails_replay_in_the_order_they_rode_the_message():
    """One recorded string, so the order is preserved by construction rather than by a
    second mechanism that could disagree with `_tails`.
    """
    from rsched.engine import obstails

    tails = "\n[REMINDERS: added rem-1]\n[BUDGET LEFT: 3 of 60 turns]"
    payload = obstails.record(_wrote(), tails)
    content = _user(replay_messages([{"type": "observation", "payload": payload}])[0])[0]

    assert content.endswith(tails)
    assert content.index("[REMINDERS:") < content.index("[BUDGET LEFT:")


def test_an_observation_that_carried_no_tail_replays_exactly_as_before():
    """The shape every other reader already knows: no key, no trailing newline, no change."""
    from rsched.engine import obstails

    payload = _wrote()
    assert obstails.record(payload, "") == payload
    assert obstails.of(payload) == ""
    plain = _user(replay_messages([{"type": "observation", "payload": payload}])[0])[0]
    assert not plain.endswith("\n")
