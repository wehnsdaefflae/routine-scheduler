"""Run control plane: the abort switch, the pause gate, mid-run model switches, and the
turn-boundary message feeds (injected user messages, finished sub-workflow announcements).

Everything here runs BETWEEN turns and mutates only the loop's message list / context —
never the model call itself. control.json stays web-owned: the engine only reads it
(pause, switch_model) and reacts at the next turn boundary.
"""

from __future__ import annotations

import time

from ..paths import read_json
from ..schema_guard import validate
from . import executor, inbox
from .actions import ACTION_SCHEMA, validate_action
from .commands import CommandError, parse_command
from .observations import format_observation, truncate

_ABORT = {"flag": False}


def request_abort() -> None:
    _ABORT["flag"] = True


class RunAborted(Exception):  # noqa: N818 — control-flow signal (caught to finish as aborted)
    """Raised at a turn boundary when an abort was requested (signal or control.json);
    the loop catches it to finish the run as `aborted`.
    """


def pause_gate(loop, poll_s: float) -> None:
    """Hold the run while control.json says pause; the waiting time is credited back to
    the wall-clock budget.
    """
    ctx = loop.ctx
    control = ctx.root_run_dir / "control.json"
    obj = read_json(control)
    if not (isinstance(obj, dict) and obj.get("pause")):
        return
    ctx.write_status("paused")
    started = time.monotonic()
    while True:
        if loop._aborted():
            raise RunAborted
        time.sleep(poll_s)
        obj = read_json(control)
        if not (isinstance(obj, dict) and obj.get("pause")):
            break
    ctx.credit_suspended(time.monotonic() - started)
    ctx.write_status("running")


def apply_model_switch(loop) -> None:
    """Turn-boundary: honour a mid-run model switch written to control.json by the web layer.
    Edge-triggered on the signal's `ts` so the engine never has to write control.json (which
    stays web-owned). The switch lands on the NEXT completion, since for_model re-resolves
    ctx.routine.models every turn — the model, its context size, and effort all self-correct.
    """
    ctx = loop.ctx
    obj = read_json(ctx.root_run_dir / "control.json")
    sw = obj.get("switch_model") if isinstance(obj, dict) else None
    if not isinstance(sw, dict) or not sw.get("ts") or sw["ts"] == loop._last_switch_ts:
        return
    loop._last_switch_ts = str(sw["ts"])
    applied = []
    for kind in ("main", "subroutine", "tool_call"):
        name = sw.get(kind)   # a catalog model NAME; roles re-resolve every turn via for_model
        if isinstance(name, str) and name in ctx.server.models:
            ctx.routine.models[kind] = name
            applied.append(f"{kind} → {name}")
    if applied:
        note = "model switched mid-run: " + "; ".join(applied)
        ctx.transcript.event("user_injection", {"text": f"[engine] {note}", "source": "engine"})
        loop.messages.append({"role": "user", "content":
            f"ENGINE NOTE: {note}. Continue the run on the new model."})


def inject_user_message(loop, m: dict) -> None:
    """Append ONE inbox message to the conversation as a visible mid-run injection,
    auto-attaching image/PDF media the main endpoint can show — the single place the
    injected-message shape is built (turn-boundary drain and boot-drain alike). A
    message flagged `command` is not prose for the model: it is a user-authored ACTION
    and executes instead.
    """
    if m.get("command"):
        run_user_command(loop, m)
        return
    ctx = loop.ctx
    ctx.transcript.event("user_injection", {"text": m["text"]})
    msg: dict = {"role": "user", "content": f"USER MESSAGE (injected mid-run):\n{m['text']}"}
    if m.get("attachments") and (media := executor.media_from_paths(ctx, m["attachments"])):
        msg["media"] = media
    loop.messages.append(msg)


def run_user_command(loop, m: dict) -> None:
    """Execute ONE user-authored action (a chat slash command) at the turn boundary —
    the model action's exact path (parse → schema validate → validate_action against the
    same workflow tools ∩ capabilities → executor.dispatch) minus the model, so it costs
    no turn. The observation lands in the transcript (the chat renders it) AND in the
    message list (the assistant sees exactly what the user did); a parse/validation/
    dispatch failure becomes a teaching observation instead of killing the run.
    """
    ctx = loop.ctx
    text = str(m.get("text") or "")
    ctx.transcript.event("user_injection", {"text": text, "command": True})
    try:
        action = parse_command(text)
        problems = (validate(action, ACTION_SCHEMA)
                    or validate_action(action, allowed_kinds=loop.allowed_tools,
                                       grants=loop.grants))
        if problems:
            raise CommandError("; ".join(problems))
        obs = executor.dispatch(action, ctx)
    except CommandError as exc:
        obs = {"kind": "user_command", "error": str(exc)}
    except Exception as exc:  # a failing command must never kill the run
        obs = {"kind": "user_command", "error": f"command failed: {exc}"}
    ctx.transcript.event("observation", {**obs, "user_command": True})
    rendered = (f"COMMAND ERROR: {obs['error']}" if obs.get("kind") == "user_command"
                else format_observation(obs))
    msg: dict = {"role": "user", "content":
                 f"USER COMMAND (the user executed this action directly):\n{text}\n{rendered}"}
    if obs.get("media"):  # a /view_image the model can show natively
        msg["media"] = obs["media"]
    loop.messages.append(msg)
    ctx.write_status()


def drain_injections(loop) -> None:
    """Feed mid-run user messages from the inbox into the conversation (root runs only)."""
    ctx = loop.ctx
    if ctx.depth > 0:
        return
    for m in inbox.drain_messages(ctx.routine.dir, loop.consumed_dir):
        inject_user_message(loop, m)


def announce_finished_subruns(loop) -> None:
    """Turn-boundary notification: children that exited since the last boundary — the
    "child finished" hook. A SEQUENTIAL subtask's completion prompts result-forwarding; a
    PARALLEL subrun's is informational (keeps `SUB-WORKFLOW FINISHED`, pinned in the docs).
    """
    for sub in loop.subruns.take_finished_unannounced():
        summary, _ = truncate(sub.summary, cap=4000)
        if getattr(sub, "mode", "parallel") == "sequential":
            loop.messages.append({"role": "user", "content":
                f"SUBTASK FINISHED — #{sub.n} {sub.label!r} (workflow {sub.workflow}, status "
                f"{sub.status}, {sub.ctx.turn} turns). Fold this result into your next subtask's "
                f"brief, or finish:\n{summary}"})
        else:
            loop.messages.append({"role": "user", "content":
                f"SUB-WORKFLOW FINISHED — #{sub.n} {sub.label!r} (workflow {sub.workflow}, "
                f"status {sub.status}, {sub.ctx.turn} turns):\n{summary}"})
