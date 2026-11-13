"""The request timing middleware, and the daemon's SELF-SAMPLE while a request is still slow.

F871. The console's 10-30 second waits are a BLOCKED EVENT LOOP, not slow handlers: on
2026-11-10 `/api/llm-tasks` — an `async def` that returns an in-memory snapshot and does no I/O
at all — recorded a median 10,067 ms and a worst of 34,396 ms, and unrelated endpoints finished
in the SAME SECOND to within 20 ms (18:07:15 `/api/desktops` 34,409 ms beside `/api/llm-tasks`
34,396 ms; 18:09:32 31,613 / 31,603; 18:11:21 30,443 / 30,425). A handler that does nothing
cannot be slow by itself, and three pairs of unrelated handlers do not agree to the millisecond
by coincidence: something held the process while both waited.

The case the existing evidence CANNOT explain is the quiet one. 33 of 79 server-side slow rows
were recorded with `in_flight = 0` — no other HTTP request being served — and still took a
median 2,700 ms (worst 6,900). Twenty-four of those were `/api/health/blocked`, across 14
different hours, and that endpoint's handler is provably not the cost: `blocked_fleet` measures
18.6 ms cold and 1.4 ms warm against the live store, `operator_blocks.annotate` adds 0.0 ms, and
the one slow dependency (`backup_watch.staleness`, 400 ms) is called once per request by
construction — so ~2.59 s of a 2.61 s request was spent neither in the handler nor behind
another HTTP request. `in_flight` cannot narrow it further by construction: it is counted by an
http middleware, and an http middleware never sees a websocket scope, so a row at 0 REFUTES HTTP
queueing for that request and says nothing about open streams, engine work or pytest workers.

Hence this module. `/api/debug/threads` has always been able to name the holder — both pools, as
`api_debug` insists, since reading anyio's limiter alone during the 2026-09-12 stall was reading
a nearly idle pool while the loop's DEFAULT EXECUTOR was saturated — but it only answers while
an operator is awake, looking, and already inside the stall. Nobody is, at 18:07. So the daemon
takes that reading FOR itself, once per stall, and leaves it beside the slow row.

WHY A THREAD, and not the middleware's `finally`: the sample has to be taken WHILE the request
is still in flight. A coroutine, a `call_later`, or anything else scheduled on the event loop
cannot run during the very condition it is meant to measure — a blocked loop runs no callbacks,
so the reading would be taken after the stall released, against a pool that had already drained.
That is the 2026-09-12 misreading with a timer attached. So the watcher is one ordinary OS
thread: it wakes on its own clock, walks the frames off-loop, and writes off-loop. It holds no
anyio token (so it cannot deepen the shortage it is measuring) and no pool thread.

WHAT BOUNDS IT, since it fires on a request that is already in trouble:
- ONE sample per stall, not one per victim: `cooldown_s` (30 s) gates the whole watcher, so the
  fleet-wide shape above — dozens of endpoints crossing the threshold inside one second — leaves
  one snapshot, not forty. Every request stalled at that moment points AT that one snapshot.
- A bounded ring on disk (`SAMPLE_KEEP` files, oldest pruned on write), like the `slow_requests`
  deque it sits beside, so a daemon stalling all night cannot fill the disk.
- A bounded reading: `api_debug.FRAMES_PER_THREAD` (12) frames per thread, and the concurrent
  request list is clipped to `SLOW_KEEP`.
- ONE thread for the whole app, started on demand and retired after `idle_ticks` quiet ticks, so
  a process that is never slow carries no thread and the test suite grows none per app.

WHY NO NEW HEALTH EVENT: `health_vocab.EVENTS` is the registry, and its file is AT the 350-line
bar — a new event's mandatory paragraph has nowhere to go, four surfaces would have to move
together, and `readmodels/health_stream.BLOCKED_EVENTS` carries a written-out count pinned in two
docs. None of that buys anything here: a snapshot is not blocked work and no fold counts it. The
snapshot is written as a FILE under `.control/slow-samples/` and the EXISTING slow row points at
it by name (`api_traces.record_slow_request`), because that row's `detail` is capped at 200
characters and is parsed by regex downstream (`_ms`, `_in_flight`) — a thread dump cannot ride
in it, and a pointer costs ~30 characters and breaks no reader.
"""

from __future__ import annotations

import collections
import contextlib
import itertools
import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI

from ..ids import now_iso
from ..paths import atomic_write_json
from . import api_debug, api_traces

log = logging.getLogger("rsched.web")

#: A request slower than this is recorded (`app.state.slow_requests`, `/api/debug/slow`), logged,
#: and — while it is still running — self-sampled. Two seconds: every console read model measures
#: under 0.3 s on this instance, so a request over two is queueing or contention.
SLOW_REQUEST_S = 2.0
SLOW_KEEP = 50

#: Where a snapshot lands, under `<routines_home>/.control/` beside the health stream.
SAMPLES_DIR = "slow-samples"

#: The ring. Twenty stalls is more than any diagnosis has ever needed, and the whole point of
#: writing to disk is that the evidence outlives the process — unlike the deque above, which dies
#: with a daemon that restarts several times a week for its own releases.
SAMPLE_KEEP = 20

#: One sample per stall. The measured failure is SHARED: three pairs of unrelated endpoints
#: finished within 20 ms of each other, so the threshold is crossed by many requests at once and
#: they all have the same answer. Sampling each would multiply the frame walk by the size of the
#: pile-up, exactly when the process can least afford it.
SAMPLE_COOLDOWN_S = 30.0

#: How often the watcher looks. Well under `SLOW_REQUEST_S`, so a request that crosses the
#: threshold is caught inside it rather than at its end, and cheap enough to be irrelevant: one
#: wakeup, a dict copy and a subtraction.
TICK_S = 0.25

#: Quiet ticks before the watcher retires (2 s at the default tick). A process that is never slow
#: ends up with no extra thread, and the fast suite does not accumulate one per app.
IDLE_TICKS = 8


class SlowWatch:
    """The in-flight register, and the off-loop thread that samples the process while it stalls.

    One instance per app (`app.state.slow_watch`). `arm()` on request entry, `disarm()` on exit,
    both O(1) under a lock — the hot path pays a dict insert and a dict pop. The thread is this
    object's, started by the first `arm()` that finds it gone.
    """

    def __init__(self, app: FastAPI, *, cooldown_s: float = SAMPLE_COOLDOWN_S,
                 tick_s: float = TICK_S, keep: int = SAMPLE_KEEP,
                 idle_ticks: int = IDLE_TICKS) -> None:
        self.app = app
        self.cooldown_s = cooldown_s
        self.tick_s = tick_s
        self.keep = keep
        self.idle_ticks = idle_ticks
        self._lock = threading.Lock()
        self._live: dict[int, dict] = {}
        self._named: dict[int, str] = {}
        self._ids = itertools.count(1)
        #: The watcher thread's own counter, so a file name never draws from the hot path's
        #: token sequence and two stalls inside one second cannot collide on a name.
        self._samples = itertools.count(1)
        self._last = 0.0
        self._thread: threading.Thread | None = None

    # ---- the hot path ---------------------------------------------------------------------

    def arm(self, method: str, path: str) -> int:
        """Register a request as in flight; returns the token `disarm` takes."""
        with self._lock:
            token = next(self._ids)
            self._live[token] = {"method": method, "path": path, "started": time.monotonic()}
            alive = self._thread is not None and self._thread.is_alive()
            if not alive:
                # HERE, on the event loop: anyio resolves its thread limiter from the running
                # async context, so the watcher's own thread cannot read it — it would record
                # `threadpool: {"error": …}` and lose half of what the snapshot exists to show
                # (caught by this feature's own test). `arm` is the middleware's entry, so it
                # is the last point on the loop before the thread needs it.
                with contextlib.suppress(Exception):   # hot path: fail open, never raise here
                    api_debug.capture_limiter()
                self._thread = threading.Thread(target=self._watch, name="rsched-slow-watch",
                                                daemon=True)
                self._thread.start()
            return token

    def disarm(self, token: int) -> str:
        """Deregister the request; returns the snapshot file that was taken while it ran, or "".
        """
        with self._lock:
            self._live.pop(token, None)
            return self._named.pop(token, "")

    # ---- the watcher ---------------------------------------------------------------------

    def _watch(self) -> None:
        idle = 0
        while idle < self.idle_ticks:
            time.sleep(self.tick_s)
            try:
                idle = 0 if self._tick() else idle + 1
            except Exception:   # a diagnostic never takes the daemon down with it
                log.warning("slow self-sample tick failed", exc_info=True)
                idle += 1

    def _tick(self) -> bool:
        """One look. Returns whether anything was in flight (a quiet tick retires the thread)."""
        floor = float(getattr(self.app.state, "slow_request_s", SLOW_REQUEST_S))
        now = time.monotonic()
        with self._lock:
            live = dict(self._live)
            due = {t: r for t, r in live.items()
                   if now - r["started"] >= floor and t not in self._named}
            if due and now - self._last >= self.cooldown_s:
                self._last = now
            else:
                due = {}
        if due:
            self._take(now, due, live)
        return bool(live)

    def _take(self, now: float, due: dict[int, dict], live: dict[int, dict]) -> None:
        """Walk the process off-loop, write ONE snapshot, and point every stalled request at it.

        The trigger named in the file is the OLDEST request still waiting: in the measured shape
        the pile-up is simultaneous, so the one that has waited longest is the one whose wait the
        snapshot is most likely to explain.
        """
        row = min(due.values(), key=lambda r: r["started"])
        name = self._write(now, row, live)
        if not name:
            return
        with self._lock:
            for t in due:
                if t in self._live:          # one that finished mid-walk has its own row already
                    self._named[t] = name
        log.warning("slow self-sample %s: %s %s still in flight after %.1fs",
                    name, row["method"], row["path"], now - row["started"])

    def _write(self, now: float, row: dict, live: dict[int, dict]) -> str:
        """The snapshot file, or "" when there is nowhere to put it."""
        server = getattr(self.app.state, "server", None)
        home = getattr(server, "routines_home", None)
        if home is None:
            return ""
        waiting = sorted(({"method": r["method"], "path": r["path"],
                           "elapsed_s": round(now - r["started"], 2)} for r in live.values()),
                         key=lambda r: -float(r["elapsed_s"]))[:SLOW_KEEP]
        body = {"ts": now_iso(),
                "why": "a request crossed the slow threshold and was STILL in flight",
                "trigger": {"method": row["method"], "path": row["path"],
                            "elapsed_s": round(now - row["started"], 2)},
                "slow_request_s": float(getattr(self.app.state, "slow_request_s",
                                                SLOW_REQUEST_S)),
                "waiting": waiting,
                # BOTH pools plus the stream gate, from the reader `/api/debug/threads` serves:
                # the anyio limiter alone is the pool every `asyncio.to_thread` never touches.
                "report": api_debug.thread_report(self.app.state)}
        d = Path(home) / ".control" / SAMPLES_DIR
        # Zero-padded, because `_prune` keeps the newest by SORTED NAME: unpadded, a storm of
        # ten stalls inside one second would sort `-10` before `-2` and prune the wrong file.
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{next(self._samples):03d}.json"
        try:
            atomic_write_json(d / name, body)
        except OSError as exc:
            # A dead sample store must not be silent, and must not fail the request either.
            log.warning("slow self-sample write failed (%s): %s", d / name, exc)
            return ""
        self._prune(d)
        return name

    def _prune(self, d: Path) -> None:
        """Keep the newest `keep` snapshots. The names sort chronologically by construction."""
        try:
            files = sorted(d.glob("*.json"))
        except OSError:
            return
        for stale in files[:max(0, len(files) - self.keep)]:
            with contextlib.suppress(OSError):
                stale.unlink()


# ---- the seams the middleware uses (fail open: everything here is on the request path) -------

def arm(state, method: str, path: str) -> int | None:
    """Register an in-flight request, or None when this app carries no watch.

    `getattr` and a broad catch on purpose: this runs before every call in the process, and
    anything the request path reaches is handed stand-ins by design in tests.
    """
    watch = getattr(state, "slow_watch", None)
    if watch is None:
        return None
    try:
        return int(watch.arm(method, path))
    except Exception:
        log.warning("slow self-sample arm failed", exc_info=True)
        return None


def disarm(state, token: int | None) -> str:
    """The snapshot taken while that request ran, or "" — never raises on the request path."""
    watch = getattr(state, "slow_watch", None)
    if watch is None or token is None:
        return ""
    try:
        return str(watch.disarm(token))
    except Exception:
        log.warning("slow self-sample disarm failed", exc_info=True)
        return ""


def install(app: FastAPI) -> None:
    """Wire the timing middleware and the watch onto `app`.

    WHERE THIS IS CALLED IS LOAD-BEARING: `web/app.py` calls it after `_add_middlewares` and
    after `fresh_ui`, which keeps gzip the innermost of the layers (see `appwiring`) and this
    one outermost, so the time it measures includes every layer below it.
    """
    app.state.in_flight = 0
    app.state.slow_request_s = SLOW_REQUEST_S
    app.state.slow_requests = collections.deque(maxlen=SLOW_KEEP)
    app.state.slow_watch = SlowWatch(app)

    @app.middleware("http")
    async def slow_requests(request, call_next):
        app.state.in_flight += 1
        token = arm(app.state, request.method, str(request.url.path))
        started = time.monotonic()
        try:
            return await call_next(request)
        finally:
            app.state.in_flight -= 1
            took = time.monotonic() - started
            sample = disarm(app.state, token)
            if took >= app.state.slow_request_s:
                entry = {"method": request.method, "path": str(request.url.path),
                         "ts": now_iso(), "seconds": round(took, 2),
                         "in_flight": app.state.in_flight, "sample": sample}
                api_traces.record_slow_request(app.state, entry)
                log.warning("slow request: %s %s took %.1fs (in_flight=%d)",
                            request.method, request.url.path, took, app.state.in_flight)
