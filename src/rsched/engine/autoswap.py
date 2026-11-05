"""The ENGINE-SERVED CHEAP POLL — a repeat poll is answered by the cheap model, unasked (F785).

D185 gave a run `role_swap`: it may drop ITSELF to the routine's `tool_call` model and climb
back (`engine/roleswap.py`). Nothing in the fleet used it — 0 of 32 routines even configured a
distinct main/tool_call pair — so the turns F785 measured went on paying main-model prices to
read a one-line answer: *"2,651 turns do nothing but re-ask whether something finished (a release
gate's status, a child's subruns table) … Each one re-sends the entire prompt to the main model."*
The operator's answer to D217 was *"Both — the automatic cheap poll AND a real tool_call model to
swap to"*; this module is the first half. The second is D230: `ServerConfig`'s
`system_tool_call_model`, the instance-wide default an unset `tool_call` resolves through
(`config.base.role_default_model`). The role is therefore read through that function here and not
off `routine.models` alone — otherwise this module would stay the no-op it shipped as for the 31
routines that name no `tool_call`, which is the fleet D217 was answered for. The default SHIPS
EMPTY, so until the operator names a model there this module still serves nothing and says
nothing, rather than claiming a saving it cannot make.

It is the same mechanism `roleswap` and `switches.apply_model_switch` use — assign
`ctx.routine.models["main"]`, which `endpoints.for_model` re-resolves on every turn — driven by
the engine instead of by the run. It hangs off `sidefields.land`, the seam `loop._land` already
calls, so the turn loop gains no line and no new call site.

WHAT IT COSTS A RUN TO BE WRONG decides every threshold here, because the cheap model is cheap
at exactly the thing a stuck run needs:

* **Two consecutive polls are normal.** A run that re-checks a gate once has not entered a loop,
  so `SERVE_FROM` is the SECOND consecutive repeat: the models never change under a single
  re-check.
* **The escape turn is never cheap.** At `wrongturn.WARN_STREAK` identical actions the engine
  already tells the run it is stuck, lifts the provider schema for its next reply
  (`loop._repeat_warning`) and fails the run at `loopnudge.REPEAT_FAIL`. That next reply has to
  re-plan an escape WITHOUT a grammar to lean on — the worst turn in a run to hand to the cheap
  model — so a byte-identical streak that deep restores the main model instead of serving cheap.
* **Whoever else points `main` wins.** The operator's console switch (`switches`) and the run's
  own `role_swap` both write the same slot; this layer only ever undoes a swap IT made, and
  yields outright for the rest of the run the moment an action carries `role_swap`.

MEASURED REACH, so no reader has to guess (686 retained run dirs were F785's basis; this was
measured over the 401 transcripts retained on 2026-11-01, 32,255 landed turns): turns whose
action is poll-class at all — 14,395 (44.6%), which is the shape of F785's 11.5% claim, a count
BY KIND. Turns where a poll class repeats twice in a row, which is what this module detects — 6
(0.019%), of which the escape-turn stand-down below leaves 1 actually served (0.003%). The gap
is structural, not a tuning choice: `REPEAT_FAIL` ends a run at 5 identical actions, so the real
poll loop F785 measured is `poll → read the answer → poll` and its repeats are never ADJACENT.
Worse for the two cases F785 names: when the poll's action never varies (`subruns` is declared
`((), ())` — no fields at all), the class depth and the byte-identical streak are the SAME
number, so `SERVE_FROM` always coincides with `WARN_STREAK` and the escape-turn stand-down fires
instead of the serve. Adjacency detection — the conservative shape that was authorized —
therefore cannot reach them at all; widening it (a poll class seen twice within a window of 4
turns: 22 turns, 0.07%; or dropping adjacency entirely: 1,142 turns, 3.5%) is a decision this
module does not take on its own. `tests/test_auto_cheap_poll.py` pins that empty band.
"""

from __future__ import annotations

import json

from . import enginenote, roleswap
from .wrongturn import WARN_STREAK

#: Kinds whose entire content is "has it finished?", so a repeat is the same question again
#: however the fields read: `subruns` takes no arguments at all, and `wait` names only which
#: child or handle is being waited on.
POLL_KINDS = ("subruns", "wait")

#: Kinds that are poll-class ONLY when the action repeats byte for byte. A `script` or `util`
#: call is work, not a poll — unless the identical call is made again, which is exactly how a
#: release gate's `status` poll looks.
IDENTITY_POLL_KINDS = ("script", "util")

#: The class depth the cheap serve starts at: 1 is the first poll, 2 the first repeat (normal),
#: 3 the SECOND consecutive repeat — the first depth at which a run is demonstrably polling.
SERVE_FROM = 3


def canonical(action: dict) -> str:
    """The action as the engine's own repeat counter sees it (`loopnudge.repeat_streak`):
    everything but `say`, order-insensitive. One reading of "the same action", not two.
    """
    return json.dumps({k: v for k, v in action.items() if k != "say"}, sort_keys=True)


def poll_class(action: dict) -> str | None:
    """The POLL CLASS of an action, or None when it is not a poll at all.

    Two classes, because "the same question again" means two different things: for a kind that
    asks nothing else it is the kind itself; for a kind that does real work it is the identical
    call. Anything else is a change of course and restores the main model.
    """
    kind = str(action.get("kind") or "")
    if kind in POLL_KINDS:
        return kind
    if kind in IDENTITY_POLL_KINDS:
        return f"{kind} {canonical(action)}"
    return None


def _cheap_model(loop) -> str | None:
    """The DISTINCT cheap model this run's turns can be served from, or None when there is none.

    Resolved the way the engine resolves the role (`config.base.role_default_model`): the
    routine's own `tool_call`, else the instance's `system_tool_call_model`. The second half is
    D230, the other half of the operator's "both" answer to D217, and it is what moves this
    module off the no-op it shipped as: 31 of the fleet's 32 routines configure no `tool_call`
    model, and returning None for them was right only because an unset role resolved to
    `system_model` — the model `main` was already on, and not necessarily cheaper. A default FOR
    THE ROLE is the operator's fleet-wide choice of a cheaper model, so it is servable; with no
    default set this still returns None and the mechanism stays silent.

    A cheap model equal to the configured `main` is the same nothing, however it was resolved.
    One rule, held in `roleswap.cheap_model`, because the run-chosen swap and this engine-served
    one must not disagree about whether a cheaper model exists.
    """
    return roleswap.cheap_model(loop.ctx)


def _serve_cheap(loop) -> None:
    """Point `main` at the cheap model for the next completion, and tell the run ONCE."""
    ctx = loop.ctx
    models = ctx.routine.models
    cheap = _cheap_model(loop)
    if cheap is None or models.get("main") == cheap:
        return
    # The same record `roleswap` keeps, and for the same reason: `models['main']` is the thing
    # being mutated, so where to climb back to has to be remembered before the first swap. One
    # attribute shared by both layers, so a run's own `strong` climbs back correctly after an
    # engine-served swap.
    if not hasattr(ctx, "configured_main"):
        ctx.configured_main = models.get("main") or ""
    models["main"] = cheap
    # what THIS layer served, so `_restore` can tell its own swap from someone else's
    loop._autoswap_model = cheap
    loop._autoswap_on = True
    if getattr(loop, "_autoswap_told", False):
        return      # once per run: a note on every poll would be rent on the cheap stretch
    loop._autoswap_told = True
    enginenote.append(
        loop, f"the engine is serving your REPEAT POLLS on `{cheap}` (the tool_call model for "
              f"this routine): the last {SERVE_FROM} turns asked the same question again, and "
              "reading a one-line answer does not need your main model. You asked for nothing "
              "and need "
              "do nothing — your main model is restored the moment you take an action of a "
              "different kind, and the transcript's per-turn model stamp names which model "
              "answered each turn.")


def _restore(loop) -> None:
    """Undo an engine-served swap — and ONLY one this layer made."""
    if not getattr(loop, "_autoswap_on", False):
        return
    models = loop.ctx.routine.models
    strong = getattr(loop.ctx, "configured_main", None) or ""
    loop._autoswap_on = False
    if models.get("main") == strong:
        return
    if models.get("main") != getattr(loop, "_autoswap_model", None):
        # Someone else re-pointed `main` since: the operator's console switch, or a failover.
        # Theirs is the live decision and this layer does not clobber it.
        #
        # Compared against the model this layer actually SERVED, recorded at the swap — not
        # against `models['tool_call']` (since D230 the served model may have come from the
        # instance default, leaving that key empty) and not against a recomputed
        # `_cheap_model` (which answers None once the swap is in place on a routine that
        # configures no `main`, because the cheap model is then what `main` resolves to).
        # Either of those reads refuses every restore and strands the run on the cheap model.
        return
    if not strong:
        # The routine configures no `main` of its own, so the honest inverse of the swap is
        # removing the override: `main` resolves through the instance default again, which is
        # exactly where it was before this layer touched it.
        models.pop("main", None)
        return
    models["main"] = strong


def _yield_to_run(loop) -> None:
    """Stand down for good: the run is steering its own model with `role_swap`.

    A LATCH, not a reset. A run that has asked for a position once is managing its own turns,
    and an engine that resumed guessing three polls later would fight the run for one slot —
    the failure mode that makes an automatic mechanism worse than none.
    """
    loop._autoswap_yielded = True
    loop._autoswap_on = False
    loop._autoswap_class = None
    loop._autoswap_depth = 0
    loop._autoswap_canon = None
    loop._autoswap_same = 0


def at_landing(loop, action: dict) -> None:
    """Serve or restore, from the action that just became a turn (`sidefields.land`).

    Called where the run's own swap is applied, so both land on the NEXT completion and never
    on the turn that triggered them. Best-effort and turn-free by contract, like every side
    effect at this seam.
    """
    if action.get("role_swap"):
        _yield_to_run(loop)
        return
    if getattr(loop, "_autoswap_yielded", False):
        return
    canon = canonical(action)
    repeated = getattr(loop, "_autoswap_canon", None) == canon
    loop._autoswap_canon = canon
    # The engine's own stuck counter, read not copied: a byte-identical streak this deep is one
    # the loop is already warning the run about.
    loop._autoswap_same = (getattr(loop, "_autoswap_same", 0) + 1) if repeated else 1
    cls = poll_class(action)
    if cls is None or cls != getattr(loop, "_autoswap_class", None):
        loop._autoswap_class = cls
        loop._autoswap_depth = 1 if cls else 0
        _restore(loop)
        return
    loop._autoswap_depth = depth = getattr(loop, "_autoswap_depth", 0) + 1
    if depth < SERVE_FROM:
        return
    if loop._autoswap_same >= WARN_STREAK:
        _restore(loop)   # the escape turn is the main model's, schema shed and all
        return
    _serve_cheap(loop)
