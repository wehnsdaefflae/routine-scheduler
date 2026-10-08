"""docs/prompt-anatomy.md is contract documentation for the Help tab: it must track the
prompt surface. This pins the load-bearing engine strings — change composer/loop/schema
wording and this fails until the doc is revised to match.

The needle list is held to the source in BOTH directions. `doc ⊇ engine` catches a wording
change the doc has not followed; `engine ⊇ doc` catches the opposite and harder failure —
prose that outlived its feature, which a one-way check reads as a pass. (A `GROUP FIRE PHASE`
paragraph survived the machinery it described for months that way.)
"""

import re
from pathlib import Path
from types import SimpleNamespace

import rsched
from helpers import run_context, server_config
from rsched.engine.actions import KIND_EXAMPLES
from rsched.engine.actionschema import ACTION_SCHEMA, KINDS
from rsched.engine.composer import build_system_prompt, kickoff_message, state_digest
from rsched.grantpolicy import GrantPolicy
from rsched.schema_guard import retry_message

DOC = (Path(__file__).resolve().parents[1] / "docs" / "prompt-anatomy.md").read_text(encoding="utf-8")


def _package_source() -> str:
    """Every `src/rsched` module as ONE searchable string, normalised so a needle spanning a
    wrapped literal still matches: whitespace collapsed, then adjacent string literals joined
    (`"a " "b"` -> `a b`, prefixes included), which is what the reader sees at runtime.
    """
    text = " ".join(" ".join(p.read_text(encoding="utf-8").split())
                    for p in sorted(Path(rsched.__file__).parent.rglob("*.py")))
    return re.sub(r"""['"] ?(?:[fFrRbBuU]{1,2})?['"]""", "", text)


ENGINE_SRC = _package_source()

#: Needles the engine BUILDS rather than spells, so no source scan can find them. Keep this
#: list at zero-plus-a-reason: an entry is an unchecked needle.
COMPOSED_NEEDLES: frozenset[str] = frozenset()


def _system_prompt(make_routine, tmp_path, depth=0) -> str:
    d = make_routine(slug=f"anatomy{depth}")
    ctx = run_context(d, "20260712-070000",
                      server=server_config(libraries_home=tmp_path / "libraries"))
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
        "OBSERVATION (budget spent)",   # the reserved finish turn (loopnudge.reserve_finish)
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
        # the ask-back rung (finishgate): an approval a finish's `remind` op filed was asked
        # back on — a finish that stands has no observation for the operator's words to ride
        "the user replied to the approval this finish asked for WITHOUT deciding",
        # write_util doc-standard rejections carry their own head, never the selftest one
        # (R93, observations.format_observation)
        "docstring HEADER violations",
        # the terminal acknowledgment (kindsurface report bullet + ACTION_SCHEMA `closes`):
        # a reply that completes an exchange ends the thread settled instead of ratcheting
        "sets `closes: true` so the thread ends settled",
        "it settles its target(s) AND is itself born settled",
        # the FOLD (F492) + the open-thread cap (D110): routing that leaves the original
        # behind is a copy, and a cap is only fair beside an operation that can consolidate
        "leaving triage at once and settling when it settles",
        "a new report is REFUSED and the open ids are named",
        "it leaves triage now and settles when this one settles",
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
        # A blocking ask answered with "ask back": the user's own words ARE the observation
        # (obs_admin) — it fell through to the plain deferred line, and the model never saw them
        "the user replied WITHOUT deciding",
        # …on ANY blocking decision, in ONE wording (obs_admin.dialog_reply): the approvals and
        # the secret gate dropped the words and said "approval requested", and a re-submission
        # never replaced the record it answered. The shared promise, the request's way forward
        # and the curated reminder's (the one that rides a note, not an observation head)
        "the re-submission replaces the open record",
        "its question answering them",
        "carry the same `remind` op again",
        # A kill that has not landed yet must not read as a termination (obs_children)
        "still winding down",
        # SHARED STORES: the harness contract of a run whose write roots include a shared store
        # names each store, who else shares it and its collision contract — and the hub line
        "SHARED STORES (read+write roots you share with other routines",
        "the heading your card sits under on the Steward hub",
        # The goal model: the FINISH block the digest says once at boot (finish_digest), the
        # brief that stands in for a recipe's Done when (engine/brief.py), the accounting rung
        # and the claim check (accounting.deferral, verifier.challenge_message), and the rules
        # named with their moments (composer, rules.when_lines)
        "FINISH LINE (the operator's",
        "THIS RUN'S BRIEF",
        "At your finish, the `accounting` field carries",
        "THE LAST RUN LEFT UNMET",
        "your `accounting` is incomplete",
        "a check of your own transcript does not support",
        "GENERAL RULES you practise",
        # F337: the one wording a live run gets for a config change — naming the fields that
        # WAIT is as load-bearing as naming the ones that land
        "IN EFFECT NOW, from this turn on",
        "Saved, but it takes effect at your NEXT RUN",
        # A util, script or shell call the run's abort ended (utils_run.run_jailed): a resumed
        # run replays this observation — the only place it learns the call has no result
        # because the run stopped, not because the command failed
        "was ended by the run's abort after",
        "was not started: the run was aborted",
        # F335: the light channel between routines sharing a store — the digest block the
        # notes arrive in and the write-gate refusal of a note nobody would ever read
        "NOTES FROM ROUTINES YOU SHARE A STORE WITH",
        "a routine reads notes only from the stores among its own read-write roots",
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
        # A write carrying its script (engine/thenscript.py): the standing instruction in the
        # script gloss, the skip a change that did not land reports, and the hold head that
        # says the write did not run either
        "put it ON the change",
        "[then_script NOT run]",
        "which did not run either",
        "one hold per action string per run",
        "[REMINDERS: ",
        "The labels: could_not",
        "fired and is STILL unlabelled",
        # The TASK layer (engine/taskops.py, docs/tasks.md): the working-directory exception,
        # the digest section, the briefing an open task answers with, the finish rung that holds
        # a run to its due tasks, and the carry line a run's summary gains
        "except while a TASK is open",
        "TASKS — this routine's standing work",
        "TASK OPEN —",
        "your WORKING DIRECTORY until you checkpoint this task",
        "GATED PROCESSING — task(s) due this run have no checkpoint yet",
        "Task(s) carried to the next run",
        # A message for a routine that reads nothing is refused WITH where to send it instead
        # (rsched/recipients.py): the report refusal's last sentence and the note gate's words
        "Routines that would read it: ",
        "No routine that would read it is related to this one.",
        "so it would never read this note. Nothing was written.",
        # NOTE: the F292 two-phase fire ("GROUP FIRE PHASE: ingest/outbound") was once pinned
        # here as a needle. D90 retired the machinery and the engine stopped emitting those
        # strings, but the doc kept describing them and this guard kept passing — it only checks
        # doc ⊇ engine, so prose that outlives its feature is invisible to it. Removed with the
        # doc text. A needle here must name a string the engine ACTUALLY emits today.
    ]
    for needle in needles:
        assert needle in DOC, f"engine string {needle!r} missing from docs/prompt-anatomy.md"
        if needle not in COMPOSED_NEEDLES:
            assert needle in ENGINE_SRC, (
                f"{needle!r} is pinned here but no longer emitted anywhere in src/rsched — "
                "drop the needle and the doc prose it guards, or name it in COMPOSED_NEEDLES")


def test_doc_pins_the_deliberation_levels():
    """The four say-contract levels are documented with their distinctive cores — change
    engine/deliberation.py wording and this fails until the doc follows."""
    from rsched.config import DELIBERATION_LEVELS

    for level in DELIBERATION_LEVELS:
        assert level in DOC, f"deliberation level {level!r} missing from the doc"
    for core in ("ONE terse clause", "beyond this run", "state/notes.md"):
        assert core in DOC, f"deliberation contract core {core!r} missing from the doc"


def test_section_5_shows_no_field_the_schema_does_not_define():
    """§5 is a PROJECTION — a routine's own kinds only — so a property the schema defines may
    legitimately be absent and a description may legitimately be shorter. The reverse cannot be
    legitimate: a field shown there that `ACTION_SCHEMA` no longer defines is a field the model
    is never given, in the page that calls itself exactly what the orchestrator sees.
    """
    section5 = DOC[DOC.index("## 5 · Full verbatim example"):]
    shown = set(re.findall(r'^\s{2}"([a-z_]+)": \{', section5, re.MULTILINE))
    assert shown, "the §5 schema dump did not parse — has the example's formatting changed?"
    assert not shown - set(ACTION_SCHEMA["properties"]), (
        "docs/prompt-anatomy.md §5 shows action fields the schema does not define: "
        f"{sorted(shown - set(ACTION_SCHEMA['properties']))}")


def test_doc_names_every_action_kind_and_the_finish_example_matches():
    for kind in KINDS:
        assert kind in DOC, f"action kind {kind!r} missing from docs/prompt-anatomy.md"
    # the finish guidance shown in the doc must track the example's altitude
    assert KIND_EXAMPLES["finish"]["summary"].strip("<>") == "detailed 8-20 line result summary"
    assert "8-20 line" in DOC
