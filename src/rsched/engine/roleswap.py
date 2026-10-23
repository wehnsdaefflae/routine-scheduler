"""The RUN-CHOSEN ROLE SWAP — a run drops itself to the cheap model, and climbs back (D185).

The operator asked for Claude Code's `opusplan` generalized to any model pair, to cut token
spend: *"i want this … but for generic models. we need it to save on tokens"*. Four triggers were
put to him on 2026-10-16 and he chose the third: **the run itself decides**, because a stage-based
or turn-count rule is wrong for any run whose hard thinking comes late.

So this is not a new action KIND. It is a SIDE FIELD, like `note` and `remind`: it rides whatever
action the run was going to take anyway, at no turn cost. That is the whole point of the design —
a dedicated action would spend one full turn of the EXPENSIVE model asking to become cheap, which
is the cost the feature exists to avoid, and it would do so on every swap back.

The swap itself is the mechanism `switches.apply_model_switch` already uses for the operator's
mid-run switch from the console: assign `ctx.routine.models["main"]`. Nothing else is needed,
because `endpoints.for_model` re-resolves the role from that mapping on every turn
(`completion.next_action`), so the model, its context window and its effort all self-correct at
the next completion. The run is told what happened in the one voice the engine speaks with, an
appended ENGINE NOTE (`enginenote`) — which is also what makes the swap visible in the run's own
transcript.

**Where the cheap model comes from, and why no new config field.** A routine already names its
models per ROLE (`RoutineConfig.models`: main, tool_call, uncensored, supervisor). `tool_call` is
by construction the cheap one — it serves the `llm` action's scoped subcalls. So `cheap` means
"serve `main` with the model this routine already trusts for its tool calls", and `strong`
means "serve it with the model the operator configured for `main`". A run therefore cannot
escalate itself onto a model its routine was never granted, and the operator needs no new knob to
make this safe: the pair is whatever he already chose.

**Auditability was a condition of the decision.** D188 (0.440.0) put the per-turn model and role
on the transcript, so a swap is readable after the fact: the turns between a `cheap` and the next
`strong` are stamped with the cheap model that answered them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import enginenote

if TYPE_CHECKING:
    from .run_context import RunContext

#: The two positions. `cheap` serves `main` from the routine's `tool_call` model; `strong`
#: restores the model the routine configured for `main`. Deliberately not a free-text model
#: name: a run picks a POSITION on its own routine's configured pair, never an arbitrary model
#: (that is `list_models` + a per-call `model` override, which is a different question).
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

    The JSON Schema's own enum catches this for a provider that constrains decoding; this is
    the same check for one that does not, so the run is corrected before the action becomes a
    turn. Whether the position can actually be SERVED is decided at apply time, not here: a
    routine with no `tool_call` model is a config fact, not a mistake in the run's action, and
    refusing the whole action for it would cost a turn the run cannot fix.
    """
    want = action.get("role_swap")
    if want is None:
        return []
    if want not in POSITIONS:
        return [f"role_swap must be one of {list(POSITIONS)}"]
    return []


def _target(ctx: RunContext, want: str) -> str | None:
    """The catalog model NAME `main` should resolve to for `want`, or None if it already does.

    `ctx.configured_main` is the run's own record of what the routine configured for `main`
    before any swap — set once by the first swap, because `ctx.routine.models` is the thing
    being mutated and would otherwise forget where to climb back to.
    """
    models = ctx.routine.models
    if want == "cheap":
        return models.get("tool_call")
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
        enginenote.append(
            loop, f"role_swap={want} could not be served: this routine configures no "
                  f"{'tool_call' if want == 'cheap' else 'main'} model, so your turns continue "
                  "on the model that is answering them. Do not retry the swap.")
        return
    if models.get("main") == target:
        return   # already there — a repeated swap is not an error and needs no note
    models["main"] = target
    enginenote.append(
        loop, f"role_swap={want}: your turns are now answered by `{target}` (the routine's "
              f"{'tool_call' if want == 'cheap' else 'main'} model), from this turn's "
              "observation on. Your context is unchanged. Swap back with "
              f"role_swap={'strong' if want == 'cheap' else 'cheap'} on any action.")
