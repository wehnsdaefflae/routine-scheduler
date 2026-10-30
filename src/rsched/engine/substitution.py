"""What a SUBSTITUTED model is told about its own situation (F539, D146-B).

A failover is recorded in four places and, until this module existed, three and a half of them
were for a reader AFTER the fact: the transcript `error` event with its `failover` payload, the
`model_failover` health event with `from_model`/`to_model`/`reason`, the usage row that
attributes each turn to the model that served it — and, for the one turn it was written for, an
appended notice that `completion` then drops with the rest of the turn's debris.

The RUN is the reader that can still act, and the one thing it needs most is the one nothing
carried. `engine/capabilities.py` states the window ONCE, at boot, under the model the routine
was BOUND to ("Model: <endpoint>/<model> — context window ≈ 200,000 tokens; the engine archives
the middle … at ~60-80% of that, so budget your reads"). A fallback chain may step DOWN in
window — a chain is ordered by preference, and `endpoints/failover.fits` prefers a rung that
holds the request but deliberately never vetoes one that does not, because a chain reporting
itself exhausted while a usable model remains would kill the run outright. So after a downward
switch the single standing statement a run holds about its own window is false, by as much as a
factor of six, and the prompt cannot be rewritten to correct it (it is immutable under the
caching contract — only appended to).

Two live runs paid for this:

* `llmsectest-weekday:20260922-060001` — bound to a 200k-class model, served OpenRouter's
  `z-ai/glm-5.2:free` (32,768 tokens), died at turn 106 having spent $1.03 on a run that was
  unrunnable from turn 0: "prompt is too long and cannot be shrunk further: 57,250 estimated
  tokens … provider maximum 32,768". Nothing was stale; every component did its job. The only
  part that could have planned differently was the run, and the run was never told.
* `weightloss:20260923-220004` — two rungs down (503 auth → 402 → GLM 5.2), never told, read its
  own field-shifted output and concluded "the model cannot reliably hold the action schema; pick
  a stronger model". A false diagnosis about itself. $1.01 over 193 turns.

So there are exactly two things to say at a switch, and they have different lifetimes, which is
why they are one module and not one message:

* WHO is serving (`_notice`) — the turn's debris, because it exists to steer THIS reply: the
  inherited turn is the one at risk of being written as though the bound model were answering.
* WHAT THE RUN NOW HAS TO BUDGET AGAINST (`_window_correction`) — durable, through the engine's
  one note seam (`engine/enginenote.py`), because it corrects a standing fact. A run reads its
  CAPABILITIES window on every planning decision it makes for the rest of the run.

The window correction is emitted only when the window actually shrank against the BOUND model's
figure, and only when it shrank further than the run has already been told. A note per switch
regardless would be the layer talking about itself, and that is this repo's standing rule for
engine notes (`window._warn_before_eviction` is once per run for the same reason).

NOT a refusal. Nothing here vetoes a rung, refuses a boot or aborts a run: this module only ever
appends prose. Whether a run whose incompressible floor exceeds the resolved window should be
stopped BEFORE it starts was a separate, behaviour-changing question, and the operator answered
it as D223: a HEALTH EVENT only, and the run starts anyway (`engine/promptfloor.py`, fired from
`boot`). So that half is a count now rather than a veto — and for the reason this module's own
figures give: the window stated here is the CATALOG window, which an F278 run-local correction
can make smaller than the real clamp, so refusing a run on it would rest on an approximation.
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

    One appended line, at the switch, never re-rendered per turn: the message list is
    appended-to and never mutated (CLAUDE.md), and a failover has already invalidated the
    provider cache by changing model, so the append costs nothing extra. It is the turn's
    debris like the retries around it (`turndebris`): it informs the reply it was written for,
    and the transcript's failover event is the durable record.
    """
    rungs = loop.ctx.failover_rungs
    step = "one rung" if rungs == 1 else f"{rungs} rungs"
    # One naming convention for all three models in the sentence. `ctx.configured_model` is
    # "<endpoint>/<model>" while a ModelRef's `name` is its catalog alias, and mixing them put
    # three spellings of the same kind of thing in one paragraph.
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

    Measured against the BOUND model — the chain HEAD, whose figure is the one
    `capabilities.capabilities_digest` composed the prompt's CAPABILITIES line from — and never
    against the rung that happened to fail last. `weightloss:20260923-220004` is why: 200k →
    200k → 32k contains no shrinking STEP, and a run served from its third rung is nevertheless
    168k short of what it was told.

    Then latched at the smallest window announced so far, so a later switch between two equally
    small rungs does not repeat a correction the run is already holding.
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

    A resumed leg REPLAYS the note it was given (that is what `enginenote` records it verbatim
    for), so a leg that starts with the latch empty would hand the run a second copy the moment
    the chain stepped again — and the smallest window announced is exactly the figure the note
    carries, so it is read back from the note rather than recomputed from a chain the resumed
    leg may resolve differently.
    """
    floors = [int(payload["substitution_floor"])
              for ev in events if ev.get("type") == "user_injection"
              for payload in [ev.get("payload") or {}]
              if payload.get("substitution_floor")]
    loop._substitution_floor = min(floors) if floors else 0


def _window_correction(loop, chain, new_ref) -> None:
    """Correct the run's standing window figure — durably, because it plans against it.

    The numbers ride as NUMBERS and the prompt section is named, because the run holds a
    contradiction otherwise: a CAPABILITIES line it was told to budget reads against, and a
    transport notice that says a different model is serving without saying what that costs.
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
        f"anything load-bearing into a durable store (a `note`, memory_write, the LEDGER) "
        f"earlier than you otherwise would — the archive that elides the middle of this "
        f"conversation now bites proportionally sooner. If a turn fails on prompt size, that "
        f"is this substitution and not a fault in your own planning."),
        substitution_floor=new_ref.context_tokens)
