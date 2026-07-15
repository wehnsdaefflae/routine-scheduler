"""The `detach` action handler — launch a long background task that outlives this reply.

`detach` is the CROSS-REPLY counterpart to `subtask`/`spawn` (within-reply children that die
when the reply's process exits). It does NOT run anything in-process: it drops an intent file in
`background_home/.requests/` and returns immediately. The daemon's DetachedManager picks the
intent up, materializes + fires the task as its own `engine-run` subprocess, and — on completion
— delivers the result back into this conversation (see daemon/detached.py). So the engine side is
deliberately tiny; all lifecycle logic lives in the daemon (the single writer of background_home).

Structural rule: detach is only valid from a ROOT CONVERSATION (depth 0, dir directly under
conversations_home). A scheduled routine has no waiting user to relay the result to, and a
within-reply child (depth > 0) or a detached task itself must not spawn further detaches.
"""

from __future__ import annotations

from ..ids import background_task_id
from ..paths import atomic_write_json
from .run_context import RunContext


def _is_root_conversation(ctx: RunContext) -> bool:
    try:
        conv_home = ctx.server.conversations_home.resolve()
        return ctx.depth == 0 and ctx.routine.dir.resolve().parent == conv_home
    except Exception:  # a bare/degraded ctx (tests, tools) means "not a conversation"
        return False


def is_detached_run(ctx: RunContext) -> bool:
    """True if THIS run is itself a detached background task (its dir sits under
    background_home). Such a run defers every ask (no user is watching it), so it never parks in
    waiting_user and can't hold a self-update restart in the 'defer' state.
    """
    try:
        return ctx.routine.dir.resolve().parent == ctx.server.background_home.resolve()
    except Exception:  # a bare/degraded ctx means "not a detached run"
        return False


def handle_detach(ctx: RunContext, action: dict) -> dict:
    """Write the detached-task intent (or reject when not a root conversation). Returns the
    observation dict the loop records and renders.
    """
    if not _is_root_conversation(ctx):
        return {"kind": "detach", "rejected": True,
                "reason": "detach is only available from a top-level conversation (not a scheduled "
                          "routine, and not a within-reply child). Do the work directly, or use "
                          "subtask/spawn to decompose it inside this reply."}
    workflow = (action.get("workflow") or "").strip() or "general-task"
    label = (action.get("label") or "").strip() or "background task"
    taskid = background_task_id(ctx.routine.slug)
    reqs = ctx.server.background_home / ".requests"
    reqs.mkdir(parents=True, exist_ok=True)
    atomic_write_json(reqs / f"{taskid}.json",
                      {"taskid": taskid, "prompt": (action.get("prompt") or "").strip(),
                       "workflow": workflow, "label": label,
                       "owner": {"slug": ctx.routine.slug, "dir": str(ctx.routine.dir)}})
    return {"kind": "detach", "taskid": taskid, "label": label, "workflow": workflow}
