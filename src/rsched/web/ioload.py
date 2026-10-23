"""The web layer's I/O threads: ONE sized pool, and the share of it live streams may hold.

Every off-loop read in `web/` and `daemon/` goes through `asyncio.to_thread`, which runs on the
EVENT LOOP'S DEFAULT EXECUTOR — a `ThreadPoolExecutor` Python sizes `min(32, cpu_count + 4)` and
nothing in this codebase ever sized. That one pool serves, concurrently and unpartitioned, both
halves of a pairing that does not mix:

- microseconds, and many per second: a run tail's transcript poll and `status.json` read
  (`streams.run_stream`, two reads every `POLL_S`), `paths.read_json`;
- SECONDS, holding their thread the whole time: SSH to omen / predator / ubuntuserver
  (`settings/machines.py`), a provider's credits / quota / login / live-completion probe
  (`settings/endpoint_probe.py`), an `rmtree` of a pruned run, a registry scan, an index refresh.

Release 0.376.0 removed the only thing that had ever bounded the fast half. Under SSE a browser's
~6-connections-per-origin ceiling capped concurrent run tails and `stream.js` rationed them to
three on top of that (`MAX_TAIL_STREAMS`, F263); WebSockets count against a far larger browser
limit, so BOTH caps went at once — and the server never had one. A tail's load is linear in the
number of open sockets (2 jobs per 0.4 s each), so the product of (live sockets) x (jobs per
second) was bounded nowhere: ten tails across a few tabs is 50 file-I/O jobs a second queueing
ahead of an SSH scan that holds its thread for seconds.

So this module owns two numbers and the arithmetic that ties them:

    MAX_LIVE_STREAMS + SLOW_IO_RESERVE <= POOL_WORKERS

`run_stream` awaits its two reads SEQUENTIALLY, so one live tail occupies at most ONE pool thread
at any instant. A ceiling of MAX_LIVE_STREAMS concurrent sockets therefore leaves at least
SLOW_IO_RESERVE threads that stream polls can never be holding. That is the bulkhead, bought by
bounding the PRODUCER rather than by moving the slow callers onto a second pool.

Why not the documented `anyio` explicit limiter instead (AnyIO 4.15, "Working with threads"): a
`CapacityLimiter` binds `anyio.to_thread.run_sync`, NOT `asyncio.to_thread`. The slow callers use
the latter, so handing them their own limiter means first moving them onto anyio's worker threads
— a transport change to two busy files, to reach the same guarantee this ceiling already gives
them. `api_debug.py` reports BOTH pools for the same reason: the anyio limiter it used to report
alone (40 tokens, also never set) is the one every caller above never touches, so an operator
reading `borrowed_tokens` during a stall was reading an idle pool.

The pool is also NAMED (`rsched-io-*`). `/api/debug/threads` exists to name the thread holding the
GIL, and `ThreadPoolExecutor-0_7` names nothing. A loop shuts its own default executor down when
it closes (`asyncio.Runner`, which is how uvicorn runs), so installing one adds no teardown.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

#: Worker threads for every `asyncio.to_thread` call on this loop. EXPLICIT, because asyncio's
#: own `min(32, cpu_count + 4)` makes the arithmetic above depend on the host: 32 threads on the
#: deployment, 8 on a four-core laptop — where eight live tails alone would own the whole pool
#: and the SSH probe would simply wait. A number that moves with the machine is not a bound.
POOL_WORKERS = 32

#: Threads a live-stream poll may never be holding, kept for the callers that take SECONDS: the
#: three build machines' SSH, a provider probe, an `rmtree`, a registry scan, an index refresh.
#: Six of those callers exist and the slowest (a live completion) can run for tens of seconds.
SLOW_IO_RESERVE = 8

#: The server's ceiling on concurrent live-stream sockets — the bound 0.376.0 removed from the
#: client with nothing put in its place. Derived, never typed independently, so the invariant
#: above cannot be crossed by editing one number. A refused socket is TOLD so
#: (`streams.CLOSE_BUSY`), and `static/stream.js` already backs off with jitter and catches up
#: over REST, so a waiting view stays correct rather than going blank.
#:
#: The GLOBAL BUS socket takes a slot too, though it polls nothing and costs no pool thread
#: (`bus_stream` awaits a queue). That is deliberately CONSERVATIVE rather than precise: every
#: slot a non-polling socket holds is one fewer tail, so the pool invariant above still holds
#: — and it bounds the other per-socket cost, since each publish fans out to every subscriber
#: on the loop. Admission cannot tell the two apart without a second ceiling to size, and one
#: number an operator can read beats two that have to be reconciled.
MAX_LIVE_STREAMS = POOL_WORKERS - SLOW_IO_RESERVE


class BusyError(RuntimeError):
    """The server is at its ceiling of live stream sockets, so this one is refused.

    The message is the one the refused client is TOLD (`streams.serve` sends it as the close
    reason), so it is written once, here, rather than once per raise site: a refusal that says
    only "busy" sends an operator to the logs for something the socket could have said.
    """

    def __init__(self, ceiling: int) -> None:
        super().__init__(f"the server is at its ceiling of {ceiling} live stream sockets — "
                         f"this one will reconnect")
        self.ceiling = ceiling


class StreamGate:
    """The live-socket counter: `hold()` admits one socket for as long as its block runs.

    One instance per app (`app.state.stream_gate`, built in `web/app.py` beside the stream-ticket
    store). The check-and-occupy is LOCKED, cheaply and unconditionally: a websocket handler is a
    coroutine on the one loop, but `app.state` is reached by sync handlers on pool threads too,
    and `self.live += 1` is a read-modify-write that spans bytecodes — the stream-ticket store
    beside it (`app.py`) carries a comment about exactly that race, found in production. A
    ceiling that can be overshot under load is not a ceiling.

    `refused` is kept because a bound nobody can see is indistinguishable from a bug: it is what
    `/api/debug/threads` reports when an operator asks why a tab's tail will not open.
    """

    def __init__(self, ceiling: int = MAX_LIVE_STREAMS) -> None:
        self.ceiling = ceiling
        self.live = 0
        self.refused = 0
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def hold(self) -> Iterator[None]:
        """Occupy one slot, or raise `BusyError` without occupying anything."""
        with self._lock:
            if self.live >= self.ceiling:
                self.refused += 1
                raise BusyError(self.ceiling)
            self.live += 1
        try:
            yield
        finally:
            with self._lock:
                self.live -= 1

    def stats(self) -> dict:
        with self._lock:
            return {"live": self.live, "ceiling": self.ceiling, "refused": self.refused}


def attach_state(app) -> None:
    """Give `app` its live-stream gate and an empty I/O-pool slot (the lifespan fills the slot —
    only it has a running loop). One call rather than two assignments in `create_app`, so the
    two pieces of state this module owns are wired where its numbers are explained.
    """
    app.state.stream_gate = StreamGate()
    app.state.io_pool = None


def install_pool(loop: asyncio.AbstractEventLoop | None = None,
                 workers: int = POOL_WORKERS) -> ThreadPoolExecutor:
    """Make every `asyncio.to_thread` on this loop run on a NAMED pool of exactly `workers`
    threads, and return it (the caller keeps it on `app.state.io_pool` so `pool_stats` can
    report it without reaching into the loop's privates).
    """
    loop = loop or asyncio.get_running_loop()
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rsched-io")
    loop.set_default_executor(pool)
    return pool


def pool_stats(pool: ThreadPoolExecutor | None) -> dict:
    """What tells "the I/O is slow" from "the I/O is QUEUED": the pool's size, how many of its
    threads have been spawned, and how many jobs are waiting for a thread right now. A non-zero
    `queued` under a high `live` stream count is this item's failure mode, visible.
    """
    if pool is None:   # no lifespan ran (a bare create_app in a test): asyncio's own default
        return {"installed": False, "workers": None, "threads": None, "queued": None}
    return {"installed": True, "workers": pool._max_workers,
            "threads": len(pool._threads), "queued": pool._work_queue.qsize()}
