"""Scripted end-to-end engine runs: the loop's whole behavior surface, no network."""

import json

from conftest import finish, shell

from rsched.config import ServerConfig
from rsched.endpoints.base import EndpointError
from rsched.engine.loop import run_routine
from rsched.engine.transcript import read_events
from rsched.paths import atomic_write_json, read_json

TS = "20260708-070000"


def _run(make_routine, scripted, replies, *, slug="testr", ts=TS, **routine_kwargs):
    d = make_routine(slug=slug, **routine_kwargs)
    ep = scripted(replies)
    status, run_dir = run_routine(d, ServerConfig(), run_ts=ts)
    events, _ = read_events(run_dir / "transcript.jsonl")
    return d, ep, status, run_dir, events


def types(events):
    return [e["type"] for e in events]


def test_happy_path(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        {"say": "Write the artifact.", "kind": "write_file", "path": "state/out.txt",
         "content": "artifact"},
        finish(summary="Wrote state/out.txt with the artifact."),
    ])
    assert status == "ok"
    assert (d / "state" / "out.txt").read_text() == "artifact"
    assert types(events) == ["header", "assistant_action", "observation", "assistant_action", "finish"]
    assert (run_dir / "result.md").read_text().startswith("Wrote state/out.txt")
    st = read_json(run_dir / "status.json")
    assert st["state"] == "finished" and st["turn"] == 2 and st["usage"]["in"] > 0
    # the system prompt carried workflow + instruction + digest
    system = ep.calls[0]["messages"][0]["content"]
    assert "# WORKFLOW" in system and "Test instruction" in system and "no previous runs" in system


def test_shell_and_guard_rejection(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        shell("echo hi"),                       # not allowlisted → rejected, run continues
        shell("git status"),                    # allowlisted (not a repo → nonzero exit, still fine)
        finish(),
    ])
    assert status == "ok"
    obs = [e for e in events if e["type"] == "observation"]
    assert obs[0]["payload"]["rejected"] is True
    assert "REJECTED" in ep.calls[1]["messages"][-1]["content"]
    assert obs[1]["payload"].get("rejected") is None and "exit" in obs[1]["payload"]


def test_invalid_json_retry_then_ok(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        "utter prose, no JSON at all",
        shell("git status"),
        finish(),
    ])
    assert status == "ok"
    errs = [e for e in events if e["type"] == "error"]
    assert len(errs) == 1 and errs[0]["payload"]["where"] == "schema"
    assert "not a valid action" in ep.calls[1]["messages"][-1]["content"]


def test_three_invalid_attempts_fail_run(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted,
                                          ["nope", "still nope", "nope again"])
    assert status == "failed"
    assert len([e for e in events if e["type"] == "error"]) == 3
    assert events[-1]["type"] == "finish" and events[-1]["payload"]["status"] == "failed"
    assert read_json(run_dir / "status.json")["state"] == "failed"


def test_turn_budget_forces_partial_finish(make_routine, scripted):
    d, ep, status, run_dir, events = _run(
        make_routine, scripted,
        [shell("git status", say=f"s{i}") for i in range(3)],
        budgets={"max_turns": 2})
    assert status == "partial"
    fin = events[-1]["payload"]
    assert "turn budget exhausted" in fin["summary"] and fin["authored"] is False
    assert len(ep.calls) == 2  # no third completion happened


def test_repeated_action_warn_then_fail(make_routine, scripted):
    same = shell("git status", say="again")
    d, ep, status, run_dir, events = _run(make_routine, scripted, [same, same, same, same, same])
    assert status == "failed"
    fin = events[-1]["payload"]
    assert "repeated" in fin["summary"]
    warned = [c for c in ep.calls if "ENGINE WARNING" in c["messages"][-1]["content"]]
    assert warned, "warning observation was injected before the hard stop"
    # 5 actions authored, but only 4 executed (5th hit the fail gate)
    assert len([e for e in events if e["type"] == "observation"]) == 4


def test_fabricated_finish_rejected(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        finish(summary="All done! Committed everything."),   # turn 1: pure fabrication
        shell("git status"),
        finish(summary="Now actually done."),
    ], slug="fabber")
    assert status == "ok"
    rejected = [e for e in events if e["type"] == "observation"
                and e["payload"].get("kind") == "finish" and e["payload"].get("rejected")]
    assert len(rejected) == 1
    assert "not executed a single action" in ep.calls[1]["messages"][-1]["content"]
    # a failed-finish without work IS allowed (e.g. broken preconditions)
    d2, ep2, status2, run_dir2, events2 = _run(make_routine, scripted, [
        finish(status="failed", summary="Cannot start: credentials missing."),
    ], slug="failer")
    assert status2 == "failed" and events2[-1]["payload"]["authored"] is True


def test_ask_user_deferred(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        {"say": "Need input eventually.", "kind": "ask_user", "question": "Which city?",
         "mode": "deferred"},
        finish(),
    ])
    assert status == "ok"
    qs = list((d / "questions" / "pending").glob("*.json"))
    assert len(qs) == 1 and read_json(qs[0])["question"] == "Which city?"
    assert "filed as deferred" in ep.calls[1]["messages"][-1]["content"]


def test_ask_user_blocking_answered(make_routine, scripted):
    qid = f"q-{TS}-1"

    def write_answer_later():
        return {"say": "Must know now.", "kind": "ask_user", "question": "Go?",
                "mode": "blocking"}

    d = make_routine(slug="blocker")
    atomic_write_json(d / "inbox" / f"answer-{qid}.json",
                      {"qid": qid, "text": "yes, go", "source": "test"})
    ep = scripted([write_answer_later, finish()])
    status, run_dir = run_routine(d, ServerConfig(), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert status == "ok"
    assert [e["type"] for e in events].count("question") == 1
    ans = next(e for e in events if e["type"] == "answer")
    assert ans["payload"]["text"] == "yes, go"
    assert "user answered" in ep.calls[1]["messages"][-1]["content"]
    assert not list((d / "questions" / "pending").glob("*.json"))


def test_ask_user_blocking_timeout_defers(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        {"say": "q", "kind": "ask_user", "question": "Anyone?", "mode": "blocking"},
        finish(),
    ], slug="timeouter", budgets={"ask_timeout_h": 0})
    assert status == "ok"
    assert list((d / "questions" / "pending").glob("*.json"))
    assert "filed as deferred" in ep.calls[1]["messages"][-1]["content"]


def test_deferred_answer_reaches_next_run_digest(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        {"say": "q", "kind": "ask_user", "question": "Favorite color?", "mode": "deferred"},
        finish(),
    ], slug="qcarry")
    qid = read_json(next((d / "questions" / "pending").glob("*.json")))["qid"]
    atomic_write_json(d / "inbox" / f"answer-{qid}.json", {"qid": qid, "text": "teal"})
    ep2 = scripted([shell("git status"), finish(summary="noted teal")])
    status2, run_dir2 = run_routine(d, ServerConfig(), run_ts="20260709-070000")
    assert status2 == "ok"
    system = ep2.calls[0]["messages"][0]["content"]
    assert "Favorite color?" in system and "teal" in system
    assert not list((d / "questions" / "pending").glob("*.json"))


def test_subinstruction_nested_run(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        {"say": "Delegate.", "kind": "subinstruction", "prompt": "Compute the answer to X.",
         "label": "research"},
        # ↓ consumed by the CHILD loop
        finish(summary="X is 42, verified twice."),
        # ↓ back in the parent
        finish(summary="Done via subrun."),
    ])
    assert status == "ok"
    assert "subrun_start" in types(events) and "subrun_end" in types(events)
    sub_end = next(e for e in events if e["type"] == "subrun_end")
    assert sub_end["payload"]["status"] == "ok" and "42" in sub_end["payload"]["summary"]
    sub_events, _ = read_events(run_dir / "sub" / "1" / "transcript.jsonl")
    assert sub_events[0]["depth"] == 1 and sub_events[0]["parent"] == f"testr:{TS}"
    child_system = ep.calls[1]["messages"][0]["content"]
    assert "Compute the answer to X." in child_system and "subrun — no routine state digest" in child_system
    assert "42" in ep.calls[2]["messages"][-1]["content"]  # parent observed the child summary


def test_subrun_depth_and_count_caps(make_routine, scripted):
    sub = {"say": "d", "kind": "subinstruction", "prompt": "go deeper"}
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        sub,          # parent spawns child (depth 1)
        sub,          # child tries to spawn grandchild → rejected (depth cap 1)
        finish(summary="child done"),
        sub,          # parent spawns second child (count 2 = cap)
        finish(summary="child2 done"),
        sub,          # parent tries a third → rejected (count cap)
        finish(summary="parent done"),
    ], slug="deep")
    assert status == "ok"
    rejects = [e for e in events
               if e["type"] == "observation" and e["payload"]["kind"] == "subinstruction"
               and "REJECTED" in e["payload"]["summary"]]
    assert len(rejects) == 1  # the parent's third spawn; the child's own rejection is in the sub transcript
    sub_events, _ = read_events(run_dir / "sub" / "1" / "transcript.jsonl")
    child_rejects = [e for e in sub_events
                     if e["type"] == "observation" and "depth" in e["payload"].get("summary", "")]
    assert child_rejects


def test_llm_subcall(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        {"say": "Scoped call.", "kind": "llm", "prompt": "Summarize: hello world",
         "role": "cheap"},
        "a plain text llm reply",   # ← consumed by the subcall (no schema → text)
        finish(),
    ])
    assert status == "ok"
    obs = next(e for e in events if e["type"] == "observation")
    assert obs["payload"]["kind"] == "llm" and obs["payload"]["reply"] == "a plain text llm reply"
    assert ep.calls[1]["schema"] is None  # subcall had no response_schema
    assert ep.calls[1]["messages"][-1]["content"] == "Summarize: hello world"


def test_injection_mid_run(make_routine, scripted):
    d = make_routine(slug="inject")

    def action_then_inject():
        atomic_write_json(d / "inbox" / "msg-1.json", {"text": "also mention the moon"})
        return shell("git status")

    ep = scripted([action_then_inject, finish()])
    status, run_dir = run_routine(d, ServerConfig(), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert status == "ok"
    inj = next(e for e in events if e["type"] == "user_injection")
    assert inj["payload"]["text"] == "also mention the moon"
    assert any("USER MESSAGE (injected mid-run)" in m["content"]
               for m in ep.calls[1]["messages"])
    assert (run_dir / "consumed" / "msg-1.json").exists()


def test_boot_inbox_message_lands_in_system_prompt(make_routine, scripted):
    d = make_routine(slug="bootmsg")
    atomic_write_json(d / "inbox" / "msg-0.json", {"text": "priority: check the deploy"})
    ep = scripted([shell("git status"), finish()])
    status, _ = run_routine(d, ServerConfig(), run_ts=TS)
    assert status == "ok"
    assert "priority: check the deploy" in ep.calls[0]["messages"][0]["content"]


def test_abort_flag(make_routine, scripted):
    import rsched.engine.loop as loop_mod

    def act_then_abort():
        loop_mod._ABORT["flag"] = True
        return shell("git status")

    d = make_routine(slug="aborted")
    ep = scripted([act_then_abort, finish()])
    status, run_dir = run_routine(d, ServerConfig(), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert status == "aborted"
    assert events[-1]["payload"]["status"] == "aborted"
    assert read_json(run_dir / "status.json")["state"] == "aborted"


def test_endpoint_error_fails_run(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        EndpointError("boom 401", auth=True),
    ], slug="eperr")
    assert status == "failed"
    err = next(e for e in events if e["type"] == "error")
    assert err["payload"]["where"] == "endpoint"
    assert "~/.credentials/" in events[-1]["payload"]["summary"]


def test_pause_gate(make_routine, scripted):
    d = make_routine(slug="pauser")
    run_dir_holder = {}

    def act_and_pause():
        rd = d / "runs" / TS
        run_dir_holder["rd"] = rd
        atomic_write_json(rd / "control.json", {"pause": True})
        return shell("git status")

    def unpause_soon():
        # runs inside the SECOND completion call — by then the engine must have gone
        # through the pause gate; we clear it from a thread to release the engine.
        raise AssertionError("must not be reached while paused")

    import threading
    import time as _t

    ep = scripted([act_and_pause, finish()])

    def clearer():
        for _ in range(200):  # wait until the run dir is known, then release the pause
            _t.sleep(0.05)
            if "rd" in run_dir_holder:
                atomic_write_json(run_dir_holder["rd"] / "control.json", {"pause": False})
                return

    t = threading.Thread(target=clearer)
    t.start()
    status, run_dir = run_routine(d, ServerConfig(), run_ts=TS)
    t.join()
    assert status == "ok"
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert types(events)[-1] == "finish"
