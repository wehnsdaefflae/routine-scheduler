"""What the daemon is doing RIGHT NOW — the operator's stack sample without ptrace.

On 2026-09-12 every sync API handler took 20 to 50 seconds while five runs were active
(`/api/lanes`, which reads one small JSON file, took 27 s), one worker thread sat at 70% CPU,
and nothing could name it: the engine container has no `ps`, py-spy cannot ptrace a process it
did not start, and every read model measured in a fresh process came back in well under a
second. The daemon looked slow from outside and had nothing to say about why from inside.

Two views, both read-only and OPERATOR-token only — `app.ROUTINE_TOKEN_DENIED_READS` holds
this whole prefix, beside the other reads that would hand a run what its sandbox forbids (a
stack is the daemon's own business, and a run has no use for it):

- `GET /api/debug/threads` — every Python thread with its name and its top frames
  (`sys._current_frames()`), the threadpool's token accounting (how many of the sync-handler
  workers are borrowed), and the requests in flight. Taken while it is slow, this names the
  thread that holds the GIL and the handler that is waiting.
- `GET /api/debug/slow` — the last `SLOW_KEEP` requests that exceeded `SLOW_REQUEST_S`,
  recorded by the timing middleware in `app.py` (method, path, seconds, in-flight count at
  the time). Survives nobody watching: the evidence is there the morning after.

Nothing here writes, spawns, or mutates state; the only cost is the frame walk itself.
"""

from __future__ import annotations

import sys
import threading
import traceback

from fastapi import APIRouter, Request

router = APIRouter(tags=["debug"])

FRAMES_PER_THREAD = 12


def thread_stacks(limit: int = FRAMES_PER_THREAD) -> list[dict]:
    """Every live thread: name, ident, daemon flag, and its innermost `limit` frames as
    `file:line function` strings, innermost LAST (the way a traceback reads).
    """
    names = {t.ident: (t.name, t.daemon) for t in threading.enumerate()}
    out = []
    for ident, frame in sys._current_frames().items():   # sanctioned: a live stack sample
        name, daemon = names.get(ident, ("?", False))
        frames = traceback.extract_stack(frame)[-limit:]
        out.append({"ident": ident, "name": name, "daemon": daemon,
                    "frames": [f"{fr.filename}:{fr.lineno} {fr.name}" for fr in frames]})
    return sorted(out, key=lambda t: t["name"])


def _limiter() -> dict:
    """The anyio worker-thread limiter: the pool a SYNC def handler runs on (starlette's
    `run_in_threadpool`), 40 tokens by default and never set here.

    It is NOT the pool this process does most of its I/O on, and reading it alone during the
    2026-09-12 stall was reading a nearly idle pool: every `asyncio.to_thread` call — the run
    tails' transcript polls, the machines' SSH, the endpoint probes, every `rmtree` and registry
    scan — goes to the loop's DEFAULT EXECUTOR instead, which `io_pool` reports. Both are here
    because an operator cannot tell which one is saturated without seeing both.
    """
    try:
        import anyio
        lim = anyio.to_thread.current_default_thread_limiter()
        return {"total_tokens": lim.total_tokens, "borrowed_tokens": lim.borrowed_tokens}
    except Exception as exc:   # a diagnostic never fails the diagnostic
        return {"error": f"{type(exc).__name__}: {exc}"}


@router.get("/debug/threads")
async def debug_threads(request: Request) -> dict:
    """Thread stacks plus the three numbers that tell "slow" from "QUEUED": the anyio limiter,
    the named I/O pool every `asyncio.to_thread` runs on (`workers` / `threads` / `queued`),
    and the live-stream gate (`live` / `ceiling` / `refused`) whose ceiling is derived from the
    pool size in `web/ioload.py`. A non-zero `io_pool.queued` while `streams.live` sits at its
    ceiling is this pool's contention, named.
    """
    from .ioload import pool_stats

    state = request.app.state
    gate = getattr(state, "stream_gate", None)
    return {"in_flight": int(getattr(state, "in_flight", 0)),
            "threadpool": _limiter(),
            "io_pool": pool_stats(getattr(state, "io_pool", None)),
            "streams": gate.stats() if gate is not None else None,
            "threads": thread_stacks()}


@router.get("/debug/slow")
async def debug_slow(request: Request) -> dict:
    return {"slow_request_s": request.app.state.slow_request_s,
            "requests": list(getattr(request.app.state, "slow_requests", []))}
