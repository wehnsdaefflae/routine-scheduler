"""Model failover: ordered fallback chains over the catalog + a provider cooldown registry.

A catalog model may declare `fallbacks:` — an ordered list of catalog model NAMES tried
when this model fails HARD (the adapter's own transport retries are exhausted, or the
error was never retryable). Chains are TRANSITIVE and breadth-first: the model's own
fallbacks first, then theirs, each entry resolved with its own endpoint and attributes
(`EndpointRegistry.resolve_chain`). Duplicates, self-references and unresolvable rungs
are skipped.

Two cooperating levels, so a flapping provider is never hammered:

- RESOLVE-TIME AVOIDANCE — `EndpointRegistry.for_model`/`for_uncensored`/`for_system`
  resolve the whole chain and `pick()` the member serving it. Every resolution site in the
  engine benefits without changing: the `llm` action, compaction's tool_call archival,
  subroutine spawns, clarify sessions.
- CALL-TIME FAILOVER — the engine's turn completion (engine/degrade.py) advances down
  the chain in place when the serving model fails mid-turn, logging the switch as a
  transcript `error` event with a `failover` payload and a `model_failover` health event.

Cooldowns are marked centrally in `InstrumentedEndpoint` (the one seam every LLM call
passes through), keyed by (endpoint name, provider model id): a model-specific failure
does not blind its endpoint's sibling models, while the same provider model resolved via
different catalog entries shares the mark. The registry is process-local — an engine
subprocess is one run tree; a fresh run probes the primary again (one cheap attempt) and
re-marks it if the outage persists.

## Forward-only: which member is SERVING a chain

A cooldown expires; a quota does not. When the head of a chain is out of weekly quota, a
cooldown-only `pick` hands the run straight back to it the moment the 300 s mark lapses —
so the run oscillates for its whole life, paying the failed model's retry cycle and a COLD
CACHE WRITE on both models at every swing. Measured 2026-09-12..09-22: 123 switches across
28 runs, p50 390 s apart, and 6.5 M cache-WRITE tokens in the 18 full-prefix rewrites that
landed within 90 s of a switch.

So a chain remembers which member is SERVING it and the mark only ever moves FORWARD:
`pick` starts the scan there instead of at the head. Within a process — one engine
subprocess is one run tree — a model the run has already abandoned is never picked again; a
fresh run still probes the head once, which is what makes a recovered provider come back
without anything to reset.

ONLY `next_after` writes the mark, because only the engine's mid-turn failover is a
DELIBERATE abandonment. `pick` reading it but never writing it is the whole safety of the
scheme: the mark is keyed by chain HEAD and roles share heads (a routine with
`main: Astra high` and an `llm` role on the same model resolves one head), so a write from
`pick` would let one transient 5xx on a cheap subcall demote the main turn loop for the
rest of the run.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import ModelRef

# How long a hard-failed (endpoint, model) is avoided at resolve time. Long enough that a
# run doesn't re-probe a dead provider every turn; short enough that a recovered provider
# is back in rotation within minutes.
COOLDOWN_S = 300.0

_lock = threading.Lock()
_cooling: dict[tuple[str, str], float] = {}   # (endpoint, model id) → monotonic deadline
#: chain HEAD (endpoint, model id) → the member currently serving that chain. Forward-only.
_serving: dict[tuple[str, str], tuple[str, str]] = {}


def mark_failed(endpoint: str, model: str, *, cooldown_s: float = COOLDOWN_S) -> None:
    """Start (or refresh) the cooldown after a hard failure."""
    with _lock:
        _cooling[(endpoint, model)] = time.monotonic() + cooldown_s


def cooldown_for(exc) -> float:
    """How long to avoid a model that just failed: the default, unless the provider ASKED
    for longer. A `Retry-After` is the provider stating when it will serve again, and
    re-probing before then is a guaranteed failure — the engine's 5-minute default is a
    guess, the header is not. (`with_retries` caps the same hint at 30 s, but that bounds
    how long ONE attempt may sleep; nothing is waiting here.)
    """
    return max(COOLDOWN_S, float(getattr(exc, "retry_after", None) or 0.0))


def clear(endpoint: str, model: str) -> None:
    """Lift a cooldown early — for failures later explained by something other than the
    provider (the engine's media fallback: the image was the problem, not the endpoint).
    """
    with _lock:
        _cooling.pop((endpoint, model), None)


def is_cooling(endpoint: str, model: str) -> bool:
    with _lock:
        deadline = _cooling.get((endpoint, model))
        if deadline is None:
            return False
        if time.monotonic() >= deadline:
            del _cooling[(endpoint, model)]
            return False
        return True


def reset() -> None:
    """Drop all cooldowns and every chain's serving mark (tests)."""
    with _lock:
        _cooling.clear()
        _serving.clear()


def _id(ref: ModelRef) -> tuple[str, str]:
    return (ref.endpoint, ref.model)


def _index_of(chain: list, mark: tuple[str, str] | None) -> int:
    """Where `mark` sits in this chain — 0 for None, and 0 for a mark this chain does not
    carry (a chain was re-resolved after a config edit; starting at the head is the safe
    reading).
    """
    if mark is None:
        return 0
    return next((i for i, (_, ref) in enumerate(chain) if _id(ref) == mark), 0)


def _serving_index(chain: list) -> int:
    """Where this chain's scan starts: the member recorded as serving it, or the head."""
    with _lock:
        return _index_of(chain, _serving.get(_id(chain[0][1])))


def _serve(chain: list, index: int) -> None:
    """Record chain[index] as the member serving this chain. Never moves backward — the
    read and the write are ONE critical section, because this module is reached from the
    archival thread as well as the turn loop.
    """
    head = _id(chain[0][1])
    with _lock:
        if index > _index_of(chain, _serving.get(head)):
            _serving[head] = _id(chain[index][1])


def fits(ref: ModelRef, *, has_media: bool, prompt_tokens: int) -> bool:
    """Can this member take the request the failed one was holding, AS IT STANDS?

    A fallback is only a fallback if the same request can travel to it. Two properties
    travel with the request and are knowable before the call: images (a text-only model
    answers `HTTP 400 … does not support image inputs`) and size (a 32 k member answers
    `maximum context length is 32768 … you requested about 165078`). Trying either costs a
    round trip AND marks a perfectly healthy model as cooling for five minutes — on
    2026-09-22 `Fable Max` walked its whole chain that way, three text-only members deep,
    and the conversation died on a provider error instead of a finish.

    `prompt_tokens` is the engine's estimate, compared against what is left of the
    member's window once its own output cap is reserved; 0 means "not measured" and skips
    the size test.

    A PREFERENCE, never a veto — see `next_after`. The engine repairs both properties when
    it has to (images to vision-util text, an oversize prompt by compacting into the new
    member's window), so a member that does not fit as-is is the worse choice, not an
    impossible one.
    """
    if has_media and not ref.multimodal:
        return False
    return not (prompt_tokens
                and prompt_tokens > max(0, ref.context_tokens - ref.max_tokens))


def pick(chain: list) -> tuple:
    """The member serving this chain: the first entry at or after the serving mark that is
    not cooling down. When every one of those is cooling, the marked member itself — a run
    must never stall on cooldown bookkeeping, and the provider may have recovered.
    Chain entries are (endpoint, ModelRef) as produced by EndpointRegistry.resolve_chain.
    """
    start = _serving_index(chain)
    for i in range(start, len(chain)):
        if not is_cooling(*_id(chain[i][1])):
            return chain[i]
    return chain[start]


def next_after(chain: list, failed: ModelRef, *,
               has_media: bool, prompt_tokens: int) -> tuple | None:
    """The next usable chain member strictly AFTER the one that just failed (which
    InstrumentedEndpoint has already marked cooling), skipping members already cooling.

    Among those, one that `fits` this request wins over one that does not: order is
    preserved inside each group, so an author's chain still reads the way they wrote it,
    and a text-only or too-small rung is simply passed over while a capable one remains.
    When NONE of them fits, the first is taken anyway — the engine repairs what it can
    (media → vision-util text, an oversize prompt → compacted into the new member's
    window), and a chain that reports itself exhausted while a usable model is left would
    kill the run outright.

    The returned member becomes the chain's serving mark, so the rest of the run starts
    from it. None = chain exhausted — the caller propagates the failure.
    """
    idx = next((i for i, (_, ref) in enumerate(chain)
                if ref.name == failed.name and ref.model == failed.model), None)
    if idx is None:
        return None
    usable = [i for i in range(idx + 1, len(chain))
              if not is_cooling(*_id(chain[i][1]))]
    if not usable:
        return None
    chosen = next((i for i in usable
                   if fits(chain[i][1], has_media=has_media,
                           prompt_tokens=prompt_tokens)),
                  usable[0])
    _serve(chain, chosen)
    return chain[chosen]
