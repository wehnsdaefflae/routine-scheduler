"""ACTION CHAINING (engine/chain.py, operator 2026-10-23): a reply's actions run in order,
inside one turn, and every result comes back together at the start of the next turn.

His words: "I want action chaining to work like in the terminal. Chained actions provide their
return values together in the next run", "on for every routine but can be toggled off in the
routine settings", and the correction "stop at the first failed action, like &&".
"""

import json
from typing import ClassVar

import pytest
import yaml

from conftest import finish, write_file
from helpers import server_for
from rsched.endpoints.base import Completion
from rsched.engine import chain
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events


def read(path, say="Reading it."):
    return {"say": say, "kind": "read_file", "path": path}


def reply(*actions, usage=None):
    """One model reply carrying `actions` in order — the live shape: every action but the last
    as JSON in the text block, the last as the tool call (replyactions reads them in that
    order, which is the order the model wrote them)."""
    text = "\n".join(json.dumps(a) for a in actions[:-1])
    return Completion(text=text, parsed=actions[-1], usage=usage or {"in": 1, "out": 1})


def chaining(directory, *, on: bool) -> None:
    cfg = yaml.safe_load((directory / "routine.yaml").read_text(encoding="utf-8"))
    cfg["chain_actions"] = on
    (directory / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def actions_of(events):
    return [(e["turn"], e["payload"]["kind"], e.get("chained"))
            for e in events if e["type"] == "assistant_action"]


def test_every_action_of_one_reply_runs_in_the_order_it_was_written(make_routine, scripted):
    d = make_routine(slug="chain1")
    ep = scripted([reply(write_file("state/a.txt", content="a"),
                         write_file("state/b.txt", content="b"),
                         write_file("state/c.txt", content="c")),
                   finish(summary="done")])
    status, run_dir = run_routine(d, server_for(d), run_ts="20261023-140000")
    assert status == "ok"
    assert [(d / "state" / f"{n}.txt").read_text() for n in "abc"] == ["a", "b", "c"]
    events, _ = read_events(run_dir / "transcript.jsonl")
    # ONE turn, three actions — a chain is one model call, and that is what it saves
    assert actions_of(events)[:3] == [(1, "write_file", None), (1, "write_file", 2),
                                      (1, "write_file", 3)]
    assert len(ep.calls) == 2                       # the chain, then the finish turn


def test_the_results_arrive_together_at_the_start_of_the_next_turn(make_routine, scripted):
    """Terminal-style: nothing is posted to the model between elements, and the next turn's
    prompt carries every observation in order."""
    d = make_routine(slug="chain2")
    ep = scripted([reply(write_file("state/one.txt"), write_file("state/two.txt")),
                   finish(summary="done")])
    run_routine(d, server_for(d), run_ts="20261023-140100")
    seen = ep.calls[1]["messages"]
    assert [m["role"] for m in seen[-4:]] == ["assistant", "user", "assistant", "user"]
    assert json.loads(seen[-4]["content"])["path"] == "state/one.txt"
    assert "state/one.txt" in seen[-3]["content"]
    assert json.loads(seen[-2]["content"])["path"] == "state/two.txt"
    assert "state/two.txt" in seen[-1]["content"]


def test_the_chain_stops_at_the_first_failed_action(make_routine, scripted):
    """`&&`: the actions after a failure were written for a world where it succeeded."""
    d = make_routine(slug="chain3")
    doomed = {"say": "Reading a file that is not there.", "kind": "read_file",
              "path": "state/missing.txt"}
    after = write_file("state/after.txt", content="x", say="Now writing, as if the read worked.")
    ep = scripted([reply(write_file("state/before.txt"), doomed, after),
                   finish(summary="done")])
    status, run_dir = run_routine(d, server_for(d), run_ts="20261023-140200")
    assert status == "ok"
    assert (d / "state" / "before.txt").exists()
    assert not (d / "state" / "after.txt").exists()
    events, _ = read_events(run_dir / "transcript.jsonl")
    stopped = next(e["payload"] for e in events if e["type"] == "observation"
                   and e["payload"].get("chain_stopped"))
    assert "stops at its first failure" in stopped["chain_stopped"]
    assert stopped["not_executed"][0]["action"].startswith("write_file path=state/after.txt")
    told = ep.calls[1]["messages"][-1]["content"]
    assert "NOT EXECUTED" in told and "as if the read worked" in told


def test_a_finish_written_after_another_action_does_not_run(make_routine, scripted):
    """Its summary and accounting were composed before that action's result existed — the
    R2443 false-claim shape, and the one thing a chain must not let through."""
    d = make_routine(slug="chain4")
    scripted([reply(write_file("state/work.txt"), finish(summary="all done")),
              finish(summary="really done")])
    status, run_dir = run_routine(d, server_for(d), run_ts="20261023-140300")
    assert status == "ok"
    assert (run_dir / "result.md").read_text().strip() == "really done"
    events, _ = read_events(run_dir / "transcript.jsonl")
    stopped = next(e["payload"] for e in events if e["type"] == "observation"
                   and e["payload"].get("chain_stopped"))
    assert "only runs as a reply's FIRST action" in stopped["chain_stopped"]


def test_a_finish_as_the_first_action_ends_the_run_and_nothing_after_it_runs(make_routine,
                                                                            scripted):
    """A finish IS allowed to open a reply — it is the actions written after it that never
    run, because the run ends there."""
    d = make_routine(slug="chain5")
    scripted([write_file("state/work.txt"),
              reply(finish(summary="stopping here"), write_file("state/never.txt"))])
    status, _ = run_routine(d, server_for(d), run_ts="20261023-140400")
    assert status == "ok"
    assert (d / "state" / "work.txt").exists()
    assert not (d / "state" / "never.txt").exists()


def test_a_chained_action_meets_the_same_checks_as_the_first(make_routine, scripted):
    """Every element is validated (schema, kind, capability, grant) — a chain may not be a
    hole in the permission model (R2823). An element that fails them stops the chain."""
    d = make_routine(slug="chain6")
    forbidden = {"say": "Running a shell nobody granted.", "kind": "shell",
                 "command": "echo sneaky"}
    ep = scripted([reply(write_file("state/ok.txt"), forbidden, write_file("state/no.txt")),
                   finish(summary="done")])
    status, run_dir = run_routine(d, server_for(d), run_ts="20261023-140500")
    assert status == "ok"
    assert (d / "state" / "ok.txt").exists() and not (d / "state" / "no.txt").exists()
    events, _ = read_events(run_dir / "transcript.jsonl")
    stopped = next(e["payload"] for e in events if e["type"] == "observation"
                   and e["payload"].get("chain_stopped"))
    assert "did not pass the checks" in stopped["chain_stopped"]
    assert not [e for e in events if e["type"] == "assistant_action"
                and e["payload"]["kind"] == "shell"]
    told = ep.calls[1]["messages"][-1]["content"]
    assert "NOT EXECUTED" in told and "did not pass the checks" in told


def test_a_read_in_the_same_chain_does_not_ground_an_overwrite(make_routine, scripted,
                                                               tmp_path):
    """The write-grounding gate: the model has NOT seen what an earlier element read — the
    result arrives next turn — so a chained read may not authorize destroying that file."""
    outside = tmp_path / "project"
    outside.mkdir()
    target = outside / "owned.txt"
    target.write_text("precious", encoding="utf-8")
    d = make_routine(slug="chain7")
    cfg = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    cfg["fs_write_roots"] = [str(outside)]
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    overwrite = {"say": "Replacing it.", "kind": "write_file", "path": str(target),
                 "content": "clobbered"}
    scripted([reply(read(str(target)), overwrite), finish(summary="done")])
    status, _ = run_routine(d, server_for(d), run_ts="20261023-140600")
    assert status == "ok"
    assert target.read_text() == "precious"


def test_a_read_in_an_earlier_turn_still_grounds_a_later_overwrite(make_routine, scripted,
                                                                  tmp_path):
    """The other half: once the run has READ the result, the next turn is grounded as always."""
    outside = tmp_path / "project"
    outside.mkdir()
    target = outside / "owned.txt"
    target.write_text("precious", encoding="utf-8")
    d = make_routine(slug="chain8")
    cfg = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    cfg["fs_write_roots"] = [str(outside)]
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    scripted([reply(read(str(target)), write_file("state/note.txt")),
              {"say": "Now I have read it.", "kind": "write_file", "path": str(target),
               "content": "knowingly replaced"},
              finish(summary="done")])
    status, _ = run_routine(d, server_for(d), run_ts="20261023-140700")
    assert status == "ok"
    assert target.read_text() == "knowingly replaced"


def test_the_switch_off_restores_one_action_per_turn(make_routine, scripted):
    d = make_routine(slug="chain9")
    chaining(d, on=False)
    ep = scripted([reply(write_file("state/first.txt"), write_file("state/second.txt")),
                   finish(summary="done")])
    status, run_dir = run_routine(d, server_for(d), run_ts="20261023-140800")
    assert status == "ok"
    assert (d / "state" / "first.txt").exists()
    assert not (d / "state" / "second.txt").exists()
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert all(e.get("chained") is None for e in events if e["type"] == "assistant_action")
    assert "NOT EXECUTED" in ep.calls[1]["messages"][-1]["content"]


def test_a_routine_that_never_mentions_the_switch_chains(make_routine):
    """Default ON, and a missing key reads as ON — R2782's lesson: nothing writes this field
    but a user turning it off."""
    from rsched.config import load_routine

    d = make_routine(slug="chain10")
    cfg, problems = load_routine(d)
    assert problems == []
    assert cfg.chain_actions is True
    chaining(d, on=False)
    cfg, problems = load_routine(d)
    assert problems == [] and cfg.chain_actions is False


def test_a_reply_may_not_run_more_than_the_cap(make_routine, scripted):
    d = make_routine(slug="chain11")
    many = [write_file(f"state/f{i}.txt") for i in range(chain.MAX_CHAIN + 3)]
    ep = scripted([reply(*many), finish(summary="done")])
    status, run_dir = run_routine(d, server_for(d), run_ts="20261023-140900")
    assert status == "ok"
    written = sorted(p.name for p in (d / "state").glob("f*.txt"))
    assert len(written) == chain.MAX_CHAIN
    events, _ = read_events(run_dir / "transcript.jsonl")
    stopped = next(e["payload"] for e in events if e["type"] == "observation"
                   and e["payload"].get("chain_stopped"))
    assert f"at most {chain.MAX_CHAIN} actions" in stopped["chain_stopped"]
    assert "NOT EXECUTED" in ep.calls[1]["messages"][-1]["content"]


def test_every_chained_action_is_counted_and_recorded_as_its_own_turn_record(make_routine,
                                                                            scripted):
    """One turn for the budget, N records for the digest: the compaction digest and the
    repeat-streak guard both read `turn_records`, which must carry each action."""
    d = make_routine(slug="chain12")
    scripted([reply(write_file("state/x.txt"), write_file("state/y.txt")),
              finish(summary="done")])
    status, run_dir = run_routine(d, server_for(d), run_ts="20261023-141000")
    assert status == "ok"
    status_doc = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status_doc["turn"] == 2                 # the chain's one turn, then the finish


@pytest.mark.parametrize(("obs", "expected"), [
    ({"kind": "read_file", "error": "no such file"}, "stops at its first failure"),
    ({"kind": "util", "exit": 1}, "stops at its first failure"),
    ({"kind": "reminder_hold", "reminder": "rem-1"}, "was HELD"),
    ({"kind": "util", "exit": 0, "cancelled": True}, "cancelled from outside"),
])
def test_what_stops_a_chain(obs, expected, make_routine, scripted, tmp_path):
    """The stop conditions, each asked of the one predicate that already classifies it."""
    from rsched.engine import hold as hold_mod

    assert ("was HELD" in expected) == hold_mod.is_hold(obs)
    reason = chain._stop_reason(_FakeLoop(), obs)
    assert reason is not None and expected in reason


class _FakeLoop:
    """The least a `_stop_reason` call needs: a queue with something in it, no abort, budget
    left. Everything it asks of the loop and nothing else."""

    chain_queue: ClassVar[list[dict]] = [{"kind": "read_file", "path": "x"}]
    chain_run = 1

    class ctx:  # noqa: N801 — a stand-in, not a type
        @staticmethod
        def budget_spent():
            return None

    @staticmethod
    def _aborted():
        return False
