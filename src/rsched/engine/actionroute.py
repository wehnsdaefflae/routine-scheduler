"""Action routing — which handler owns which kind."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from . import (
    admin_handlers,
    askbatch,
    authoring,
    background,
    childfields,
    create_routine,
    detach,
    donothing,
    executor,
    goalops,
    interact,
    jobops,
    manage_lane,
    secretgate,
    thenscript,
)
from .control import RunAborted
from .loopconst import POLL_S

log = logging.getLogger("rsched.engine.actionroute")


def dispatch_action(loop, action: dict, ctx) -> dict:
    """Route ONE validated action to the handler that owns its kind, and turn a handler that
    RAISES into an error observation the run can read and route around.

    Anything without a named handler falls through to `executor.dispatch` (the effect kinds).
    A handler that raises costs the run one error observation, not the run: the traceback goes
    to the log and the transcript's `error` stream. `RunAborted` (control flow) and every
    BaseException pass through.
    """
    try:
        if action.get("background"):
            # A backgrounded call (engine/background.py); `validate_action` already judged the
            # kind deferrable. Every call-time gate the foreground applies runs HERE, on the
            # starting turn (F633) — a gate in one path and not the other is one a flag walks
            # around. A refusal or pending request becomes this turn's observation. No
            # concurrency cap, by decision (D166; see `background.py`).
            gated = _gate_for_background(loop, action)
            if gated is not None:
                return gated
            return background.start(loop, action, ctx)
        return _route(loop, action, ctx)
    except RunAborted:
        raise
    except Exception as exc:
        log.exception("action %s raised in its handler", action.get("kind"))
        message = f"{type(exc).__name__}: {exc}"
        ctx.transcript.event("error", {"where": "dispatch", "kind": action.get("kind"),
                                       "message": message}, turn=ctx.turn)
        return {"kind": action.get("kind"), "engine_error": True,
                "error": f"{message} — a defect in the engine, not in your input. Do not "
                         "repeat the identical action; work around it, and report it if "
                         "the task depends on it."}


#: The call-time secret gate a kind passes before it runs (D39), whichever path runs it: `_route`
#: asks it before the executor, `_gate_for_background` on a deferred call's STARTING turn (F633).
_SECRET_GATES = {
    "util": secretgate.gate_util_secrets,
    "script": secretgate.gate_script_secrets,
}


def _gate_for_background(loop, action: dict) -> dict | None:
    """The gate a backgroundable kind must pass on the STARTING turn, or None.

    D39's call-time secret gate may file a BLOCKING ask, which a thread cannot wait on — so it
    runs here, synchronously. The flag defers only the work, never the decision.
    """
    gate = _SECRET_GATES.get(action["kind"])
    return gate(loop, action, poll_s=POLL_S) if gate else None


#: The kinds whose handler is a STRAIGHT mapping: nothing gates the call and no field chooses
#: between handlers. Each entry looks its handler up when called, exactly as a branch would.
_HANDLERS: dict[str, Callable[[Any, dict, Any], dict]] = {
    "write_util": lambda loop, a, _ctx: authoring.handle_write_util(loop, a, poll_s=POLL_S),
    "remove_util": lambda loop, a, _ctx: authoring.handle_remove_util(loop, a, poll_s=POLL_S),
    "write_rule": lambda loop, a, _ctx: authoring.handle_write_rule(loop, a, poll_s=POLL_S),
    "schedule_run": lambda loop, a, _ctx: admin_handlers.handle_schedule_run(loop, a),
    "report": lambda loop, a, _ctx: admin_handlers.handle_report(loop, a),
    "detach": lambda _loop, a, ctx: detach.handle_detach(ctx, a),
    "create_routine": lambda _loop, a, ctx: create_routine.handle_create_routine(ctx, a),
    "manage_lane": lambda _loop, a, ctx: manage_lane.handle_manage_lane(ctx, a),
    "job": lambda loop, a, _ctx: jobops.handle_job(loop, a),
    "goal": lambda loop, a, _ctx: goalops.handle_goal(loop, a),
    # D186: nothing was due. Dispatches like any kind; grounds no finish (donothing.py).
    "do_nothing": lambda _loop, a, _ctx: donothing.handle(a),
    "children": lambda loop, _a, _ctx: loop.subruns.status_table(),
    "wait": lambda loop, a, _ctx: loop.subruns.wait(a, poll_s=POLL_S, aborted=loop._aborted),
}


def _route(loop, action: dict, ctx) -> dict:  # noqa: PLR0911 — one exit per kept branch
    """The handler that owns `action`'s kind: `_HANDLERS`, then the branches below — each kept
    because its gate or its choice of handler reads more than the kind — then the effect kinds'
    `executor.dispatch`.
    """
    kind = action["kind"]
    if (handler := _HANDLERS.get(kind)) is not None:
        return handler(loop, action, ctx)
    if kind == "ask_user":
        # D212: several `questions` in one action go to the batch handler.
        ask = askbatch.handle if action.get("questions") else interact.handle_ask
        return ask(loop, action, poll_s=POLL_S)
    if (gate := _SECRET_GATES.get(kind)) is not None:
        # D39: per-routine secret exposure is decided at CALL time — the gate asks/refuses/
        # passes; None means the call proceeds on the executor the background thread uses too.
        return gate(loop, action, poll_s=POLL_S) or background._runner(kind)(action, ctx)
    if (script := thenscript.riding(action)) is not None:
        # a write or edit carrying the script that checks it: the change, then that script
        # behind the SAME secret gate as the branch above (engine/thenscript.py)
        return thenscript.run(loop, action, script, ctx)
    # `shell` has no branch on purpose: it declares no secrets, so no call-time gate applies.
    if kind == "child":
        # D241: ONE kind, its scheduling in `mode`; parallel and sequential keep two handlers.
        if childfields.mode_of(action) == "sequential":
            return loop.subruns.subtask(action)
        return loop.subruns.spawn(action)
    if kind == "kill":
        # Two namespaces (D176): `handle` is a background call, `n` a sub-workflow;
        # `validate_action` already refused neither-and-both.
        if str(action.get("handle") or "").strip():
            return background.cancel(loop, str(action["handle"]).strip())
        return loop.subruns.kill(action["n"])
    return executor.dispatch(action, ctx)
