"""Where a slow request's seconds actually went: before the handler, in it, or after it.

F906. The console's residual multi-second waits are recorded but not ATTRIBUTED. The
self-sample (`web/slowsample.py`) names the frame a worker was in, which answers "where was
the thread" and not "how was the whole wait spent"; and the handlers themselves have been
measured out of the running. `/api/health/blocked` is the worst offender in the newest fold —
834 server samples, median 2,780 ms, and still 2,550 ms over the 395 where it was the SOLE
request in flight — while its whole server-side pipeline runs in about 30 ms warm, measured in
process against the live stream. Two orders of magnitude are therefore spent somewhere no
existing instrument looks, and fleet-wide the same shape holds: 512 requests with nothing else
in flight had a 2,700 ms median, which no handler's own work explains.

So this splits one request's elapsed time into three spans that cannot be confused:

- `pre_ms` — from the moment the ASGI server hands the scope over, to the moment the innermost
  middleware layer is reached. Event-loop scheduling, the thread-pool handoff and every
  middleware above the application sit in here.
- `app_ms` — the application's own span: routing, dependencies, the handler body, and the
  response object being built.
- `send_ms` — from the handler returning to the LAST response byte leaving. Serialization,
  compression and the write to the client sit in here. `first_byte_ms` splits it further, so a
  slow write is distinguishable from a slow encode.

One number decides the open question. A large `pre_ms` means the request was QUEUED before
anything of ours ran, and the cause is the loop or the pool; a large `send_ms` means the work
happened AFTER the handler returned, and the cause is a middleware or the response path. The
existing self-sample cannot tell those apart, which is why four runs of evidence have not
closed this.

WHY TWO LAYERS AND WHY PURE ASGI. A `BaseHTTPMiddleware` (what `@app.middleware("http")` adds)
re-emits the response as a stream and never sees the raw `send` events, so it can measure
neither the first byte nor the last. And one layer cannot measure `pre_ms` at all: the span
being measured is the time taken to REACH the measuring code. So the outer layer stamps arrival
and watches the send events, the inner layer stamps the moment the chain bottoms out, and the
difference between the two stamps is the queueing nobody could see.

WHAT IT COSTS, because it runs on every request in the process: two `time.monotonic()` calls
and a dict mutation on the hot path, and the row is assembled ONLY for a request that was
already slow enough to be recorded. Nothing here can raise: a timing instrument that can fail a
request is worse than no timing instrument, so both layers pass the call through on any
exception of their own and the phases are simply absent from the row.

NO CREDENTIALS, NO BODIES, NO QUERY STRINGS. The row carries the method, the path, and numbers
(F906's own constraint: credential-free telemetry). A path is already what the slow row carries.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

log = logging.getLogger("rsched.web.reqphases")

#: The scope key the two layers share. A dict already travels with every ASGI scope, so the
#: stamps need no context var, no task-local and no lock — and a scope the outer layer never
#: touched (a websocket, a lifespan message) simply has no entry.
KEY = "rsched_phases"


def mark(scope: dict, name: str) -> None:
    """Stamp `name` on this scope, if it is carrying a phase record. Never raises."""
    try:
        phases = scope.get(KEY)
        if phases is not None:
            phases[name] = time.monotonic()
    except Exception:   # deliberately broad: a timing stamp may never fail a request
        log.debug("phase stamp %s failed", name, exc_info=True)


def phases_of(scope: dict) -> dict[str, float] | None:
    """The three spans in MILLISECONDS for a finished request, or None when untimed.

    Every span is rounded to one decimal: the numbers are compared against medians in the
    thousands of milliseconds, so more precision would only make the rows harder to read.
    """
    stamps = (scope or {}).get(KEY)
    if not isinstance(stamps, dict) or "entry" not in stamps:
        return None
    entry, app_in = stamps.get("entry"), stamps.get("app_in")
    app_out, first, last = stamps.get("app_out"), stamps.get("first_byte"), stamps.get("last_byte")
    out: dict[str, float] = {}
    if app_in is not None:
        out["pre_ms"] = round((app_in - entry) * 1000, 1)
        # The row is composed by a `BaseHTTPMiddleware`, whose `call_next` returns at
        # `http.response.start` — so no real request has an `app_out` stamp yet when its row is
        # built, and the app's span must be read to the FIRST BYTE instead. Without this branch
        # `app_ms` is absent on every row, `pre_ms` is the only known span, and the whole wait
        # is attributed to the one span that can be proven not to hold it.
        if app_out is not None:
            out["app_ms"] = round((app_out - app_in) * 1000, 1)
        elif first is not None:
            out["app_ms"] = round((first - app_in) * 1000, 1)
    end = last if last is not None else first
    if app_out is not None and end is not None:
        out["send_ms"] = round((end - app_out) * 1000, 1)
    if first is not None:
        out["first_byte_ms"] = round((first - entry) * 1000, 1)
    if end is not None:
        out["total_ms"] = round((end - entry) * 1000, 1)
    return out or None


#: How much of the measured wait the named span must actually hold. A span that explains almost
#: none of it names the wrong cause with the same confidence as the right one, and a reader has
#: no way to tell the two rows apart — so below this fraction the row says nothing instead.
ATTRIBUTION_FLOOR = 0.5


def dominant(phases: dict[str, float] | None, elapsed_ms: float | None = None) -> str:
    """Which span holds most of the wait: `pre`, `app`, `send` — or "" when it is not known.

    This is the one word a reader needs, and it is computed here rather than in the eye of
    whoever reads the row: three numbers in a log line get averaged by a human into whatever
    they already believed.

    `elapsed_ms` is the wait as the caller measured it, and it is what keeps the word honest.
    The spans are stamped by layers that do not bracket the whole request, so their sum can be
    a rounding error beside the elapsed time — and the largest of three tiny spans is still the
    largest. Naming it would attribute multi-second stalls to a tenth of a millisecond.
    """
    if not phases:
        return ""
    spans = {name: phases.get(f"{name}_ms") for name in ("pre", "app", "send")}
    known = {name: value for name, value in spans.items() if value is not None}
    if not known:
        return ""
    best = max(known, key=lambda name: known[name])
    if elapsed_ms and known[best] < ATTRIBUTION_FLOOR * elapsed_ms:
        return ""
    return best


class PhaseOuter:
    """The OUTERMOST layer: stamps arrival, then every send event, by wrapping `send`.

    It must be added LAST (`add_middleware` puts each new layer outside the previous), so that
    its arrival stamp precedes every other layer in the process.
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            # A websocket or lifespan scope has no request phases, and the live streams are
            # websockets — the same reason the timing middleware cannot count one.
            await self.app(scope, receive, send)
            return
        scope[KEY] = {"entry": time.monotonic()}

        async def stamping_send(message: dict) -> None:
            kind = message.get("type")
            if kind == "http.response.start":
                mark(scope, "first_byte")
            elif kind == "http.response.body" and not message.get("more_body"):
                mark(scope, "last_byte")
            await send(message)

        await self.app(scope, receive, stamping_send)


class PhaseInner:
    """The INNERMOST layer: stamps where the middleware chain bottoms out, and where it returns.

    Added FIRST, so every other middleware sits outside it and the span between the two stamps
    is exactly "everything above the application".
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        mark(scope, "app_in")
        try:
            await self.app(scope, receive, send)
        finally:
            mark(scope, "app_out")
