"""System-prompt assembly: harness contract, state digest, kickoff — composed ONCE at
run start; the messages array then grows turn by turn (prompt-size management lives in
window.py and compaction.py). The harness contract itself is written in harness.py, the
CAPABILITIES section in capabilities.py; observation rendering in observations.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import answerlines, patchreceipt
from ..runledger import digest_tail
from . import digesttail, notemeta, notes, outputs, schemaindex
from .actionschema import example_action
from .capabilities import capabilities_digest
from .harness import harness_contract
from .kindsurface import effective_kinds, schema_for_kinds
from .run_context import RunContext
from .runkind import is_conversation

PLAN_MAX_LINES = 60

#: How many files a directory listing in the digest may name. Every growing part of the
#: digest is capped, since it is re-read on EVERY turn; 40 orients rather than inventories —
#: a run that needs the rest lists the directory itself.
DIR_LIST_MAX = 40


def _dir_listing(directory: Path) -> str:
    """`name (123B)` for the files in `directory`, MOST RECENTLY WRITTEN FIRST and capped.

    Recency is the useful axis for a run reading its own workspace, and the one that survives
    truncation. Empty string when the directory holds no files.
    """
    files = [p for p in directory.iterdir() if p.is_file()]
    try:
        files.sort(key=lambda p: (-p.stat().st_mtime, p.name))
    except OSError:                      # a file vanishing mid-scan is not worth a failure
        files.sort(key=lambda p: p.name)
    shown = [f"{p.name} ({p.stat().st_size}B)" for p in files[:DIR_LIST_MAX]
             if p.exists()]
    if not shown:
        return ""
    more = len(files) - len(shown)
    return ", ".join(shown) + (f" … and {more} more file(s) not listed" if more > 0 else "")


def _plan_text(routine_dir: Path) -> str:
    """state/plan.md, trimmed to PLAN_MAX_LINES. The plan is a working skeleton, not a
    document — a longer one belongs in stages/<name>.md, read on demand.
    """
    plan = routine_dir / "state" / "plan.md"
    if not plan.is_file():
        return ""
    lines = plan.read_text(encoding="utf-8").strip().splitlines()
    if len(lines) <= PLAN_MAX_LINES:
        return "\n".join(lines)
    return "\n".join(lines[:PLAN_MAX_LINES]) + (
        f"\n[... {len(lines) - PLAN_MAX_LINES} more lines — read_file state/plan.md for the "
        "rest, and TRIM it: a plan this long belongs in stages/<name>.md]")


def state_digest(routine_dir: Path, deferred_qa: list[dict], open_qs: list[dict], *,  # noqa: PLR0913 — the digest's ONE builder: every parameter past the first three is keyword-only and names one documented SECTION input (the same reason inbox_questions.file_question carries this). D200's `recipe_unlocked` is the 11th; grouping them into a context object would touch every caller of a function the whole engine composes through, for no reader's benefit
                 routines_home: Path | None = None, slug: str = "",
                 held_rules: list[str] | None = None,
                 store_notes: list[dict] | None = None, goals: list[dict] | None = None,
                 conversation: bool = False, jobs_section: str = "",
                 recipe_unlocked: bool = False, recipe_ask: bool = True) -> str:
    from ..paths import read_json

    parts: list[str] = []
    # The user's ⚑ flags on items THIS routine owns — first, so they are read before planning.
    # Optional: a digest without routines_home context is simply unflagged.
    if routines_home is not None and slug:
        from ..priorities import digest_section
        if prio := digest_section(routines_home, slug):
            parts.append(prio)
    # Shared-store notes for this routine — drained by boot beforehand, so this stays pure and
    # a note is delivered exactly once.
    from ..sharedstores import digest_section as notes_section
    if notes_block := notes_section(store_notes or []):
        parts.append(notes_block)
    # The jobs this run owes (engine/jobops.py), before the phase ("" for most runs).
    parts.append(jobs_section)
    phase = read_json(routine_dir / "state" / "phase.json")
    parts.append(f"Current phase: {json.dumps(phase, ensure_ascii=False)}" if phase
                 else "Current phase: (none recorded — likely the first run)")
    # The WORKING PLAN: the run's own living decomposition — a conversation's spine, since it
    # has no compiled recipe. Written by the run; the engine only carries it forward, capped.
    if plan := _plan_text(routine_dir):
        parts.append("WORKING PLAN (state/plan.md — YOUR living decomposition of this job; the "
                     "step marked in progress is where you are. Revise it as the work teaches "
                     "you: tick off what is done, re-order, add what you discovered, drop what "
                     "turned out unnecessary. Delete the file once the job is finished):\n"
                     + plan)
    # Where the routine stands against its FINISH LINE, the run's GOALS, what its finish owes in
    # `accounting`, and what the last run left unmet — said once, here, at boot
    # (engine/finish_digest.py).
    from . import finish_digest
    if finish_sec := finish_digest.digest_section(routine_dir, goals=goals or [],
                                                  conversation=conversation,
                                                  recipe_unlocked=recipe_unlocked,
                                                  recipe_ask=recipe_ask):
        parts.append(finish_sec)
    state_dir = routine_dir / "state"
    if state_dir.is_dir():
        parts.append("state/ (newest first): " + (_dir_listing(state_dir) or "(empty)"))
    # captured notes reach the next run without a read; the full file stays on-demand
    if noted := notes.tail(routine_dir):
        parts.append("Recent notes (state/notes.md tail — findings captured via the note "
                     "field; read_file the full file for older ones):\n" + noted)
    background = read_json(routine_dir / "state" / "background.json")
    if isinstance(background, list) and background:
        blines = "\n".join(
            f"- [{t.get('state', '?')}] {t.get('label', '?')} (id {t.get('taskid', '?')})"
            + (" — result already delivered" if t.get("delivered") else " — still running")
            for t in background)
        parts.append("Background tasks you launched (detached; each reports its result back HERE "
                     "as a message when it finishes — relay any newly-finished result to the user, "
                     "and answer 'how's it going?' from this list):\n" + blines)
    stages_dir = routine_dir / "stages"
    if stages_dir.is_dir():
        names = [p.name for p in sorted(stages_dir.iterdir()) if p.is_file() and p.suffix == ".md"]
        if names:
            parts.append("stages/ stage modules (read the relevant one on demand with read_file): "
                         + ", ".join(names))
    # Deliverables so far, so a reply extends what it already made instead of rebuilding it.
    art_dir = routine_dir / "artifacts"
    if art_dir.is_dir() and (arts := _dir_listing(art_dir)):
        parts.append("artifacts/ delivered so far, newest first (the user sees these rendered in "
                     "the side panel; re-writing a filename UPDATES that artifact in place — "
                     "extend what is there instead of making report-2.md): " + arts)
    if held_rules:
        parts.append("GENERAL RULES you practise — read one with read_rule when its moment "
                     "comes and apply the principle to your own case:\n"
                     + "\n".join(f"- {line}" for line in held_rules))
    runs_dir = routine_dir / "runs"
    runs = sorted(runs_dir.glob("*/result.md")) if runs_dir.is_dir() else []
    if runs:
        last = runs[-1]
        # Bounded in characters (engine/digesttail.py): the writer chooses a summary's length.
        # The head is kept; the marker names the file for the rest.
        parts.append(f"Last run result ({last.parent.name}):\n"
                     + digesttail.clip(last.read_text(encoding="utf-8").strip(),
                                       read_with=f"runs/{last.parent.name}/result.md"))
    else:
        parts.append("Last run result: (no previous runs)")
    # the engine's process record (rsched/runledger.py) — less the last run's own entry, whose
    # summary is the result printed just above
    if ledger := digest_tail(routine_dir, skip_run=runs[-1].parent.name if runs else ""):
        parts.append(ledger)
    mem_index = routine_dir / ".memory" / "INDEX.md"
    if mem_index.exists():
        # D268: a note past its `stale_after` is listed under its OWN heading, not among the
        # notes to consult.
        parts.extend(notemeta.digest_blocks(mem_index.read_text(encoding="utf-8")))
    elif (mem_dir := routine_dir / ".memory").is_dir():
        names = [p.name for p in sorted(mem_dir.glob("*.md"))]
        if names:
            parts.append(".memory/ notes (INDEX.md is MISSING — re-save each with "
                         "memory_write to rebuild it): " + ", ".join(names))
    # Spilled output an EARLIER run paid for has no other route into the prompt.
    if spilled := outputs.digest(routine_dir):
        parts.append(spilled)
    # F529: inbox freight THIS leg may not consume (a resumed leg drains only
    # LIVE_MESSAGE_VIAS) — named, never delivered, so the run does not misread it as absent.
    from . import inbox as inbox_mod
    freight = inbox_mod.queued_freight(routine_dir, exclude_vias=inbox_mod.LIVE_MESSAGE_VIAS)
    if freight:
        flines = "\n".join(
            f"- [report {f['report']} from {f['from'] or 'a sibling routine'}] {f['text']}"
            if f.get("report") else
            f"- [{f['via'] or 'queued'} · {f['ts'][:16]}] {f['text']}"
            for f in freight)
        parts.append("QUEUED FOR THIS ROUTINE'S NEXT FRESH RUN (not delivered to this leg; "
                     "read the file in inbox/ before describing any of it as open):\n"
                     + flines)
    if open_qs:
        # A question carrying a `config_patch` names the control that would apply it — a prose
        # answer never does.
        qlines = "\n".join(f"- [{q['qid']}] {q['question']} (asked {q.get('asked', '?')})"
                           + patchreceipt.pending_suffix(q) for q in open_qs)
        parts.append(f"Open deferred questions (still unanswered):\n{qlines}")
    if deferred_qa:
        # Each answer carries its verdicts — patch APPLIED/NOT APPLIED, a decline, a proof —
        # in one order shared with the mid-run door (rsched/answerlines.py).
        alines = "\n".join(f"- Q: {p['question']}\n  A: {p['answer']}"
                           + answerlines.suffixes(p) for p in deferred_qa)
        parts.append(f"ANSWERS received to earlier questions (consume now):\n{alines}")
    return "\n\n".join(p for p in parts if p)


def system_sections(ctx: RunContext, workflow_body: str, instruction: str,
                    digest: str, inbox_msgs: list[str],
                    allowed_kinds: set[str] | None = None,
                    report_msgs: list[str] | None = None) -> list[tuple[str, str]]:
    """The composed system prompt as `(name, text)` sections, in prompt order — what
    `build_system_prompt` joins, and what the run record measures (`prompt_chars`).
    """
    # Utils at name+summary only; rule prose not inlined; the schema PROJECTED onto the kinds
    # this run may emit (kindsurface).
    kinds = effective_kinds(allowed_kinds, ctx.grants)
    projection = schema_for_kinds(kinds, reminders=bool(ctx.grants and ctx.grants.reminders_on))
    sections = [
        ("harness", harness_contract(ctx, kinds)),
        # Where the wire enforces the schema, this is only the compact FIELD INDEX
        # (`engine/schemaindex.py` says when the full dump comes back).
        ("schema", schemaindex.section(projection, kinds, ctx)),
        ("example", "# EXAMPLE of a valid reply\n" + json.dumps(example_action(), indent=1)),
        ("workflow", "# WORKFLOW (the control flow you follow)\n" + workflow_body.strip()),
    ]
    # A routine's task is its recipe, so no instruction section; a SUBRUN's instruction is its
    # brief, and a CONVERSATION's is its first message — both carry it.
    if ctx.depth > 0 or (is_conversation(ctx) and instruction.strip()):
        sections.append(("instruction", "# INSTRUCTION (your assigned task)\n"
                         + instruction.strip()))
    sections.append(("capabilities", "# CAPABILITIES (what this run can actually use)\n"
                     + capabilities_digest(ctx, allowed_kinds)))
    sections.append(("digest", "# STATE DIGEST (fresh at run start)\n" + digest))
    if inbox_msgs:
        joined = "\n\n".join(f"--- message {i + 1} ---\n{m}" for i, m in enumerate(inbox_msgs))
        sections.append(("messages", "# MESSAGES FROM THE USER (consume now)\n" + joined))
    if report_msgs:
        # No standing prose: the `report` contract is in the harness already.
        sections.append(("reports", "# REPORTS ADDRESSED TO YOU (consume now)\n"
                         + "\n\n".join(report_msgs)))
    return sections


def join_sections(sections: list[tuple[str, str]]) -> str:
    return "\n\n".join(text for _name, text in sections)


def build_system_prompt(ctx: RunContext, workflow_body: str, instruction: str,
                        digest: str, inbox_msgs: list[str],
                        allowed_kinds: set[str] | None = None,
                        report_msgs: list[str] | None = None) -> str:
    return join_sections(system_sections(ctx, workflow_body, instruction, digest, inbox_msgs,
                                         allowed_kinds, report_msgs))


def kickoff_message(ctx: RunContext) -> str:
    """The first user message of a fresh run — "start working, here is what a reply is".

    It says what the FIRST TURN is (one step, not a plan) without denying chaining — the last
    instruction before the first action must not contradict the harness contract.
    """
    return (f"Begin run {ctx.run_id}. Nothing has been executed yet — the workflow starts now, "
            "at step 1. Reply with your first actual step as a JSON action object (not a plan, "
            "not a summary, not a finish) — or, if the first few steps are independent and you "
            "already know them, chain them in that one reply.")
