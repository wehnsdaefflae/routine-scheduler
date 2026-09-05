"""docs/prompt-anatomy.md is contract documentation for the Help tab: it must track the
prompt surface. This pins the load-bearing engine strings — change composer/loop/schema
wording and this fails until the doc is revised to match."""

from pathlib import Path
from types import SimpleNamespace

from rsched.config import ServerConfig, load_routine
from rsched.engine.actions import KIND_EXAMPLES
from rsched.engine.actionschema import KINDS
from rsched.engine.budgets_config import Budgets
from rsched.engine.composer import build_system_prompt, kickoff_message, state_digest
from rsched.engine.run_context import RunContext
from rsched.engine.transcript import Transcript
from rsched.grantpolicy import GrantPolicy
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
        "converge DELIBERATELY",
        "OBSERVATION (budget spent)",   # the reserved finish turn (loop._reserve_finish)
        "read_file the index and the relevant files before relying on memory",
        "USER MESSAGE (injected mid-run)",
        "CHILD RUN FINISHED",
        "CONTEXT COMPACTED",
        "ENGINE WARNING: this exact action has now run",
        "OBSERVATION (",
        # resume (both flavors) + fabrication guard
        "do NOT restart from step 1",
        "NOT a new run: do not restart the workflow",
        "OBSERVATION (finish REJECTED)",
        # the finish-window race (R108, loop.run + _finish_run): a message landing as the
        # model finishes defers the finish — or, on the spent reserved turn, is surfaced
        # as still queued in the summary
        "OBSERVATION (finish deferred)",
        "it stays queued and opens the next run/reply",
        # write_util doc-standard rejections carry their own head, never the selftest one
        # (R93, observations.format_observation)
        "docstring HEADER violations",
        # the terminal acknowledgment (kindsurface report bullet + ACTION_SCHEMA `closes`):
        # a reply that completes an exchange ends the thread settled instead of ratcheting
        "sets `closes: true` so the thread ends settled",
        "it settles its target AND is itself born settled",
        # the say contract (composer harness line + ACTION_SCHEMA description)
        "lead with what the last observation taught you",
        # the note channel (ACTION_SCHEMA description + composer contract sentence)
        "worth keeping beyond this context window",
        # the finish-summary rendering contract (composer finish gloss + ACTION_SCHEMA
        # summary description) — md.js renders these on block surfaces, so the model is
        # told tables/quotes are worth emitting
        "pipe tables and > blockquotes",
        "pipe tables, > quotes",
        # the util-output spill pointer (outputs.pointer_line) + its digest section
        # (outputs.digest) — the only route to output the observation could not carry
        "instead of re-running the util",
        "rather than re-running the util",
        # the anti-batching override (composer harness paragraph, F180): the CLI harness
        # advertises multi-tool batching; the engine executes at most one action per reply
        "ONE tool call per reply",
        # access requests (the four-state grant model): the request field's schema
        # description, the denial routing + tombstone wording (grants.request_route),
        # the decided observation, the once-grant CAPABILITIES line, and the declined
        # catalog badge — change any of them and the doc must follow
        "a typed ACCESS REQUEST, one grant-entity id",
        "The user decides: allow/deny, once or forever.",
        "PERMANENTLY declined",
        "do not re-request it now",
        "OBSERVATION (ask_user — access request decided)",
        "Granted for THIS RUN only",
        "[reserved — declined by the user]",
        # allow-once (D65): the decision phrase, the consuming observation's engine line,
        # and the CAPABILITIES annotation for a boot-seeded once-grant
        "allowed for ONE action only",
        "ONCE-GRANT SPENT",
        "(one action only)",
        # the group shared store (D67): a grouped run's harness contract names the
        # injected root and its collision contract
        "Group shared store (read+write",
        # F334/D98: the stopping-conditions block renders the STRUCTURE — the joiner, each
        # group's connective, and the satisfied announcement. A run that cannot see two
        # conditions are an OR treats them as an AND. The two SCOPES render apart, and only
        # the GOAL one announces that the routine itself is over.
        "STOPPING CONDITIONS",
        "FINAL GOAL",
        "EVERY final-goal condition is met",
        "ANY of:",
        # F337: the one wording a live run gets for a config change — naming the fields that
        # WAIT is as load-bearing as naming the ones that land
        "IN EFFECT NOW, from this turn on",
        "Saved, but it takes effect at your NEXT RUN",
        # F335: the light channel between teammates, named beside the store root it lives in
        "NOTES FROM YOUR GROUP",
        # Rule ASSISTS: the one shape a curated rule takes when its moment arrives, at all
        # three moments. The route back to the full rule is part of the wording — a surfaced
        # line is deliberately terse, and terseness is only honest if the rest is reachable.
        "[RULE ",
        "the full rule: read_rule name=",
        "a general rule you practise applies to how this run ends",
        # The consequence-reminder layer: the standing instruction (harness), the ONE
        # observation that is not a dispatch result (observations), the anti-livelock rule
        # that makes re-emitting the held action the confirmation, the engine note the ops
        # ride back on, and the label gloss + its nudge (reminders.LABEL_HELP, remind).
        # A hold the model cannot act on precisely is a turn spent for nothing, so every
        # one of these is load-bearing prose.
        "An action can have an effect you did not intend",
        "ACTION HELD — it did NOT run.",
        "one hold per action string per run",
        "[REMINDERS: ",
        "The labels: could_not",
        "fired and is STILL unlabelled",
        # NOTE: the F292 two-phase group fire ("GROUP FIRE PHASE: ingest/outbound") was pinned
        # here until 2026-08-27. D90 retired the machinery and the engine stopped emitting those
        # strings, but the doc kept describing them and this guard kept passing — it only checks
        # doc ⊇ engine, so prose that outlives its feature is invisible to it. Removed with the
        # doc text. A needle here must name a string the engine ACTUALLY emits today.
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
