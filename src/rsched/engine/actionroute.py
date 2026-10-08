"""Action routing — which handler owns which kind."""

from __future__ import annotations

import logging

from . import (
    admin_handlers,
    authoring,
    background,
    create_routine,
    detach,
    executor,
    interact,
    manage_lane,
    secretgate,
    taskops,
)
from .control import RunAborted
from .loopconst import POLL_S

log = logging.getLogger("rsched.engine.actionroute")


def dispatch_action(loop, action: dict, ctx) -> dict:
    """Route ONE validated action to the handler that owns its kind, and turn a handler that
    RAISES into an error observation the run can read and route around.

    Split out of `EngineLoop.run` (F393). `run` is the turn state machine — budgets,
    boundaries, retries, the finish gates; this is the routing table it consults, and
    the two change for entirely different reasons. Anything without a named handler
    falls through to `executor.dispatch`, which is where the effect kinds live.

    A bug in one handler used to propagate out of the loop and end the whole run, with the
    model never told which action broke — the same failure a user's slash command already
    absorbs (`control.run_user_command`). The traceback goes to the log and the transcript's
    `error` stream; the model gets the exception's one line. `RunAborted` is control flow,
    not a failure, and passes through, as does every BaseException (an interpreter exit).
    """
    try:
        if action.get("background"):
            # D118 phase 1: a read or a fetch the run asked not to wait for. It starts in a
            # thread and the turn comes back AT ONCE; the real observation is appended at a
            # later turn boundary (engine/background.py). Which kinds may do this is
            # `actions.BACKGROUNDABLE_KINDS`, enforced by `validate_action` inside the
            # schema-retry cycle — so an action reaching here with the flag has already been
            # judged safe to defer, and this branch does not re-litigate it.
            #
            # F633: it does, however, go through the SAME per-kind routing the foreground
            # goes through. Two of the backgroundable kinds are owned by `_route` and not by
            # `executor.DISPATCH` — `script` has no DISPATCH entry at all (a backgrounded one
            # raised `KeyError: 'script'` every time) and `util`'s D39 call-time secret gate
            # lives in `_route`, so backgrounding one relocated a security decision out of
            # the run's reach. `_gate_for_background` runs that gate HERE, on the starting
            # turn: it can ask the user a blocking question and a background thread has no
            # turn to block on. A refusal or a pending request becomes THIS turn's
            # observation and nothing is backgrounded.
            #
            # D118 phase 4 / D166: the CONCURRENCY CAP is read first, ahead of the secret
            # gate, because the gate can file a BLOCKING question — asking the user to decide
            # a credential exposure for a call that is about to be refused anyway spends his
            # attention on nothing. A refusal here is this turn's observation, in the
            # schema-retry cycle, naming the live handles so the run can decide what to drop.
            capped = background.refuse_at_capacity(loop, action)
            if capped is not None:
                return capped
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


#: The call-time secret gate each kind needs BEFORE it may be backgrounded (F633). Exactly the
#: two kinds `_route` owns for that reason — keep this table and `_route`'s two gated branches
#: in step: a kind gated in one and not the other is a gate a flag can walk around.
_BACKGROUND_GATES = {
    "util": secretgate.gate_util_secrets,
    "script": secretgate.gate_script_secrets,
}


def _gate_for_background(loop, action: dict) -> dict | None:
    """The gate a backgroundable kind must pass on the STARTING turn, or None.

    D39 decided secret exposure at CALL time, deliberately: the question is about this call's
    declarations, and it is answered by the user while the run waits. That makes it impossible
    to run inside the background thread — `gate_*_secrets` files a BLOCKING ask and a thread
    has no turn to block on — so it runs here instead, synchronously, before anything starts.
    The flag then defers only the work, never the decision.
    """
    gate = _BACKGROUND_GATES.get(action["kind"])
    return gate(loop, action, poll_s=POLL_S) if gate else None


def _route(loop, action: dict, ctx) -> dict:  # noqa: PLR0911 — a flat kind->handler table; one branch per action
                                    # kind is the clearest possible form, and collapsing
                                    # it would only hide which handler owns which kind.
    if action["kind"] == "ask_user":
        return interact.handle_ask(loop, action, poll_s=POLL_S)
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
    # `shell` has no branch here on purpose: it declares no secrets, so there is nothing for
    # a call-time exposure gate to ask about, and it falls through to executor.dispatch with
    # the other effect kinds. Its capability gate already ran inside the schema-retry cycle.
    if action["kind"] == "schedule_run":
        return admin_handlers.handle_schedule_run(loop, action)
    if action["kind"] == "report":
        return admin_handlers.handle_report(loop, action)
    if action["kind"] == "spawn":
        return loop.subruns.spawn(action)
    if action["kind"] == "subtask":
        return loop.subruns.subtask(action)
    if action["kind"] == "detach":
        return detach.handle_detach(ctx, action)
    if action["kind"] == "create_routine":
        return create_routine.handle_create_routine(ctx, action)
    if action["kind"] == "manage_lane":
        return manage_lane.handle_manage_lane(ctx, action)
    if action["kind"] == "task":
        return taskops.handle_task(loop, action)
    if action["kind"] == "subruns":
        return loop.subruns.status_table()
    if action["kind"] == "kill":
        return loop.subruns.kill(action["n"])
    if action["kind"] == "wait":
        return loop.subruns.wait(action, poll_s=POLL_S, aborted=loop._aborted)
    return executor.dispatch(action, ctx)
