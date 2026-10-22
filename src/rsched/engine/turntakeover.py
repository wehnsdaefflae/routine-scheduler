"""The TURN-seam half of the uncensored takeover (engine/refusal.py's partner).

refusal.py owns the episode arithmetic - detection, flagging, the back-off windows,
the `llm` seam's re-issue. This module owns what only the TURN loop needs: resolving
which model serves THIS turn (the open-window check, the regular chain head bookkeeping)
and the free-text prose-refusal seam inside next_action's schema-retry loop. Split out
when refusal.py crossed the 350-line house bar (the bar's rule: a file that crossed is
a file to split).

net: fs: none
"""

from __future__ import annotations

from . import refusal


def serve_turn_head(loop):
    """The TURN resolution head for completion.next_action: while a back-off window is
    open the uncensored model serves THIS turn (its own chain; a mid-turn hard failure
    walks the UNCENSORED model's fallbacks, never the regular chain), else the regular
    chain serves. Returns (chain, endpoint, ref, on_takeover); the endpoint/ref pair is
    adopted (window-corrected, prompt re-fitted) and ready to complete against.
    """
    from ..endpoints import failover
    from .completion import _adopt_model, _override_window, compact_if_needed
    ctx = loop.ctx
    takeover = refusal.takeover_turn(ctx)
    if takeover is not None:
        chain = [takeover]
        endpoint, ref = _adopt_model(loop, takeover)
        return chain, endpoint, ref, True
    chain = ctx.registry.for_model_chain("main", ctx.routine.models, ctx.routine.efforts)
    # The chain HEAD is what the routine was configured with, whatever is serving now.
    # Recorded once so a failure verdict can name the model the operator chose as well
    # as the one that failed (F547/D146) - `main_model` below cannot, because it
    # follows every switch.
    if not ctx.configured_model and chain:
        head = chain[0][1]
        ctx.configured_model = f"{head.endpoint}/{head.model}"
    endpoint, ref = failover.pick(chain)  # first chain member not in provider cooldown
    ref = _override_window(loop, ref)    # re-apply run-local window correction (F278)
    ctx.main_model = f"{ref.endpoint}/{ref.model}"  # in status.json; updates on a switch
    compact_if_needed(loop, endpoint, ref)
    return chain, endpoint, ref, False


def note_takeover(refstate: dict, on_takeover: bool, served: bool) -> bool:
    """Record that the uncensored model is serving this turn, and return the turn's
    takeover flag — one call where next_action had a bare `on_takeover = …` assignment.

    The two facts are NOT the same and D188 is where that stopped being harmless. A turn is
    flagged `on_takeover` whenever a refusal was handled, INCLUDING the case where no
    uncensored model could serve it and the refused turn is re-driven on the regular chain:
    attributing that turn to `uncensored` would print a role beside a `usage["model"]` naming
    the main model. So `served` — did a takeover target actually take the turn — is what moves
    the role, and the flag keeps its own meaning (never reset the back-off episode).
    """
    if served:
        refstate["role"] = "uncensored"
    return on_takeover or served


def engage_turn_takeover(loop, ref, completion, refstate: dict | None = None) -> tuple | None:
    """The free-text refusal seam inside next_action's schema-retry loop: judge the
    prose reply, engage the back-off episode, and re-drive the refused TURN whole on
    the uncensored model when one can serve it. Returns the ADOPTED (endpoint, ref)
    pair the caller re-drives the turn on and consumes no schema attempt for, or None
    = keep the regular retry path. The turn's latch (refstate) stays the CALLER's -
    one engage per turn, either seam.
    """
    from .completion import _adopt_model
    ctx = loop.ctx
    if not (completion.parsed is None and _is_prose_reply(completion.text)
            and refusal.is_refusal(ctx, completion.text)):
        return None
    _, target = refusal.engage_takeover(
        ctx, refusal=completion.text, where="loop",
        model=ref.name or ref.model, refusing=(ref.endpoint, ref.model))
    if target is None:
        return None
    # The refused TURN is re-driven whole on the uncensored model - its output IS this
    # turn's action (operator, 2026-10-16; the honeypot essence-excision is retired).
    if refstate is not None:
        refstate["role"] = "uncensored"   # D188: the turn is attributed to what serves it
    return _adopt_model(loop, target)


def _is_prose_reply(text: str) -> bool:
    """Whether the reply carries no JSON object at all (mirrors completion's own
    _is_prose_reply, kept local to dodge the import cycle): a refusal is PROSE; a
    malformed action is not one.
    """
    from ..schema_guard import SchemaViolation, extract_json
    try:
        extract_json(text or "")
    except SchemaViolation:
        return True
    return False


