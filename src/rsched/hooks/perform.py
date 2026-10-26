"""PERFORM — run ONE already-authored action at a hook point, without spending a model turn.

The operator, 2026-10-26, asked what a hook may DO at a point and answered it himself:
*"Any action. This includes utils and therefore scripts."* He did not name a sandbox or a
command form — he named the ACTION VOCABULARY, which is the one this engine already has.

So this module is deliberately NOT a new execution path. It is the path a USER's slash command
has taken since `engine/control.run_user_command`: parse/author an action dict, validate it
against the model's own schema, validate it against the run's workflow tools ∩ capabilities,
then hand it to `actionroute.dispatch_action` — at a turn boundary, with no model call. That
function's docstring carries the trap this module inherits and must keep:

    the routing table is the model's, NOT `executor.dispatch` directly: a `/util` went straight
    to the executor and so skipped the D39 secret-exposure gate that stands in front of the
    model's util call, and the executor injects every REQUIRED secret a util declares — a
    secret the user had declined for this routine reached a util the chat ran.

The same sentence with "a hook" in place of "the chat" is why the hook payload is built on this
seam instead of beside it. A hook is authored by a rule, a routine or a harness² feature; it is
not a decision about exposure, so the gate asks, or applies the answer on record.

## What the capability model gives for free

`validate_action(action, allowed_kinds=loop.allowed_tools, grants=loop.grants)` is the model's
own gate, so a hook can never do what the FIRING ROUTINE may not: no kind outside its workflow,
no util it was not granted, no reserved channel. Nothing extra had to be built to bound a hook's
reach, and nothing here may weaken that — a caller that wants a wider reach is asking for a
capability change, which is the operator's, not a hook's.

## What is NOT here, and why

**The VETO.** Performing an action at a point is one thing; letting its exit code REFUSE the
action the run was about to take is another, and it is unruled. `points.HOLDING_POINTS` is
pinned by count precisely so a third way to change control flow is a decision rather than a
side effect — and the operator DECIDED this one the same day: *"No veto ever — a hook performs
and reports; only a hold (prose to the model) can stop an action."*

So this function has no refusal path and must not grow one. Its return value is an OBSERVATION,
never a verdict: a failing action yields an `error` key the caller may surface, and the action
the run was about to take still runs. Stopping an action stays what it has always been — a
`hold`, which TELLS the model and spends a turn on its decision. Hence `action` is allowed only
at points that cannot change control flow (`points.ACTION_POINTS`, derived), and
`HOLDING_POINTS` stays pinned by count: it now records his ruling, not an open question.
"""

from __future__ import annotations

import logging

from ..schema_guard import validate

log = logging.getLogger("rsched.hooks.perform")

#: What a performed action's observation is tagged with in the transcript, so every reader can
#: tell a hook's action from the model's and from the user's. `user_command` is the user's flag
#: on the same seam; this is the hook's.
OBS_FLAG = "hook_action"


def perform(loop, action: dict, *, point: str, source: str) -> dict:
    """Execute ONE authored action at `point`, returning its observation.

    `source` says who authored it (a rule id, a routine slug, a feature name) and rides the
    transcript so a performed action is never anonymous — the question "what ran here, and who
    said so" has to be answerable off the record alone.

    Every failure is absorbed into the observation: a hook layer live in every run must not be
    able to kill one (`dispatch.at`'s rule 2, at the executing end). The caller decides what to
    do with the returned observation; this function does not render and does not deliver.
    """
    from ..engine.actionroute import dispatch_action
    from ..engine.actions import validate_action
    from ..engine.actionschema import ACTION_SCHEMA
    from . import points

    ctx = loop.ctx
    if not points.allows(point, "action"):
        return {"kind": OBS_FLAG, "point": point, "source": source,
                "error": (f"point {point!r} does not carry an `action` payload — "
                          f"the points that do: {list(points.ACTION_POINTS)}")}
    try:
        problems = (validate(action, ACTION_SCHEMA)
                    or validate_action(action, allowed_kinds=loop.allowed_tools,
                                       grants=loop.grants))
        if problems:
            return {"kind": OBS_FLAG, "point": point, "source": source,
                    "error": "; ".join(problems)}
        obs = dispatch_action(loop, action, ctx)
    except Exception as exc:  # a hook must never fail the run it fires in
        log.exception("hook action from %r at %r failed", source, point)
        return {"kind": OBS_FLAG, "point": point, "source": source,
                "error": f"hook action failed: {exc}"}
    ctx.transcript.event("observation", {**obs, OBS_FLAG: True, "point": point,
                                         "source": source})
    # a performed action is a real, observed action — it grounds a later model finish exactly as
    # a slash command does (the fabrication guard rejects a finish only when NOTHING ran)
    loop.executed_actions += 1
    return obs
