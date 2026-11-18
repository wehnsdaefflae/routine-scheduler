"""Bounded serve-time checks for a run's files card (F896).

`servable_rel` resolves and stats EACH row, including paths on SSHFS. Keep that fresh D172
predicate intact, but overlap the I/O: one app-owned pool, at most FILE_CHECK_WORKERS checks
across concurrent requests, and at most that many outstanding submissions PER request.

The existing `ioload` executor belongs to the event loop and reserves capacity for live-stream
polls. Sync handlers cannot borrow AnyIO's own handler threads recursively either: callers
waiting for their rows could occupy every token their checks need. This separate, named pool
keeps slow filesystem checks out of both pools; no executor is created per poll. Threads start
on demand (so a small card only needs its row count), and the app lifespan shuts the pool down.

Only transcript rows are memoized, by the read model. Neither roots nor file verdicts live here
between calls. Results retain first-touched order, and rows are copied, NEVER annotated in place
on the memoized read model. Unexpected errors propagate in input order, just as in the old
comprehension; queued checks are cancelled on exit. Running checks are read-only and finish
normally. No resolved paths, root lists or exception details are added to the response.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from itertools import islice
from pathlib import Path

from . import servable

#: Independent of card size, request count and CPU count; a 400-row card must not spawn 400
#: workers. Sixteen overlaps network stats without consuming the loop's 32 I/O threads.
FILE_CHECK_WORKERS = 16


def _checked_row(row: dict, run_dir: Path, roots: Sequence[Path]) -> dict:
    return {**row, "servable": servable.servable_rel(str(row.get("path") or ""), run_dir,
                                                     roots, row.get("bases") or [])}


class FileChecks:
    """One pool per app; bounded execution globally and bounded submission per request."""

    def __init__(self, workers: int = FILE_CHECK_WORKERS) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rsched-files")
        self._width = workers

    def rows(self, rows: Sequence[dict], run_dir: Path, roots: Sequence[Path]) -> list[dict]:
        """Apply the unchanged file predicate concurrently, returning rows in input order.

        `Executor.map` submits the WHOLE iterable on Python 3.12. A sliding window instead
        bounds pending futures even for a large transcript. Waiting in input order preserves
        the old ordering of both rows and unexpected exceptions, not completion order.
        """
        source = iter(rows)
        pending: deque[Future[dict]] = deque()
        checked: list[dict] = []
        try:
            for row in islice(source, self._width):
                pending.append(self._pool.submit(_checked_row, row, run_dir, roots))
            while pending:
                checked.append(pending.popleft().result())
                for row in islice(source, 1):
                    pending.append(self._pool.submit(_checked_row, row, run_dir, roots))
            return checked
        finally:
            for future in pending:
                future.cancel()

    def shutdown(self, *, wait: bool = False) -> None:
        """Cancel queued checks at app shutdown without blocking its event loop on network I/O."""
        self._pool.shutdown(wait=wait, cancel_futures=True)
