"""The ONE writer of the util-stats snapshot: a debounced reaction to run events.

`readmodels/util_stats.write_util_stats_snapshot` is expensive — one `git log --name-only`
walk of the library, a parse of the whole workflow-usage stream, and a scan of every retained
transcript the stream has not counted yet — and it produces a file NO engine ever reads: the
Stats tab computes `util_stats()` live per request, and the only reader of the snapshot is a
routine's own `util-stats` util (which is why it lives under XDG_STATE_HOME; see
`util_stats.snapshot_path`). It used to be called by `engine/runtime.py` at the end of EVERY
root run, so every run in the fleet paid for a read model it had no use for, and a burst of
finishes paid for it once each. One process owns it now.

Shaped like the limits refresh (`daemon/scheduler._refresh_limits`) and the Web Push listener
(`web/push.bus_listener`), whose two lessons it inherits:

- **Off the loop.** The pass is a git subprocess plus file I/O — seconds, not milliseconds. On
  the event loop it would hold every fire, every live stream and every request behind it, so it
  runs in a thread (`asyncio.to_thread`).
- **One pass at a time, debounced.** This loop is sequential: it awaits its own pass before
  taking the next event, so no two passes can ever overlap (what `_off_tick` enforces for the
  refreshes that are STARTED from a tick). A burst — twenty runs finishing within a minute —
  coalesces into one pass: wait for `_QUIET_S` of silence, but never longer than
  `_MAX_COALESCE_S`, because a busy fleet never goes quiet.
- **Never fatal.** The daemon is long-lived. A telemetry pass that raises must not end the
  subscriber and freeze the snapshot for the rest of the process's life, so each pass is
  guarded and leaves a breadcrumb (the only thing that made F97 diagnosable).

Only `run_*` events wake it. Every LLM call of every live run is also a bus event
(`llm_task`, `llm_process`), and recomputing on those would be dramatically worse than the
per-run write this replaces. The test is the event NAME's prefix rather than a hand-kept tuple,
so a new run event joins the family without an edit here.

One behaviour changes with the move: a run started OUTSIDE the daemon (`rsched run-once`)
publishes no bus event, so it no longer refreshes the snapshot by itself — the next
daemon-fired run does. The Stats tab is unaffected (it never reads the file).
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("rsched.util_stats_watch")

#: Silence that ends a burst, and the longest a pass may be deferred while events keep coming.
#: Wider than Web Push's (2 s / 10 s) on purpose: nobody is waiting on this file, and the pass
#: is orders of magnitude dearer than a decisions diff.
_QUIET_S = 5.0
_MAX_COALESCE_S = 60.0


def _is_run_event(event: object) -> bool:
    return (isinstance(event, dict)
            and str(event.get("event") or "").startswith("run_"))


async def _pass(server) -> None:
    """One snapshot pass, in a thread, never fatal."""
    # Imported per pass, and the function is reached through its MODULE, so the compute the
    # readmodels package owns stays patchable (and this module imports no read model at boot).
    from ..readmodels import util_stats

    try:
        await asyncio.to_thread(util_stats.write_util_stats_snapshot, server)
    except Exception as exc:
        # write_util_stats_snapshot is already best-effort internally; this catches what it
        # cannot (a thread that could not be started, a server object it could not read) so
        # the listener survives it.
        log.warning("util-stats snapshot pass failed: %s", exc, exc_info=True)


async def bus_listener(server, bus) -> None:
    """Daemon-side subscriber: recompute the util-stats snapshot once per burst of run
    events. Runs for the daemon's lifetime (started in `web/appwiring.py`'s lifespan).
    """
    loop = asyncio.get_running_loop()
    with bus.subscribe() as q:
        while True:
            if not _is_run_event(await q.get()):
                continue
            deadline = loop.time() + _MAX_COALESCE_S
            while (left := deadline - loop.time()) > 0:
                try:
                    await asyncio.wait_for(q.get(), timeout=min(_QUIET_S, left))
                except TimeoutError:
                    break
            await _pass(server)
