"""Compose the initial message list — a fresh kickoff, or a resume that rehydrates the
prior transcript (replayed messages, cumulative usage base, the once-only guards,
orphaned-children notes, the ended-run follow-up note). Called once per leg by loop.run();
the system prompt itself is composed in composer.py.
"""

from __future__ import annotations

from .. import reports, rules, sharedstores
from ..paths import read_json
from . import (
    askback,
    enginenote,
    freightnote,
    gate_digest,
    goals,
    guardscope,
    history,
    inbox,
    jobops,
    mediaops,
    obligations,
    promptfloor,
    promptsegments,
    recipeproposal,
    reviseleg,
    rewind,
    runkind,
    subsalvage,
    unknownoutcome,
)
from .composer import join_sections, kickoff_message, state_digest, system_sections
from .control import inject_user_message, run_user_command
from .transcript import read_events


def _mark_orphans_dead(ctx, orphans: list[dict]) -> None:
    """Record every child that did not survive the interruption, with WHAT IT SPENT.

    The payload shape is `subruns._collect`'s, so every `subrun_end` consumer reads one shape.
    Spend and turns come from the child's OWN transcript (`subsalvage`): an uncollected child
    was never folded into the parent's counter (R3112). What the disk records, never an
    estimate — `{}` means "nothing recorded there".
    """
    for o in orphans:
        child_dir = ctx.run_dir / "sub" / str(o["n"])
        spent = subsalvage.spend_reached(child_dir)
        if spent:
            ctx.add_usage(spent)
        ctx.transcript.event("subrun_end", {
            "n": o["n"], "label": o["label"], "workflow": o.get("workflow", ""),
            "mode": o["mode"], "status": "aborted",
            "summary": "did not survive the run's interruption",
            "turns": subsalvage.turns_reached(child_dir), "usage": spent})


def boot(loop) -> None:
    ctx = loop.ctx
    if loop.resume and ctx.depth == 0:
        # status.json still holds the prior leg's cumulative counters: reseed them BEFORE
        # write_status("starting") overwrites it, so they stay cumulative like usage_base.
        prior = read_json(ctx.run_dir / "status.json")
        if isinstance(prior, dict):
            for attr, val in history.prior_counters(prior).items():
                setattr(ctx, attr, val)
    ctx.write_status("starting")
    resuming = loop.resume and ctx.depth == 0
    # what this run has done so far — read before the digest, which names the goals it left
    events = (read_events(ctx.run_dir / "transcript.jsonl", 0)[0] if resuming else [])
    # D278: an action killed before its observation is answered on the record HERE, before
    # anything reads `events` (engine/unknownoutcome.py; a no-op on a fresh boot).
    unknownoutcome.record(ctx, events)
    if ctx.depth == 0:
        # A RESUMED leg consumes only what is addressed to IT — the user's messages, a
        # detached task's result, answers to its OWN questions (F359). Everything else waits
        # for the next FRESH run, whose boot digest presents it with full context.
        deferred_qa = inbox.collect_deferred_answers(
            ctx.routine.dir, loop.consumed_dir,
            own_run_ts=ctx.run_ts if resuming else None)
        # Access decisions made between runs seed THIS run's grant overlay before the prompt
        # is composed (the live twin is control.drain_injections).
        from .requests import apply_deferred_decisions
        apply_deferred_decisions(loop, deferred_qa)
        open_qs = inbox.open_questions(ctx.routine.dir)
        msgs = inbox.drain_messages(
            ctx.routine.dir, loop.consumed_dir,
            vias=inbox.LIVE_MESSAGE_VIAS if resuming else None)
        ctx.reports_open += reports.stamp_delivered(
            ctx.server.routines_home, msgs, run_id=ctx.run_id)
        # what is STILL queued is what the digest will name: the boundary clock counts it said
        freightnote.seed(loop)
        # shared-store notes for this routine — read and DELETED: delivered exactly once
        store_notes = sharedstores.drain(ctx.server.routines_home, ctx.routine.slug,
                                         list(ctx.routine.fs_write_roots))
        # the job ledger (engine/jobops.py), read before the digest that names it
        jobs_section = jobops.boot(ctx)
        # GOALS: carried obligations + the brief on a fresh routine run; the transcript's
        # last word on a resumed one (a conversation's outlive every reply)
        ctx.goals = goals.replay(events, ctx.brief, carried=(
            [] if runkind.is_conversation(ctx) else obligations.carried(ctx.routine.dir)))
        # an answer given between runs is the person SPEAKING to this run: an `answer` event,
        # so a goal may quote it
        for qa in deferred_qa:
            ctx.transcript.event("answer", {"qid": qa.get("qid", ""), "text": qa["answer"],
                                            "source": "deferred", "question": qa["question"]})
        digest = state_digest(ctx.routine.dir, deferred_qa, open_qs,
                              routines_home=ctx.server.routines_home,
                              slug=ctx.routine.slug,
                              held_rules=rules.when_lines(ctx.server.rules_home,
                                                          list(ctx.routine.rules)),
                              store_notes=store_notes, goals=ctx.goals,
                              conversation=runkind.is_conversation(ctx),
                              jobs_section=jobs_section,
                              # D200: which recipe-revision ask (make the edit / propose it)
                              recipe_unlocked=bool(
                                  getattr(ctx.grants, "recipe_unlocked", False)),
                              # ...and whether at all — the predicate the card seam reads too
                              recipe_ask=recipeproposal.asks_for_revisions(ctx)) \
            + gate_digest.block(ctx)   # the checks for the NEXT fire (rsched/gatecompose.py)
    else:
        msgs = []
        digest = "(subrun — no routine state digest; everything you need is in the instruction)"
    # queued slash commands EXECUTE at boot (below); only prose becomes MESSAGES sections,
    # and an addressed report gets its own section rather than reading as the user speaking
    prose =[] if resuming else [m for m in msgs if not m.get("command")]
    sections = system_sections(ctx, loop.workflow_body, loop.instruction, digest,
                               [m["text"] for m in prose if not m.get("report")],
                               allowed_kinds=loop.allowed_tools,
                               report_msgs=[m["text"] for m in prose if m.get("report")])
    # what each always-on section weighs (chars, and window units — D270), plus its text once
    # per leg beside the transcript
    ctx.prompt_chars, ctx.prompt_segments = promptsegments.note_sections(ctx, sections)
    system = join_sections(sections)
    if resuming:
        replayed, last_turn, records = history.replay_messages(events)
        loop.messages = [{"role": "system", "content": system}, *replayed]
        loop.turn_records = records
        ctx.turn = last_turn
        # the live phase: the last phased action's stamp
        ctx.phase = next((str(e["phase"]) for e in reversed(events) if e.get("phase")), "")
        # write_file's grounding set — a file an earlier leg read stays overwritable
        history.rehydrate_seen_paths(ctx, events)
        ctx.budget_base_turn = last_turn        # a fresh budget window from the resume point
        # reporting stays cumulative even though the budget window is fresh
        ctx.usage_base = history.prior_usage(events)
        # replayed observations ground the fabrication guard, minus held actions and
        # `do_nothing` (D186) — the same predicate the live loop asks
        # (donothing.grounds_a_finish) — plus the KINDS behind that count for the claim guard
        from .donothing import grounding_from

        loop.executed_actions, loop.executed_kinds = grounding_from(events)
        # …and the once-only guards, spent for the rest of the guard scope
        guardscope.rebuild(loop, events)
        # …and open ask-back records, so a re-submission in this leg supersedes them
        askback.rebuild_dialogs(loop, events)
        # Children RUNNING at the interruption are dead: mark each aborted (an honest tree, no
        # re-detection) and tell the model, or it would `wait` forever for them.
        orphans = history.orphaned_children(events)
        _mark_orphans_dead(ctx, orphans)
        if orphans:
            # names each child's work still on disk under sub/<n>/ (F778)
            enginenote.append(loop, subsalvage.resume_note(ctx.run_dir, orphans),
                              replay=False)
        fin = next((e for e in reversed(events) if e.get("type") == "finish"), None)
        fin_payload = (fin.get("payload") or {}) if fin else {}
        # an authored finish means the model handed the speaker turn back to the user — a
        # command-only resume then keeps the turn with the user (loop.run's gate)
        loop.leg_after_authored = bool(fin_payload.get("authored"))
        if reviseleg.active(loop):   # an approved recipe-revision card: an edit, not a run
            enginenote.append(loop, reviseleg.boot(loop, fin_payload), replay=False)
        elif fin_payload.get("authored"):
            # the model itself concluded this run: a follow-up, not crash recovery
            status = fin_payload.get("status", "?")
            # replay=False: `boot` re-authors the note every leg; replaying would stack copies
            enginenote.append(loop,
                f"this run already ENDED (status {status}) — the conversation "
                "continues in place; the user's message follows. This is a follow-up, "
                "NOT a new run: do not restart the workflow and do not redo work that is "
                "already done. Respond to the user's message — do new work only if it asks "
                "for some — then finish again with an updated summary (the previous result "
                "plus what this follow-up changed). Anything left open waits for the user's "
                "next reply in this same conversation — never hand it to a 'next run'.",
                replay=False)
        else:
            # framing depends on whether anything of the run is above (rewind.py)
            enginenote.append(loop, rewind.resume_framing(replayed), replay=False)
        if loop.util_reminder:   # one-shot, on the resume note (the kickoff's counterpart)
            loop.messages[-1] = {"role": "user",
                                 "content": loop.messages[-1]["content"] + loop.util_reminder}
        _ingest(loop, msgs, resuming=True)   # commands execute; prose injects after the note
    else:
        # Fresh-boot prose rides the MESSAGES section AND is a transcript event (`boot`
        # marker) so the renderer shows it and a resume replays it. The channel rides along:
        # goal grounding must tell an addressed report from the person speaking.
        for m in msgs:
            if not m.get("command"):
                ctx.transcript.event("user_injection", {
                    "text": m["text"], "boot": True,
                    **({"via": str(m["via"])} if m.get("via") else {}),
                    **({"report": True} if m.get("report") else {})})
        kickoff = {"role": "user", "content": kickoff_message(ctx) + loop.util_reminder}
        attach_first_message_media(loop, kickoff)  # conversation: images the user attached
        loop.messages = [{"role": "system", "content": system}, kickoff]
        _ingest(loop, msgs, resuming=False)  # commands execute; prose is already in the prompt
    # Absorb messages that landed WHILE boot ran (rapid slash commands) so this leg handles
    # them all; a prose straggler upgrades the leg to a reply (see loop.run's command-only gate).
    if ctx.depth == 0:
        while extra := inbox.drain_messages(
                ctx.routine.dir, loop.consumed_dir,
                vias=inbox.LIVE_MESSAGE_VIAS if resuming else None):
            _ingest(loop, extra, resuming=True)
        _setup_gap_note(loop)     # fresh boot AND resume: the library may have moved between
        # D223: a prompt already over the model's window gets a HARD COMPACTION that states
        # the loss (engine/promptfloor.py) — never a refusal, never raising.
        promptfloor.check(loop)
    ctx.write_status("running")



def _setup_gap_note(loop) -> None:
    """The fourth evaluation moment: tell the run what its setup is MISSING, before turn one.

    The other three address a person; this one addresses the run, so it does not spend turns
    discovering a gap. Advisory only: a broken library or unreadable store yields no note
    rather than a dead run.
    """
    from ..readmodels.remedies import surface_lines
    from ..readmodels.surface import BOOT_SEVERITIES, routine_surface

    ctx = loop.ctx
    if ctx.depth > 0:                # a child inherits its parent's resources, not its config
        return
    try:
        surface = routine_surface(ctx.server, ctx.routine)
        # the routine's OWN declaration rows (outcome) fail no call and no run can close them
        surface["nodes"] = [n for n in surface["nodes"] if not n["source"].get("recipe")]
        lines = surface_lines(surface, BOOT_SEVERITIES)
    except (OSError, ValueError, AttributeError):
        return
    if not lines:
        return
    body = "\n".join(f"- {ln}" for ln in lines)
    note = ("this routine's setup has gaps its own config cannot close. Plan "
            "around them — do not spend turns discovering them.\n" + body
            + "\nFAIL means the call will be rejected or fail outright; WARN means it will "
              "stop to ask you. Report anything that blocks the task in your finish summary "
              "so the user can grant it.")
    # replay=False: re-authored every leg, so a replayed copy would be a stale duplicate
    enginenote.append(loop, note, replay=False)


def _ingest(loop, msgs: list[dict], *, resuming: bool) -> None:
    """Route each boot-drained message and flag the leg: a slash command EXECUTES (no model
    turn), prose becomes a visible injection (resume) or is already in the prompt (fresh).
    `leg_commands`/`leg_prose` let loop.run tell a command-only wake (execute, stay idle)
    from one that owes the user a reply.
    """
    for m in msgs:
        if m.get("command"):
            run_user_command(loop, m)
            loop.leg_commands = True
        else:
            if resuming:
                inject_user_message(loop, m)
            loop.leg_prose = True


def attach_first_message_media(loop, kickoff: dict) -> None:
    """A conversation records its first message's attachments in state/pending-media.json;
    auto-attach the image/PDF ones the main endpoint can show to the kickoff, then clear
    the file (later replies carry attachments through the inbox instead).
    """
    ctx = loop.ctx
    if ctx.depth > 0:
        return
    pend = ctx.routine.dir / "state" / "pending-media.json"
    data = read_json(pend)
    rels = data.get("attachments") if isinstance(data, dict) else None
    if rels and (media := mediaops.media_from_paths(ctx, [str(r) for r in rels])):
        kickoff["media"] = media
    if pend.exists():
        try:
            pend.unlink()
        except OSError:
            pass
