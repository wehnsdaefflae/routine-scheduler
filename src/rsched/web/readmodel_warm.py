"""Recompute the two expensive read models off the request, after the events that move them.

Both are memoized on their sources (`readmodels/memo`), so a request pays only a fingerprint
check while nothing moved — and the WHOLE recompute when something did. Timed cold on
2026-11-21 against the live store:

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
than that one's because a person is waiting on these. It also passes once at boot: every memo
starts empty in a new process, and the first console load after a restart was the slowest.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("rsched.web")

#: Silence that ends a burst of run events, and the longest a pass waits while they keep coming.
_QUIET_S = 2.0
_MAX_COALESCE_S = 15.0


#: The events that move them: a run's (its status, its usage row, its questions) and a
#: question's own (`question_asked`/`question_answered`). Never `llm_*`, which every model call
#: of every live run publishes several times a second.
_PREFIXES = ("run_", "question_")


def _moves_a_read_model(event: object) -> bool:
    return isinstance(event, dict) and str(event.get("event") or "").startswith(_PREFIXES)


def warm(server) -> None:
    """One pass: recompute what moved, in the caller's thread. A model that raises is skipped —
    the request path will compute it itself, and report whatever it raises there.
    """
    from ..readmodels import change_effects
    from . import decisions_read

    for name, compute in (("change effects", lambda: change_effects.changes(server.routines_home)),
                          ("decisions", lambda: decisions_read.open_decisions(server))):
        try:
            compute()
        except Exception as exc:
            log.warning("read-model warm: %s raised: %s", name, exc, exc_info=True)


async def bus_listener(server, bus) -> None:
    """Pass once now, then once per burst of run or question events, for the daemon's lifetime
    (started in `web/appwiring.py`'s lifespan).
    """
    loop = asyncio.get_running_loop()
    with bus.subscribe() as q:
        await asyncio.to_thread(warm, server)
        while True:
            if not _moves_a_read_model(await q.get()):
                continue
            deadline = loop.time() + _MAX_COALESCE_S
            while (left := deadline - loop.time()) > 0:
                try:
                    await asyncio.wait_for(q.get(), timeout=min(_QUIET_S, left))
                except TimeoutError:
                    break
            await asyncio.to_thread(warm, server)
