"""In-process event bus feeding the global SSE stream (and dashboard badges).

Events: {"event": "run_started"|"run_state"|"run_finished"|"question_asked",
         "routine": slug, "run_id": ..., ...}. Fire-and-forget; slow subscribers drop
oldest events rather than blocking the daemon.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

QUEUE_SIZE = 200


class EventBus:
    """Fire-and-forget pub/sub: bounded per-subscriber queues, oldest event dropped on
    overflow — a slow browser never backs up the daemon.

    `publish` is safe from ANY thread. A subscriber's queue is an asyncio.Queue bound to the
    loop it subscribed on, and asyncio queues are not thread-safe: a put from a worker thread
    (a sync route, the LLM task center fed from a run's thread) neither locks against the
    loop nor wakes it, so an open view heard about the event whenever something else next
    woke the loop. Off that loop, the put is marshalled onto it with `call_soon_threadsafe`;
    on it, it happens inline as before.
    """

    def __init__(self) -> None:
        self._subscribers: dict[asyncio.Queue, asyncio.AbstractEventLoop | None] = {}

    def publish(self, event: dict) -> None:
        try:
            here: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        for q, loop in list(self._subscribers.items()):
            if loop is None or loop is here:
                _deliver(q, event)
                continue
            try:
                loop.call_soon_threadsafe(_deliver, q, event)
            except RuntimeError:        # that loop is closed — its subscriber is gone
                self._subscribers.pop(q, None)

    @contextmanager
    def subscribe(self):
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        try:
            loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:            # a synchronous reader (tests): delivered inline
            loop = None
        self._subscribers[q] = loop
        try:
            yield q
        finally:
            self._subscribers.pop(q, None)


def _deliver(q: asyncio.Queue, event: dict) -> None:
    """Put one event, dropping the oldest when the queue is full."""
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        try:
            q.get_nowait()  # drop oldest
            q.put_nowait(event)
        except asyncio.QueueEmpty:
            pass
