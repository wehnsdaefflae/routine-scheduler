"""Scripted end-to-end engine runs: the loop's whole behavior surface, no network."""

import json
import time

from conftest import finish, spawn, util, wait_, write_file

from rsched.config import ServerConfig
from rsched.endpoints.base import EndpointError
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.paths import atomic_write_json, read_json

TS = "20260708-070000"


def _server(routine_dir) -> ServerConfig:
    s = ServerConfig()
    # hermetic: no library on disk → sub-workflows use the builtin fallback body;
    # util actions on a missing name return a "missing" observation. confirm off so
    # write_util tests don't block on approval.
    s.libraries_home = routine_dir.parent.parent / "no-library"
    s.confirm_util_changes = False
    return s


def probe(say="Doing work."):
    """A generic successful effectful action."""
    return write_file("state/probe.txt", content="probe", say=say)


def _run(make_routine, scripted, replies, *, slug="testr", ts=TS, **routine_kwargs):
    d = make_routine(slug=slug, **routine_kwargs)
    ep = scripted(replies)
    status, run_dir = run_routine(d, _server(d), run_ts=ts)
    events, _ = read_events(run_dir / "transcript.jsonl")
    return d, ep, status, run_dir, events


def types(events):
    return [e["type"] for e in events]


def test_apply_model_switch(make_routine):
    """The engine applies a mid-run model switch from control.json, edge-triggered on its ts, and
    ignores an unknown endpoint. for_model re-resolves every turn, so the next turn uses it."""
    from rsched.config import ModelRef, load_routine
    from rsched.engine.control import apply_model_switch
    from rsched.engine.loop import EngineLoop
    from rsched.engine.run_context import Budgets, RunContext
    from rsched.engine.transcript import Transcript

    d = make_routine(slug="sw")
    server = _server(d)
    server.endpoints = {"fast": None, "slow": None}          # only key membership is checked
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True)
    cfg, _ = load_routine(d)
    ctx = RunContext(routine=cfg, server=server, registry=None, run_ts=TS, run_dir=run_dir,
                     transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    loop = EngineLoop(ctx, "## Run flow", "instr")

    apply_model_switch(loop)                                  # no signal → no-op
    assert "main" not in ctx.routine.models
    atomic_write_json(run_dir / "control.json", {"switch_model": {
        "main": {"endpoint": "slow", "model": "big", "effort": "high"}, "ts": "t1"}})
    apply_model_switch(loop)
    assert ctx.routine.models["main"] == ModelRef("slow", "big", "high")
    ctx.routine.models["main"] = ModelRef("x", "y")           # same ts → not re-applied
    apply_model_switch(loop)
    assert ctx.routine.models["main"] == ModelRef("x", "y")
    atomic_write_json(run_dir / "control.json", {"switch_model": {
        "main": {"endpoint": "ghost", "model": "z"}, "ts": "t2"}})   # unknown endpoint ignored
    apply_model_switch(loop)
    assert ctx.routine.models["main"] == ModelRef("x", "y")
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert any(e["type"] == "user_injection" and "model switched" in e["payload"]["text"] for e in events)


def test_ensure_decomposed_builds_main_on_run(make_routine, monkeypatch):
    """A routine created as (workflow + instruction) with no main.md — the wizard's clarify session —
    is decomposed on run: main.md + steps written, carrying the workflow's tools allowlist through."""
    from rsched.config import load_routine
    from rsched.engine import runtime as runtime_mod
    from rsched.workflows import adapt as adapt_mod
    from rsched.workflows import library as lib_mod

    d = make_routine(slug="clarifyish")
    (d / "main.md").unlink()                                   # make it un-decomposed
    (d / "instruction.md").write_text("Refine this draft.\n")
    monkeypatch.setattr(lib_mod, "read_workflow", lambda home, slug: (
        {"tools": ["ask_user", "write_file", "finish"], "includes": ["ask-policy"], "version": 4}, "", ""))
    monkeypatch.setattr(lib_mod, "head_commit", lambda home: "deadbee")
    monkeypatch.setattr(adapt_mod, "decompose", lambda server, slug, instruction, **k: {
        "main": "## Run flow\n1. ask\n## Completion criteria\ndone", "modules": {"ask-step": "ask the user"}})

    cfg, _ = load_routine(d)
    runtime_mod._ensure_decomposed(d, cfg, _server(d))
    assert (d / "main.md").exists() and (d / "steps" / "ask-step.md").read_text().startswith("ask the user")
    import frontmatter
    meta = frontmatter.load(d / "main.md").metadata
    assert meta["tools"] == ["ask_user", "write_file", "finish"]              # allowlist carried through
    assert meta["materialized_from"]["slug"] == cfg.workflow_slug


TOOLED_MD = """---
materialized_from: {slug: test-flow, commit: abc123, version: 1}
tools: [read_file, write_file, ask_user]
---

## Run flow
1. Only read_file / write_file / ask_user are available; do the work and finish.

## Completion criteria
- The instruction is fulfilled within the allowlist.
"""


def test_tools_allowlist_enforced_at_runtime(make_routine, scripted):
    """A `tools:` frontmatter allowlist rejects other kinds inside the schema-retry cycle —
    the model is told which kinds ARE allowed and the run continues on a permitted action."""
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        util("websearch"),                       # util is NOT in the allowlist
        probe(),                                 # write_file is
        finish(summary="stayed inside the allowlist"),
    ], slug="tooled", workflow_md=TOOLED_MD)
    assert status == "ok"
    errs = [e for e in events if e["type"] == "error"]
    assert len(errs) == 1 and errs[0]["payload"]["where"] == "schema"
    assert "not available" in errs[0]["payload"]["message"]
    retry = ep.calls[1]["messages"][-1]["content"]
    for kind in ("ask_user", "finish", "read_file", "write_file"):
        assert kind in retry                     # the model is told what it MAY use
    assert (d / "state" / "probe.txt").exists()  # the permitted action then executed
    # no observation was recorded for the disallowed attempt — it never became a turn
    obs_kinds = [e["payload"]["kind"] for e in events if e["type"] == "observation"]
    assert "util" not in obs_kinds


def test_inbox_unreadable_message_logged_and_left(tmp_path, caplog):
    """An unreadable inbox file is skipped with a log trace and left for the next drain."""
    import logging
    import os

    import pytest

    from rsched.engine import inbox as inbox_mod

    if os.geteuid() == 0:
        pytest.skip("permission-based unreadability needs a non-root user")

    d = tmp_path / "r"
    (d / "inbox").mkdir(parents=True)
    good = d / "inbox" / "msg-1.json"
    good.write_text('{"text": "hello"}', encoding="utf-8")
    bad = d / "inbox" / "msg-2.json"
    bad.write_text("secret", encoding="utf-8")
    bad.chmod(0o000)
    try:
        with caplog.at_level(logging.WARNING, logger="rsched.inbox"):
            out = inbox_mod.drain_messages(d, tmp_path / "consumed")
    finally:
        bad.chmod(0o600)
    assert out == ["hello"]
    assert bad.exists()                          # left in place for the next drain
    assert "cannot read msg-2.json" in caplog.text


def test_resume_rehydrates_and_continues(make_routine, scripted):
    """A resumed run reuses the same run dir, replays the prior transcript into its prompt, and
    continues (appending to the transcript) rather than restarting from step 1."""
    d = make_routine(slug="res")
    scripted([probe("first work"), finish(summary="first pass done")])
    status1, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status1 == "ok"
    n1 = len(read_events(run_dir / "transcript.jsonl")[0])

    ep2 = scripted([write_file("state/more.txt", content="more", say="continuing"),
                    finish(summary="resumed and finished")])
    status2, run_dir2 = run_routine(d, _server(d), run_ts=TS, resume_from=TS)
    assert status2 == "ok" and run_dir2 == run_dir
    assert (d / "state" / "more.txt").read_text() == "more"
    events2, _ = read_events(run_dir / "transcript.jsonl")
    assert len(events2) > n1                                    # appended, not restarted
    assert any(e["type"] == "user_injection" and "resumed" in e["payload"]["text"] for e in events2)
    # the resumed run's FIRST prompt carried the prior conversation + the resume note
    joined = " ".join(m["content"] for m in ep2.calls[0]["messages"])
    assert "state/probe.txt" in joined and "ENGINE NOTE" in joined
    st = read_json(run_dir / "status.json")
    assert st["state"] == "finished" and st["turn"] == 4      # continued past the first run's 2 turns


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


def test_util_missing_then_continue(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        util("nonexistent-util", ["arg"]),      # no such util → missing observation, run continues
        probe(),
        finish(),
    ])
    assert status == "ok"
    obs = [e for e in events if e["type"] == "observation"]
    assert obs[0]["payload"]["missing"] is True and obs[0]["payload"]["name"] == "nonexistent-util"
    assert "does not exist" in ep.calls[1]["messages"][-1]["content"]


def test_write_util_gating_and_commit(make_routine, scripted, monkeypatch):
    import rsched.utils_lib as ul

    seen = {}
    monkeypatch.setattr(ul, "ensure_library", lambda home, remote="": None)
    monkeypatch.setattr(ul, "exists", lambda home, name: False)  # always "creating"
    monkeypatch.setattr(ul, "write_util_file",
                        lambda home, name, content: seen.update(name=name, content=content))
    monkeypatch.setattr(ul, "selftest", lambda home, name, **k: (True, "selftest: ok"))
    monkeypatch.setattr(ul, "git_commit", lambda home, msg: seen.update(commit=msg) or True)
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        {"say": "No util fits — creating one.", "kind": "write_util", "name": "adder",
         "content": "# /// script\n# ///\n\"\"\"adder — add.\"\"\"\n"},
        finish(summary="created adder"),
    ], slug="wu")
    assert status == "ok"
    assert seen["name"] == "adder" and "create adder" in seen["commit"]
    wu = next(e for e in events if e["type"] == "observation" and e["payload"]["kind"] == "write_util")
    assert wu["payload"]["selftest_ok"] and wu["payload"]["created"]


def test_write_util_selftest_failure_not_committed(make_routine, scripted, monkeypatch):
    import rsched.utils_lib as ul

    committed = []
    monkeypatch.setattr(ul, "ensure_library", lambda home, remote="": None)
    monkeypatch.setattr(ul, "exists", lambda home, name: False)
    monkeypatch.setattr(ul, "write_util_file", lambda home, name, content: None)
    monkeypatch.setattr(ul, "selftest", lambda home, name, **k: (False, "AssertionError: boom"))
    monkeypatch.setattr(ul, "git_commit", lambda home, msg: committed.append(msg) or True)
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        {"say": "Create it.", "kind": "write_util", "name": "bad", "content": "# broken"},
        finish(status="partial", summary="util did not pass selftest"),
    ], slug="wubad")
    assert status == "partial" and committed == []
    wu = next(e for e in events if e["type"] == "observation" and e["payload"]["kind"] == "write_util")
    assert wu["payload"]["selftest_ok"] is False and "boom" in wu["payload"]["output"]
    assert "selftest FAILED" in ep.calls[1]["messages"][-1]["content"]


def test_write_util_confirmation_declined(make_routine, scripted, monkeypatch):
    import rsched.utils_lib as ul

    monkeypatch.setattr(ul, "ensure_library", lambda home, remote="": None)
    monkeypatch.setattr(ul, "exists", lambda home, name: False)
    wrote = []
    monkeypatch.setattr(ul, "write_util_file", lambda *a: wrote.append(a))
    d = make_routine(slug="wuconfirm")
    qid = f"q-{TS}-1"
    atomic_write_json(d / "inbox" / f"answer-{qid}.json", {"qid": qid, "text": "decline"})
    server = _server(d)
    server.confirm_util_changes = True   # gate ON
    ep = scripted([
        {"say": "Propose a util.", "kind": "write_util", "name": "risky", "content": "# x"},
        finish(status="partial", summary="util declined"),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert status == "partial" and wrote == []          # never written — user declined
    assert any(e["type"] == "question" for e in events)  # approval was requested (blocking)
    wu = next(e for e in events if e["type"] == "observation" and e["payload"]["kind"] == "write_util")
    assert wu["payload"]["declined"]


def test_invalid_json_retry_then_ok(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        "utter prose, no JSON at all",
        probe(),
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
        [probe(say=f"s{i}") for i in range(3)],
        budgets={"max_turns": 2})
    assert status == "partial"
    fin = events[-1]["payload"]
    assert "turn budget exhausted" in fin["summary"] and fin["authored"] is False
    assert len(ep.calls) == 2  # no third completion happened


def test_repeated_action_warn_then_fail(make_routine, scripted):
    same = probe(say="again")
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
        probe(),
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
    ep2 = scripted([probe(), finish(summary="noted teal")])
    status2, run_dir2 = run_routine(d, ServerConfig(), run_ts="20260709-070000")
    assert status2 == "ok"
    system = ep2.calls[0]["messages"][0]["content"]
    assert "Favorite color?" in system and "teal" in system
    assert not list((d / "questions" / "pending").glob("*.json"))


PARENT = "Test instruction"  # marker present only in the parent's system prompt


def test_spawn_and_wait_collects_child(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        (PARENT, spawn("CHILD-A: compute the answer to X.", label="research")),
        ("CHILD-A", finish(summary="X is 42, verified twice.")),
        (PARENT, wait_(all_=True)),
        (PARENT, finish(summary="Done via sub-workflow.")),
    ])
    assert status == "ok"
    assert "subrun_start" in types(events) and "subrun_end" in types(events)
    sub_end = next(e for e in events if e["type"] == "subrun_end")
    assert sub_end["payload"]["status"] == "ok" and "42" in sub_end["payload"]["summary"]
    sub_events, _ = read_events(run_dir / "sub" / "1" / "transcript.jsonl")
    assert sub_events[0]["depth"] == 1 and sub_events[0]["parent"] == f"testr:{TS}"
    child_system = next(c for c in ep.calls if "CHILD-A" in c["messages"][0]["content"])
    assert "no routine state digest" in child_system["messages"][0]["content"]
    # the child summary reached the parent — via the wait observation or (if the child
    # beat the wait to a turn boundary) via the FINISHED notification message
    final_messages = json.dumps(ep.calls[-1]["messages"])
    assert "42" in final_messages
    # parent usage includes the child's tokens
    fin = events[-1]
    assert fin["usage_total"]["in"] >= 40  # 3 parent + 1 child completion × 10


def test_parallel_children_notify_at_boundary(make_routine, scripted):
    def slow_child(summary):
        def reply():
            time.sleep(0.05)
            return finish(summary=summary)
        return reply

    def slow_probe():          # a slow PARENT turn so both children finish during it
        time.sleep(0.4)
        return probe()

    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        (PARENT, spawn("CHILD-A: research alpha.", label="a")),
        (PARENT, spawn("CHILD-B: research beta.", label="b")),
        ("CHILD-A: research alpha.", slow_child("alpha result ready")),
        ("CHILD-B: research beta.", slow_child("beta result ready")),
        (PARENT, slow_probe),   # parent works while both children finish
        (PARENT, finish(summary="collected both")),
    ], slug="par")
    assert status == "ok"
    assert len([e for e in events if e["type"] == "subrun_end"]) == 2
    # both finish notifications reached the parent before its last completion
    final_call = ep.calls[-1]["messages"]
    joined = json.dumps(final_call)
    assert "SUB-WORKFLOW FINISHED" in joined
    assert "alpha result ready" in joined and "beta result ready" in joined


def test_kill_child(make_routine, scripted):
    def sleepy():
        time.sleep(0.5)
        return finish(summary="should never land")

    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        (PARENT, spawn("CHILD-S: sleep forever.", label="slow")),
        ("CHILD-S", sleepy),
        (PARENT, {"say": "Too slow — killing it.", "kind": "kill", "n": 1}),
        (PARENT, finish(summary="killed the slowpoke")),
    ], slug="killer")
    assert status == "ok"
    kill_obs = next(e for e in events if e["type"] == "observation"
                    and e["payload"]["kind"] == "kill")
    assert kill_obs["payload"].get("killed") or kill_obs["payload"].get("already_finished")
    sub_end = next(e for e in events if e["type"] == "subrun_end")
    assert sub_end["payload"]["status"] == "aborted"


def test_children_never_outlive_parent(make_routine, scripted):
    def sleepy():
        time.sleep(0.8)
        return finish(summary="late child")

    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        (PARENT, spawn("CHILD-L: long job.", label="long")),
        ("CHILD-L", sleepy),
        (PARENT, finish(summary="parent leaves early")),
    ], slug="leaver")
    assert status == "ok"
    fin = events[-1]
    assert "terminated at run end" in fin["payload"]["summary"]
    sub_end = next(e for e in events if e["type"] == "subrun_end")
    assert sub_end["payload"]["status"] == "aborted"


def test_spawn_caps(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        (PARENT, spawn("CHILD-1: quick job.")),
        ("CHILD-1: quick job.", spawn("GRANDCHILD: deeper.")),  # child hits the depth cap
        ("CHILD-1: quick job.", finish(summary="child done")),
        (PARENT, wait_(all_=True)),
        (PARENT, spawn("CHILD-2: second.")),
        ("CHILD-2: second.", finish(summary="child2 done")),
        (PARENT, wait_(all_=True)),
        (PARENT, spawn("CHILD-3: third.")),   # total cap (max_subruns 2) → rejected
        (PARENT, finish(summary="parent done")),
    ], slug="capped")
    assert status == "ok"
    parent_rejects = [e for e in events if e["type"] == "observation"
                      and e["payload"]["kind"] == "spawn" and e["payload"].get("rejected")]
    assert len(parent_rejects) == 1 and "budget" in parent_rejects[0]["payload"]["reason"]
    sub_events, _ = read_events(run_dir / "sub" / "1" / "transcript.jsonl")
    child_reject = [e for e in sub_events if e["type"] == "observation"
                    and e["payload"].get("kind") == "spawn" and e["payload"].get("rejected")]
    assert child_reject and "depth" in child_reject[0]["payload"]["reason"]


def test_spawn_picks_library_workflow(make_routine, scripted, tmp_path):
    lib = tmp_path / "lib"
    (lib / "workflows").mkdir(parents=True)
    (lib / "workflows" / "echo-task.py").write_text(
        '"""Echo pattern."""\n'
        'META = {"name": "Echo", "slug": "echo-task", "description": "d", "when_to_use": "w",\n'
        '        "version": 1, "status": "stable", "tags": ["a", "b", "c"]}\n'
        'PHASES = ["only"]\n'
        'COMPLETION = "done"\n'
        "def main():\n"
        '    """MARKER-ECHO-BODY: do the echo."""\n'
        "    echo()\n"
        "def echo():\n"
        '    """Do the echo."""\n')
    d = make_routine(slug="libpick")
    server = ServerConfig()
    server.libraries_home = lib
    ep = scripted([
        (PARENT, spawn("CHILD-E: echo it.", workflow="echo-task")),
        ("CHILD-E", finish(summary="echoed")),
        (PARENT, wait_(all_=True)),
        (PARENT, finish(summary="done")),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    child_system = next(c for c in ep.calls if "CHILD-E" in c["messages"][0]["content"])
    assert "MARKER-ECHO-BODY" in child_system["messages"][0]["content"]
    events, _ = read_events(run_dir / "transcript.jsonl")
    start = next(e for e in events if e["type"] == "subrun_start")
    assert start["payload"]["workflow"] == "echo-task"


def test_llm_subcall(make_routine, scripted):
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        {"say": "Scoped call.", "kind": "llm", "prompt": "Summarize: hello world"},
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
        return probe()

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
    ep = scripted([probe(), finish()])
    status, _ = run_routine(d, ServerConfig(), run_ts=TS)
    assert status == "ok"
    assert "priority: check the deploy" in ep.calls[0]["messages"][0]["content"]


def test_abort_flag(make_routine, scripted):
    import rsched.engine.loop as loop_mod

    def act_then_abort():
        loop_mod._ABORT["flag"] = True
        return probe()

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
        return probe()

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


def test_retry_shows_kind_example_and_repeat_notice(make_routine, scripted):
    """A payload-merged write_file (the glm-5.2 failure shape: file keys at top level,
    no content) gets a retry message carrying a concrete kind example; returning the
    identical action again adds the do-not-repeat escalation."""
    bad = {"say": "writing phase", "kind": "write_file", "path": "state/phase.json",
           "status": "ok", "summary": "phase = orient", "workflow": "self-audit"}
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        dict(bad), dict(bad), write_file("state/phase.json", '{"phase": "orient"}'),
        finish(),
    ])
    assert status == "ok"
    first_retry = ep.calls[1]["messages"][-1]["content"]
    assert '"kind": "write_file"' in first_retry          # the concrete example
    assert "plain JSON object" in first_retry
    assert "SAME invalid action" not in first_retry       # not yet a repeat
    second_retry = ep.calls[2]["messages"][-1]["content"]
    assert "SAME invalid action" in second_retry
    assert ep.calls[2]["schema"] is None                  # final attempt: grammar dropped


def test_write_file_accepts_structured_content(make_routine, scripted):
    """A JSON object as `content` is serialized pretty-printed — no escaping gymnastics."""
    d, ep, status, run_dir, events = _run(make_routine, scripted, [
        {"say": "Recording the phase.", "kind": "write_file",
         "path": "state/phase.json", "content": {"phase": "gather-evidence", "n": 2}},
        finish(),
    ])
    assert status == "ok"
    import json as _json
    on_disk = _json.loads((d / "state" / "phase.json").read_text())
    assert on_disk == {"phase": "gather-evidence", "n": 2}
