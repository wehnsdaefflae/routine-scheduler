"""The RUN-CHOSEN ROLE SWAP — a run drops itself to the cheap model, and climbs back (D185).

The run itself decides when, because a stage- or turn-count rule is wrong for any run whose hard
thinking comes late. It is a SIDE FIELD like `note` and `remind`, not an action KIND: a
dedicated action would spend a full turn of the expensive model asking to become cheap.

The swap is the mechanism of the console's mid-run switch (`switches.apply_model_switch`):
assign `ctx.routine.models["main"]`. `endpoints.for_model` re-resolves the role every turn, so
model, window and effort self-correct at the next completion; the run is told by an ENGINE NOTE.

`cheap` serves `main` from the `tool_call` role, resolved through
`config.base.role_default_model` (the routine's own, else the instance's
`system_tool_call_model`); `strong` restores the model configured for `main`. Both positions name
a model the OPERATOR configured, so a run cannot escalate onto one nobody chose. The per-turn
model stamp on the transcript makes every swap auditable after the fact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config.base import role_default_model
from . import enginenote

if TYPE_CHECKING:
    from .run_context import RunContext

#: The two positions — deliberately not a free-text model name: a run picks a POSITION on its
#: routine's configured pair, never an arbitrary model.
POSITIONS = ("cheap", "strong")

#: The `role_swap` side field — the micro-doc the model reads is this description.
ROLE_SWAP_FIELD = {
    "type": "string", "enum": list(POSITIONS),
    "description": (
        "OPTIONAL, on any action: SWAP THE MODEL answering your own turns, from the next turn "
        "on — it rides this action at no turn cost. `cheap` serves your turns with this "
        "routine's tool-call model, for work you judge mechanical (a long gate wait, a batch of "
        "reads, bookkeeping you have already decided). `strong` puts the routine's main model "
        "back, for work that needs judgment again. You keep your whole context either way; only "
        "the model answering changes, and the transcript stamps which one answered each turn. "
        "Swap down the moment the remaining work stops needing you, and back up BEFORE the "
        "thinking starts — not after a cheap turn has already made the decision badly."),
}


def field_problems(action: dict) -> list[str]:
    """Reject a `role_swap` value that is not a position, inside the schema-retry cycle.

    The schema enum's check for a provider that does not constrain decoding. Whether the
    position can be SERVED is decided at apply time: that is a config fact, and refusing the
    action for it would cost a turn the run cannot fix.
    """
    want = action.get("role_swap")
    if want is None:
        return []
    if want not in POSITIONS:
        return [f"role_swap must be one of {list(POSITIONS)}"]
    return []


def cheap_model(ctx: RunContext) -> str | None:
    """The DISTINCT cheap model this run's turns can be served from, or None when there is none.

    The role resolves to
    the routine's own `tool_call`, else the instance's `system_tool_call_model`, and must DIFFER
    from what `main` resolves to.
    """
    models = ctx.routine.models
    server = getattr(ctx, "server", None)
    cheap = models.get("tool_call") or role_default_model(server, "tool_call")[0]
    if not cheap:
        return None
    strong = getattr(ctx, "configured_main", None) or models.get("main") \
        or role_default_model(server, "main")[0]
    return None if cheap == strong else cheap


#: Why a position could not be served — the two causes `cheap_model` collapses into None, kept
#: apart so the engine note names the one that fired (their fixes are opposite).
UNSET = "unset"   # the role resolves to nothing: no routine model, no instance default
SAME = "same"     # the role resolves to the model `main` is already being served from


def cheap_unservable(ctx: RunContext) -> str:
    """Which cause made `cheap_model` answer None — `UNSET`, `SAME`, or "" when it found one.

    The same resolution as `cheap_model`, run again for the one caller that needs the reason
    (the engine note), so the others need not unpack one. `SAME` is the common case: settings
    patterns ship `tool_call` equal to `main`.
    """
    models = ctx.routine.models
    server = getattr(ctx, "server", None)
    cheap = models.get("tool_call") or role_default_model(server, "tool_call")[0]
    if not cheap:
        return UNSET
    strong = getattr(ctx, "configured_main", None) or models.get("main") \
        or role_default_model(server, "main")[0]
    return SAME if cheap == strong else ""


def _target(ctx: RunContext, want: str) -> str | None:
    """The catalog model NAME `main` should resolve to for `want`, or None if it already does.

    `ctx.configured_main` records what the routine configured for `main` before any swap — set
    by the first swap, because `ctx.routine.models` is what gets mutated.
    """
    if want == "cheap":
        return cheap_model(ctx)
    return getattr(ctx, "configured_main", None) or None


def apply(loop, action: dict) -> None:
    """Honour an accepted action's `role_swap`. Called where `note` is filed (loop._land), so
    the swap lands on the NEXT completion and never on the turn that asked for it.

    Best-effort by construction: a swap that cannot be served must not cost the run a turn, so
    an unserveable position is NAMED to the run as an engine note and otherwise ignored. The run
    keeps its context; only the model answering changes.
    """
    want = str(action.get("role_swap") or "")
    if want not in POSITIONS:
        return
    ctx = loop.ctx
    models = ctx.routine.models
    if not hasattr(ctx, "configured_main"):
        ctx.configured_main = models.get("main") or ""
    target = _target(ctx, want)
    if not target:
        # `strong` with no recorded main climbs back out of a swap on a routine that configures
        # no `main`: REMOVING the override restores the instance default, as before the swap.
        if want == "strong" and "main" in models:
            models.pop("main")
            enginenote.append(
                loop, "role_swap=strong: your turns are answered by this instance's default "
                      "model for the main role again (this routine configures none of its "
                      "own), from this turn's observation on. Your context is unchanged.")
            return
        tail = ("so your turns continue on the model that is answering them. "
                "Do not retry the swap.")
        if want == "cheap" and cheap_unservable(ctx) == SAME:
            # Configured, but equal to `main` — not "configures no tool_call model".
            enginenote.append(
                loop, f"role_swap={want} could not be served: this routine's tool_call model "
                      f"is the same model as its main, so there is nothing cheaper to serve "
                      f"your turns from — {tail}")
        else:
            enginenote.append(
                loop, f"role_swap={want} could not be served: this routine configures no "
                      f"{'tool_call' if want == 'cheap' else 'main'} model and this instance "
                      f"sets no fleet default for it, {tail}")
        return
    if models.get("main") == target:
        return   # already there — a repeated swap is not an error and needs no note
    models["main"] = target
    enginenote.append(
        loop, f"role_swap={want}: your turns are now answered by `{target}` (the "
              f"{'tool_call' if want == 'cheap' else 'main'} model for this routine), from this "
              "turn's observation on. Your context is unchanged. Swap back with "
              f"role_swap={'strong' if want == 'cheap' else 'cheap'} on any action.")
