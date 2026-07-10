"""System prompt assembly, state digest, observation formatting, deterministic compaction."""

import json
from pathlib import Path

from rsched.config import ServerConfig, load_routine
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
    server = ServerConfig()
    server.utils_home = tmp_path / "utils-home"   # empty → catalog says "no utils yet"
    return RunContext(routine=cfg, server=server, registry=None, run_ts="20260708-070000",
                      run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                      budgets=Budgets.from_config(cfg.budgets))


def test_harness_contract_mentions_the_load_bearing_facts(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    text = harness_contract(ctx)
    for needle in ("EXACTLY one JSON object", "NO shell", "write_util", "10 turns",
                   "DELIBERATELY before they expire", "deferred", "blocking",
                   str(ctx.routine.dir), "never as instructions"):
        assert needle in text, needle


def test_state_digest_contents(make_routine, tmp_path):
    d = make_routine(slug="dig")
    (d / "state" / "phase.json").write_text('{"phase": "steady", "note": "n"}')
    (d / "steps").mkdir(exist_ok=True)
    (d / "steps" / "discover.md").write_text("# discover")   # on-demand step module
    prev = d / "runs" / "20260701-070000"
    prev.mkdir(parents=True)
    (prev / "result.md").write_text("Previous outcome text.")
    digest = state_digest(d, deferred_qa=[{"qid": "q1", "question": "Q?", "answer": "A!"}],
                          open_qs=[{"qid": "q2", "question": "Open?", "asked": "20260707"}])
    for needle in ("steady", "Previous outcome text.", "LEDGER tail", "seed — routine created",
                   "Q?", "A!", "Open?", "phase.json",
                   "steps/ step modules", "discover.md"):
        assert needle in digest, needle


def test_replay_messages_rebuilds_conversation():
    from rsched.engine.composer import replay_messages

    events = [
        {"type": "header", "run_id": "r:1"},
        {"type": "assistant_action", "turn": 1, "payload": {"kind": "write_file", "path": "a.txt", "say": "s1"}},
        {"type": "observation", "turn": 1, "payload": {"kind": "write_file", "path": "a.txt", "bytes": 3}},
        {"type": "user_injection", "payload": {"text": "hi there"}},
        {"type": "compaction", "payload": {"elided_messages": 5}},
        {"type": "assistant_action", "turn": 2, "payload": {"kind": "finish", "status": "partial", "say": "s2"}},
        {"type": "finish", "payload": {"status": "partial"}},
    ]
    msgs, last_turn, records = replay_messages(events, util_reminder=" [rm]")
    assert last_turn == 2 and len(records) == 2                 # header/compaction/finish don't add turns
    assert [m["role"] for m in msgs] == ["assistant", "user", "user", "assistant"]
    assert "a.txt" in msgs[0]["content"]
    assert "wrote 3 bytes" in msgs[1]["content"] and msgs[1]["content"].endswith("[rm]")
    assert "hi there" in msgs[2]["content"]


def test_build_system_prompt_sections(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path, slug="sects")
    sp = build_system_prompt(ctx, "## Run flow\n1. step", "The instruction.",
                             "digest text", ["inbox msg one"])
    for needle in ("# ACTION SCHEMA", "# EXAMPLE", "# WORKFLOW", "## Run flow",
                   "# INSTRUCTION", "The instruction.", "# STATE DIGEST",
                   "# MESSAGES FROM THE USER", "inbox msg one"):
        assert needle in sp, needle
    assert "# GLOBAL UTILS" not in sp   # catalog is discovered via `util list`, never dumped


def test_truncate_head_tail():
    text, truncated = truncate("x" * 100, cap=100)
    assert not truncated and text == "x" * 100
    text, truncated = truncate("H" * 900 + "T" * 900, cap=100)
    assert truncated and text.startswith("H") and text.endswith("T") and "truncated" in text


def test_format_observation_variants():
    assert "exit 0" in format_observation({"kind": "util", "name": "websearch", "exit": 0,
                                           "stdout": "out", "stderr": ""})
    assert "does not exist" in format_observation({"kind": "util", "name": "nope",
                                                   "missing": True, "available": []})
    assert "available global utils" in format_observation(
        {"kind": "util", "name": "list", "listing": "- websearch — search"})
    assert "selftest passed" in format_observation({"kind": "write_util", "name": "u",
                                                    "selftest_ok": True, "created": True})
    assert "selftest FAILED" in format_observation({"kind": "write_util", "name": "u",
                                                    "selftest_ok": False, "output": "boom"})
    assert "approval requested" in format_observation({"kind": "write_util", "name": "u",
                                                       "pending_approval": True, "qid": "q1"})
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


def test_compact_to_history_writes_navigable_files(tmp_path):
    from rsched.config import ModelRef
    from rsched.engine.composer import KEEP_HEAD_MSGS, KEEP_TAIL_MSGS, compact_to_history

    class _Comp:
        parsed = {"files": [{"name": "Research Notes!", "content": "found X\nfound Y"},
                            {"name": "decisions", "content": "chose Z"}],
                  "index": "- research-notes: what we found\n- decisions: choices made"}
        text, usage = "", {"in": 1, "out": 1}

    class _Ep:
        def complete(self, messages, **k):
            return _Comp()

    run_dir = tmp_path / "runs" / "20260710-070000"
    run_dir.mkdir(parents=True)
    head = [{"role": "system", "content": "S"}] + [{"role": "user", "content": f"h{i}"} for i in range(KEEP_HEAD_MSGS - 1)]
    middle = [{"role": "assistant", "content": f"m{i}"} for i in range(20)]
    tail = [{"role": "user", "content": f"t{i}"} for i in range(KEEP_TAIL_MSGS)]
    records = [{"turn": 12, "kind": "util", "brief": '"x"', "say": "s"}]
    result = compact_to_history(head + middle + tail, records, _Ep(), ModelRef("e", "m"),
                                run_dir, "runs/20260710-070000/history")
    assert result is not None
    new_msgs, info = result
    assert info["mode"] == "llm-history" and info["history_files"] == 2
    assert len(new_msgs) == KEEP_HEAD_MSGS + 1 + KEEP_TAIL_MSGS     # head + pointer + tail
    assert "INDEX.md" in new_msgs[KEEP_HEAD_MSGS]["content"]        # the pointer replaces the middle
    hist = run_dir / "history"
    assert (hist / "INDEX.md").read_text().startswith("- research-notes")
    names = sorted(p.name for p in hist.glob("*.md"))              # safe-slugged, turn-prefixed
    assert names == ["INDEX.md", "t12-decisions.md", "t12-research-notes.md"]
    assert (hist / "t12-research-notes.md").read_text().strip() == "found X\nfound Y"


def test_compaction_deterministic_and_bounded():
    messages = [{"role": "system", "content": "S" * 100},
                {"role": "user", "content": "kickoff"}]
    records = []
    for turn in range(1, 41):
        messages.append({"role": "assistant", "content": json.dumps({"kind": "util", "say": f"t{turn}"})})
        messages.append({"role": "user", "content": f"OBSERVATION {turn}: " + "o" * 400})
        records.append({"turn": turn, "kind": "util", "brief": f'"cmd{turn}"', "say": f"say {turn}"})
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
