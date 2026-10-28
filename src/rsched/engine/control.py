"""Run control plane: the abort switch, the pause gate, and the turn-boundary message feeds
(injected user messages and slash commands, finished sub-workflow announcements) with the one
renderer each that a resume's replay shares. The control.json SWITCHES — model, deliberation,
rule bindings, a live config change — are `switches.py`.

Everything here runs BETWEEN turns and mutates only the loop's message list / context —
never the model call itself. control.json stays web-owned: the engine only reads it (here, its
`pause`) and reacts at the next turn boundary. Two things are read MID-turn as well, both by a
util, script or shell command in flight: the abort flag, which it asks through
`RunContext.aborted` and ends the whole run with (`utils_run.run_jailed`; the command's own
session keeps every abort signal away from it), and control.json's `cancel_action`, which
`cancelled_for_turn` turns into the per-call cancel — one call stopped, the run carrying on.
"""

from __future__ import annotations

import logging
import time

from .. import reports
from ..paths import read_json
from ..schema_guard import validate
from . import (
    assist,
    cancelcall,
    channels,
    child,
    freightnote,
    hookmirror,
    inbox,
    mediaops,
    refusal,
    runtree,
)
from .actions import validate_action
from .actionschema import ACTION_SCHEMA
from .commands import CommandError, parse_command
from .observations import format_observation, live_observation, truncate
from .shiftedfields import util_rejection_outcome

log = logging.getLogger("rsched.engine.control")

_ABORT = {"flag": False}


def request_abort() -> None:
    """The process-wide abort: what `rsched engine-run`'s SIGTERM handler (the daemon's abort)
    and `run-once`'s SIGINT/SIGTERM handlers call. Read at turn boundaries by every loop in the
    process and, while a jailed command runs, by `utils_run.run_jailed`.
    """
    _ABORT["flag"] = True


class RunAborted(Exception):  # noqa: N818 — control-flow signal (caught to finish as aborted)
    """Raised at a turn boundary when an abort was requested — the process-wide flag
    `request_abort` raises, or a parent's kill of this child — and caught by the loop to
    finish the run as `aborted`.
    """


def pause_gate(loop, poll_s: float) -> None:
    """Hold the run while control.json says pause; the waiting time is credited back to
    the wall-clock budget.
    """
    ctx = loop.ctx
    control = ctx.root_run_dir / "control.json"
    from ..daemon.pause import generation

    def held() -> bool:
        obj = read_json(control)
        return isinstance(obj, dict) and bool(
            obj.get("pause") or (obj.get("scheduling_pause")
                                 and obj["scheduling_pause"] == generation(ctx.server)))

    if not held():
        return
    ctx.write_status("paused")
    started = time.monotonic()
    try:
        while True:
            if loop._aborted():
                raise RunAborted
            time.sleep(poll_s)
            if not held():
                break
    finally:
        # an abort mid-pause credits the waited time too — paused waiting must never be
        # booked as active wall-clock in the final status
        ctx.credit_suspended(time.monotonic() - started)
    ctx.write_status("running")


#: The PER-CALL cancel (F586/D160-C) lives with what it IS — `engine/cancelcall.py`. Everything
#: in this module runs BETWEEN turns and mutates only the loop's message list; a cancel is read
#: MID-turn, by a util, script or shell command in flight. Re-exported under the name
#: `executor`, `background` and the tests already use, so no caller moved (F529: the extraction
#: is what made room here for the turn-boundary freight clock below).
cancelled_for_turn = cancelcall.cancelled_for_turn


def inject_user_message(loop, m: dict) -> None:
    """Append ONE inbox message to the conversation as a visible mid-run injection,
    auto-attaching image/PDF media the main endpoint can show — the single place the
    injected-message shape is built (turn-boundary drain and boot-drain alike). A
    message flagged `command` is not prose for the model: it is a user-authored ACTION
    and executes instead. One carrying a `refusal_flag` is preceded by that flag's
    `refusal` event — a record for the reader; the model reads only the message.
    """
    if m.get("command"):
        run_user_command(loop, m)
        return
    ctx = loop.ctx
    if isinstance(m.get("refusal_flag"), dict):
        # the operator flagged the reply this message produced as a refusal and re-sent it
        # (web/api_refusal_flag): the record goes down first, where the flag happened
        refusal.record_operator_flag(ctx, m["refusal_flag"])
    # The event carries the attachment rels so the transcript UI can render the files
    # (thumbnails / links) instead of the bare filename list inside the text block —
    # ALL of them, not just the media the model can view (a csv is still linkable).
    #
    # It also carries the DELIVERY CHANNEL (`via`, plus the `source` the writer stamped).
    # Without it every injection read as the operator speaking, whatever filed it: an
    # escalation ladder's directive — machine-authored, binding on the run, filed on the
    # `oversight` channel by engine/ladder.py — rendered as a plain "📨 user:" message, so a
    # transcript reader could not tell who had redirected the run. The payload says which
    # channel spoke; the renderer decides how to label it.
    ctx.transcript.event("user_injection", {
        "text": m["text"],
        **({"via": str(m["via"])} if m.get("via") else {}),
        **({"source": str(m["source"])} if m.get("source") else {}),
        **({"report": True} if m.get("report") else {}),
        **({"attachments": m["attachments"]} if m.get("attachments") else {})})
    if inbox.user_authored(str(m.get("via") or "")):
        # `user_replies` is "has the user spoken" — the create_routine confirm gate and the
        # `user-spoke` assist predicate both read it. A report, a background result and a
        # branch hand-back are machines delivering; the CHANNEL is what says so (inbox.VIAS).
        ctx.user_replies += 1
    msg: dict = {"role": "user",
                 "content": injected_message(m["text"], report=bool(m.get("report")),
                                             via=str(m.get("via") or ""))}
    if m.get("attachments") and (media := mediaops.media_from_paths(ctx, m["attachments"])):
        msg["media"] = media
    loop.messages.append(msg)
    # An injection is ONE CONFIGURATION OF A HOOK (D152): trigger `send`, addressee
    # `run:<run_id>`, delivery `turn_boundary`, consumed once. Mirrored AFTER the message is
    # really in the conversation, so the row records a delivery and not an intent.
    hookmirror.injection_delivered(loop, m)


#: The injected-message rendering lives with the channel table it reads (`channels.py`), and
#: is re-exported here because `history.replay_messages` and every test know it by this name.
injected_message = channels.injected_message


def command_message(text: str, obs: dict, ctx=None) -> str:
    """One rendering for a slash command AND its result — the single message the live path
    appends, and the single message a replay must rebuild from the injection + observation
    pair it was recorded as. Only the live path passes `ctx`: a renderer defect is recorded
    once, when it happens, never again on each replay (obs_floor).
    """
    return ("USER COMMAND (the user executed this action directly):\n"
            f"{text}\n{render_command_result(obs, ctx)}")


def render_command_result(obs: dict, ctx=None) -> str:
    """One rendering for a slash command's outcome — live (run_user_command) and replayed
    (history.replay_messages) prompts must read identically.
    """
    if obs.get("kind") == "user_command":
        return f"COMMAND ERROR: {obs.get('error')}"
    return format_observation(obs) if ctx is None else live_observation(obs, ctx)


def run_user_command(loop, m: dict) -> None:
    """Execute ONE user-authored action (a chat slash command) at the turn boundary —
    the model action's exact path (parse → schema validate → validate_action against the
    same workflow tools ∩ capabilities → actionroute.dispatch_action) minus the model, so it
    costs no turn. The observation lands in the transcript (the chat renders it) AND in the
    message list (the assistant sees exactly what the user did); a parse/validation/
    dispatch failure becomes a teaching observation instead of killing the run.

    The routing table is the model's, not `executor.dispatch` directly: a `/util` went
    straight to the executor and so skipped the D39 secret-exposure gate that stands in front
    of the model's util call, and the executor injects every REQUIRED secret a util declares —
    a secret the user had declined for this routine reached a util the chat ran. Typing the
    command is not a decision about exposure; the gate asks it, or applies the one on record.
    """
    ctx = loop.ctx
    text = str(m.get("text") or "")
    ctx.transcript.event("user_injection", {"text": text, "command": True})
    ctx.user_replies += 1       # typing an action is the user speaking, like any message
    try:
        action = parse_command(text)
        problems = (validate(action, ACTION_SCHEMA)
                    or validate_action(action, allowed_kinds=loop.allowed_tools,
                                       grants=loop.grants))
        if problems:
            # per-util telemetry: user slash commands hit the same gates as model actions
            # and count the same way (a denied call never reaches the executor)
            counted = util_rejection_outcome(action, allowed_kinds=loop.allowed_tools,
                                             grants=loop.grants)
            if counted is not None:
                ctx.count_util(*counted)
            from .renamedkinds import hint as renamed_kind_hint
            raise CommandError("; ".join([*problems,
                                          *filter(None, [renamed_kind_hint(action)])]))
        from .actionroute import dispatch_action  # actionroute imports this module
        obs = dispatch_action(loop, action, ctx)
    except CommandError as exc:
        obs = {"kind": "user_command", "error": str(exc)}
    except RunAborted:
        raise           # the exposure gate's ask can be ended by an abort — control flow
    except Exception as exc:  # a failing command must never kill the run
        log.exception("slash command %r failed", text[:80])
        obs = {"kind": "user_command", "error": f"command failed: {exc}"}
    ctx.transcript.event("observation", {**obs, "user_command": True})
    msg: dict = {"role": "user", "content": command_message(text, obs, ctx)}
    if obs.get("media"):  # a /view_image the model can show natively
        msg["media"] = obs["media"]
    loop.messages.append(msg)
    # a command is a real, observed action — it grounds a later model finish (the
    # fabrication guard rejects a finish only when NOTHING has been executed this run)
    loop.executed_actions += 1
    ctx.write_status()


def drain_injections(loop) -> None:
    """Feed mid-run messages from the inbox in. A CHILD drains only its run-tree hooks
    (`runtree.drain_child`); the operator's channels are the root run's, by design.
    """
    ctx = loop.ctx
    if ctx.depth > 0:
        runtree.drain_child(loop)
        return
    # F195: an answer to a DEFERRED question this run filed must reach the run while it
    # is still alive — before this, it sat in the inbox for the NEXT run's digest while
    # the live run finished claiming "awaits your answer" (observed 2026-07-24 with
    # q-20260724-121507-11). Same delivery as any mid-run user message; the pending
    # record is consumed with it.
    # F359 + user order 2026-08-20 (F368): EVERY root run's turn boundary drains only the
    # LIVE set — the user talking to this run (live run view / composer) plus a detached
    # task's result delivery. Queued freight (audit feedback, reports, routine-page
    # messages, trigger texts, other runs' answers) is addressed to the routine's NEXT
    # fresh run and is consumed only by that run's boot — mid-run injection is the live
    # run view's channel, by design. Answers to THIS run's own deferred questions still
    # arrive mid-run (F195, below).
    pairs = inbox.collect_deferred_answers(ctx.routine.dir, loop.consumed_dir,
                                           own_run_ts=ctx.run_ts if loop.resume else None)
    if pairs:
        # R118: when the answer is a typed ACCESS-REQUEST decision, the GRANT must
        # arrive with the prose — seed the run overlay and rebuild the live policy
        # BEFORE injecting the text, so the answer's "usable now" is true from the very
        # next action (the util sandbox and the file actions read ctx.granted_now
        # live). Without this bridge only the words reached the run and e.g. a mid-run
        # fs-write grant stayed EACCES until the next run.
        from .requests import apply_deferred_decisions
        apply_deferred_decisions(loop, pairs)
    # R2778 + R2197: an answer says whether a patch is LIVE and whether it is a REFUSAL, in the
    # same words the state digest uses (rsched/patchreceipt.py, rsched/decline.py) — the mid-run
    # door leads to the harm the boot door did: "Ok", and no way to tell what actually happened.
    from .. import decline, patchreceipt

    for qa in pairs:
        extra = patchreceipt.digest_suffix(qa) + decline.digest_suffix(qa)
        inject_user_message(loop, {"text": f"ANSWER to your deferred question "
                                           f"“{qa['question']}”:\n{qa['answer']}{extra}"})
    drained = inbox.drain_messages(ctx.routine.dir, loop.consumed_dir,
                                   vias=inbox.LIVE_MESSAGE_VIAS)
    for m in drained:
        inject_user_message(loop, m)
    # an INTERVENTION: the person said something while the run worked — a quality signal
    # (engine/runrecord.py), unlike the message that opens a leg (boot) or an answer it asked for
    ctx.interventions += sum(1 for m in drained if not m.get("command")
                             and inbox.user_authored(str(m.get("via") or "")))
    ctx.reports_open += reports.stamp_delivered(
        ctx.server.routines_home, drained, run_id=ctx.run_id)
    # F529, the other half of the exclusion above: what this run may NOT consume is NAMED
    # here, now that the drain has taken everything it may. The boot digest said this once,
    # at boot — and the operator answered three decisions into a run whose digest was five
    # hours old, then asked whether his answers had been lost. Named, never delivered.
    freightnote.at_boundary(loop)


def child_finished_message(*, mode: str, n: int, label: str, workflow: str, status: str,
                           turns: int, summary: str, collected: tuple = ()) -> str:
    """The one wording for a child-exit notification — used live by
    announce_finished_subruns AND by history.replay_messages when it reconstitutes an
    announcement from a `subrun_end` event, so a resumed prompt reads like the live one.

    ONE headline for every mode (F338): a child run finished, and the mode says how it was
    scheduled. The modes used to announce themselves under different nouns, which is how the
    prompt copy drifted apart in the first place. Only the follow-on instruction differs,
    because only that genuinely differs: a sequential child's result feeds the next one.

    `collected` names the child's deliverables that the engine copied up. Without it a parent
    had to know the child's dir, search it and copy files out by hand — a procedure every
    routine reinvented, and one the spawn contract used to describe WRONGLY (R409/R410: it
    claimed children share the parent's working directory; they never did). The shape is
    `child.handback_text`, shared with the branch and background hand-backs, so one hand-back
    reads one way whichever mode produced it.
    """
    head, _ = truncate(summary, cap=4000)
    return child.handback_text(
        headline=(f"CHILD RUN FINISHED ({child.mode_noun(mode)}) — #{n} {label!r} "
                  f"(pattern {workflow}, status {status}, {turns} turns)"),
        summary=head, paths=collected,
        follow_on=("Fold this result into your next child run's brief, or finish."
                   if mode == child.SEQUENTIAL else ""))


def announce_finished_subruns(loop) -> None:
    """Turn-boundary notification: children that exited since the last boundary — the
    "child finished" hook. One `CHILD RUN FINISHED` headline for every mode; a SEQUENTIAL
    child's completion additionally prompts result-forwarding, a PARALLEL one is
    informational (engine/child.py, pinned in the docs).
    """
    for sub in loop.subruns.take_finished_unannounced():
        loop.messages.append({"role": "user", "content": child_finished_message(
            mode=sub.mode, n=sub.n, label=sub.label, workflow=sub.workflow,
            status=sub.status, turns=sub.ctx.turn, summary=sub.summary,
            collected=sub.collected_paths)})
        # …and the `child-exit` hook point (hooks/points.py), fired AFTER the hand-back is in
        # the prompt so a rule's line reads as commentary on something the model can see.
        assist.at_child_exit(loop, {"mode": sub.mode, "n": sub.n, "label": sub.label,
                                    "workflow": sub.workflow, "status": sub.status,
                                    "turns": sub.ctx.turn, "summary": sub.summary})
