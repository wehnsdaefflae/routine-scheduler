"""System prompt assembly + state digest (composer.py), the CAPABILITIES section
(capabilities.py), observation formatting (observations.py), and compaction / on-disk
history / transcript replay (history.py)."""

import json

from rsched.config import ServerConfig, load_routine
from rsched.engine.budgets_config import Budgets
from rsched.engine.compaction import maybe_compact, messages_size
from rsched.engine.composer import build_system_prompt, state_digest
from rsched.engine.harness import harness_contract
from rsched.engine.observations import format_observation, truncate
from rsched.engine.run_context import RunContext
from rsched.engine.transcript import Transcript


def _ctx(make_routine, tmp_path, **kwargs) -> RunContext:
    d = make_routine(**kwargs)
    cfg, _problems = load_routine(d)
    assert cfg is not None
    run_dir = d / "runs" / "20260708-070000"
    run_dir.mkdir(parents=True)
    server = ServerConfig()
    server.libraries_home = tmp_path / "libraries"   # empty → catalog says "no utils yet"
    return RunContext(routine=cfg, server=server, registry=None, run_ts="20260708-070000",
                      run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                      budgets=Budgets.from_config(cfg.budgets))


def test_harness_contract_mentions_the_load_bearing_facts(make_routine, tmp_path):
    # Only the BEHAVIORAL facts are pinned here: the one-action contract, how code runs, the
    # ask modes, the configured budget rendered in, and the working dir.
    # Prose-level wording is owned by docs/prompt-anatomy.md + test_prompt_anatomy.py;
    # the write_util gloss variants are pinned by the grants tests below.
    ctx = _ctx(make_routine, tmp_path)
    text = harness_contract(ctx)
    # NOT "you have NO shell": that was false for the 14 routines holding the shell
    # permission, which read it in the same prompt whose CAPABILITIES section granted them
    # the reserved shell util. The contract says how code runs, not what does not exist.
    assert "NO shell" not in text
    for needle in ("EXACTLY one JSON object", "the `util` action", "10 turns",
                   "deferred", "blocking", str(ctx.routine.dir),
                   # the anti-batching override: the CLI harness advertises multi-tool
                   # batching, but the engine executes at most one action per reply
                   # (F180: batched actions were silently dropped with success ACKs)
                   "ONE tool call per reply"):
        assert needle in text, needle


def test_harness_contract_reflects_grants(make_routine, tmp_path):
    """The contract tells the model what its grants allow: authoring denied without the
    grant, and the confirm level (always / creations-only) spelled out with it."""
    from rsched.grantpolicy import GrantPolicy

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
    from rsched.grantpolicy import GrantPolicy

    ctx = _ctx(make_routine, tmp_path, slug="memg")
    ctx.grants = GrantPolicy()                       # memory not granted → no gloss
    assert "memory_read / memory_write:" not in harness_contract(ctx)
    ctx.grants = GrantPolicy(actions=frozenset({"memory_read", "memory_write"}))
    text = harness_contract(ctx)
    assert "memory_read / memory_write:" in text and "INDEX.md" in text


def test_state_digest_contents(make_routine, tmp_path):
    d = make_routine(slug="dig")
    (d / "state" / "phase.json").write_text('{"phase": "steady", "note": "n"}')
    (d / "stages").mkdir(exist_ok=True)
    (d / "stages" / "discover.md").write_text("# discover")   # on-demand stage module
    prev = d / "runs" / "20260701-070000"
    prev.mkdir(parents=True)
    (prev / "result.md").write_text("Previous outcome text.")
    digest = state_digest(d, deferred_qa=[{"qid": "q1", "question": "Q?", "answer": "A!"}],
                          open_qs=[{"qid": "q2", "question": "Open?", "asked": "20260707"}])
    for needle in ("steady", "Previous outcome text.", "LEDGER tail", "seed — routine created",
                   "Q?", "A!", "Open?", "phase.json",
                   "stages/ stage modules", "discover.md"):
        assert needle in digest, needle


def test_state_digest_inlines_the_working_plan(make_routine):
    """state/plan.md is the run's OWN decomposition — a conversation's emergent counterpart
    to a routine's compiled stages/. It rides the digest in full so every later reply opens
    on where the job stands instead of re-deriving it from the chat scrollback.
    """
    from rsched.engine.composer import PLAN_MAX_LINES

    d = make_routine(slug="plandig")
    assert "WORKING PLAN" not in state_digest(d, [], [])       # no file → no section
    (d / "state" / "plan.md").write_text(
        "# goal: port the reader\n- [x] inventory call sites\n- [>] port readmodels/\n"
        "- [ ] delete the shim\n", encoding="utf-8")
    digest = state_digest(d, [], [])
    assert "WORKING PLAN" in digest and "port readmodels/" in digest
    assert "Delete the file once the job is finished" in digest
    # the plan is a skeleton, not a document: an overgrown one is trimmed and told so
    (d / "state" / "plan.md").write_text(
        "\n".join(f"- step {i}" for i in range(PLAN_MAX_LINES + 20)), encoding="utf-8")
    long_digest = state_digest(d, [], [])
    assert f"step {PLAN_MAX_LINES - 1}" in long_digest
    assert f"step {PLAN_MAX_LINES + 1}" not in long_digest
    assert "belongs in stages/<name>.md" in long_digest


def test_state_digest_lists_delivered_artifacts(make_routine):
    """A conversation accumulates artifacts/ across replies and the UI renders them; without
    this the run had no idea what it had already delivered and rebuilt or duplicated it.
    """
    d = make_routine(slug="artdig")
    assert "artifacts/" not in state_digest(d, [], [])          # no dir → no section
    (d / "artifacts").mkdir()
    (d / "artifacts" / "report.md").write_text("# findings", encoding="utf-8")
    digest = state_digest(d, [], [])
    assert "artifacts/ delivered so far" in digest and "report.md" in digest
    assert "UPDATES that artifact in place" in digest


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


def test_state_digest_lists_held_rules(make_routine):
    # The rules a routine holds come from its CONFIG, not from any directory — the digest
    # names them so the run knows what to read, and carries no improve-* lens block.
    d = make_routine(slug="lens")
    digest = state_digest(d, [], [], held_rules=["ask-policy", "decision-record"])
    assert "General rules binding this routine" in digest
    assert "ask-policy, decision-record" in digest
    assert "Active improve-* lenses" not in digest
    assert "report-only" not in digest
    # no held rules → no section at all, rather than an empty heading
    assert "General rules binding this routine" not in state_digest(d, [], [])


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


def test_conversation_prompt_carries_its_first_message(make_routine, tmp_path):
    # A conversation runs at depth 0 but its task is instruction.md (the first message), NOT a
    # self-contained recipe. It is discriminated by HOME: its dir sits directly under the server's
    # conversations_home. Without the section the agent only sees the converse HOW-to pattern and
    # never its actual task (F91).
    ctx = _ctx(make_routine, tmp_path, slug="convo")
    ctx.server.conversations_home = ctx.routine.dir.parent
    task = "Summarize the attached quarterly report and flag the risks."
    sp = build_system_prompt(ctx, "## Run flow", task, "digest", [])
    assert "# INSTRUCTION (your assigned task)" in sp and task in sp
    # the ownership prose names instruction.md as the task and preserves multi-turn work
    hc = harness_contract(ctx)
    assert "first message that opened this conversation" in hc and "instruction.md" in hc
    assert "may take several turns" in hc
    # a routine whose dir is NOT under conversations_home still drops its transient seed
    ctx.server.conversations_home = tmp_path / "some-other-home"
    sp2 = build_system_prompt(ctx, "## Run flow", task, "digest", [])
    assert "# INSTRUCTION (your assigned task)" not in sp2 and task not in sp2


def test_capabilities_digest_utils_kinds_and_grants(make_routine, tmp_path):
    """The CAPABILITIES section names every util (one line each), the action kinds this run
    may use, and marks reserved-but-ungranted utils — so a run (or the clarify wizard,
    which cannot even call `util name=list`) plans against reality."""
    from rsched.engine.capabilities import capabilities_digest
    from rsched.grantpolicy import GrantPolicy

    ctx = _ctx(make_routine, tmp_path, slug="caps")
    for name, summary, tags in (("frob", "flips widgets", "code, dev"),
                                ("discord", "phone channel", "communication, chat")):
        d = ctx.server.libraries_home / "utils" / name
        d.mkdir(parents=True)
        (d / "main.py").write_text(
            f'"""{name} — {summary}.\n\nusage: gu {name} X\ntags: {tags}\n"""\n',
            encoding="utf-8")
    ctx.grants = GrantPolicy(active=("run-history",),
                             gated_utils={"discord": ("messaging-discord",)})
    text = capabilities_digest(ctx)
    assert "frob — flips widgets." in text
    assert "discord — phone channel.  [reserved — not granted to this routine]" in text
    # D52 Phase 1: the catalog is grouped by a controlled category vocabulary, not a flat list.
    assert "grouped by domain" in text
    assert "### Code & development (1)" in text          # frob (tags: code, dev)
    assert "### Email & messaging (1)" in text            # discord (tags: communication, chat)
    from rsched.engine.capabilities import _util_category
    assert _util_category(["code", "dev"]) == "Code & development"
    assert _util_category(["communication"]) == "Email & messaging"  # a util TAG, not the permission
    assert _util_category([]) == "Other"                  # no tags → Other, still listed
    # order-based collision resolution: a util tagged both health AND logs files under the
    # meta/logs group (listed first), NOT under Health & fitness.
    assert _util_category(["health", "logs"]) == "Scheduler, runs, logs & audit"
    kinds_line = next(line for line in text.splitlines() if line.startswith("Action kinds"))
    assert "util" in kinds_line and "write_util" not in kinds_line   # authoring not granted
    assert "Capabilities enabled (user-set, engine-enforced):" in text
    assert "Held permissions (conduct notes below): run-history" in text
    # a tools-restricted run (the wizard's clarify session) still SEES the catalog
    text2 = capabilities_digest(ctx, allowed_kinds={"ask_user", "read_file",
                                                    "write_file", "finish"})
    assert "cannot CALL utils" in text2 and "frob" in text2
    kinds2 = next(line for line in text2.splitlines() if line.startswith("Action kinds"))
    assert "spawn" not in kinds2 and "ask_user" in kinds2


def test_group_notes_reach_the_prompt_and_drain_once(make_routine, tmp_path):
    """F335 end to end through the composer: the harness contract NAMES the light channel (a
    channel a run does not know about is a channel that does not exist), and the state digest
    carries what teammates left — once, then it is gone.
    """
    from rsched import groups
    from rsched.engine.composer import state_digest
    from rsched.engine.harness import harness_contract

    ctx = _ctx(make_routine, tmp_path, slug="steward")
    ctx.server.routines_home = tmp_path / "routines"
    gid = groups.create(ctx.server.routines_home, name="FAU",
                        members=[{"slug": "steward"}, {"slug": "ingest"}])["id"]
    ctx.group_store_roots = groups.member_store_roots(ctx.server.routines_home, "steward",
                                                      create=True)

    contract = harness_contract(ctx)
    assert "write a note for them" in contract and "ingest" in contract
    assert "`report` when someone must ACT" in contract      # and when NOT to use it

    # written the way a routine writes one: an ordinary file into the group's shared store,
    # which is the only writer this channel has (`groupnotes` exposes none by design)
    store = groups.store_dir(ctx.server.routines_home, gid) / "notes" / "steward"
    store.mkdir(parents=True, exist_ok=True)
    (store / "note-20260902-120000-aaaaaa.json").write_text(
        json.dumps({"from": "ingest", "ts": "2026-09-02T12:00:00+02:00",
                    "text": "staged the batch for you"}), encoding="utf-8")
    kw = {"routines_home": ctx.server.routines_home, "slug": "steward"}
    digest = state_digest(ctx.routine.dir, [], [], **kw)
    assert "NOTES FROM YOUR GROUP" in digest and "staged the batch for you" in digest
    # the digest is built once per run and the note is delivered exactly once
    assert "NOTES FROM YOUR GROUP" not in state_digest(ctx.routine.dir, [], [], **kw)


def test_capabilities_digest_reports_actual_share_state_not_config(make_routine, tmp_path):
    """R514. The machine row must state what this run HAS, not what the catalog asked for.

    A share advertised from `MachineConfig.share` alone told a run "files mounted at mnt/gpu/"
    even when the sshfs mount had failed — and the empty mountpoint directory left behind read
    exactly like an empty share, so `dir-tree` answered `entries: 0` for a populated box. A
    share is named only once it is proven live; one that is not gets its reason instead.
    """
    from types import SimpleNamespace

    from rsched.engine.capabilities import capabilities_digest

    ctx = _ctx(make_routine, tmp_path, slug="mnts")
    ctx.routine.machines = ["gpu"]
    ctx.server.machines = {"gpu": SimpleNamespace(name="gpu", host="h", user="u", port=22,
                                                  host_key="", workdir="", share="/srv",
                                                  description="the GPU box", tags=[],
                                                  key_var="K")}

    ctx.mounted_shares, ctx.unavailable_shares = {"gpu"}, {}
    live = capabilities_digest(ctx)
    assert "files mounted at mnt/gpu/" in live and "SHARE NOT MOUNTED" not in live

    ctx.mounted_shares, ctx.unavailable_shares = set(), {"gpu": "sshfs failed: host down"}
    dead = capabilities_digest(ctx)
    assert "SHARE NOT MOUNTED this run (sshfs failed: host down)" in dead
    assert "files mounted at mnt/gpu/" not in dead
    assert "there is no mnt/gpu/ directory" in dead
    # the machine itself is still bound and still reachable for COMPUTE — only the mount is gone
    assert "gpu — the GPU box" in dead


def test_capabilities_digest_surfaces_provisioned_secret_names_never_values(make_routine, tmp_path):
    """D46: the CAPABILITIES section names the secrets provisioned in the central store so a run
    knows which credentials exist up front — NAMES only, never a value, no consent prompt."""
    from rsched import secrets as secret_store
    from rsched.engine.capabilities import capabilities_digest
    from rsched.grantpolicy import GrantPolicy

    ctx = _ctx(make_routine, tmp_path, slug="secdig")
    ctx.grants = GrantPolicy()
    # empty store → no secrets section at all
    assert "Secrets provisioned" not in capabilities_digest(ctx)
    secret_store.set_secret("DEEPGRAM_API_KEY", "super-secret-value-123")
    secret_store.set_secret("NOTION_TOKEN", "another-secret")
    text = capabilities_digest(ctx)
    assert "Secrets provisioned in the central store" in text
    assert "DEEPGRAM_API_KEY" in text and "NOTION_TOKEN" in text
    # the VALUE must never appear
    assert "super-secret-value-123" not in text and "another-secret" not in text


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
    from rsched.engine.compaction import KEEP_HEAD_MSGS, KEEP_TAIL_MSGS

    head = [{"role": "system", "content": "S"}] + [{"role": "user", "content": f"h{i}"}
                                                   for i in range(KEEP_HEAD_MSGS - 1)]
    middle = [{"role": "assistant", "content": f"m{i}"} for i in range(20)]
    tail = [{"role": "user", "content": f"t{i}"} for i in range(KEEP_TAIL_MSGS)]
    return head + middle + tail


def test_compact_to_history_writes_navigable_files(tmp_path):
    from rsched.config import ModelRef
    from rsched.engine.compaction import KEEP_HEAD_MSGS, KEEP_TAIL_MSGS, compact_to_history

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


def test_compact_to_history_non_json_reply_names_the_model(tmp_path):
    """A weak archival model answering prose instead of the schema must raise the
    teaching error — model + reply head — not a bare json "Expecting value" (F309,
    c-20260810-213335: that bare error every message); the caller's deterministic
    fallback then takes the pass."""
    import pytest

    from rsched.config import ModelRef
    from rsched.engine.compaction import compact_to_history

    class _Comp:
        parsed = None
        text, usage = "Sorry, I cannot produce JSON.", {}

    class _Ep:
        def complete(self, messages, **k):
            return _Comp()

    run_dir = tmp_path / "runs" / "20260710-070000"
    run_dir.mkdir(parents=True)
    with pytest.raises(RuntimeError) as exc:
        compact_to_history(_history_messages(), [], _Ep(), ModelRef("e", "m"),
                           run_dir, "history")
    msg = str(exc.value)
    assert "e/m" in msg and "non-JSON" in msg and "Sorry, I cannot" in msg


def test_compact_to_history_reports_its_own_usage(tmp_path):
    """The archival call's spend rides the compaction info so the loop can fold it into
    the run's usage — full-context calls must never be invisible to accounting."""
    from rsched.config import ModelRef
    from rsched.engine.compaction import compact_to_history

    ep = _history_endpoint({"files": [{"name": "n", "content": "c"}], "index": "- n: c"})
    run_dir = tmp_path / "runs" / "20260710-070000"
    run_dir.mkdir(parents=True)
    _, info = compact_to_history(_history_messages(), [], ep, ModelRef("e", "m"),
                                 run_dir, "history")
    assert info["usage"] == {"in": 1, "out": 1} and info["model"] == "e/m"


def test_compact_to_history_timeout_scales_with_middle_size(tmp_path):
    """F376: the archival call's timeout grows with the middle being read — a fixed 180s
    died on a 1.25M-char middle while the digest fallback took every pass. 180s base
    + 60s/200k chars, capped at the endpoint default (600s)."""
    from typing import ClassVar

    from rsched.config import ModelRef
    from rsched.engine.compaction import KEEP_HEAD_MSGS, KEEP_TAIL_MSGS, compact_to_history

    seen = []

    class _Comp:
        parsed: ClassVar = {"files": [{"name": "n", "content": "c"}], "index": "- n: c"}
        text, usage = "", {}

    class _Ep:
        def complete(self, messages, **k):
            seen.append(k["timeout"])
            return _Comp()

    def _msgs(middle_chars):
        head = [{"role": "system", "content": "S"}] * KEEP_HEAD_MSGS
        tail = [{"role": "user", "content": "t"}] * KEEP_TAIL_MSGS
        return [*head, {"role": "assistant", "content": "x" * middle_chars}, *tail]

    run_dir = tmp_path / "runs" / "20260710-070000"
    run_dir.mkdir(parents=True)
    for middle_chars in (1_000, 450_000, 1_300_000, 2_000_000):
        compact_to_history(_msgs(middle_chars), [], _Ep(), ModelRef("e", "m"),
                           run_dir, "history")
    assert seen[0] == 180                     # small middle keeps the old base
    assert seen[1] == 300                     # ~450k chars → 180 + 2*60
    assert seen[2] == 540                     # 1.3M chars (the F376 specimen) → 180 + 6*60
    assert seen[3] == 600                     # 2M chars → capped at the endpoint default


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
    from rsched.engine.compaction import compact_to_history

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
    from rsched.engine.compaction import compact_to_history

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
    from rsched.engine.compaction import compact_to_history

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


def test_replay_reconstitutes_child_announcements():
    """A child's exit announcement is a live message-list append with no 1:1 event — replay
    rebuilds it from `subrun_end`, placed where the live message sat (before the model's
    next action). A child whose summary rode a `wait` observation is NOT re-announced."""
    from rsched.engine.history import replay_messages

    events = [
        {"type": "assistant_action", "turn": 1,
         "payload": {"kind": "subtask", "prompt": "p", "label": "t1", "say": "s"}},
        {"type": "observation", "turn": 1, "payload": {"kind": "subtask", "n": 1,
                                                       "label": "t1", "started": True}},
        # the child finishes between turns; announcement precedes the next action live
        {"type": "subrun_end", "payload": {"n": 1, "label": "t1", "workflow": "general-task",
                                           "mode": "sequential", "status": "ok",
                                           "summary": "CHILD-RESULT-SENTINEL", "turns": 4}},
        {"type": "assistant_action", "turn": 2,
         "payload": {"kind": "finish", "status": "ok", "summary": "done", "say": "s"}},
    ]
    msgs, _, _ = replay_messages(events)
    contents = [m["content"] for m in msgs]
    assert any("CHILD RUN FINISHED (sequential child run)" in c
               and "CHILD-RESULT-SENTINEL" in c for c in contents)
    # placement: the announcement sits between turn 1's observation and turn 2's action
    idx = next(i for i, c in enumerate(contents) if "CHILD RUN FINISHED" in c)
    assert "finish" in contents[idx + 1]

    # wait-delivered: the summary is inside the wait observation — no extra announcement
    events_wait = [
        {"type": "assistant_action", "turn": 1,
         "payload": {"kind": "wait", "n": 1, "say": "s"}},
        {"type": "subrun_end", "payload": {"n": 1, "label": "t1", "workflow": "general-task",
                                           "mode": "sequential", "status": "ok",
                                           "summary": "WAITED-RESULT", "turns": 4}},
        {"type": "observation", "turn": 1,
         "payload": {"kind": "wait", "timed_out": False, "still_running": [],
                     "finished": [{"n": 1, "label": "t1", "status": "ok", "turns": 4,
                                   "mode": "sequential", "summary": "WAITED-RESULT"}]}},
        {"type": "assistant_action", "turn": 2,
         "payload": {"kind": "finish", "status": "ok", "summary": "done", "say": "s"}},
    ]
    msgs2, _, _ = replay_messages(events_wait)
    joined = "\n".join(m["content"] for m in msgs2)
    assert joined.count("WAITED-RESULT") == 1     # once (in the wait obs), never twice


def test_replay_does_not_duplicate_blocking_answers():
    """The answer text already lives inside the ask_user observation — replaying the
    `answer` event too used to inject it twice."""
    from rsched.engine.history import replay_messages

    events = [
        {"type": "assistant_action", "turn": 1,
         "payload": {"kind": "ask_user", "question": "Go?", "mode": "blocking", "say": "s"}},
        {"type": "question", "payload": {"qid": "q1", "question": "Go?", "mode": "blocking"}},
        {"type": "answer", "payload": {"qid": "q1", "text": "UNIQUE-ANSWER", "source": "web"}},
        {"type": "observation", "turn": 1,
         "payload": {"kind": "ask_user", "qid": "q1", "mode": "blocking", "answered": True,
                     "answer": "UNIQUE-ANSWER", "source": "web"}},
    ]
    msgs, _, _ = replay_messages(events)
    joined = "\n".join(m["content"] for m in msgs)
    assert joined.count("UNIQUE-ANSWER") == 1


def _gate_loop(monkeypatch, *, usage, phase="", last_seen_phase=None):
    """A minimal loop stub for compact_if_needed: 40 x 1750-char messages = 70k chars
    against a 100k window - between the 0.6 (60k) and 0.8 (80k) thresholds."""
    from types import SimpleNamespace

    from rsched.engine import window

    calls = []
    monkeypatch.setattr(window, "compact_to_history",
                        lambda msgs, *_a, **_k: (calls.append("llm") or (msgs, None)))
    monkeypatch.setattr(window, "maybe_compact",
                        lambda msgs, *_a, **_k: (calls.append("digest") or (msgs, None)))

    class _Reg:
        def for_model(self, kind, models):
            raise RuntimeError("no tool_call model in this stub")

    ctx = SimpleNamespace(usage=usage, tokens_remaining=lambda: None, registry=_Reg(),
                          routine=SimpleNamespace(models={}), run_dir=None, phase=phase,
                          transcript=SimpleNamespace(event=lambda *a, **k: None),
                          add_usage=lambda u: None)
    loop = SimpleNamespace(ctx=ctx, turn_records=[], _hist_rel="history",
                           _last_compact_after=0, _history_active=False,
                           _hist_note_countdown=0, _last_seen_phase=last_seen_phase,
                           messages=[{"role": "user", "content": "x" * 1750}
                                     for _ in range(40)])
    return loop, calls


def test_compaction_gate_uncached_compacts_at_60pct(monkeypatch):
    from rsched.config import ModelRef
    from rsched.engine.window import compact_if_needed

    loop, calls = _gate_loop(monkeypatch, usage={})
    # max_tokens=0 isolates the FRACTION gate: the output reservation (F265, tested in
    # test_history.test_input_cap_*) contributes nothing, so only the 0.6/0.8 trigger is under test.
    compact_if_needed(loop, endpoint=None,
                      ref=ModelRef("e", "m", context_chars=100_000, max_tokens=0))
    assert calls, "70k chars over a 100k window must compact at the uncached 0.6 gate"


def test_compaction_gate_cached_waits_for_80pct(monkeypatch):
    """Observed cache hits raise the gate to 0.8: compaction rewrites the prefix and
    invalidates the whole cache, so carried context is cheaper than re-archiving."""
    from rsched.config import ModelRef
    from rsched.engine.window import compact_if_needed

    loop, calls = _gate_loop(monkeypatch, usage={"cached_in": 5_000})
    # max_tokens=0 isolates the FRACTION gate (the F265 output reservation is tested separately
    # in test_history): 70k over 100k sits under the 0.8 cached trigger with no reservation.
    compact_if_needed(loop, endpoint=None,
                      ref=ModelRef("e", "m", context_chars=100_000, max_tokens=0))
    assert not calls, "with cache hits, 70k over 100k sits under the 0.8 gate - no compaction"


def test_a_stage_boundary_compacts_a_prompt_only_approaching_the_gate(monkeypatch):
    """Anticipatory compaction. The size gate is indifferent to WHERE in the work it trips, so it
    can rewrite the prefix three actions into a multi-action step — worst for both coherence and
    the cache. Entering a new stage module is a boundary the engine already detects, and a pass
    taken there pre-empts the forced mid-step one.

    Identical prompt and model to the cached-gate test above, which does NOT compact: 70k over a
    100k window sits under the 0.8 cached trigger. The ONLY difference is standing at a boundary,
    which brings the trigger to 0.8 x 0.85 = 68k.
    """
    from rsched.config import ModelRef
    from rsched.engine.window import compact_if_needed

    loop, calls = _gate_loop(monkeypatch, usage={"cached_in": 5_000}, phase="draft")
    compact_if_needed(loop, endpoint=None,
                      ref=ModelRef("e", "m", context_chars=100_000, max_tokens=0))
    assert calls, "at a stage boundary a prompt approaching the gate is archived early"
    assert loop._last_seen_phase == "draft"     # and the boundary is spent, not re-triggered


def test_mid_step_inside_the_same_stage_does_not_anticipate(monkeypatch):
    """It is the BOUNDARY that lowers the trigger, not the phase. A run already working inside
    `draft` is mid-step, and compacting there is the very thing this avoids."""
    from rsched.config import ModelRef
    from rsched.engine.window import compact_if_needed

    loop, calls = _gate_loop(monkeypatch, usage={"cached_in": 5_000}, phase="draft",
                             last_seen_phase="draft")
    compact_if_needed(loop, endpoint=None,
                      ref=ModelRef("e", "m", context_chars=100_000, max_tokens=0))
    assert not calls, "already inside the stage — the ordinary 0.8 gate applies"


def test_anticipation_cannot_force_a_pass_the_anti_thrash_guards_refuse(monkeypatch):
    """Moving WHEN a compaction happens must never add one. A middle too small to pay for an
    archival call is still refused at a boundary."""
    from types import SimpleNamespace

    from rsched.config import ModelRef
    from rsched.engine.window import compact_if_needed

    loop, calls = _gate_loop(monkeypatch, usage={"cached_in": 5_000}, phase="draft")
    # 30 messages = a 0-message middle against the 6+24 head/tail floor
    loop.messages = [{"role": "user", "content": "x" * 4_000} for _ in range(30)]
    compact_if_needed(loop, endpoint=None,
                      ref=ModelRef("e", "m", context_chars=100_000, max_tokens=0))
    assert not calls, "no middle to archive — the boundary must not override the floor"
    assert isinstance(loop.ctx, SimpleNamespace)


def test_harness_contract_recipe_line_follows_unlock(make_routine, tmp_path):
    """The prompt must tell the TRUTH about recipe ownership: sealed by default, but a run
    whose grants carry recipe_unlocked (a user fs_write_root covers the routine's own dir —
    the routine-improver's case) must be told its recipe IS writable. The unconditional
    "READ-ONLY to you" sentence made the improver skip every lens on its own self-target
    despite the include-toggle being on (F165, routine-improver:20260723-112446 t11/t13)."""
    from rsched.grantpolicy import GrantPolicy

    ctx = _ctx(make_routine, tmp_path, slug="recun")
    ctx.grants = GrantPolicy()                        # sealed — the default for every run
    assert "READ-ONLY to you" in harness_contract(ctx)
    ctx.grants = GrantPolicy(recipe_unlocked=True)    # user write root covers the own dir
    text = harness_contract(ctx)
    assert "IS WRITABLE" in text
    assert "READ-ONLY to you" not in text
