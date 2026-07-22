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
import contextvars
import json
import threading
import uuid
from pathlib import Path
from typing import IO

from ..ids import now_iso
from . import failover
from .base import DEFAULT_TIMEOUT, ChatEndpoint, Completion, EndpointError, Message

# --- parent-process context --------------------------------------------------
# A frontend-initiated process (routine creation) sets this so the complete()
# calls it triggers attach as children. It propagates across asyncio.to_thread (the context
# is copied into the worker thread), so a request handler sets it once and the deep workflow
# call picks it up without an id plumbed through every function.
_process: contextvars.ContextVar[str | None] = contextvars.ContextVar("llm_process", default=None)


def current_process() -> str | None:
    return _process.get()


@contextlib.contextmanager
def process_scope(process_id: str | None):
    """Attribute every complete() in this block (incl. ones dispatched to to_thread) to
    `process_id`. A no-op when process_id is None.
    """
    token = _process.set(process_id)
    try:
        yield
    finally:
        _process.reset(token)


# --- records -----------------------------------------------------------------
def make_record(phase: str, *, id: str, endpoint: str, model: str, purpose: str,
                kind: str | None = None, process_id: str | None = None,
                usage: dict | None = None, provider: str | None = None,
                error: str | None = None) -> dict:
    """One lifecycle line. The descriptive fields ride every phase so a record is
    self-describing even if an earlier phase's event was dropped by a full SSE queue.
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


def set_sink(sink) -> None:
    """Install the process-global sink (a `.record(rec)` object). Call once at boot."""
    global _sink  # noqa: PLW0603 — the one process-global seam, set once at boot by design
    _sink = sink


def get_sink():
    return _sink


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

    @property
    def context_chars(self) -> int:
        return self._inner.context_chars

    def __getattr__(self, item):
        # adapter-specific attributes fall through to the wrapped endpoint. Guard `_inner`
        # so a missing attribute never recurses into itself.
        if item == "_inner":
            raise AttributeError(item)
        return getattr(self._inner, item)

    def complete(self, messages: list[Message], *,  # noqa: PLR0913 — protocol + audit kwargs
                 model: str, schema: dict | None = None,
                 effort: str | None = None, max_tokens: int | None = None,
                 timeout: int = DEFAULT_TIMEOUT, session: str | None = None,
                 temperature: float | None = None,
                 purpose: str | None = None, process: str | None = None,
                 kind: str | None = None) -> Completion:
        inner_kwargs = {"model": model, "schema": schema, "effort": effort,
                        "max_tokens": max_tokens, "timeout": timeout, "session": session,
                        "temperature": temperature}
        sink = _sink
        if sink is None:                       # fast path: nothing observing
            try:
                return self._inner.complete(messages, **inner_kwargs)
            except EndpointError as exc:
                self._mark_health(exc, model)
                raise
        common = {"id": uuid.uuid4().hex[:12], "endpoint": self._inner.name, "model": model,
                  "purpose": purpose or "LLM call", "kind": kind,
                  "process_id": process if process is not None else current_process()}
        _emit(sink, make_record("started", **common))
        try:
            comp = self._inner.complete(messages, **inner_kwargs)
        except BaseException as exc:
            if isinstance(exc, EndpointError):
                self._mark_health(exc, model)
            _emit(sink, make_record("failed", **common, error=str(exc)[:300]))
            raise
        _emit(sink, make_record("finished", **common, usage=comp.usage,
                                provider=comp.provider or None))
        return comp

    def _mark_health(self, exc: EndpointError, model: str) -> None:
        """Cooldowns are a PROVIDER-HEALTH signal: start one only when the adapter's own
        transport retries were exhausted on a retryable-class failure (outage, rate limit,
        network). A deterministic failure — bad key, malformed request, a Settings probe
        with a wrong credential — is a CONFIG error: cooling it would poison resolution
        for 5 minutes after the user fixes the config. (The engine's turn completion still
        cools a model it abandons MID-TURN, whatever the error class — that judgment lives
        at the engine seam, engine/completion.py.)
        """
        if exc.retryable:
            failover.mark_failed(self._inner.name, model)


def _emit(sink, rec: dict) -> None:
    """Recording must never break a real LLM call (disk full, a slow subscriber…)."""
    try:
        sink.record(rec)
    except Exception:
        pass
