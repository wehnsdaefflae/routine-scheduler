"""The web layer's I/O threads and the live-stream ceiling derived from them (web/ioload.py).

The point of these tests is the BOUND, not the constants: the pool is installed and then shown
to queue a job it has no thread for, and the gate is filled and then shown to refuse. Both are
driven by events, never by sleeping against a real clock.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading

import pytest
from starlette.websockets import WebSocketDisconnect

from rsched.web import ioload

# --------------------------------------------------------------- the arithmetic that ties them


def test_the_ceiling_reserves_threads_no_stream_poll_can_ever_hold():
    """MAX_LIVE_STREAMS is DERIVED, so the invariant cannot be broken by editing one number.

    `run_stream` awaits its two reads sequentially, so one live tail occupies at most one pool
    thread at an instant; a ceiling of MAX_LIVE_STREAMS therefore leaves SLOW_IO_RESERVE threads
    the SSH / provider-probe / rmtree callers can always get. This asserts the relation, not a
    literal: the two tests below are what demonstrate that either number binds anything.
    """
    assert ioload.MAX_LIVE_STREAMS == ioload.POOL_WORKERS - ioload.SLOW_IO_RESERVE
    assert ioload.MAX_LIVE_STREAMS + ioload.SLOW_IO_RESERVE <= ioload.POOL_WORKERS
    assert ioload.SLOW_IO_RESERVE >= 6     # the six slow call sites named in the module docstring
    assert ioload.MAX_LIVE_STREAMS > 0     # a ceiling that admits nothing is not a bound


# --------------------------------------------------------------- the pool really is the bound


async def test_the_installed_pool_is_what_to_thread_runs_on_and_queues_past_its_size():
    """`install_pool` makes `asyncio.to_thread` use OUR named pool, and the size it is given
    actually limits concurrency: with two workers, two jobs run and a third WAITS in the queue.

    This is the half a constant cannot prove, and nothing here waits on a clock: three jobs are
    submitted, each blocks on an Event, and the job itself signals back onto the loop when it
    gets a thread. The test then observes that only TWO ever did while the third sat in the
    pool's queue — which is what "the number binds concurrency" means.
    """
    loop = asyncio.get_running_loop()
    # restored by assignment, not set_default_executor: that setter raises TypeError on None,
    # which is what a loop that has not run a to_thread call yet holds
    previous = loop._default_executor
    pool = ioload.install_pool(loop, workers=2)
    release = threading.Event()
    names: list[str] = []
    two_running = asyncio.Event()

    def note_start() -> None:                  # on the loop thread
        if len(names) >= 2:
            two_running.set()

    def job() -> None:                         # on a pool thread
        names.append(threading.current_thread().name)
        loop.call_soon_threadsafe(note_start)
        release.wait(5)

    try:
        tasks = [asyncio.ensure_future(asyncio.to_thread(job)) for _ in range(3)]
        # Awaiting here is also what lets the three to_thread coroutines run and submit; the
        # loop drains that whole batch before it processes a call_soon_threadsafe callback,
        # so all three jobs are in the pool by the time this returns.
        await asyncio.wait_for(two_running.wait(), 5)
        assert len(names) == 2                 # two got a thread; the third did NOT
        assert ioload.pool_stats(pool) == {"installed": True, "workers": 2,
                                           "threads": 2, "queued": 1}
        release.set()                          # free the two, and the queued job runs
        await asyncio.wait_for(asyncio.gather(*tasks), 5)
        # the queued job got a thread once one came free — the bound delays, never drops
        assert len(names) == 3
        assert ioload.pool_stats(pool)["queued"] == 0
    finally:
        release.set()
        loop._default_executor = previous
        # NOT asserted after this point: shutdown() puts one None wake-up sentinel per idle
        # thread into the same work queue, so qsize() is non-zero afterwards for reasons that
        # have nothing to do with our jobs
        pool.shutdown(wait=True)

    # every job ran on the named pool — this is what makes /api/debug/threads able to say which
    # thread is holding the GIL ("rsched-io_0"), where "ThreadPoolExecutor-3_1" named nothing
    assert len(names) == 3
    assert all(n.startswith("rsched-io") for n in names), names


def test_pool_stats_reports_the_absence_of_an_installed_pool():
    """A bare `create_app` (every test that never enters the lifespan) runs on asyncio's own
    default executor. The diagnostic says so rather than inventing numbers for it."""
    assert ioload.pool_stats(None) == {"installed": False, "workers": None,
                                       "threads": None, "queued": None}


# --------------------------------------------------------------- the gate really refuses


def test_the_gate_admits_exactly_its_ceiling_then_refuses():
    """Fill the gate and the next acquire RAISES — it does not queue silently or overshoot.
    Every slot is counted, and the refusal is counted too (an invisible bound reads as a bug).
    """
    gate = ioload.StreamGate(ceiling=3)
    with contextlib.ExitStack() as stack:
        for expected in range(1, 4):
            stack.enter_context(gate.hold())
            assert gate.live == expected
        with pytest.raises(ioload.BusyError) as exc:
            stack.enter_context(gate.hold())
        assert exc.value.ceiling == 3
        assert "3 live stream sockets" in str(exc.value)
        assert gate.live == 3           # a refused socket occupies nothing
        assert gate.stats() == {"live": 3, "ceiling": 3, "refused": 1}
    assert gate.stats() == {"live": 0, "ceiling": 3, "refused": 1}


def test_a_released_slot_is_reusable_and_an_error_inside_releases_it():
    """The slot is held for the socket's LIFE and released however it ends — a stream that
    raises must not leak its slot, or the ceiling would ratchet down to zero over a day of
    daemon restarts and dropped tabs."""
    gate = ioload.StreamGate(ceiling=1)
    with gate.hold(), pytest.raises(ioload.BusyError), gate.hold():
        pass
    with gate.hold():                   # the first slot came back
        pass
    with pytest.raises(ValueError), gate.hold():
        raise ValueError("the stream failed")
    assert gate.live == 0
    with gate.hold():                   # still usable after a failure inside the block
        assert gate.live == 1
    assert gate.stats() == {"live": 0, "ceiling": 1, "refused": 1}


def test_the_default_ceiling_is_the_derived_one():
    assert ioload.StreamGate().ceiling == ioload.MAX_LIVE_STREAMS


# --------------------------------------------------------------- the ceiling at the real seam


def test_a_live_socket_holds_a_slot_for_its_whole_life(api_client):
    """Through the app: an open live stream OCCUPIES one of the server's slots the whole time
    it is open, and gives it back when it closes. That is what makes the ceiling below a bound
    on CONCURRENT sockets rather than on connections per minute."""
    c, _ = api_client
    gate = ioload.StreamGate(ceiling=2)
    c.app.state.stream_gate = gate
    ticket = c.post("/api/stream-ticket").json()["ticket"]

    with c.websocket_connect(f"/api/events?ticket={ticket}"):
        assert gate.stats() == {"live": 1, "ceiling": 2, "refused": 0}
    assert gate.stats() == {"live": 0, "ceiling": 2, "refused": 0}


def test_a_socket_past_the_ceiling_is_closed_busy_and_the_slot_is_reusable(api_client):
    """Through the app: at the ceiling, a correctly ticketed socket is admitted and then closed
    CLOSE_BUSY with an honest reason — and once a slot frees, the next socket is served.

    This is the behaviour 0.376.0 left out, asserted where it happens rather than by reading a
    constant. The ceiling is reached by HOLDING a slot the way a live socket does (one TestClient
    websocket at a time: two nested sessions share one portal and tear each other down), so what
    the socket meets is the real `serve` path with the gate full.
    """
    from rsched.web import streams

    c, _ = api_client
    gate = ioload.StreamGate(ceiling=1)
    c.app.state.stream_gate = gate

    def ticket() -> str:
        return c.post("/api/stream-ticket").json()["ticket"]

    with gate.hold():                       # the one slot is taken, as by a live tail
        with (c.websocket_connect(f"/api/events?ticket={ticket()}") as ws,
              pytest.raises(WebSocketDisconnect) as exc):
            ws.receive_json()
        assert exc.value.code == streams.CLOSE_BUSY == 4429
        # the refusal is HONEST about what happened and what the client will do about it
        assert exc.value.reason == ("the server is at its ceiling of 1 live stream sockets — "
                                    "this one will reconnect")
        assert gate.live == 1               # the refused socket occupied nothing
    assert gate.stats() == {"live": 0, "ceiling": 1, "refused": 1}

    # the freed slot is reusable: the next socket is served, not refused
    with c.websocket_connect(f"/api/events?ticket={ticket()}"):
        assert gate.live == 1
    assert gate.stats() == {"live": 0, "ceiling": 1, "refused": 1}


def test_debug_threads_reports_the_io_pool_and_the_stream_gate(api_client):
    """An operator must be able to SEE the bound: /api/debug/threads carries the gate's
    live/ceiling/refused beside the pool that the ceiling is derived from, and still carries
    anyio's limiter (the pool `run_in_threadpool` handlers use, which is a different one)."""
    c, _ = api_client
    c.app.state.stream_gate = ioload.StreamGate(ceiling=4)
    body = c.get("/api/debug/threads").json()
    assert body["streams"] == {"live": 0, "ceiling": 4, "refused": 0}
    assert set(body["io_pool"]) == {"installed", "workers", "threads", "queued"}
    assert body["threadpool"]["total_tokens"] >= 1       # the anyio limiter, still reported
    # TestClient enters the lifespan, so the named pool is installed and sized explicitly
    assert body["io_pool"] == {"installed": True, "workers": ioload.POOL_WORKERS,
                               "threads": body["io_pool"]["threads"],
                               "queued": body["io_pool"]["queued"]}
    assert body["io_pool"]["queued"] == 0
