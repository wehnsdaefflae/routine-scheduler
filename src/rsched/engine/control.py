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
from collections.abc import Callable

from .. import reports
from ..paths import read_json
from ..schema_guard import validate
from . import child, inbox, mediaops, refusal
from .actions import util_rejection_outcome, validate_action
from .actionschema import ACTION_SCHEMA
from .commands import CommandError, parse_command
from .observations import format_observation, truncate

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


def cancelled_for_turn(ctx) -> Callable[[], bool]:
    """The per-call cancel check a jailed command polls (F586, decided as D160-C): control.json's
    `cancel_action` names the TURN whose call the operator stopped, and this returns True only
    while that turn is the one running.

    KEYED BY TURN, and that is the whole safety property. A bare `{"cancel": true}` flag is read
    by whatever call is in flight when the engine next looks — which, for a cancel clicked just
    as a long call finishes, is the NEXT call, one the operator never asked to stop. The turn is
    what the UI's red × already knows (it renders a specific action's row), so the intent
    travels as "stop the call of turn N" and expires by itself.

    It is a POLLED FILE READ, not the abort's mechanism: `request_abort`'s flag is process-wide
    and set by a signal handler, while a cancel arrives from the web with the engine blocked
    inside `run_jailed`'s wait — there is no signal and no turn boundary to read it at. So this
    mirrors `pause_gate` above: `paths.read_json` on the run's own control.json, which is
    web-owned and engine-read. A missing or malformed file reads as "not cancelled" — the file
    is advisory, and a command must never be killed by a parse error. So does a value that is
    not a plain int, BOOLS INCLUDED: `True == 1` in Python, so a bare `{"cancel_action": true}`
    would otherwise stop turn 1's call. And a context with no run dir at all (a test, a CLI
    call) never cancels, the way `run_context._never_aborted` answers for the abort.
    """
    # A BACKGROUND call carries its own stop predicate, keyed by its handle (engine/background:
    # `_cancellable_ctx`). It takes precedence because the turn key cannot work there: a
    # background call outlives the turn that started it, so a check keyed on `ctx.turn` can
    # never fire again once the run moves on — which left `kill handle=…` unable to stop even a
    # `shell` blocked on a ten-minute subprocess. The file channel stays for the foreground.
    if (stop := getattr(ctx, "background_stop", None)) is not None:
        return stop
    run_dir = getattr(ctx, "root_run_dir", None)
    turn = getattr(ctx, "turn", 0)
    if run_dir is None:
        # A context no loop drives (a test, a CLI call, a selftest) has no run dir to read, and
        # the right answer there is `_never_aborted`'s: never cancelled. Raising instead would
        # make the cancel's presence break every caller that never had one.
        return lambda: False
    control = run_dir / "control.json"

    def cancelled() -> bool:
        wanted = read_json(control)
        if not isinstance(wanted, dict):
            return False
        value = wanted.get("cancel_action")
        # `isinstance(True, int)` and `True == 1` are both true in Python, so a BARE FLAG —
        # exactly the `{"cancel_action": true}` shape this design rejects — would otherwise
        # cancel turn 1's call. Found by this step's own test, not by review.
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        return value == turn

    return cancelled


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
                 "content": injected_message(m["text"], report=bool(m.get("report")))}
    if m.get("attachments") and (media := mediaops.media_from_paths(ctx, m["attachments"])):
        msg["media"] = media
    loop.messages.append(msg)


def injected_message(text: str, *, report: bool = False) -> str:
    """One rendering for a mid-run injected message — live and replayed prompts must read
    identically, or a resumed leg's prefix differs from the leg that wrote it and the whole
    conversation is re-written to the provider instead of re-read from its cache.

    A delivered report already carries its own "REPORT <id> from routine <slug>" heading
    (reports.message_text) — labelling it a USER MESSAGE would name the wrong sender.
    """
    lead = "REPORT (injected mid-run)" if report else "USER MESSAGE (injected mid-run)"
    return f"{lead}:\n{text}"


def command_message(text: str, obs: dict) -> str:
    """One rendering for a slash command AND its result — the single message the live path
    appends, and the single message a replay must rebuild from the injection + observation
    pair it was recorded as.
    """
    return ("USER COMMAND (the user executed this action directly):\n"
            f"{text}\n{render_command_result(obs)}")


def render_command_result(obs: dict) -> str:
    """One rendering for a slash command's outcome — live (run_user_command) and replayed
    (history.replay_messages) prompts must read identically.
    """
    if obs.get("kind") == "user_command":
        return f"COMMAND ERROR: {obs.get('error')}"
    return format_observation(obs)


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
            raise CommandError("; ".join(problems))
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
    msg: dict = {"role": "user", "content": command_message(text, obs)}
    if obs.get("media"):  # a /view_image the model can show natively
        msg["media"] = obs["media"]
    loop.messages.append(msg)
    # a command is a real, observed action — it grounds a later model finish (the
    # fabrication guard rejects a finish only when NOTHING has been executed this run)
    loop.executed_actions += 1
    ctx.write_status()


def drain_injections(loop) -> None:
    """Feed mid-run user messages from the inbox into the conversation (root runs only)."""
    ctx = loop.ctx
    if ctx.depth > 0:
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
    for qa in pairs:
        inject_user_message(loop, {"text": f"ANSWER to your deferred question "
                                           f"“{qa['question']}”:\n{qa['answer']}"})
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
