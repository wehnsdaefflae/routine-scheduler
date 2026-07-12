"""docs/prompt-anatomy.md is contract documentation for the Help tab: it must track the
prompt surface. This pins the load-bearing engine strings — change composer/loop/schema
wording and this fails until the doc is revised to match."""

from pathlib import Path
from types import SimpleNamespace

from rsched.config import ServerConfig, load_routine
from rsched.engine.actions import KINDS, KIND_EXAMPLES
from rsched.engine.composer import build_system_prompt, kickoff_message, state_digest
from rsched.engine.run_context import Budgets, RunContext
from rsched.engine.transcript import Transcript
from rsched.grants import GrantPolicy
from rsched.schema_guard import retry_message

DOC = (Path(__file__).resolve().parents[1] / "docs" / "prompt-anatomy.md").read_text(encoding="utf-8")


def _system_prompt(make_routine, tmp_path) -> str:
    d = make_routine(slug="anatomy")
    cfg, _ = load_routine(d)
    run_dir = d / "runs" / "20260712-070000"
    run_dir.mkdir(parents=True)
    server = ServerConfig()
    server.libraries_home = tmp_path / "libraries"
    ctx = RunContext(routine=cfg, server=server, registry=None, run_ts="20260712-070000",
                     run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    ctx.grants = GrantPolicy(active=("global-utils",),
                             actions=frozenset({"write_util", "memory_read", "memory_write"}))
    return build_system_prompt(ctx, "## Run flow", "task", state_digest(d, [], []),
                               ["hello"], fragments_text="standards body")


def test_doc_carries_every_system_prompt_section_header(make_routine, tmp_path):
    prompt = _system_prompt(make_routine, tmp_path)
    # the composer's own section headers are "# UPPERCASE …" — fragment/workflow bodies may
    # carry their own "# …" headings, which are not part of the composition contract
    headers = [ln for ln in prompt.splitlines()
               if ln.startswith("# ") and ln.split()[1].isupper()]
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
        # resume + fabrication guard
        "do NOT restart from step 1",
        "OBSERVATION (finish REJECTED)",
    ]
    for needle in needles:
        assert needle in DOC, f"engine string {needle!r} missing from docs/prompt-anatomy.md"


def test_doc_names_every_action_kind_and_the_finish_example_matches():
    for kind in KINDS:
        assert kind in DOC, f"action kind {kind!r} missing from docs/prompt-anatomy.md"
    # the finish guidance shown in the doc must track the example's altitude
    assert KIND_EXAMPLES["finish"]["summary"].strip("<>") in ("detailed 8-20 line result summary",)
    assert "8-20 line" in DOC
