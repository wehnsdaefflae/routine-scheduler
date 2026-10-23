"""A CHAINED turn through the readers of a transcript (D203, engine/chain.py).

One turn now carries several `assistant_action` events, all stamped with the same `turn` and
every one but the first marked `chained: n`. Everything that reads a transcript keys on those
two facts, so this file is what says each reader still means what it says: a resume replays the
whole chain into paired messages, a cut "through turn N" keeps all of it, and the phase rail
counts TURNS rather than actions.
"""
from __future__ import annotations

import json

from conftest import finish, write_file
from helpers import server_for
from rsched.endpoints.base import Completion
from rsched.engine.history import replay_messages
from rsched.engine.rewind import cut_index_for_turn, rewind_transcript
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.readmodels.statemap import phase_stats


def reply(*actions):
    text = "\n".join(json.dumps(a) for a in actions[:-1])
    return Completion(text=text, parsed=actions[-1], usage={"in": 1, "out": 1})


def _chained_run(make_routine, scripted, slug, ts):
    d = make_routine(slug=slug)
    scripted([reply(write_file("state/a.txt", content="a"),
                    write_file("state/b.txt", content="b"),
                    write_file("state/c.txt", content="c")),
              finish(summary="done")])
    status, run_dir = run_routine(d, server_for(d), run_ts=ts)
    assert status == "ok"
    events, _ = read_events(run_dir / "transcript.jsonl")
    return d, run_dir, events


def test_a_resumed_leg_replays_every_action_of_a_chain_in_order(make_routine, scripted):
    """`replay_messages` is what a resumed leg hands the model. A chain must come back as the
    same alternating pairs the live turn appended — three actions, three observations, in
    order — or the next leg reads a history that never happened."""
    _d, _run_dir, events = _chained_run(make_routine, scripted, "rchain1", "20261023-150000")
    messages, last_turn, records = replay_messages(events)
    actions = [json.loads(m["content"]) for m in messages if m["role"] == "assistant"]
    assert [a["kind"] for a in actions] == ["write_file", "write_file", "write_file", "finish"]
    assert [a.get("path") for a in actions[:3]] == ["state/a.txt", "state/b.txt", "state/c.txt"]
    roles = [m["role"] for m in messages]
    assert roles[:6] == ["assistant", "user", "assistant", "user", "assistant", "user"]
    assert last_turn == 2                                  # the chain is ONE turn, then finish
    # the digest remembers each ACTION (assist predicates read written paths off these)
    assert [r["turn"] for r in records] == [1, 1, 1, 2]


def test_a_cut_through_a_chained_turn_keeps_the_whole_chain(make_routine, scripted):
    """"Through turn N" is through the LAST action of turn N. Taking the first would drop
    actions whose observations are in the transcript — a world the run had already changed,
    with no record that it had."""
    _d, run_dir, events = _chained_run(make_routine, scripted, "rchain2", "20261023-150100")
    idx = cut_index_for_turn(events, 1)
    assert idx is not None
    kept = events[:idx + 1]
    assert sum(1 for e in kept if e["type"] == "assistant_action") == 3
    assert kept[-1]["type"] == "observation"               # a clean boundary: a paired tail
    summary = rewind_transcript(run_dir, keep_through_turn=1)
    assert summary is not None and summary["kept_through_turn"] == 1
    after, _ = read_events(run_dir / "transcript.jsonl")
    assert sum(1 for e in after if e["type"] == "assistant_action") == 3
    assert not [e for e in after if e["type"] == "finish"]
    messages, _last, _records = replay_messages(after)
    assert [m["role"] for m in messages] == ["assistant", "user"] * 3


def test_the_phase_rail_counts_a_chain_as_one_turn(make_routine, scripted):
    """`turns` on the phase rail is a count of TURNS. Three actions in one reply are one."""
    _d, run_dir, _events = _chained_run(make_routine, scripted, "rchain3", "20261023-150200")
    rows = phase_stats(run_dir)
    assert sum(r["turns"] for r in rows) == 2              # the chain, then the finish
