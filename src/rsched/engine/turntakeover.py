"""The TURN-seam half of the uncensored takeover (engine/refusal.py's partner).

refusal.py owns the episode arithmetic - detection, flagging, the back-off windows,
the `llm` seam's re-issue. This module owns what only the TURN loop needs: resolving
which model serves THIS turn (the open-window check, the regular chain head bookkeeping)
and the free-text prose-refusal seam inside next_action's schema-retry loop.

net: fs: none
"""

from __future__ import annotations

from . import refusal


def adopt_takeover(loop, pair) -> tuple:
    """Adopt an (endpoint, ref) the UNCENSORED takeover is handing this turn to — the one
    place a takeover pair becomes the serving pair, for every seam that hands one over.

    Before the request (D315): a turn whose prompt carries image `media` going to a model that
    is not multimodal has its media converted to vision-util TEXT here
    (`mediafallback.media_preflight`), and the transcript says so. Every routine here binds a
    non-multimodal uncensored model, so a refused image turn used to post its images, collect
    the provider's "does not support image inputs" 400, and only then convert on the
    post-error path (`mediafallback.apply_media_fallback`) — one wasted round-trip per turn,
    with nothing in the transcript saying why until it failed. Same conversion, same record,
    one request earlier.

    The conversion runs BEFORE `_adopt_model`, whose compaction must size the prompt that will
    actually be posted: a description is not the same length as the image it replaces.

    A PLAIN failover switch keeps its media and is NOT adopted here — `failover.fits` already
    weighs multimodality when it picks the next chain member, while the takeover deliberately
    does not (operator decision D315: with every uncensored model non-multimodal, selecting
    through `fits` would leave a refused image turn with no takeover at all).
    """
    from .completion import _adopt_model
    from .mediafallback import media_preflight
    media_preflight(loop, pair[1])
    return _adopt_model(loop, pair)


def serve_turn_head(loop):
    """The TURN resolution head for completion.next_action: while a back-off window is
    open the uncensored model serves THIS turn (its own chain; a mid-turn hard failure
    walks the UNCENSORED model's fallbacks, never the regular chain), else the regular
    chain serves. Returns (chain, endpoint, ref, on_takeover); the endpoint/ref pair is
    adopted (window-corrected, prompt re-fitted) and ready to complete against.
    """
    from ..endpoints import failover
    from .completion import _override_window, compact_if_needed
    ctx = loop.ctx
    takeover = refusal.takeover_turn(ctx)
    if takeover is not None:
        chain = [takeover]
        endpoint, ref = adopt_takeover(loop, takeover)
        # the uncensored model is often bound to an endpoint that refuses `response_format`,
        # so a takeover turn is the likeliest to arrive with no grammar at all
        _contract_if_unenforced(loop, endpoint)
        return chain, endpoint, ref, True
    chain = ctx.registry.for_model_chain("main", ctx.routine.models, ctx.routine.efforts)
    # The chain HEAD is what the routine was configured with, recorded once so a failure
    # verdict can name it (D146) — `main_model` below follows every switch.
    if not ctx.configured_model and chain:
        head = chain[0][1]
        ctx.configured_model = f"{head.endpoint}/{head.model}"
    endpoint, ref = failover.pick(chain)  # first chain member not in provider cooldown
    ref = _override_window(loop, ref)    # re-apply run-local window correction (F278)
    ctx.main_model = f"{ref.endpoint}/{ref.model}"  # in status.json; updates on a switch
    compact_if_needed(loop, endpoint, ref)
    _contract_if_unenforced(loop, endpoint)
    return chain, endpoint, ref, False


def _contract_if_unenforced(loop, endpoint) -> None:
    """A run whose prompt carries only the FIELD INDEX gets the full action contract as an
    ENGINE NOTE the first turn nothing else states it — an unenforced wire serving the turn
    (the uncensored takeover among them) or the grammar shed by the repeat-streak escape.

    Here because this is the one function that knows which endpoint is about to serve. The
    decision and the note are `engine/schemaindex.py`'s; this is the call site.
    """
    from . import schemaindex
    if schemaindex.turn_needs_the_contract(loop, endpoint):
        schemaindex.send_the_contract(loop, loop.action_schema)


def note_takeover(refstate: dict, on_takeover: bool, served: bool) -> bool:
    """Record that the uncensored model is serving this turn, and return the turn's
    takeover flag — one call where next_action had a bare `on_takeover = …` assignment.

    The two facts differ (D188): a turn is flagged `on_takeover` whenever a refusal was
    handled, even when no uncensored model could serve and the turn re-ran on the regular chain.
    Only `served` — a takeover target actually took the turn — moves the role; the flag keeps
    its own meaning (never reset the back-off episode).
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
    # turn's action.
    if refstate is not None:
        refstate["role"] = "uncensored"   # D188: the turn is attributed to what serves it
    return adopt_takeover(loop, target)


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


