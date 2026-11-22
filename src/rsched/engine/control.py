"""Run control plane: the abort switch, the pause gate, and the turn-boundary message feeds
(injected user messages and slash commands, finished sub-workflow announcements) with the one
renderer each that a resume's replay shares. The control.json SWITCHES — model, deliberation,
rule bindings, a live config change — are `switches.py`.

Everything here runs BETWEEN turns and mutates only the loop's message list / context.
control.json stays web-owned: the engine only reads it. Two things are also read MID-turn by a
command in flight: the abort flag (`RunContext.aborted`, ending the run) and control.json's
`cancel_action` (`cancelled_for_turn`, stopping one call).
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


#: The PER-CALL cancel lives in `engine/cancelcall.py`; re-exported under the name its callers use.
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
        # the operator flagged the reply as a refusal and re-sent it: the record goes down first
        refusal.record_operator_flag(ctx, m["refusal_flag"])
    # The event carries ALL attachment rels (the UI renders them) and the DELIVERY CHANNEL
    # (`via`, `source`), so a transcript reader can tell who spoke — the operator, or e.g. the
    # escalation ladder's `oversight` channel.
    ctx.transcript.event("user_injection", {
        "text": m["text"],
        **({"via": str(m["via"])} if m.get("via") else {}),
        **({"source": str(m["source"])} if m.get("source") else {}),
        **({"report": True} if m.get("report") else {}),
        # a hand-back's facts (which child, which files) — the chat renders them as a card
        **({"handback": m["handback"]} if isinstance(m.get("handback"), dict) else {}),
        **({"attachments": m["attachments"]} if m.get("attachments") else {})})
    if inbox.user_authored(str(m.get("via") or "")):
        # `user_replies` is "has the user spoken"; machine deliveries are told apart by CHANNEL.
        ctx.user_replies += 1
    msg: dict = {"role": "user",
                 "content": injected_message(m["text"], report=bool(m.get("report")),
                                             via=str(m.get("via") or ""))}
    if m.get("attachments") and (media := mediaops.media_from_paths(ctx, m["attachments"])):
        msg["media"] = media
    loop.messages.append(msg)
    # An injection is one configuration of a hook (D152), mirrored AFTER the message is in the
    # conversation, so the row records a delivery and not an intent.
    hookmirror.injection_delivered(loop, m)


#: Lives in `channels.py`; re-exported under the name `history.replay_messages` and tests use.
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
    costs no turn. The observation lands in the transcript AND the message list; a failure
    becomes a teaching observation instead of killing the run.

    It routes through the model's table, not `executor.dispatch`, so the D39 secret-exposure
    gate holds: typing a command is not a decision about exposure.
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
    # An answer to a DEFERRED question THIS run filed reaches it while it is alive. Otherwise
    # the turn boundary drains only the LIVE set (the user talking to this run, a detached
    # task's result); queued freight is for the routine's NEXT run's boot, by design.
    pairs = inbox.collect_deferred_answers(ctx.routine.dir, loop.consumed_dir,
                                           own_run_ts=ctx.run_ts if loop.resume else None)
    if pairs:
        # An ACCESS-REQUEST decision's GRANT is applied BEFORE the text is injected, so its
        # "usable now" is true from the very next action.
        from .requests import apply_deferred_decisions
        apply_deferred_decisions(loop, pairs)
    # The verdicts riding an answer, in one order shared with the boot digest.
    from .. import answerlines

    for qa in pairs:
        extra = answerlines.suffixes(qa)
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
    # What this run may NOT consume is NAMED here (never delivered), so it is not read as lost.
    freightnote.at_boundary(loop)


def child_finished_message(*, mode: str, n: int, label: str, workflow: str, status: str,
                           turns: int, summary: str, collected: tuple = ()) -> str:
    """The one wording for a child-exit notification — used live by
    announce_finished_subruns AND by history.replay_messages when it reconstitutes an
    announcement from a `child_end` event, so a resumed prompt reads like the live one.

    ONE headline for every mode; only the follow-on differs (a sequential child's result feeds
    the next one). `collected` names the deliverables the engine copied up, rendered by
    `child.handback_text`, which every hand-back shares.
    """
    head, _ = truncate(summary, cap=4000)
    return child.handback_text(
        headline=child.finished_headline(mode=mode, n=n, label=label, status=status,
                                         turns=turns, workflow=workflow),
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


def announce_started_subruns(loop) -> None:
    """The `spawn` hook point (hooks/points.py) — `child-exit`'s twin at the other end of a
    child's life, fired once per child at the first boundary after it started.

    No message of its own (the spawn observation is already in the prompt): the fire site
    only. The `brief` is read off the child's loop, which IS the record of what it was asked.
    """
    for sub in loop.subruns.subruns.values():
        if sub.announced_start:
            continue
        sub.announced_start = True
        assist.at_spawn(loop, {"mode": sub.mode, "n": sub.n, "label": sub.label,
                               "workflow": sub.workflow,
                               "brief": getattr(sub.loop, "instruction", "")})
