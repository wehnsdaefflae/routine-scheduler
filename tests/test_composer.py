"""System prompt assembly, state digest, observation formatting (composer.py) and
compaction / on-disk history / transcript replay (history.py)."""

import json
from pathlib import Path

from rsched.config import ServerConfig, load_routine
from rsched.engine.composer import (build_system_prompt, format_observation, harness_contract,
                                    state_digest, truncate)
from rsched.engine.history import maybe_compact, messages_size
from rsched.engine.run_context import Budgets, RunContext
from rsched.engine.transcript import Transcript


def _ctx(make_routine, tmp_path, **kwargs) -> RunContext:
    d = make_routine(**kwargs)
    cfg, problems = load_routine(d)
    assert cfg is not None
    run_dir = d / "runs" / "20260708-070000"
    run_dir.mkdir(parents=True)
    server = ServerConfig()
    server.libraries_home = tmp_path / "libraries"   # empty → catalog says "no utils yet"
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


def test_harness_contract_reflects_grants(make_routine, tmp_path):
    """The contract tells the model what its grants allow: authoring denied without the
    grant, and the confirm level (always / creations-only) spelled out with it."""
    from rsched.grants import GrantPolicy

    ctx = _ctx(make_routine, tmp_path, slug="granted")
    ctx.grants = GrantPolicy()                       # write_util switched off
    text = harness_contract(ctx)
    assert "switched OFF in this routine's capabilities" in text
    ctx.grants = GrantPolicy(actions=frozenset(["write_util"]), confirm="creations")
    text2 = harness_contract(ctx)
    assert "auto-approved once its selftest passes" in text2
    ctx.grants = GrantPolicy(actions=frozenset(["write_util"]), confirm="always")
    assert "needs the user's approval" in harness_contract(ctx)


def test_harness_contract_memory_line_follows_grant(make_routine, tmp_path):
    from rsched.grants import GrantPolicy

    ctx = _ctx(make_routine, tmp_path, slug="memg")
    ctx.grants = GrantPolicy()                       # memory not granted → no gloss
    assert "memory_read / memory_write:" not in harness_contract(ctx)
    ctx.grants = GrantPolicy(actions=frozenset({"memory_read", "memory_write"}))
    text = harness_contract(ctx)
    assert "memory_read / memory_write:" in text and "INDEX.md" in text


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


def test_state_digest_inlines_background_tasks(make_routine):
    d = make_routine(slug="bgdig")
    assert "Background tasks you launched" not in state_digest(d, [], [])   # no file → no section
    (d / "state" / "background.json").write_text(
        '[{"taskid": "bg-x-1", "label": "scrape", "state": "finished", "delivered": false},'
        ' {"taskid": "bg-x-2", "label": "convert", "state": "running", "delivered": false}]',
        encoding="utf-8")
    digest = state_digest(d, [], [])
    assert "Background tasks you launched" in digest
    assert "scrape" in digest and "bg-x-1" in digest and "[finished]" in digest
    assert "convert" in digest and "still running" in digest


def test_state_digest_lists_traits_without_lens_gating(make_routine):
    # Improvement moved to the routine-improver meta routine: the digest lists the trait
    # files plainly and carries NO improve-* lens/authorization block anymore.
    d = make_routine(slug="lens")
    traits = d / "traits"
    traits.mkdir()
    (traits / "ask-policy.md").write_text("# ask", encoding="utf-8")
    digest = state_digest(d, [], [])
    assert "traits/ practice modules" in digest and "ask-policy.md" in digest
    assert "Active improve-* lenses" not in digest
    assert "report-only" not in digest


def test_state_digest_surfaces_memory_index(make_routine):
    d = make_routine(slug="mem")
    assert ".memory" not in state_digest(d, [], [])            # no dir → no section
    mem = d / ".memory"
    mem.mkdir()
    (mem / "quirks.md").write_text("# quirks\n", encoding="utf-8")
    digest = state_digest(d, [], [])
    assert "INDEX.md is MISSING" in digest and "quirks.md" in digest
    (mem / "INDEX.md").write_text("- quirks.md: env surprises, check before setup\n",
                                  encoding="utf-8")
    digest = state_digest(d, [], [])
    assert "- quirks.md: env surprises, check before setup" in digest
    assert "memory_read the relevant topic" in digest
    (mem / "INDEX.md").write_text("\n".join(f"- f{i}.md: x" for i in range(70)), encoding="utf-8")
    assert "full 70 lines" in state_digest(d, [], [])          # long index → head + pointer


def test_replay_messages_rebuilds_conversation():
    from rsched.engine.history import replay_messages

    events = [
        {"type": "header", "run_id": "r:1"},
        {"type": "assistant_action", "turn": 1, "payload": {"kind": "write_file", "path": "a.txt", "say": "s1"}},
        {"type": "observation", "turn": 1, "payload": {"kind": "write_file", "path": "a.txt", "bytes": 3}},
        {"type": "user_injection", "payload": {"text": "hi there"}},
        {"type": "compaction", "payload": {"elided_messages": 5}},
        {"type": "assistant_action", "turn": 2, "payload": {"kind": "finish", "status": "partial", "say": "s2"}},
        {"type": "finish", "payload": {"status": "partial"}},
    ]
    msgs, last_turn, records = replay_messages(events)
    assert last_turn == 2 and len(records) == 2                 # header/compaction/finish don't add turns
    assert [m["role"] for m in msgs] == ["assistant", "user", "user", "assistant"]
    assert "a.txt" in msgs[0]["content"]
    assert "wrote 3 bytes" in msgs[1]["content"]
    assert "hi there" in msgs[2]["content"]


def test_build_system_prompt_sections(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path, slug="sects")
    sp = build_system_prompt(ctx, "## Run flow\n1. step", "SEED-BODY-SENTINEL",
                             "digest text", ["inbox msg one"])
    for needle in ("# ACTION SCHEMA", "# EXAMPLE", "# WORKFLOW", "## Run flow",
                   "# CAPABILITIES", "# STATE DIGEST",
                   "# MESSAGES FROM THE USER", "inbox msg one"):
        assert needle in sp, needle
    # a top-level routine's instruction is the SEED, compiled into the steps — NOT in the prompt
    assert "# INSTRUCTION (your assigned task)" not in sp and "SEED-BODY-SENTINEL" not in sp
    assert "(none in the library yet)" in sp   # empty test library → capabilities say so


def test_subrun_prompt_carries_its_instruction(make_routine, tmp_path):
    # a subrun (depth > 0) has no decomposed steps — its instruction IS the parent's self-contained
    # brief, so it stays in the prompt (unlike a top-level routine, whose task lives in its steps)
    ctx = _ctx(make_routine, tmp_path, slug="subsects")
    ctx.depth = 1
    sp = build_system_prompt(ctx, "## Run flow", "Do the delegated thing.", "(subrun)", [])
    assert "# INSTRUCTION (your assigned task)" in sp and "Do the delegated thing." in sp


def test_capabilities_digest_utils_kinds_and_grants(make_routine, tmp_path):
    """The CAPABILITIES section names every util (one line each), the action kinds this run
    may use, and marks reserved-but-ungranted utils — so a run (or the clarify wizard,
    which cannot even call `util name=list`) plans against reality."""
    from rsched.engine.composer import capabilities_digest
    from rsched.grants import GrantPolicy

    ctx = _ctx(make_routine, tmp_path, slug="caps")
    for name, summary in (("frob", "flips widgets"), ("discord", "phone channel")):
        d = ctx.server.utils_home / "utils" / name
        d.mkdir(parents=True)
        (d / "main.py").write_text(f'"""{name} — {summary}.\n\nusage: gu {name} X\n"""\n',
                                   encoding="utf-8")
    ctx.grants = GrantPolicy(active=("run-history",),
                             gated_utils={"discord": ("communication",)})
    text = capabilities_digest(ctx)
    assert "frob — flips widgets." in text
    assert "discord — phone channel.  [reserved — not granted to this routine]" in text
    kinds_line = next(l for l in text.splitlines() if l.startswith("Action kinds"))
    assert "util" in kinds_line and "write_util" not in kinds_line   # authoring not granted
    assert "Capabilities enabled (user-set, engine-enforced):" in text
    assert "Held permissions (conduct notes below): run-history" in text
    # a tools-restricted run (the wizard's clarify session) still SEES the catalog
    text2 = capabilities_digest(ctx, allowed_kinds={"ask_user", "read_file",
                                                    "write_file", "finish"})
    assert "cannot CALL utils" in text2 and "frob" in text2
    kinds2 = next(l for l in text2.splitlines() if l.startswith("Action kinds"))
    assert "spawn" not in kinds2 and "ask_user" in kinds2


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
    # batched multi-path read: one section per file, failures inline
    multi = format_observation({"kind": "read_file", "files": [
        {"path": "a.md", "start_line": 1, "end_line": 2, "total_lines": 2, "content": "A"},
        {"path": "b.md", "error": "no such file"}]})
    assert "2 files" in multi and "--- a.md (lines 1-2 of 2) ---\nA" in multi
    assert "--- b.md FAILED: no such file" in multi
    assert "replaced 1 occurrence" in format_observation(
        {"kind": "edit_file", "path": "f.md", "replacements": 1, "bytes": 9})
    assert "FAILED" in format_observation(
        {"kind": "edit_file", "path": "f.md", "error": "anchor not found"})


def _history_endpoint(payload):
    class _Comp:
        parsed = payload
        text, usage = "", {"in": 1, "out": 1}

    class _Ep:
        def complete(self, messages, **k):
            self.last_prompt = messages[-1]["content"]
            return _Comp()

    return _Ep()


def _history_messages():
    from rsched.engine.history import KEEP_HEAD_MSGS, KEEP_TAIL_MSGS

    head = [{"role": "system", "content": "S"}] + [{"role": "user", "content": f"h{i}"}
                                                   for i in range(KEEP_HEAD_MSGS - 1)]
    middle = [{"role": "assistant", "content": f"m{i}"} for i in range(20)]
    tail = [{"role": "user", "content": f"t{i}"} for i in range(KEEP_TAIL_MSGS)]
    return head + middle + tail


def test_compact_to_history_writes_navigable_files(tmp_path):
    from rsched.config import ModelRef
    from rsched.engine.history import KEEP_HEAD_MSGS, KEEP_TAIL_MSGS, compact_to_history

    ep = _history_endpoint({"files": [{"name": "Research Notes!", "content": "found X\nfound Y"},
                                      {"name": "decisions", "content": "chose Z"}],
                            "index": "- research-notes: what we found\n- decisions: choices made"})
    run_dir = tmp_path / "runs" / "20260710-070000"
    run_dir.mkdir(parents=True)
    records = [{"turn": 12, "kind": "util", "brief": '"x"', "say": "s"}]
    result = compact_to_history(_history_messages(), records, ep, ModelRef("e", "m"),
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


def test_compact_to_history_reports_its_own_usage(tmp_path):
    """The archival call's spend rides the compaction info so the loop can fold it into
    the run's usage — full-context calls must never be invisible to accounting."""
    from rsched.config import ModelRef
    from rsched.engine.history import compact_to_history

    ep = _history_endpoint({"files": [{"name": "n", "content": "c"}], "index": "- n: c"})
    run_dir = tmp_path / "runs" / "20260710-070000"
    run_dir.mkdir(parents=True)
    _, info = compact_to_history(_history_messages(), [], ep, ModelRef("e", "m"),
                                 run_dir, "history")
    assert info["usage"] == {"in": 1, "out": 1} and info["model"] == "e/m"


def test_prior_usage_sums_all_legs():
    """Resume accounting: every usage-carrying event across the whole transcript counts —
    actions, llm subcalls, compactions — so status.json shows the run's true total."""
    from rsched.engine.history import prior_usage

    events = [
        {"type": "assistant_action", "usage": {"in": 100, "out": 10, "cached_in": 50}},
        {"type": "observation", "payload": {"kind": "llm", "usage": {"in": 20, "out": 5,
                                                                     "cost": 0.01}}},
        {"type": "observation", "payload": {"kind": "write_file", "bytes": 3}},   # no usage
        {"type": "compaction", "payload": {"usage": {"in": 200, "out": 40}}},
        {"type": "finish", "payload": {"status": "partial"}},
        {"type": "assistant_action", "usage": {"in": 30, "out": 3, "cache_write": 7}},
    ]
    assert prior_usage(events) == {"in": 350, "out": 58, "cached_in": 50,
                                   "cache_write": 7, "cost": 0.01}


def test_compact_to_history_second_pass_accumulates_atomically(tmp_path):
    """A later compaction carries the earlier files over, rewrites INDEX.md, and leaves no
    temp/displaced siblings behind — the swap is all-or-nothing."""
    from rsched.config import ModelRef
    from rsched.engine.history import compact_to_history

    run_dir = tmp_path / "runs" / "20260710-080000"
    run_dir.mkdir(parents=True)
    ep1 = _history_endpoint({"files": [{"name": "alpha", "content": "first findings"}],
                             "index": "- alpha: first findings"})
    assert compact_to_history(_history_messages(), [{"turn": 10, "kind": "util", "brief": '"x"',
                                                     "say": "s"}],
                              ep1, ModelRef("e", "m"), run_dir, "history") is not None
    ep2 = _history_endpoint({"files": [{"name": "beta", "content": "later findings"}],
                             "index": "- alpha: first findings\n- beta: later findings"})
    assert compact_to_history(_history_messages(), [{"turn": 20, "kind": "util", "brief": '"y"',
                                                     "say": "s"}],
                              ep2, ModelRef("e", "m"), run_dir, "history") is not None
    assert "There is already a history index" in ep2.last_prompt   # prior INDEX fed to the LLM
    hist = run_dir / "history"
    names = sorted(p.name for p in hist.glob("*.md"))
    assert names == ["INDEX.md", "t10-alpha.md", "t20-beta.md"]    # earlier file carried over
    assert "beta" in (hist / "INDEX.md").read_text()
    leftovers = [p.name for p in run_dir.iterdir() if p.name != "history"]
    assert leftovers == []                                         # no tmp/displaced dirs remain


def test_compact_to_history_failure_leaves_prior_history_intact(tmp_path, monkeypatch):
    """If the swap fails mid-way, the pre-existing history survives untouched and the temp
    build dir is cleaned up (the caller then falls back to the deterministic digest)."""
    import os

    from rsched.config import ModelRef
    from rsched.engine.history import compact_to_history

    run_dir = tmp_path / "runs" / "20260710-090000"
    hist = run_dir / "history"
    hist.mkdir(parents=True)
    (hist / "INDEX.md").write_text("- t5-kept: prior notes\n", encoding="utf-8")
    (hist / "t5-kept.md").write_text("prior notes\n", encoding="utf-8")
    ep = _history_endpoint({"files": [{"name": "gamma", "content": "new stuff"}],
                            "index": "- gamma: new stuff"})

    def boom(src, dst):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", boom)
    try:
        compact_to_history(_history_messages(), [{"turn": 30, "kind": "util", "brief": '"z"',
                                                  "say": "s"}],
                           ep, ModelRef("e", "m"), run_dir, "history")
    except OSError:
        pass
    else:
        raise AssertionError("swap failure must propagate so the caller can fall back")
    monkeypatch.undo()
    assert sorted(p.name for p in hist.glob("*.md")) == ["INDEX.md", "t5-kept.md"]
    assert (hist / "t5-kept.md").read_text() == "prior notes\n"
    leftovers = [p.name for p in run_dir.iterdir() if p.name != "history"]
    assert leftovers == []                                         # temp build dir was removed


def test_compact_to_history_rejects_unusable_llm_output(tmp_path):
    """Empty files/index → None (deterministic fallback) and nothing lands on disk."""
    from rsched.config import ModelRef
    from rsched.engine.history import compact_to_history

    run_dir = tmp_path / "runs" / "20260710-100000"
    run_dir.mkdir(parents=True)
    ep = _history_endpoint({"files": [], "index": ""})
    assert compact_to_history(_history_messages(), [], ep, ModelRef("e", "m"),
                              run_dir, "history") is None
    assert list(run_dir.iterdir()) == []


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


def test_harness_contract_renders_unlimited_token_budget(make_routine, tmp_path):
    """A -1 token budget (the default) reads as 'unlimited' in the harness contract, never -1."""
    ctx = _ctx(make_routine, tmp_path, slug="unl")
    ctx.budgets.max_total_tokens = -1
    text = harness_contract(ctx)
    assert "unlimited total tokens" in text
    assert "-1" not in text.split("Budgets for this run")[1][:150]
