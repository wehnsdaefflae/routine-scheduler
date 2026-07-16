"""docs/prompt-anatomy.md is contract documentation for the Help tab: it must track the
prompt surface. This pins the load-bearing engine strings — change composer/loop/schema
wording and this fails until the doc is revised to match."""

from pathlib import Path
from types import SimpleNamespace

from rsched.config import ServerConfig, load_routine
from rsched.engine.actions import KIND_EXAMPLES, KINDS
from rsched.engine.composer import build_system_prompt, kickoff_message, state_digest
from rsched.engine.run_context import Budgets, RunContext
from rsched.engine.transcript import Transcript
from rsched.grants import GrantPolicy
from rsched.schema_guard import retry_message

DOC = (Path(__file__).resolve().parents[1] / "docs" / "prompt-anatomy.md").read_text(encoding="utf-8")


def _system_prompt(make_routine, tmp_path, depth=0) -> str:
    d = make_routine(slug=f"anatomy{depth}")
    cfg, _ = load_routine(d)
    run_dir = d / "runs" / "20260712-070000"
    run_dir.mkdir(parents=True)
    server = ServerConfig()
    server.libraries_home = tmp_path / "libraries"
    ctx = RunContext(routine=cfg, server=server, registry=None, run_ts="20260712-070000",
                     run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    ctx.depth = depth
    ctx.grants = GrantPolicy(active=("util-authoring", "memory"),
                             actions=frozenset({"write_util", "memory_read", "memory_write"}))
    return build_system_prompt(ctx, "## Run flow", "task", state_digest(d, [], []),
                               ["hello"])


def test_doc_carries_every_system_prompt_section_header(make_routine, tmp_path):
    # collect headers from both a top-level prompt AND a subrun prompt — the # INSTRUCTION section
    # is subrun-only now (a top-level routine's instruction is the compile seed, not in the prompt)
    prompts = (_system_prompt(make_routine, tmp_path, depth=0),
               _system_prompt(make_routine, tmp_path, depth=1))
    # the composer's own section headers are "# UPPERCASE …" — trait/workflow bodies may
    # carry their own "# …" headings, which are not part of the composition contract
    headers = sorted({ln for p in prompts for ln in p.splitlines()
                      if ln.startswith("# ") and ln.split()[1].isupper()})
    assert len(headers) >= 7          # the composed sections, straight from the composer
    for header in headers:
        assert header in DOC, f"system-prompt section {header!r} missing from docs/prompt-anatomy.md"


def test_doc_pins_the_canonical_engine_strings(make_routine, tmp_path):
    ctx = SimpleNamespace(run_id="job-radar:20260712-070000")
    needles = [
        # kickoff (composer.kickoff_message)
        kickoff_message(ctx).split("Begin run ")[1].split(". ", 1)[1],
        # schema-retry contract line (schema_guard.retry_message)
        retry_message(["x"]).splitlines()[-1],
        # loop.py tails + control.py feeds + history.py pointer
        "wind down DELIBERATELY",
        "read_file the index and the relevant files before relying on memory",
        "USER MESSAGE (injected mid-run)",
        "SUB-WORKFLOW FINISHED",
        "CONTEXT COMPACTED",
        "ENGINE WARNING: this exact action has now run",
        "OBSERVATION (",
        # resume (both flavors) + fabrication guard
        "do NOT restart from step 1",
        "NOT a new run: do not restart the workflow",
        "OBSERVATION (finish REJECTED)",
        # the say contract (composer harness line + ACTION_SCHEMA description)
        "lead with what the last observation taught you",
        # the note channel (ACTION_SCHEMA description + composer contract sentence)
        "worth keeping beyond this context window",
    ]
    for needle in needles:
        assert needle in DOC, f"engine string {needle!r} missing from docs/prompt-anatomy.md"


def test_doc_pins_the_deliberation_levels():
    """The four say-contract levels are documented with their distinctive cores — change
    engine/deliberation.py wording and this fails until the doc follows."""
    from rsched.config import DELIBERATION_LEVELS

    for level in DELIBERATION_LEVELS:
        assert level in DOC, f"deliberation level {level!r} missing from the doc"
    for core in ("ONE terse clause", "beyond this run", "state/notes.md"):
        assert core in DOC, f"deliberation contract core {core!r} missing from the doc"


def test_doc_names_every_action_kind_and_the_finish_example_matches():
    for kind in KINDS:
        assert kind in DOC, f"action kind {kind!r} missing from docs/prompt-anatomy.md"
    # the finish guidance shown in the doc must track the example's altitude
    assert KIND_EXAMPLES["finish"]["summary"].strip("<>") == "detailed 8-20 line result summary"
    assert "8-20 line" in DOC
