"""System-prompt assembly: harness contract, state digest, kickoff — composed ONCE at
run start; the messages array then grows turn by turn (prompt-size management lives in
history.py). The CAPABILITIES section is built in capabilities.py; observation rendering
in observations.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import notes, outputs
from .actionschema import example_action
from .capabilities import capabilities_digest
from .harness import _is_conversation, harness_contract
from .kindsurface import effective_kinds, schema_for_kinds
from .run_context import RunContext

PLAN_MAX_LINES = 60

#: How many files a directory listing in the digest may name. Every OTHER growing part of
#: this digest is capped — the plan at PLAN_MAX_LINES, the notes tail, the LEDGER tail, the
#: `.memory/` note cap — because the digest is re-read on EVERY turn of the run, so an
#: unbounded part is a tax on every turn. These two listings were the exception: weightloss
#: reached 285 state files and spent ~2 400 tokens per turn naming them (measured
#: 2026-09-11, ~9% of that routine's whole prefix). 40 is chosen to orient, not to inventory
#: — a run that needs the rest lists the directory itself.
DIR_LIST_MAX = 40


def _dir_listing(directory: Path) -> str:
    """`name (123B)` for the files in `directory`, MOST RECENTLY WRITTEN FIRST and capped.

    Recency is the useful axis for a run reading its own workspace ("what did I write last
    run"), and it is the one that survives truncation: an alphabetical cut of 285 files hides
    everything after "d", while a recency cut hides only what nothing has touched in a long
    time. Empty string when the directory holds no files.
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


def state_digest(routine_dir: Path, deferred_qa: list[dict], open_qs: list[dict], *,
                 routines_home: Path | None = None, slug: str = "",
                 held_rules: list[str] | None = None) -> str:
    from ..paths import read_json

    parts: list[str] = []
    # The user's ⚑ flags from the Messages page, resolved to the items THIS routine owns —
    # placed first so orient reads the user's "work this first" before any planning.
    # Optional inputs: a subrun/conversation digest (and most unit fixtures) has no
    # routines_home context, and a digest without the section is simply unflagged.
    if routines_home is not None and slug:
        from ..priorities import digest_section
        if prio := digest_section(routines_home, slug):
            parts.append(prio)
        # F335: notes teammates left for this routine. DRAINS — this digest is built once per
        # run, at boot, and a note is delivered exactly once (mirroring how inbox/ drains).
        from ..domainnotes import digest_section as notes_section
        if domain_notes := notes_section(routines_home, slug):
            parts.append(domain_notes)
    phase = read_json(routine_dir / "state" / "phase.json")
    parts.append(f"Current phase: {json.dumps(phase, ensure_ascii=False)}" if phase
                 else "Current phase: (none recorded — likely the first run)")
    # The WORKING PLAN, inlined in full and first: a run's own living decomposition of a job
    # too big for one pass. A scheduled routine gets that spine from its compiled recipe
    # (stages/ + phase.json); a CONVERSATION has no compiled recipe, so without this it
    # re-derived its arc from chat scrollback every reply and finished at the shortest
    # possible bar. Written and revised by the run itself (see the converse pattern) — the
    # engine only carries it forward. Capped: a plan that outgrows this is a stages/ job.
    if plan := _plan_text(routine_dir):
        parts.append("WORKING PLAN (state/plan.md — YOUR living decomposition of this job; the "
                     "step marked in progress is where you are. Revise it as the work teaches "
                     "you: tick off what is done, re-order, add what you discovered, drop what "
                     "turned out unnecessary. Delete the file once the job is finished):\n"
                     + plan)
    # F334/D98: the user's meaning-level bounds ride beside the plan — always visible, so
    # a finish can never claim it did not know them (the finish gate enforces the accounting).
    # the phase drives stage-scoped conditions (the per-stage half of the original order).
    # ONE source for it — `stopping.current_stage`, the recipe's own state/phase.json — shared
    # with the finish gate and the verifier, which used to scope by a different value.
    from . import stopping, stopping_digest
    if stop_sec := stopping_digest.digest_section(
            routine_dir, phase=stopping.current_stage(routine_dir)):
        parts.append(stop_sec)
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
    # Deliverables produced so far. A conversation writes these across many replies and the
    # UI renders the folder in its side panel — but the digest never named them, so a reply
    # re-derived "what have I already made for you" from scrollback, or silently rebuilt it.
    art_dir = routine_dir / "artifacts"
    if art_dir.is_dir() and (arts := _dir_listing(art_dir)):
        parts.append("artifacts/ delivered so far, newest first (the user sees these rendered in "
                     "the side panel; re-writing a filename UPDATES that artifact in place — "
                     "extend what is there instead of making report-2.md): " + arts)
    if held_rules:
        parts.append("General rules binding this routine (read one with read_rule before the "
                     "situation it governs; the workflow's Standing practices section says "
                     "when): " + ", ".join(held_rules))
    runs_dir = routine_dir / "runs"
    runs = sorted(runs_dir.glob("*/result.md")) if runs_dir.is_dir() else []
    if runs:
        last = runs[-1]
        parts.append(f"Last run result ({last.parent.name}):\n"
                     f"{last.read_text(encoding='utf-8').strip()}")
    else:
        parts.append("Last run result: (no previous runs)")
    ledger = routine_dir / "LEDGER.md"
    if ledger.exists():
        lines = ledger.read_text(encoding="utf-8").splitlines()
        tail = "\n".join(lines[-30:])
        more = f" (read LEDGER.md for the full {len(lines)} lines)" if len(lines) > 30 else ""
        parts.append(f"LEDGER tail{more}:\n{tail}")
    mem_index = routine_dir / ".memory" / "INDEX.md"
    if mem_index.exists():
        lines = mem_index.read_text(encoding="utf-8").strip().splitlines()
        shown = "\n".join(lines[:60])
        more = (f"\n[... read .memory/INDEX.md for the full {len(lines)} lines]"
                if len(lines) > 60 else "")
        parts.append(".memory/ index (notes from earlier work — memory_read the relevant "
                     "topic before re-discovering anything):\n" + shown + more)
    elif (mem_dir := routine_dir / ".memory").is_dir():
        names = [p.name for p in sorted(mem_dir.glob("*.md"))]
        if names:
            parts.append(".memory/ notes (INDEX.md is MISSING — re-save each with "
                         "memory_write to rebuild it): " + ", ".join(names))
    # Cross-run reuse of expensive output: the pointer for THIS run's own truncated calls
    # rides each observation, but an output an EARLIER run paid for has no other route into
    # the prompt. Absent (not an empty heading) until something has actually spilled.
    if spilled := outputs.digest(routine_dir):
        parts.append(spilled)
    # F529: what is in the inbox that THIS leg may not consume. A resumed leg drains only
    # LIVE_MESSAGE_VIAS — an audit decision, a sibling's report, a queued routine-page
    # message belongs to the next FRESH run — and the leg used to be told nothing, so a run
    # explained an answered decision as still open three legs after the answer arrived
    # ("did you lose my answer again?!"). Named, never delivered: boot drains BEFORE this is
    # composed, so a fresh run shows nothing here and a resumed leg shows exactly what it
    # may not take.
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
        qlines = "\n".join(f"- [{q['qid']}] {q['question']} (asked {q.get('asked', '?')})"
                           for q in open_qs)
        parts.append(f"Open deferred questions (still unanswered):\n{qlines}")
    if deferred_qa:
        alines = "\n".join(f"- Q: {p['question']}\n  A: {p['answer']}" for p in deferred_qa)
        parts.append(f"ANSWERS received to earlier questions (consume now):\n{alines}")
    return "\n\n".join(parts)


def build_system_prompt(ctx: RunContext, workflow_body: str, instruction: str,
                        digest: str, inbox_msgs: list[str],
                        allowed_kinds: set[str] | None = None,
                        report_msgs: list[str] | None = None) -> str:
    # CAPABILITIES lists utils at name+summary altitude only — exact usage flags stay
    # on-demand via `util name=list`, so the prompt stays lean and never serves stale flags.
    # Rule prose is NOT inlined: the library holds one copy, the workflow names the held
    # slugs and the run reads what it needs (the state digest lists them).
    # The schema is PROJECTED onto the kinds this run may actually emit (kindsurface): a
    # restricted workflow never reads the fields and prose of channels it cannot use.
    kinds = effective_kinds(allowed_kinds, ctx.grants)
    sections = [
        harness_contract(ctx, kinds),
        "# ACTION SCHEMA (your every reply matches this)\n"
        + json.dumps(schema_for_kinds(
            kinds, reminders=bool(ctx.grants and ctx.grants.reminders_on)), indent=1),
        "# EXAMPLE of a valid reply\n" + json.dumps(example_action(), indent=1),
        "# WORKFLOW (the control flow you follow)\n" + workflow_body.strip(),
    ]
    # A top-level ROUTINE's task is its self-contained recipe (main.md + stages/), so no
    # instruction is placed in the prompt — the seed isn't even persisted. A SUBRUN has no
    # decomposed stages — its instruction IS the parent's self-contained brief, so it stays
    # in the prompt. A CONVERSATION runs at depth 0 but its task IS its first message
    # (instruction.md), so it carries the section too (without it the agent never sees its
    # task — only the converse HOW-to pattern).
    if ctx.depth > 0 or (_is_conversation(ctx) and instruction.strip()):
        sections.append("# INSTRUCTION (your assigned task)\n" + instruction.strip())
    sections.append("# CAPABILITIES (what this run can actually use)\n"
                    + capabilities_digest(ctx, allowed_kinds))
    sections.append("# STATE DIGEST (fresh at run start)\n" + digest)
    if inbox_msgs:
        joined = "\n\n".join(f"--- message {i + 1} ---\n{m}" for i, m in enumerate(inbox_msgs))
        sections.append("# MESSAGES FROM THE USER (consume now)\n" + joined)
    if report_msgs:
        # No standing prose here: what to do with a report addressed to you is the `report`
        # action's own contract, already in the harness bullet above.
        sections.append("# REPORTS ADDRESSED TO YOU (consume now)\n"
                        + "\n\n".join(report_msgs))
    return "\n\n".join(sections)


def kickoff_message(ctx: RunContext) -> str:
    return (f"Begin run {ctx.run_id}. Nothing has been executed yet — the workflow starts now, "
            "at step 1. Reply with ONE JSON action object: your first actual step (not a plan, "
            "not a summary, not a finish).")
