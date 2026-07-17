"""Model failover: ordered fallback chains over the catalog + a provider cooldown registry.

A catalog model may declare `fallbacks:` — an ordered list of catalog model NAMES tried
when this model fails HARD (the adapter's own transport retries are exhausted, or the
error was never retryable). Chains are NOT transitive: a chain is the named model plus its
own `fallbacks` list, each entry resolved with its own endpoint and attributes.

Two cooperating levels, so a flapping provider is never hammered:

- RESOLVE-TIME AVOIDANCE — `EndpointRegistry.for_model`/`for_uncensored`/`for_system`
  resolve the whole chain and `pick()` the first member whose (endpoint, model id) is not
  cooling down. Every resolution site in the engine benefits without changing: the `llm`
  action, compaction's tool_call archival, subroutine spawns, the wizard.
- CALL-TIME FAILOVER — the engine's turn completion (engine/completion.py) advances down
  the chain in place when the picked model still fails mid-turn, logging the switch as a
  transcript `error` event with a `failover` payload.

Cooldowns are marked centrally in `InstrumentedEndpoint` (the one seam every LLM call
passes through), keyed by (endpoint name, provider model id): a model-specific failure
does not blind its endpoint's sibling models, while the same provider model resolved via
different catalog entries shares the mark. The registry is process-local — an engine
subprocess is one run tree; a fresh run probes the primary again (one cheap attempt) and
re-marks it if the outage persists.
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


def mark_failed(endpoint: str, model: str, *, cooldown_s: float = COOLDOWN_S) -> None:
    """Start (or refresh) the cooldown after a hard failure."""
    with _lock:
        _cooling[(endpoint, model)] = time.monotonic() + cooldown_s


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
    """Drop all cooldowns (tests)."""
    with _lock:
        _cooling.clear()


def pick(chain: list) -> tuple:
    """The first chain member not cooling down; when every member is cooling, the PRIMARY —
    a run must never stall on cooldown bookkeeping, and the primary may have recovered.
    Chain entries are (endpoint, ModelRef) as produced by EndpointRegistry.resolve_chain.
    """
    for entry in chain:
        if not is_cooling(entry[1].endpoint, entry[1].model):
            return entry
    return chain[0]


def next_after(chain: list, failed: ModelRef) -> tuple | None:
    """The next usable chain member strictly AFTER the one that just failed (which
    InstrumentedEndpoint has already marked cooling), skipping members already cooling.
    None = chain exhausted — the caller propagates the failure.
    """
    idx = next((i for i, (_, ref) in enumerate(chain)
                if ref.name == failed.name and ref.model == failed.model), None)
    if idx is None:
        return None
    for entry in chain[idx + 1:]:
        if not is_cooling(entry[1].endpoint, entry[1].model):
            return entry
    return None
