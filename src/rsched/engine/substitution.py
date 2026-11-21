"""What a SUBSTITUTED model is told about its own situation (F539, D146-B).

`engine/capabilities.py` states the context window ONCE, at boot, for the BOUND model. A
fallback chain may step DOWN in window (`failover.fits` prefers a fitting rung but never vetoes
one), so after a downward switch that standing figure is false, and the prompt is append-only.

Two things are said at a switch, with different lifetimes:

* WHO is serving (`_notice`) — turn debris, steering only the inherited reply.
* WHAT THE RUN NOW HAS TO BUDGET AGAINST (`_window_correction`) — a durable engine note,
  emitted only when the window shrank against the BOUND model's figure and further than the
  run has already been told.

Never a veto: this module only appends prose. A floor exceeding the window is handled by the
boot-time hard compaction (D223, `engine/promptfloor.py`).
"""

from __future__ import annotations

from ..endpoints.base import EndpointError
from . import enginenote, turndebris


def announce(loop, chain, failed_ref, new_ref, exc: EndpointError) -> None:
    """Tell the model now serving what happened to it: who it is, and what it has lost."""
    _notice(loop, failed_ref, new_ref, exc)
    _window_correction(loop, chain, new_ref)


def _notice(loop, failed_ref, new_ref, exc: EndpointError) -> None:
    """Tell the MODEL that is now serving that it is the fallback (D146-B).

    One appended line at the switch (the model change already invalidated the cache). It is
    turn debris (`turndebris`); the transcript's failover event is the durable record.
    """
    rungs = loop.ctx.failover_rungs
    step = "one rung" if rungs == 1 else f"{rungs} rungs"
    # one naming convention ("<endpoint>/<model>") for all three models in the sentence
    failed = f"{failed_ref.endpoint}/{failed_ref.model}"
    serving = f"{new_ref.endpoint}/{new_ref.model}"
    configured = loop.ctx.configured_model or failed
    turndebris.append(loop, {"role": "user", "content": (
        f"[MODEL FAILOVER — you are no longer the model this routine was configured with]\n"
        f"{failed} failed hard ({str(exc)[:200]}), so this turn "
        f"and the rest of the run are served by {serving} — {step} down "
        f"the fallback chain from {configured}.\n"
        f"This is a transport failure, not a judgment about the work. Two things follow for "
        f"you: hold the action schema exactly (a fallback model's most common failure is a "
        f"field-shifted action that is schema-valid but wrong), and if you cannot carry the "
        f"task at this rung, say so in a finish summary naming this switch — do not diagnose "
        f"the routine's own model, which is not what failed.")})


def _step_down(loop, bound: int, serving: int) -> bool:
    """Whether this substitution costs the run window it has not already been told about.

    Measured against the BOUND model (the chain HEAD, whose figure CAPABILITIES states), never
    the last failed rung — 200k → 200k → 32k has no shrinking step but a 168k loss. Latched at
    the smallest window announced so far, so a correction is never repeated.
    """
    if not (bound and serving) or serving >= bound:
        return False
    floor = getattr(loop, "_substitution_floor", 0) or bound
    if serving >= floor:
        return False
    loop._substitution_floor = serving
    return True


def rebuild(loop, events: list[dict]) -> None:
    """The guard scope's resume seed (engine/guardscope.py): a window already announced in this
    leg stays announced.

    Read back from the replayed note's payload rather than recomputed from a chain the resumed
    leg may resolve differently.
    """
    floors = [int(payload["substitution_floor"])
              for ev in events if ev.get("type") == "user_injection"
              for payload in [ev.get("payload") or {}]
              if payload.get("substitution_floor")]
    loop._substitution_floor = min(floors) if floors else 0


def _window_correction(loop, chain, new_ref) -> None:
    """Correct the run's standing window figure — durably, because it plans against it.

    The numbers ride as numbers and the CAPABILITIES section is named, so the run can resolve
    the contradiction with its stale figure.
    """
    bound = chain[0][1] if chain else new_ref
    if not _step_down(loop, bound.context_tokens, new_ref.context_tokens):
        return
    serving = f"{new_ref.endpoint}/{new_ref.model}"
    enginenote.append(loop, (
        f"CONTEXT WINDOW REDUCED — this run is being served by a SUBSTITUTE model with a "
        f"SMALLER context window than the one it was configured with.\n"
        f"{bound.endpoint}/{bound.model} ({bound.context_tokens:,} tokens) is the model your "
        f"CAPABILITIES section was written for; {serving} ({new_ref.context_tokens:,} tokens) "
        f"is serving you from here. That CAPABILITIES figure is now wrong and cannot be "
        f"rewritten — this run's prompt is only ever appended to.\n"
        f"Budget against {new_ref.context_tokens:,} tokens from now on: page large files with "
        f"read_file ranges instead of reading them whole, keep observations small, and move "
        f"anything load-bearing into a durable store (a `note`, memory_write) "
        f"earlier than you otherwise would — the archive that elides the middle of this "
        f"conversation now bites proportionally sooner. If a turn fails on prompt size, that "
        f"is this substitution and not a fault in your own planning."),
        substitution_floor=new_ref.context_tokens)
