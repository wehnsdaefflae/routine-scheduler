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
    overflow — a slow browser never backs up the daemon."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def publish(self, event: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()  # drop oldest
                    q.put_nowait(event)
                except asyncio.QueueEmpty:
                    pass

    @contextmanager
    def subscribe(self):
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(q)
        try:
            yield q
        finally:
            self._subscribers.discard(q)
