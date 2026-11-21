"""The ENGINE-SERVED CHEAP POLL — a repeat poll is answered by the cheap model, unasked.

The run-chosen `role_swap` (`engine/roleswap.py`) lets a run drop itself to the `tool_call`
model; this module does the same for a REPEAT POLL without being asked, since reading a
one-line "has it finished?" answer does not need the main model. The cheap model is resolved
through `config.base.role_default_model` (the routine's `tool_call`, else the instance's
`system_tool_call_model`); with no distinct cheap model the module serves nothing and says
nothing.

Same mechanism as `roleswap` and `switches.apply_model_switch` — assign
`ctx.routine.models["main"]`, which `endpoints.for_model` re-resolves every turn — hung off
`sidefields.land`, so the turn loop gains no call site.

The cost of being wrong sets every threshold:

* **Two consecutive polls are normal.** `SERVE_FROM` is the SECOND consecutive repeat, so a
  single re-check never changes models.
* **The escape turn is never cheap.** At `wrongturn.WARN_STREAK` identical actions the loop
  warns the run and sheds the schema (`loop._repeat_warning`); that reply must re-plan without a
  grammar, so a byte-identical streak that deep restores the main model instead.
* **Whoever else points `main` wins.** The console switch and the run's own `role_swap` write
  the same slot; this layer only undoes a swap IT made, and yields for the rest of the run once
  an action carries `role_swap`.

Reach is small by construction: `REPEAT_FAIL` ends a run at 5 identical actions, so real poll
loops alternate `poll → read → poll` and are rarely adjacent, and for a field-less poll
(`subruns`) the class depth equals the identical streak, so `SERVE_FROM` coincides with
`WARN_STREAK` and the band is empty. Widening to a windowed rule is an open operator decision;
`tests/test_auto_cheap_poll.py` pins the empty band.
"""

from __future__ import annotations

import json

from . import enginenote, roleswap
from .wrongturn import WARN_STREAK

#: Kinds whose entire content is "has it finished?", so a repeat is the same question again
#: however the fields read: `subruns` takes no arguments at all, and `wait` names only which
#: child or handle is being waited on.
POLL_KINDS = ("children", "wait")

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

    The routine's own `tool_call`, else the instance's `system_tool_call_model`; one equal to
    the configured `main` is None. Delegated to `roleswap.cheap_model` so the run-chosen and the
    engine-served swap cannot disagree about whether a cheaper model exists.
    """
    return roleswap.cheap_model(loop.ctx)


def _serve_cheap(loop) -> None:
    """Point `main` at the cheap model for the next completion, and tell the run ONCE."""
    ctx = loop.ctx
    models = ctx.routine.models
    cheap = _cheap_model(loop)
    if cheap is None or models.get("main") == cheap:
        return
    # Shared with `roleswap`: where to climb back to, remembered before the first swap, so a
    # run's own `strong` climbs back correctly after an engine-served swap.
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
        # Someone else re-pointed `main` since (console switch, failover): theirs stands.
        # Compared against the model this layer SERVED — `models['tool_call']` may be empty
        # (instance default) and a recomputed `_cheap_model` answers None once swapped, either
        # of which would refuse every restore and strand the run on the cheap model.
        return
    if not strong:
        # No `main` of its own: the inverse of the swap is removing the override, so `main`
        # resolves through the instance default again.
        models.pop("main", None)
        return
    models["main"] = strong


def _yield_to_run(loop) -> None:
    """Stand down for good: the run is steering its own model with `role_swap`.

    A LATCH, not a reset: a run that asked for a position once manages its own turns, and an
    engine that resumed guessing would fight it for the one slot.
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
    # Byte-identical streak, compared against the loop's own warning threshold.
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
