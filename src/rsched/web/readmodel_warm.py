"""Recompute the two expensive read models off the request, after the events that move them.

Both are memoized on their sources (`readmodels/memo`), so a request pays only a fingerprint
check while nothing moved — and the WHOLE recompute when something did. Timed cold against
the live store:

- `change_effects.changes` — 4.4 s. A fleet-wide fold over every run's signals, invalidated
  whenever any run lands in the usage stream; `/api/changes/{slug}/summary` (the production
  routine page's one development line) computes all of it to report one routine, so the first
  routine page opened after a run finished took 4.3 s for a 131-byte answer.
- `decisions_read.open_decisions` — 1.1 s. The Decisions catalog every open tab polls every 3 s
  (`static/questions-store.js`), invalidated by every question, answer and run change.

So the daemon pays both, in a thread, right after the events that invalidate them, and the
request finds them warm. The request path still computes on a miss — this only moves the usual
miss off it, so a model that is never warmed (a run started outside the daemon publishes no
event) is merely slower, never stale.

Shaped like `daemon/util_stats_watch`, whose lessons it inherits: off the loop, one pass at a
time (the loop awaits its own pass), a burst coalesced, never fatal. The debounce is shorter
than that one's because a person is waiting on these.

THE BOOT PASS IS ORDERED, because the first version made the restart burst worse. Every memo is
empty in a new process, so a boot pass is right — but the first one ran at once, beside the
scheduler's boot scans and every reconnecting console tab, and `/api/questions`
took 25-29 s in that window (16-18 s at the previous restart): the tabs' requests waited on a
Decisions compute starved by the fleet-wide fold beside it, which spawns a `git` per change. So
the boot pass waits for the scheduler's own boot (`Scheduler.booted`, at most
`BOOT_WAIT_MAX_S`), warms the Decisions catalog first — every open tab is waiting on it — and
leaves the fold until `BOOT_SETTLE_S` later, when the reconnect burst is over. Every pass
computes Decisions before the fold for the same reason.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

log = logging.getLogger("rsched.web")

#: Silence that ends a burst of run events, and the longest a pass waits while they keep coming.
_QUIET_S = 2.0
_MAX_COALESCE_S = 15.0

#: The longest the boot pass waits for the scheduler's boot; past it, it warms anyway.
BOOT_WAIT_MAX_S = 120.0

#: How long after the boot pass the fleet-wide fold is first computed — past the reconnect burst.
BOOT_SETTLE_S = 60.0


#: The events that move them: a run's (its status, its usage row, its questions) and a
#: question's own (`question_asked`/`question_answered`). Never `llm_*`, which every model call
#: of every live run publishes several times a second.
_PREFIXES = ("run_", "question_")


def _moves_a_read_model(event: object) -> bool:
    return isinstance(event, dict) and str(event.get("event") or "").startswith(_PREFIXES)


def warm(server, *, decisions: bool = True, effects: bool = True) -> None:
    """One pass, Decisions first: recompute what moved, in the caller's thread. A model that
    raises is skipped — the request path will compute it itself, and report what it raises there.
    """
    from ..readmodels import change_effects
    from . import decisions_read

    steps: list[tuple[str, Callable[[], object]]] = []
    if decisions:
        steps.append(("decisions", lambda: decisions_read.open_decisions(server)))
    if effects:
        steps.append(("change effects", lambda: change_effects.changes(server.routines_home)))
    for name, compute in steps:
        try:
            compute()
        except Exception as exc:
            log.warning("read-model warm: %s raised: %s", name, exc, exc_info=True)


async def bus_listener(server, bus, ready: asyncio.Event | None = None) -> None:
    """The ordered boot pass (module docstring), then one pass per burst of run or question
    events, for the daemon's lifetime (started in `web/appwiring.py`'s lifespan with the
    scheduler's `booted` as `ready`).
    """
    loop = asyncio.get_running_loop()
    with bus.subscribe() as q:
        if ready is not None:
            try:
                await asyncio.wait_for(ready.wait(), timeout=BOOT_WAIT_MAX_S)
            except TimeoutError:
                log.warning("read-model warm: the scheduler's boot did not finish in %.0f s; "
                            "warming anyway", BOOT_WAIT_MAX_S)
        await asyncio.to_thread(warm, server, effects=False)
        fold_at: float | None = loop.time() + BOOT_SETTLE_S     # the boot pass's second half
        while True:
            # due by the clock, never by an empty queue: a busy fleet never lets the queue empty
            if fold_at is not None and loop.time() >= fold_at:
                await asyncio.to_thread(warm, server, decisions=False)
                fold_at = None
            wait = None if fold_at is None else fold_at - loop.time()
            try:
                event = await asyncio.wait_for(q.get(), timeout=wait)
            except TimeoutError:
                continue
            if not _moves_a_read_model(event):
                continue
            deadline = loop.time() + _MAX_COALESCE_S
            while (left := deadline - loop.time()) > 0:
                try:
                    await asyncio.wait_for(q.get(), timeout=min(_QUIET_S, left))
                except TimeoutError:
                    break
            await asyncio.to_thread(warm, server, effects=fold_at is None)
