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

A cooldown expires; a quota does not. A cooldown-only `pick` would hand a run back to a
quota-exhausted head every time the mark lapsed, paying a retry cycle and a cold cache write
on both models at every swing. So a chain remembers which member is SERVING it and the mark
only moves FORWARD: `pick` starts the scan there. A fresh run (process) still probes the head
once, so a recovered provider comes back with nothing to reset.

The mark names the member by its CATALOG name (the identity `resolve_chain` dedups by): a
chain stepping down in effort holds one provider model twice (`Opus high`, `Opus low`). The
cooldown itself stays per provider model, where a failure belongs.

ONLY `next_after` writes the mark — the engine's mid-turn failover is the one DELIBERATE
abandonment. The mark is keyed by chain HEAD and roles share heads, so a write from `pick`
would let one transient 5xx on a cheap subcall demote the main turn loop for the whole run.

## The one exception: the last resort

Forward-only is the rule everywhere EXCEPT the moment the chain would otherwise report itself
exhausted and kill the run (`_last_resort`). The original argument weighs one swing against
another working model; at that moment there is no other working model, and a run killed beside
a recovered one is the worse outcome — on the fleet, 13 of 23 exhaustions in ten days happened
with an earlier member's cooldown already lapsed.

The quota half of the argument survives intact, because it has to: a spent allowance stops
reading as cooling the moment its deadline lapses while refusing exactly as before. So a
member is reachable backwards only if its mark lapsed AND its failure was not DURABLE
(`mark_failed(durable=...)`, classified at the seam that saw the error —
`errorsignal.durable_exhaustion`). Nothing downstream could recover that from a deadline.

It cannot oscillate: every failure re-cools the member that produced it, so a member can serve
a given turn at most twice — once going forward, once as the last resort — and the next
exhaustion with nothing recovered behind it returns None exactly as before.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from . import errorsignal

if TYPE_CHECKING:
    from ..config import ModelRef

# How long a hard-failed (endpoint, model) is avoided at resolve time. Long enough that a
# run doesn't re-probe a dead provider every turn; short enough that a recovered provider
# is back in rotation within minutes.
COOLDOWN_S = 300.0

_lock = threading.Lock()
_cooling: dict[tuple[str, str], float] = {}   # (endpoint, model id) → monotonic deadline
#: chain HEAD (endpoint, model id) → the catalog NAME of the member currently serving that
#: chain. Forward-only.
_serving: dict[tuple[str, str], str] = {}
#: (endpoint, model id) whose failure OUTLASTS its cooldown — a spent allowance, an unpayable
#: account, a rejected credential. Its cooldown expires like any other, but the expiry means
#: nothing, so the last-resort reach-back in `next_after` must not count it as recovered.
_durable: set[tuple[str, str]] = set()


def mark_failed(endpoint: str, model: str, *, cooldown_s: float = COOLDOWN_S,
                durable: bool = False) -> None:
    """Start (or refresh) the cooldown after a hard failure.

    `durable` records that this failure will still refuse once the cooldown lapses — a spent
    allowance, an unpayable account, a rejected credential
    (`errorsignal.durable_exhaustion`). The cooldown itself is unchanged; the flag is what
    keeps the last-resort reach-back in `next_after` off a member whose mark expiring means
    nothing. It LATCHES: a model that answered `insufficient_quota` once is not made reachable
    again by a later timeout on the same model.
    """
    with _lock:
        _cooling[(endpoint, model)] = time.monotonic() + cooldown_s
        if durable:
            _durable.add((endpoint, model))


def cooldown_for(exc) -> float:
    """How long to avoid a model that just failed: the default, unless the provider's
    `Retry-After` asks for longer (re-probing before then is a guaranteed failure).
    """
    return max(COOLDOWN_S, float(getattr(exc, "retry_after", None) or 0.0))


def durable_for(exc) -> bool:
    """Whether the failure that just happened will still refuse once the cooldown lapses.

    The companion of `cooldown_for`: one accessor, so neither cooling seam decides for itself
    what a spent allowance looks like. The reading is `errorsignal`'s declared one, never a
    phrase matched in the body.
    """
    return errorsignal.durable_exhaustion(errorsignal.of(exc))


def clear(endpoint: str, model: str) -> None:
    """Lift a cooldown early — for failures later explained by something other than the
    provider (the engine's media fallback: the image was the problem, not the endpoint).
    """
    with _lock:
        _cooling.pop((endpoint, model), None)
        # the provider was not at fault, so neither is its allowance
        _durable.discard((endpoint, model))


def is_cooling(endpoint: str, model: str) -> bool:
    with _lock:
        deadline = _cooling.get((endpoint, model))
        if deadline is None:
            return False
        if time.monotonic() >= deadline:
            del _cooling[(endpoint, model)]
            return False
        return True


def is_durable(endpoint: str, model: str) -> bool:
    """Whether this member's failure outlasts its cooldown, so an expired mark means nothing.

    Read-only, and NOT a cooling test: a durably-failed member stops reading as cooling the
    moment its deadline lapses, which is exactly when the reach-back has to know better.
    """
    with _lock:
        return (endpoint, model) in _durable


def reset() -> None:
    """Drop all cooldowns, every chain's serving mark and every durable mark (tests)."""
    with _lock:
        _cooling.clear()
        _serving.clear()
        _durable.clear()


def _id(ref: ModelRef) -> tuple[str, str]:
    return (ref.endpoint, ref.model)


def chain_state(chain: list) -> list[dict]:
    """Per member, whether it is COOLING and with how many seconds left — read at the moment
    of the call, for the record a chain exhaustion leaves behind.

    A member whose cooldown has already lapsed reads `cooling: false`, which is the fact that
    was missing: an exhaustion says every member "failed hard or is cooling", but nothing
    recorded WHICH, so a run that died while an earlier member had recovered looked exactly
    like one that died with the whole chain down. Reconstructing it afterwards meant
    differencing a failover's timestamp against COOLDOWN_S across the health stream, and the
    cooldown a provider's `Retry-After` lengthened is not recoverable that way at all.

    Read-only: it never clears an expired mark, so it cannot change what the next `pick` or
    `next_after` sees.
    """
    now = time.monotonic()
    out: list[dict] = []
    with _lock:
        for _, ref in chain:
            deadline = _cooling.get(_id(ref))
            left = (deadline - now) if deadline is not None else 0.0
            out.append({"name": ref.name or ref.model,
                        "cooling": left > 0,
                        "cooldown_left_s": round(left, 1) if left > 0 else 0.0,
                        # `cooling: false, durable: true` is the one combination a reader
                        # would otherwise misread as a member the chain could have gone back
                        # to: a spent allowance stops cooling on schedule and refuses anyway.
                        "durable": _id(ref) in _durable})
    return out


def _index_of(chain: list, mark: str | None) -> int:
    """Where the member named `mark` sits in this chain — 0 for None, and 0 for a name this
    chain does not carry (a chain was re-resolved after a config edit; starting at the head is
    the safe reading).
    """
    if not mark:
        return 0
    return next((i for i, (_, ref) in enumerate(chain) if ref.name == mark), 0)


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
            _serving[head] = chain[index][1].name


def fits(ref: ModelRef, *, has_media: bool, prompt_tokens: int) -> bool:
    """Can this member take the request the failed one was holding, AS IT STANDS?

    Two properties travel with the request and are knowable before the call: images (a
    text-only member 400s) and size. Trying a misfit costs a round trip AND marks a healthy
    model as cooling.

    `prompt_tokens` is the engine's estimate, compared against the member's window minus its
    output cap; 0 means "not measured" and skips the size test.

    A PREFERENCE, never a veto — see `next_after`: the engine can repair both properties.
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

    Among those, the first that `fits` wins (chain order kept); when none fits, the first is
    taken anyway — the engine repairs what it can, and reporting the chain exhausted while a
    usable model is left would kill the run.

    The returned member becomes the chain's serving mark, so the rest of the run starts
    from it. None = chain exhausted — the caller propagates the failure.

    When nothing forward is left, and ONLY then, the scan turns around once
    (`_last_resort`): the alternative at that point is reporting the chain exhausted and
    killing the run, and a run killed beside a recovered model is the worse outcome. Forward
    is still the only direction considered while anything forward remains.
    """
    idx = next((i for i, (_, ref) in enumerate(chain) if ref.name == failed.name), None)
    if idx is None:
        return None
    usable = [i for i in range(idx + 1, len(chain))
              if not is_cooling(*_id(chain[i][1]))]
    if not usable:
        return _last_resort(chain, idx, has_media=has_media, prompt_tokens=prompt_tokens)
    chosen = next((i for i in usable
                   if fits(chain[i][1], has_media=has_media,
                           prompt_tokens=prompt_tokens)),
                  usable[0])
    _serve(chain, chosen)
    return chain[chosen]


def _last_resort(chain: list, idx: int, *,
                 has_media: bool, prompt_tokens: int) -> tuple | None:
    """The chain has nothing forward left: one look BACK, or None and the run dies.

    This is the single exception to forward-only, and it is bounded by the alternative rather
    than by a timer. Forward-only exists because a cooldown expires and an allowance does not,
    so a `pick` that drifted back would hand runs to a spent model every five minutes — but
    that argument compares one swing against another working model. Here the comparison is
    against reporting the chain exhausted: measured on the fleet, 13 of 23 exhaustions in ten
    days happened while an earlier member's cooldown had already lapsed, and five runs died
    that way in one day after advancing a single rung.

    A member is reachable only if BOTH hold:

    * its cooldown has lapsed (or it was never marked — a rung `fits` passed over while a
      capable one remained is untried, not failed), and
    * its failure was not DURABLE. That is the quota half of the original argument, and it is
      the reason a reach-back is not simply "not cooling": a spent allowance, an unpayable
      account and a rejected credential all stop reading as cooling the moment their deadline
      lapses, while refusing exactly as before. `mark_failed(durable=...)` records it where the
      failure was classified, because nothing downstream can recover it from a deadline.

    Chain order decides between candidates, so the configured head is preferred to a rung
    below it, and a member that `fits` this request is preferred to one that does not — the
    same preference `next_after` applies going forward.
    """
    reachable = [i for i in range(idx)
                 if not is_cooling(*_id(chain[i][1]))
                 and not is_durable(*_id(chain[i][1]))]
    if not reachable:
        return None
    chosen = next((i for i in reachable
                   if fits(chain[i][1], has_media=has_media,
                           prompt_tokens=prompt_tokens)),
                  reachable[0])
    _serve_last_resort(chain, chosen)
    return chain[chosen]


def _serve_last_resort(chain: list, index: int) -> None:
    """Record chain[index] as serving, BACKWARD — the second deliberate write, and the only
    one that moves the mark up a chain.

    It is deliberate in the same sense `_serve` is: the chain had nowhere forward to go, so
    this is not a cooldown lapsing into a drift but the engine choosing a recovered member
    over killing the run. The mark has to move, because every later turn re-resolves through
    `pick`: left on the member that just failed, it would spend one dead call per turn to
    rediscover the same exhaustion. `_serve` keeps its forward-only guarantee untouched, which
    is what `pick` and the resolve-time sites still depend on.
    """
    with _lock:
        _serving[_id(chain[0][1])] = chain[index][1].name
