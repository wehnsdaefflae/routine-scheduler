"""The retry loop's own report seam: one hook `with_retries` calls from INSIDE itself.

F655. Instrumentation wraps an adapter from OUTSIDE (`instrument.InstrumentedEndpoint`) while
the retry loop lives INSIDE it (`base.with_retries`), so the task record saw ONE call however
many attempts it took — worst case three attempts of 600 s behind a single `started` row, which
is how a retrying run became indistinguishable from a frozen one.

Reporting the boundary therefore cannot be done by the wrapper: only the loop knows an attempt
ended. This module is the one-way channel between them. `base` calls `boundary()` and imports
nothing from `instrument`; `instrument` installs a reporter for the duration of one call and
knows nothing about tenacity. Both halves stay testable alone.

Scope, not a global: the reporter is a ContextVar set per call, so parallel subrun threads each
report to their own call's record rather than to whichever one installed a hook last. A thread
that nobody set one in reports nothing, which is also the whole no-sink fast path.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar

#: (attempt that just failed, attempts allowed, the error that ended it, seconds until the next)
Reporter = Callable[[int, int, BaseException, float], None]

_reporter: ContextVar[Reporter | None] = ContextVar("llm_attempt_reporter", default=None)


@contextlib.contextmanager
def reporting(reporter: Reporter) -> Iterator[None]:
    """Install `reporter` for the duration of the block, in this context only."""
    token = _reporter.set(reporter)
    try:
        yield
    finally:
        _reporter.reset(token)


def boundary(*, attempt: int, tries: int, error: BaseException, retry_in: float) -> None:
    """One retry boundary: attempt `attempt` of `tries` ended with `error`, and the loop will
    sleep `retry_in` seconds before the next one.

    Guarded like every other part of this seam — bookkeeping must never break a real LLM call,
    so a reporter that raises is swallowed here rather than inside the retry loop.
    """
    reporter = _reporter.get()
    if reporter is None:
        return
    with contextlib.suppress(Exception):
        reporter(attempt, tries, error, retry_in)
