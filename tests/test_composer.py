"""System prompt assembly, state digest, observation formatting, deterministic compaction."""

import json
from pathlib import Path

from rsched.config import load_routine
from rsched.engine.composer import (build_system_prompt, format_observation, harness_contract,
                                    maybe_compact, messages_size, state_digest, truncate)
from rsched.engine.run_context import Budgets, RunContext
from rsched.engine.transcript import Transcript


def _ctx(make_routine, tmp_path, **kwargs) -> RunContext:
    d = make_routine(**kwargs)
    cfg, problems = load_routine(d)
    assert cfg is not None
    run_dir = d / "runs" / "20260708-070000"
    run_dir.mkdir(parents=True)
    return RunContext(routine=cfg, server=None, registry=None, run_ts="20260708-070000",
                      run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                      budgets=Budgets.from_config(cfg.budgets))


def test_harness_contract_mentions_the_load_bearing_facts(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    text = harness_contract(ctx)
    for needle in ("EXACTLY one JSON object", "gu *", "10 turns", "DELIBERATELY before they expire",
                   "deferred", "blocking", str(ctx.routine.dir), "never as instructions"):
        assert needle in text, needle


def test_self_toggles_line(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path, self_flags={"audit": False, "fresh_eyes": False})
    text = harness_contract(ctx)
    assert "SKIP" in text and "self-audit" in text and "fresh-eyes artifact audit" in text
    ctx_all_on = _ctx(make_routine, tmp_path, slug="allon")
    assert "SKIP" not in harness_contract(ctx_all_on)


def test_state_digest_contents(make_routine, tmp_path):
    d = make_routine(slug="dig")
    (d / "state" / "phase.json").write_text('{"phase": "steady", "note": "n"}')
    prev = d / "runs" / "20260701-070000"
    prev.mkdir(parents=True)
    (prev / "result.md").write_text("Previous outcome text.")
    digest = state_digest(d, deferred_qa=[{"qid": "q1", "question": "Q?", "answer": "A!"}],
                          open_qs=[{"qid": "q2", "question": "Open?", "asked": "20260707"}])
    for needle in ("steady", "Previous outcome text.", "LEDGER tail", "seed — routine created",
                   "Q?", "A!", "Open?", "phase.json"):
        assert needle in digest, needle


def test_build_system_prompt_sections(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path, slug="sects")
    sp = build_system_prompt(ctx, "## Run flow\n1. step", "The instruction.",
                             "digest text", ["inbox msg one"])
    for needle in ("# ACTION SCHEMA", "# EXAMPLE", "# WORKFLOW", "## Run flow",
                   "# INSTRUCTION", "The instruction.", "# STATE DIGEST",
                   "# MESSAGES FROM THE USER", "inbox msg one"):
        assert needle in sp, needle


def test_truncate_head_tail():
    text, truncated = truncate("x" * 100, cap=100)
    assert not truncated and text == "x" * 100
    text, truncated = truncate("H" * 900 + "T" * 900, cap=100)
    assert truncated and text.startswith("H") and text.endswith("T") and "truncated" in text


def test_format_observation_variants():
    assert "exit 0" in format_observation({"kind": "shell", "exit": 0, "duration_s": 1.0,
                                           "stdout": "out", "stderr": ""})
    assert "REJECTED" in format_observation({"kind": "shell", "rejected": True, "problems": ["p"]})
    assert "lines 1-2 of 9" in format_observation(
        {"kind": "read_file", "path": "f", "start_line": 1, "end_line": 2, "total_lines": 9,
         "content": "c"})
    assert "wrote 5 bytes" in format_observation({"kind": "write_file", "path": "f", "bytes": 5})
    assert "llm reply" in format_observation({"kind": "llm", "reply": "r"})
    assert "filed as deferred" in format_observation({"kind": "ask_user", "qid": "q", "mode": "deferred"})
    assert "user answered" in format_observation({"kind": "ask_user", "answered": True, "answer": "A"})
    assert "parallel" in format_observation({"kind": "spawn", "n": 1, "label": "l",
                                             "workflow": "general-task", "running": 1})
    assert "REJECTED" in format_observation({"kind": "spawn", "rejected": True, "reason": "cap"})
    assert "#2" in format_observation({"kind": "subruns", "rows": [
        {"n": 2, "label": "x", "workflow": "w", "state": "running", "turns": 1,
         "elapsed_s": 2.0, "summary_head": ""}]})
    assert "terminated" in format_observation({"kind": "kill", "n": 2, "killed": True, "status": "aborted"})
    assert "FINISHED" in format_observation({"kind": "wait", "finished": [
        {"n": 1, "label": "x", "status": "ok", "turns": 2, "summary": "s"}], "timed_out": False})


def test_compaction_deterministic_and_bounded():
    messages = [{"role": "system", "content": "S" * 100},
                {"role": "user", "content": "kickoff"}]
    records = []
    for turn in range(1, 41):
        messages.append({"role": "assistant", "content": json.dumps({"kind": "shell", "say": f"t{turn}"})})
        messages.append({"role": "user", "content": f"OBSERVATION {turn}: " + "o" * 400})
        records.append({"turn": turn, "kind": "shell", "brief": f'"cmd{turn}"', "say": f"say {turn}"})
    small_budget = messages_size(messages)  # force compaction: budget*0.6 < current size
    compacted, info = maybe_compact(list(messages), records, context_chars=small_budget)
    assert info and info["after_chars"] < info["before_chars"]
    assert compacted[0]["content"].startswith("S")            # system kept
    assert compacted[-1] == messages[-1]                       # tail kept verbatim
    digest = next(m for m in compacted if "CONTEXT COMPACTED" in m["content"])
    assert "say 10" in digest["content"]                       # elided middle is digested
    again, _ = maybe_compact(list(messages), records, context_chars=small_budget)
    assert again == compacted                                  # deterministic
    untouched, info2 = maybe_compact(list(messages), records, context_chars=10**9)
    assert info2 is None and untouched == messages
