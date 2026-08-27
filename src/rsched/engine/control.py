"""Run control plane: the abort switch, the pause gate, mid-run model, deliberation and
rule-binding switches, and the turn-boundary message feeds (injected user messages,
finished sub-workflow announcements).

Everything here runs BETWEEN turns and mutates only the loop's message list / context —
never the model call itself. control.json stays web-owned: the engine only reads it
(pause, switch_model, set_deliberation, add_rules) and reacts at the next turn boundary.
"""

from __future__ import annotations

import logging
import time

from .. import reports
from ..config import DELIBERATION_LEVELS
from ..paths import read_json
from ..schema_guard import validate
from . import child, deliberation, executor, fileops, inbox
from .actions import ACTION_SCHEMA, util_rejection_outcome, validate_action
from .commands import CommandError, parse_command
from .observations import format_observation, truncate

log = logging.getLogger("rsched.control")

_ABORT = {"flag": False}


def request_abort() -> None:
    _ABORT["flag"] = True


def _applied_path(loop):
    return loop.ctx.root_run_dir / "control-applied.json"


def load_applied_baselines(loop) -> None:
    """Seed the mid-run-switch edge-triggers from the run's applied ledger. control.json is
    web-owned (the engine never writes it), so a consumed signal can't be cleared there —
    without this ledger every RESUME leg would re-fire the run's stale switch_model /
    set_deliberation / add_rules signals (re-pinning models the user has since changed
    back, and re-injecting the same engine notes every leg).
    """
    applied = read_json(_applied_path(loop))
    if isinstance(applied, dict):
        loop._last_switch_ts = str(applied.get("switch_model") or "")
        loop._last_deliberation_ts = str(applied.get("set_deliberation") or "")
        loop._last_rules_ts = str(applied.get("add_rules") or "")


def _mark_applied(loop, signal: str, ts: str) -> None:
    from ..paths import atomic_write_json

    applied = read_json(_applied_path(loop))
    applied = applied if isinstance(applied, dict) else {}
    applied[signal] = ts
    atomic_write_json(_applied_path(loop), applied)


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
    try:
        while True:
            if loop._aborted():
                raise RunAborted
            time.sleep(poll_s)
            obj = read_json(control)
            if not (isinstance(obj, dict) and obj.get("pause")):
                break
    finally:
        # an abort mid-pause credits the waited time too — paused waiting must never be
        # booked as active wall-clock in the final status
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
    _mark_applied(loop, "switch_model", str(sw["ts"]))
    applied = []
    for kind in ("main", "tool_call", "uncensored"):
        name = sw.get(kind)   # a catalog model NAME; roles re-resolve every turn via for_model
        if isinstance(name, str) and name in ctx.server.models:
            ctx.routine.models[kind] = name
            applied.append(f"{kind} → {name}")
    if applied:
        note = "model switched mid-run: " + "; ".join(applied)
        ctx.transcript.event("user_injection", {"text": f"[engine] {note}", "source": "engine"})
        loop.messages.append({"role": "user", "content":
            f"ENGINE NOTE: {note}. Continue the run on the new model."})


def apply_deliberation_switch(loop) -> None:
    """Turn-boundary: honour a mid-run deliberation switch written to control.json by the
    web layer. Same edge-trigger discipline as apply_model_switch — the engine never
    writes control.json. The composed prompt is immutable (prompt-caching contract), so
    the new say contract reaches the model as an appended engine note instead.
    """
    ctx = loop.ctx
    obj = read_json(ctx.root_run_dir / "control.json")
    sw = obj.get("set_deliberation") if isinstance(obj, dict) else None
    if not isinstance(sw, dict) or not sw.get("ts") or sw["ts"] == loop._last_deliberation_ts:
        return
    loop._last_deliberation_ts = str(sw["ts"])
    _mark_applied(loop, "set_deliberation", str(sw["ts"]))
    level = sw.get("level")
    if level not in DELIBERATION_LEVELS or level == ctx.deliberation:
        return
    note = deliberation.switch_note(ctx.deliberation, level)
    ctx.deliberation = level
    ctx.transcript.event("user_injection", {"text": f"[engine] {note}", "source": "engine"})
    loop.messages.append({"role": "user", "content": f"ENGINE NOTE: {note}"})


def apply_rule_additions(loop) -> None:
    """Turn-boundary: honour general rules the USER bound to a LIVE run from the web layer.

    Same edge-trigger discipline as the model/deliberation switches — the engine never writes
    control.json. Recording the slug in routine.yaml is the web layer's job (rules.py); what
    cannot wait is the prose reaching the model, and the composed prompt is immutable
    (prompt-caching contract), so each added rule arrives as an appended engine note read
    straight from the library. From the next run it is an ordinary standing practice.
    """
    from .. import library_docs

    ctx = loop.ctx
    obj = read_json(ctx.root_run_dir / "control.json")
    sw = obj.get("add_rules") if isinstance(obj, dict) else None
    if not isinstance(sw, dict) or not sw.get("ts") or sw["ts"] == loop._last_rules_ts:
        return
    loop._last_rules_ts = str(sw["ts"])
    _mark_applied(loop, "add_rules", str(sw["ts"]))
    for slug in sw.get("slugs") or []:
        if not isinstance(slug, str) or slug in ctx.consulted_rules:
            continue
        raw = library_docs.read_doc(ctx.server.rules_home, slug)
        if raw is None:
            continue
        ctx.consulted_rules.add(slug)
        text = library_docs.doc_body(raw).strip()
        note = (f"the user bound the general rule {slug!r} to this routine — it applies from "
                f"now on, and is one of your standing practices from the next run:\n\n{text}")
        ctx.transcript.event("user_injection", {"text": f"[engine] {note}", "source": "engine"})
        loop.messages.append({"role": "user", "content": f"ENGINE NOTE: {note}"})


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
    # The event carries the attachment rels so the transcript UI can render the files
    # (thumbnails / links) instead of the bare filename list inside the text block —
    # ALL of them, not just the media the model can view (a csv is still linkable).
    ctx.transcript.event("user_injection", {
        "text": m["text"],
        **({"attachments": m["attachments"]} if m.get("attachments") else {})})
    # A delivered report already carries its own "REPORT <id> from routine <slug>" heading
    # (reports.message_text) — labelling it a USER MESSAGE would name the wrong sender.
    lead = ("REPORT (injected mid-run)" if m.get("report")
            else "USER MESSAGE (injected mid-run)")
    msg: dict = {"role": "user", "content": f"{lead}:\n{m['text']}"}
    if m.get("attachments") and (media := fileops.media_from_paths(ctx, m["attachments"])):
        msg["media"] = media
    loop.messages.append(msg)


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
            # per-util telemetry: user slash commands hit the same gates as model actions
            # and count the same way (a denied call never reaches the executor)
            counted = util_rejection_outcome(action, allowed_kinds=loop.allowed_tools,
                                             grants=loop.grants)
            if counted is not None:
                ctx.count_util(*counted)
            raise CommandError("; ".join(problems))
        obs = executor.dispatch(action, ctx)
    except CommandError as exc:
        obs = {"kind": "user_command", "error": str(exc)}
    except Exception as exc:  # a failing command must never kill the run
        obs = {"kind": "user_command", "error": f"command failed: {exc}"}
    ctx.transcript.event("observation", {**obs, "user_command": True})
    rendered = render_command_result(obs)
    msg: dict = {"role": "user", "content":
                 f"USER COMMAND (the user executed this action directly):\n{text}\n{rendered}"}
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
    resumed = loop.resume and ctx.depth == 0
    pairs = inbox.collect_deferred_answers(ctx.routine.dir, loop.consumed_dir,
                                           own_run_ts=ctx.run_ts if resumed else None)
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
    reports.stamp_delivered(ctx.server.routines_home, drained, run_id=ctx.run_id)


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
    claimed children share the parent's working directory; they never did).
    """
    head, _ = truncate(summary, cap=4000)
    got = ("\nCollected from the child into your artifacts/: "
           + ", ".join(collected) + " — read them from there; the child's own dir is gone "
           "from your reach." if collected else "")
    headline = (f"CHILD RUN FINISHED ({child.mode_noun(mode)}) — #{n} {label!r} "
                f"(pattern {workflow}, status {status}, {turns} turns)")
    if mode == child.SEQUENTIAL:
        return (f"{headline}. Fold this result into your next child run's brief, or "
                f"finish:\n{head}{got}")
    return f"{headline}:\n{head}{got}"


def collect_child_artifacts(sub) -> tuple:
    """Copy a finished child's deliverables into the PARENT's artifacts/, namespaced by the
    child's number, and return the parent-relative paths — the HAND-BACK half of the child-run
    contract (engine/child.py, F338).

    The convention is the one the rest of the system already uses: a child writes what it is
    handing back into its own `artifacts/`, exactly as a detached background task does
    (daemon/detached.py `_copy_artifacts`) and exactly what the Artifacts panel lists. Nothing
    new to declare, no action-schema change — a child that writes nothing hands back only its
    summary, as before.

    Isolation is preserved on purpose: children keep their own dirs (childrun.py), so
    concurrent siblings never race a shared tree; this is the hand-back that isolation was
    missing. Best-effort — a copy failure must never turn a finished child into a failed one.
    """
    import shutil

    src = sub.ctx.routine.dir / "artifacts"
    if not src.is_dir() or not any(src.iterdir()):
        return ()
    parent_dir = sub.parent_dir
    if parent_dir is None:
        return ()          # a child built outside the normal path collects nothing
    rel = child.handback_dirname(sub.n)
    dst = parent_dir / rel
    try:
        shutil.copytree(src, dst, dirs_exist_ok=True)
    except OSError as exc:
        log.warning("subrun %s: could not collect artifacts: %s", sub.n, exc)
        return ()
    return tuple(sorted(f"{rel}/{p.relative_to(dst).as_posix()}"
                        for p in dst.rglob("*") if p.is_file()))


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
