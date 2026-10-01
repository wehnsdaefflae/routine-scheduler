"""What KIND of run this is — a conversation, a root conversation, a detached background
task — told by the home its directory sits in (`paths.directly_under`).

Four modules each carried their own copy of "is this dir directly under that home", two of
them private names other modules imported anyway. One definition, so the engine cannot
disagree with itself about whether a run has a user behind it.
"""

from __future__ import annotations

from pathlib import Path

from ..paths import directly_under
from .run_context import RunContext


def _home(ctx: RunContext, attr: str) -> Path | None:
    """A server home, or None for a stub server that has none — a bare or degraded ctx (a
    test, a tool building a partial context) is simply not that kind of run.
    """
    return getattr(ctx.server, attr, None)


def is_conversation(ctx: RunContext) -> bool:
    """This run's own dir is a conversation: a routine-shaped dir directly under the server's
    conversations_home. A conversation's task lives in instruction.md (the first message),
    unlike a scheduled routine whose task is its self-contained recipe. A child of a
    conversation runs in a dir of its own and is not one.
    """
    return directly_under(ctx.routine.dir, _home(ctx, "conversations_home"))


def is_root_conversation(ctx: RunContext) -> bool:
    """The TOP-LEVEL run of a conversation — the one run with a user in the loop, which is
    what `detach`, `create_routine` and `manage_lane` materialize for, and what the loop's
    base policy and admin leg are built for.
    """
    return ctx.depth == 0 and is_conversation(ctx)


def is_detached_run(ctx: RunContext) -> bool:
    """This run is itself a detached background task (its dir sits under background_home).
    Such a run defers every ask (no user is watching it), so it never parks in waiting_user
    and can't hold a self-update restart in the 'defer' state.
    """
    return directly_under(ctx.routine.dir, _home(ctx, "background_home"))


def lands_in_conversation(ctx: RunContext) -> bool:
    """This run's decision records land in a CONVERSATION — where the Decisions page applies
    an untargeted `config_patch` through `PATCH /api/conversations/…`, not the routine
    endpoint. A child files into its ROOT's dir (`ctx.root_routine_dir`), so the root decides.
    """
    return directly_under(ctx.root_routine_dir, _home(ctx, "conversations_home"))
