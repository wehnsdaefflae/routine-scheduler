"""Compose the initial message list — a fresh kickoff, or a resume that rehydrates the
prior transcript (replayed messages, cumulative usage base, orphaned-children notes, the
ended-run follow-up note). Called once per run by loop.run(); the system prompt itself is
composed in composer.py.
"""

from __future__ import annotations

from ..paths import read_json, resolve_rel
from . import executor, inbox
from .composer import build_system_prompt, kickoff_message, state_digest
from .control import inject_user_message, run_user_command
from .history import orphaned_children, prior_counters, prior_usage, replay_messages, seen_paths


def boot(loop) -> None:
    ctx = loop.ctx
    if loop.resume and ctx.depth == 0:
        # The run dir is reused across resume legs, so status.json still holds the prior
        # leg's final cumulative telemetry. Reseed it onto the fresh ctx BEFORE the
        # write_status("starting") below overwrites the file — otherwise a finish→reopen
        # resets the util histogram and the integer counters to this leg's own tally
        # (F131/F132), the way usage_base already keeps token spend cumulative.
        prior = read_json(ctx.run_dir / "status.json")
        if isinstance(prior, dict):
            for attr, val in prior_counters(prior).items():
                setattr(ctx, attr, val)
    ctx.write_status("starting")
    if ctx.depth == 0:
        deferred_qa = inbox.collect_deferred_answers(ctx.routine.dir, loop.consumed_dir)
        open_qs = inbox.open_questions(ctx.routine.dir)
        msgs = inbox.drain_messages(ctx.routine.dir, loop.consumed_dir)
        digest = state_digest(ctx.routine.dir, deferred_qa, open_qs)
    else:
        msgs = []
        digest = "(subrun — no routine state digest; everything you need is in the instruction)"
    resuming = loop.resume and ctx.depth == 0
    # slash commands queued while no run was live EXECUTE at boot (below) — only prose
    # messages become the prompt's MESSAGES section
    system = build_system_prompt(ctx, loop.workflow_body, loop.instruction, digest,
                                 [] if resuming
                                 else [m["text"] for m in msgs if not m.get("command")],
                                 allowed_kinds=loop.allowed_tools)
    if resuming:
        from .transcript import read_events
        events, _ = read_events(ctx.run_dir / "transcript.jsonl", 0)
        replayed, last_turn, records = replay_messages(events)
        loop.messages = [{"role": "system", "content": system}, *replayed]
        loop.turn_records = records
        ctx.turn = last_turn
        # rehydrate the live phase: the last phased action's stamp is the stage module
        # the run was in when it left off (the executor stamps stage-module reads)
        ctx.phase = next((str(e["phase"]) for e in reversed(events) if e.get("phase")), "")
        # rehydrate write_file's grounding set — a file an earlier leg read stays
        # overwritable (a root revoked between legs simply drops out of the set)
        roots = [*ctx.routine.fs_read_roots, *ctx.routine.fs_write_roots]
        for rel in seen_paths(events):
            try:
                ctx.seen_paths.add(str(resolve_rel(ctx.routine.dir, rel, roots)))
            except (OSError, PermissionError):
                continue
        ctx.budget_base_turn = last_turn        # a fresh budget window from the resume point
        # reporting stays cumulative even though the budget window is fresh — without
        # this base, status.json shows only the last leg of a resumed run
        ctx.usage_base = prior_usage(events)
        # replayed observations ground the fabrication guard — a continued conversation
        # may legitimately answer and re-finish as its very first action
        loop.executed_actions = sum(1 for e in events if e.get("type") == "observation"
                                    and not (e.get("payload") or {}).get("rejected"))
        # Children that were RUNNING at the interruption are dead (threads don't survive a
        # restart). Mark each aborted in the transcript (so the tree is honest and a re-resume
        # doesn't re-detect it) and tell the model below — otherwise it would `wait` forever
        # for a child that can never finish.
        orphans = orphaned_children(events)
        for o in orphans:
            ctx.transcript.event("subrun_end", {
                "n": o["n"], "label": o["label"], "mode": o["mode"], "status": "aborted",
                "summary": "did not survive the run's interruption",
                "turns": 0, "usage": {}})
        if orphans:
            names = ", ".join(f"#{o['n']} {o['label']!r} ({o['mode']})" for o in orphans)
            loop.messages.append({"role": "user", "content":
                f"ENGINE NOTE: these child tasks were RUNNING when the run was interrupted "
                f"and did NOT survive the restart (results lost): {names}. Re-issue any you "
                "still need."})
        fin = next((e for e in reversed(events) if e.get("type") == "finish"), None)
        fin_payload = (fin.get("payload") or {}) if fin else {}
        # an authored finish means the model handed the speaker turn back to the user — a
        # command-only resume then keeps the turn with the user (loop.run's gate)
        loop.leg_after_authored = bool(fin_payload.get("authored"))
        if fin_payload.get("authored"):
            # the model itself concluded this run (web converse on a finished run):
            # a follow-up conversation, not crash recovery
            status = fin_payload.get("status", "?")
            ctx.transcript.event("user_injection", {
                "text": "the user continued the conversation after the run ended",
                "source": "engine"})
            loop.messages.append({"role": "user", "content":
                f"ENGINE NOTE: this run already ENDED (status {status}) — the user is "
                "continuing the conversation; their message follows. This is a follow-up, "
                "NOT a new run: do not restart the workflow and do not redo work that is "
                "already done. Respond to the user's message — do new work only if it asks "
                "for some — then finish again with an updated summary (the previous result "
                "plus what this follow-up changed)."})
        else:
            ctx.transcript.event("user_injection", {"text": "run resumed after interruption",
                                                    "source": "engine"})
            loop.messages.append({"role": "user", "content":
                "ENGINE NOTE: this run was interrupted (budget/error) and is now RESUMED. The "
                "conversation above is the run so far — continue from the last observation; "
                "do NOT restart from step 1. Re-orient briefly, then proceed."})
        if loop.util_reminder:   # one-shot, on the resume note (the kickoff's counterpart)
            loop.messages[-1] = {"role": "user",
                                 "content": loop.messages[-1]["content"] + loop.util_reminder}
        _ingest(loop, msgs, resuming=True)   # commands execute; prose injects after the note
    else:
        kickoff = {"role": "user", "content": kickoff_message(ctx) + loop.util_reminder}
        attach_first_message_media(loop, kickoff)  # conversation: images the user attached
        loop.messages = [{"role": "system", "content": system}, kickoff]
        _ingest(loop, msgs, resuming=False)  # commands execute; prose is already in the prompt
    # Absorb messages that landed WHILE boot ran (rapid slash commands) so this leg handles
    # them all; a prose straggler upgrades the leg to a reply (see loop.run's command-only gate).
    if ctx.depth == 0:
        while extra := inbox.drain_messages(ctx.routine.dir, loop.consumed_dir):
            _ingest(loop, extra, resuming=True)
    ctx.write_status("running")


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
    if rels and (media := executor.media_from_paths(ctx, [str(r) for r in rels])):
        kickoff["media"] = media
    if pend.exists():
        try:
            pend.unlink()
        except OSError:
            pass
