"""Action routing — which handler owns which kind."""

from __future__ import annotations

import logging

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


#: The call-time secret gate each kind needs BEFORE it may be backgrounded (F633). Keep this in
#: step with `_route`'s two gated branches.
_BACKGROUND_GATES = {
    "util": secretgate.gate_util_secrets,
    "script": secretgate.gate_script_secrets,
}


def _gate_for_background(loop, action: dict) -> dict | None:
    """The gate a backgroundable kind must pass on the STARTING turn, or None.

    D39's call-time secret gate may file a BLOCKING ask, which a thread cannot wait on — so it
    runs here, synchronously. The flag defers only the work, never the decision.
    """
    gate = _BACKGROUND_GATES.get(action["kind"])
    return gate(loop, action, poll_s=POLL_S) if gate else None


def _route(loop, action: dict, ctx) -> dict:  # noqa: PLR0911 — a flat kind->handler table; one branch
                                    # per kind is the clearest form of which handler owns what.
    if action["kind"] == "ask_user":
        # D212: several `questions` in one action go to the batch handler.
        ask = askbatch.handle if action.get("questions") else interact.handle_ask
        return ask(loop, action, poll_s=POLL_S)
    if action["kind"] == "write_util":
        return authoring.handle_write_util(loop, action, poll_s=POLL_S)
    if action["kind"] == "remove_util":
        return authoring.handle_remove_util(loop, action, poll_s=POLL_S)
    if action["kind"] == "write_rule":
        return authoring.handle_write_rule(loop, action, poll_s=POLL_S)
    if action["kind"] == "util":
        # D39: per-routine secret exposure is decided at CALL time — the gate
        # asks/refuses/passes; None means the call proceeds normally.
        return secretgate.gate_util_secrets(loop, action, poll_s=POLL_S) \
            or executor.dispatch(action, ctx)
    if action["kind"] == "script":
        # the routine's own deterministic helper — same call-time secret gate
        return secretgate.gate_script_secrets(loop, action, poll_s=POLL_S) \
            or executor.do_script(action, ctx)
    if (script := thenscript.riding(action)) is not None:
        # a write or edit carrying the script that checks it: the change, then that script
        # behind the SAME secret gate as the branch above (engine/thenscript.py)
        return thenscript.run(loop, action, script, ctx)
    # `shell` has no branch on purpose: it declares no secrets, so no call-time gate applies.
    if action["kind"] == "schedule_run":
        return admin_handlers.handle_schedule_run(loop, action)
    if action["kind"] == "report":
        return admin_handlers.handle_report(loop, action)
    if action["kind"] == "child":
        # D241: ONE kind, its scheduling in `mode`; parallel and sequential keep two handlers.
        if childfields.mode_of(action) == "sequential":
            return loop.subruns.subtask(action)
        return loop.subruns.spawn(action)
    if action["kind"] == "detach":
        return detach.handle_detach(ctx, action)
    if action["kind"] == "create_routine":
        return create_routine.handle_create_routine(ctx, action)
    if action["kind"] == "manage_lane":
        return manage_lane.handle_manage_lane(ctx, action)
    if action["kind"] == "job":
        return jobops.handle_job(loop, action)
    if action["kind"] == "goal":
        return goalops.handle_goal(loop, action)
    if action["kind"] == "do_nothing":
        # D186: nothing was due. Dispatches like any kind; grounds no finish (donothing.py).
        return donothing.handle(action)
    if action["kind"] == "children":
        return loop.subruns.status_table()
    if action["kind"] == "kill":
        # Two namespaces (D176): `handle` is a background call, `n` a sub-workflow;
        # `validate_action` already refused neither-and-both.
        if str(action.get("handle") or "").strip():
            return background.cancel(loop, str(action["handle"]).strip())
        return loop.subruns.kill(action["n"])
    if action["kind"] == "wait":
        return loop.subruns.wait(action, poll_s=POLL_S, aborted=loop._aborted)
    return executor.dispatch(action, ctx)
