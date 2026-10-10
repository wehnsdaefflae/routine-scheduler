"""Instrumentation seam: every ChatEndpoint.complete() the registry hands out is observed,
so a central task manager can show exactly what LLM work is in flight — without any adapter
knowing about it and without touching the prompt.

`EndpointRegistry.get()` returns an `InstrumentedEndpoint(inner)`. Its `complete()` emits a
`started` record, runs the real adapter, then a `finished`/`failed` record to a
process-global sink. The default sink is None — pure passthrough, so tests and one-shot CLI
runs behave exactly as before. The daemon sets a sink that publishes to the event bus + task
center; an engine subprocess sets a `FileSink` that appends to `runs/<ts>/llm-tasks.jsonl`
for the daemon to tail.

Bookkeeping is strictly out-of-band: `complete()` returns the inner `Completion` unchanged,
re-raises the inner exception, and never mutates `messages` — the engine's prompt-caching
contract is untouched. A sink failure never breaks a real LLM call (recording is guarded).
"""

from __future__ import annotations

import contextlib
import json
import threading
import uuid
from pathlib import Path
from typing import IO

from ..ids import now_iso
from . import failover
from .base import DEFAULT_TIMEOUT, ChatEndpoint, Completion, EndpointError, Message

#: Task kinds whose prompt is a CONVERSATION — the only ones a cache breakpoint can pay for.
#: Everything else here is one-shot (an `llm` action, the archival digest, the refusal
#: classifier, a workflow draft, a playbook distillation): a fresh prefix that is never sent
#: again, so the write costs 1.25x for a read that never comes. Measured 2026-09-11: 0.3%
#: read share on `llm_action` against 96% on turns.
#:
#: Derived HERE and nowhere else, because this wrapper is the one seam every completion
#: passes through and it already knows the kind. A per-call-site flag would be a flag ten
#: call sites can forget; the adapters still default `cacheable=True`, so a path that somehow
#: bypasses instrumentation keeps caching rather than silently losing it. How a one-shot call
#: is kept OUT of the cache is each adapter's business: on the `anthropic` wire it takes one
#: marker, not none, because the subscription proxy caches any request that carries none
#: (`anthropic_messages.claim_placement`).
CACHEABLE_KINDS = frozenset({"turn"})


# --- records -----------------------------------------------------------------
def make_record(phase: str, *, id: str, endpoint: str, model: str, purpose: str,
                kind: str | None = None, process_id: str | None = None,
                usage: dict | None = None, provider: str | None = None,
                error: str | None = None) -> dict:
    """One lifecycle line. The descriptive fields ride every phase so a record is
    self-describing even if an earlier phase's event was dropped by a full bus queue.
    """
    rec: dict = {"id": id, "phase": phase, "ts": now_iso(), "endpoint": endpoint,
                 "model": model, "purpose": purpose}
    if kind:
        rec["kind"] = kind
    if process_id:
        rec["process_id"] = process_id
    if usage is not None:
        rec["usage"] = usage
    if provider:
        rec["provider"] = provider
    if error is not None:
        rec["error"] = error
    return rec


# --- sinks (process-global) --------------------------------------------------
class FileSink:
    """Engine-subprocess sink: append each record as one JSON line to a sidecar the daemon
    tails. Same discipline as Transcript (append, line-buffered, flush, no fsync); a lock
    keeps parallel subrun threads — which share this one process-global sink — from
    interleaving partial lines. Opens lazily so a run with no LLM call writes no file.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._fh: IO[str] | None = None

    def record(self, rec: dict) -> None:
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self._lock:
            if self._fh is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # deliberately long-lived (append-per-record); closed in close()
                self._fh = self.path.open("a", encoding="utf-8", buffering=1)
            self._fh.write(line)
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                with contextlib.suppress(OSError):
                    self._fh.close()
                self._fh = None

    def __del__(self) -> None:
        # The engine does NOT close its sink explicitly at exit — process teardown (and
        # this backstop for a replaced sink) releases the append handle; the file is
        # line-buffered + flushed per record, so nothing is lost either way.
        try:
            self.close()
        except Exception:
            pass


_sink = None  # None = no bookkeeping configured (pure passthrough)

#: The calls this process has STARTED and not yet terminated, by id → its `started` record.
#: Tracked because a call can die without unwinding: `engine/archival.py` runs its completion in
#: a DAEMON thread, so at process exit the interpreter kills it mid-`complete()` and the
#: `except BaseException` below never runs. Measured in conversation c-20260915-074755's sidecar
#: (884 ids): 23 had a `started` record and no terminal one, 17 of them `Compaction · archival`.
#: The task centre sets `done_at` only from a terminal record, so each of those leaked a
#: permanently-`running` task (F572). A caller that KNOWS it is giving up — `archival.settle()`
#: does, and it is still alive when it decides — terminates them through `abandon_open_calls`.
_open: dict[str, dict] = {}
_open_lock = threading.Lock()


def set_sink(sink) -> None:
    """Install the process-global sink (a `.record(rec)` object). Call once at boot."""
    global _sink  # noqa: PLW0603 — the one process-global seam, set once at boot by design
    _sink = sink
    with _open_lock:
        _open.clear()      # a new sink observes a new process's calls, never the old one's


def note_started(rec: dict) -> None:
    """Register a `started` record as an OPEN call. Called by the wrapper; public because the
    tests describe calls the wrapper never started (a daemon thread killed mid-call).
    """
    cid = rec.get("id")
    if cid:
        with _open_lock:
            _open[cid] = dict(rec)


def _note_terminal(cid: str | None) -> None:
    if cid:
        with _open_lock:
            _open.pop(cid, None)


def abandon_open_calls(*, error: str, purpose: str | None = None) -> int:
    """Write a `failed` record for every call still open, and return how many.

    This is how a caller that abandons work on purpose keeps the books honest without knowing
    the call id — the id is minted inside `complete()` and never returned, which is exactly why
    `archival.settle()` had no way to say what it already knew. Idempotent: a call terminated
    here leaves the open set, so abandoning twice writes one record.

    `purpose` narrows it to one kind of work (the abandoning caller's own), because a run that
    gives up on its archive has said nothing about the turn call still in flight beside it.
    """
    sink = _sink
    if sink is None:
        return 0
    with _open_lock:
        victims = [rec for rec in _open.values()
                   if purpose is None or rec.get("purpose") == purpose]
        for rec in victims:
            _open.pop(rec["id"], None)
    for rec in victims:
        _emit(sink, make_record("failed", id=rec["id"], endpoint=rec.get("endpoint", ""),
                                model=rec.get("model", ""), purpose=rec.get("purpose", ""),
                                kind=rec.get("kind"), process_id=rec.get("process_id"),
                                error=error[:300]))
    return len(victims)


# --- the wrapper -------------------------------------------------------------
class InstrumentedEndpoint:
    """Transparent decorator over a ChatEndpoint. Records each complete() to the current
    sink and otherwise behaves exactly like `inner` — same Completion, same exceptions,
    all standard kwargs forwarded untouched.
    """

    def __init__(self, inner: ChatEndpoint):
        object.__setattr__(self, "_inner", inner)

    @property
    def name(self) -> str:
        return self._inner.name

    def __getattr__(self, item):
        # adapter-specific attributes fall through to the wrapped endpoint. Guard `_inner`
        # so a missing attribute never recurses into itself.
        if item == "_inner":
            raise AttributeError(item)
        return getattr(self._inner, item)

    def complete(self, messages: list[Message], *,
                 model: str, schema: dict | None = None,
                 effort: str | None = None, max_tokens: int | None = None,
                 timeout: int = DEFAULT_TIMEOUT,
                 temperature: float | None = None,
                 purpose: str | None = None,
                 kind: str | None = None) -> Completion:
        inner_kwargs = {"model": model, "schema": schema, "effort": effort,
                        "max_tokens": max_tokens, "timeout": timeout,
                        "temperature": temperature, "cacheable": kind in CACHEABLE_KINDS}
        sink = _sink
        if sink is None:                       # fast path: nothing observing
            try:
                return self._inner.complete(messages, **inner_kwargs)
            except EndpointError as exc:
                self._mark_health(exc, model)
                raise
        # No `process_id` here on purpose: the one live attributor is the daemon, which stamps
        # `rec.setdefault("process_id", run.run_id)` on the way into the task centre
        # (daemon/runner.py). `make_record` omits falsy keys, so nothing downstream changes.
        common = {"id": uuid.uuid4().hex[:12], "endpoint": self._inner.name, "model": model,
                  "purpose": purpose or "LLM call", "kind": kind}
        started = make_record("started", **common)
        note_started(started)          # so an abandoning caller can terminate it (F572)
        _emit(sink, started)
        try:
            comp = self._inner.complete(messages, **inner_kwargs)
        except BaseException as exc:
            if isinstance(exc, EndpointError):
                self._mark_health(exc, model)
            _note_terminal(common["id"])
            _emit(sink, make_record("failed", **common, error=str(exc)[:300]))
            raise
        _note_terminal(common["id"])
        _emit(sink, make_record("finished", **common, usage=comp.usage,
                                provider=comp.provider or None))
        return comp

    def _mark_health(self, exc: EndpointError, model: str) -> None:
        """Cooldowns are a PROVIDER-HEALTH signal: start one only when the adapter's own
        transport retries were exhausted on a retryable-class failure (outage, rate limit,
        network). A deterministic failure — bad key, malformed request, a Settings probe
        with a wrong credential — is a CONFIG error: cooling it would poison resolution
        for 5 minutes after the user fixes the config. A provider that sent a `Retry-After`
        is cooled for at least that long — it has stated when it will serve again.
        (The engine's turn completion still cools a model it abandons MID-TURN, whatever
        the error class — that judgment lives at the engine seam, engine/degrade.py.)
        """
        if exc.retryable:
            failover.mark_failed(self._inner.name, model,
                                 cooldown_s=failover.cooldown_for(exc))


def observe_call(call, *, endpoint: str, model: str, purpose: str, kind: str):
    """Record a transport call that is not a chat completion — a decision model's
    (endpoints/decisions.py) — with the same started/finished/failed lifecycle, so it shows in
    the activity dock like any other model call. `call()` returns an object carrying `usage`
    and `provider`; it is returned unchanged, and its exception re-raised.
    """
    sink = _sink
    if sink is None:
        return call()
    common: dict = {"id": uuid.uuid4().hex[:12], "endpoint": endpoint, "model": model,
                    "purpose": purpose, "kind": kind}
    started = make_record("started", **common)
    note_started(started)
    _emit(sink, started)
    try:
        out = call()
    except BaseException as exc:
        _note_terminal(common["id"])
        _emit(sink, make_record("failed", **common, error=str(exc)[:300]))
        raise
    _note_terminal(common["id"])
    _emit(sink, make_record("finished", **common, usage=out.usage,
                            provider=out.provider or None))
    return out


def _emit(sink, rec: dict) -> None:
    """Recording must never break a real LLM call (disk full, a slow subscriber…)."""
    try:
        sink.record(rec)
    except Exception:
        pass
