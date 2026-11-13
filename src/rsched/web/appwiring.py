"""The app's middlewares and its LIFESPAN — what happens either side of serving.

Split out of `app.py` (F393): `create_app` builds an app; this decides what wraps it and what
runs beside it. Which ROUTERS it mounts, and in what order, is `web/routers.py` (2026-11-09: the
router list had grown this file to the size bar, and the order of that list is its own subject).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware

from ..config import ServerConfig
from ..daemon.events import EventBus
from ..endpoints.instrument import set_sink
from ..llm_tasks import DaemonSink, TaskCenter
from .security_headers import SecurityHeaders

log = logging.getLogger("rsched.web.appwiring")


def _observe(task: asyncio.Task, name: str) -> None:
    """A lifespan background task must never die silently: without this, an exception in
    e.g. the scheduler task unwinds it while the web app keeps serving — the daemon looks
    alive with its heart stopped. The tick body has its own guard; this catches startup
    crashes and anything the guard can't.
    """
    def _done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.critical("background task %r died: %s", name, exc, exc_info=exc)
    task.add_done_callback(_done)


async def _stop(task: asyncio.Task | None) -> None:
    """Cancel one lifespan task and wait for it to end.

    A task that had already DIED re-raises its exception at the await — `_observe` logged it
    the moment it happened — and must not cut the shutdown short: raised from here, it left
    every task after it running into a closed loop and failed the whole lifespan shutdown.
    """
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


def _make_lifespan(server: ServerConfig, bus: EventBus, task_center: TaskCenter,
                   *, with_scheduler: bool):
    """The app's startup/shutdown seam, built before the FastAPI instance exists (it only
    needs the shared server/bus/center; the scheduler is reached via app.state at runtime).
    """

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        from .. import library_docs, utils_lib
        from ..docs_build import ensure_docs
        from .ioload import install_pool

        # Every off-loop read in this process runs on the loop's DEFAULT executor
        # (`asyncio.to_thread`), which asyncio sizes `min(32, cpu_count + 4)` — 32 threads on
        # the deployment, 8 on a four-core laptop. Size it explicitly and NAME its threads
        # here, the first point with a running loop: the live-stream ceiling is derived from
        # that number (web/ioload.py), and `/api/debug/threads` can only name the thread
        # holding the GIL if the thread has a name. The loop closes its own default executor
        # at shutdown, so this needs no teardown below.
        app.state.io_pool = install_pool()

        # bootstrap the library repo (clone from remote if configured + absent, else init/leave),
        # then make sure its rules/ + permissions/ subdirs exist.
        try:
            utils_lib.ensure_library(server.libraries_home, remote=server.libraries_remote)
            library_docs.ensure_dir(server.rules_home)
            library_docs.ensure_dir(server.permissions_home)
        except Exception as exc:  # never block startup on a library hiccup
            log.warning("library bootstrap %s: %s", server.libraries_home, exc)
        # regenerate the Help tab's content (pdoc + guides) when the source changed — in a
        # thread, and ensure_docs never raises, so startup is never blocked on it
        docs_task = asyncio.create_task(asyncio.to_thread(ensure_docs, server.source_repo))
        task = None
        if with_scheduler and not os.environ.get("RSCHED_NO_SCHEDULER"):
            task = asyncio.create_task(app.state.scheduler.run_forever())
            _observe(task, "scheduler")
        # Web Push sender: idles until a browser subscribes, then pushes new decisions
        from . import push as push_mod

        push_task = asyncio.create_task(push_mod.bus_listener(server, bus))
        _observe(push_task, "web-push listener")
        # The util-stats snapshot's ONE writer: a debounced recompute per burst of run events,
        # in a thread. Every engine used to do this at its own run end — a library git walk,
        # the whole usage stream and every un-counted transcript, for a file no engine reads
        # (daemon/util_stats_watch.py). Here rather than on the scheduler's tick because it
        # reacts to events, not to a clock, and the bus lives in this layer.
        from ..daemon import util_stats_watch

        utilstats_task = asyncio.create_task(util_stats_watch.bus_listener(server, bus))
        _observe(utilstats_task, "util-stats snapshot watch")
        # The LLM task manager sink: every instrumented complete() (run in threadpool/to_thread
        # workers) marshals its lifecycle records onto THIS loop, where the task center + bus live.
        set_sink(DaemonSink(task_center, asyncio.get_running_loop()))
        # Full-text search: keep the FTS index warm in the background (bounded passes in a
        # thread) so query-time freshness top-ups stay cheap. The index is a pure cache
        # under <routines_home>/.control/ — this process is its only writer.
        from . import api_search

        search_task = asyncio.create_task(api_search.maintain(app.state.search))
        _observe(search_task, "search maintainer")
        # D191 step 3: apply the config patches a routine's own `config_confirm` dial permits.
        # A run cannot write config, so it queues the request (rsched/autoapply.py) and THIS
        # layer — the one that holds the validated writer — performs it. Here rather than on
        # the scheduler's tick so `daemon/` keeps importing nothing from `web/`.
        from . import autoapply_drain

        apply_task = asyncio.create_task(autoapply_drain.maintain(app))
        _observe(apply_task, "config auto-apply drain")
        # D178: a settings accept pressed while a run was active was REFUSED and held with that
        # run's expected end. Once the run ends it applies — through the accept endpoint
        # itself, which only this layer can call — or expires because the value moved under it.
        from . import held_accepts

        held_task = asyncio.create_task(held_accepts.maintain(app))
        _observe(held_task, "held settings-accept drain")
        # F854: the post-deploy checklist, run by the daemon ITSELF a short while after boot
        # (operator, 2026-11-09: "in-process, served-identity hitting its own port once it
        # answers — no human step ever again"). `rsched postdeploy` existed since 0.445.0 and
        # nothing ever called it, so the restart path this instance actually takes several times
        # a week verified nothing: on 2026-11-08 the daemon came up on 0.520.0 while the newest
        # record row was two days old, its own backup-freshness check a FAIL. HERE because the
        # lifespan IS the moment "this process is now serving", and the check reads its own
        # HTTP port — which is listening only once this half has yielded.
        # Gated like the scheduler task above, and for a sharper reason: the checklist walks the
        # state homes and asks our own port, so an app built WITHOUT a scheduler — every
        # `make_test_server` client, hundreds per suite run — must never start it.
        from ..daemon import postdeploy_watch

        postdeploy_task = None
        if with_scheduler and not os.environ.get("RSCHED_NO_SCHEDULER"):
            postdeploy_task = asyncio.create_task(postdeploy_watch.after_boot(server))
            _observe(postdeploy_task, "post-deploy checklist")
        yield
        # F746: the breadcrumb for THIS exit, written on the way out. uvicorn runs the lifespan's
        # shutdown half on ANY graceful SIGTERM — a deploy, `systemctl restart`, a container
        # replacement — whereas `restart.trigger_shutdown` (which writes a more specific reason
        # and is left alone here) only ever runs on the daemon's own self-update path. Without it
        # the next boot reaps every live run as `cause: unknown`: 79 orphans over 50 boots, and
        # not one ever attributed to a restart (R2793/R2800). An UNGRACEFUL kill reaches no
        # Python and still leaves nothing, which is the honest case.
        from ..daemon import restart as restart_ctl
        restart_ctl.mark_graceful_exit(server)
        # D152 step 9: the `restart` hook point, the last one nothing fired at — and the only
        # one outside a run, because the party that knows a restart is happening is the one
        # exiting. HERE, after the breadcrumb and before anything is torn down, while the
        # runner still knows which engines this exit takes down with it. Its carrier is
        # `run-end`'s (`state/notes.md`), so the line reaches whichever leg reads that file
        # next — the automatic resume, or the next scheduled run when no resume is granted.
        from ..daemon import restart_hook

        runner = getattr(app.state, "runner", None)
        if runner is not None:
            restart_hook.at_daemon_exit(server, runner.active.values(),
                                        "external shutdown (SIGTERM)")
        set_sink(None)
        await _stop(search_task)
        app.state.search.shutdown()
        for running in (docs_task, push_task, utilstats_task, apply_task, held_task,
                        postdeploy_task, task):
            await _stop(running)
        # The I/O pool LAST, once nothing is left to submit to it. `wait=False` so a shutdown is
        # never held hostage by an SSH call or a provider probe mid-flight (those threads finish
        # on their own); `cancel_futures` drops what never started. Explicit rather than left to
        # the loop's own `shutdown_default_executor`, because a pool nobody shuts down keeps
        # non-daemon threads alive through its atexit handler — invisible in production, and
        # thousands of them across a test session that builds an app per test.
        if app.state.io_pool is not None:
            app.state.io_pool.shutdown(wait=False, cancel_futures=True)
            app.state.io_pool = None

    return lifespan

def _add_middlewares(app: FastAPI) -> None:
    """The response middlewares, outermost LAST — one seam so their order is readable in one
    place (`add_middleware` puts each new one OUTSIDE the previous).

    `SecurityHeaders` — the CSP on every response, refusals too (D219). See that module.

    `GZipMiddleware` — the console sent every byte uncompressed, and the operator's complaint
    was *"The webui at rsched.markwernsdorfer.com is super slow"* (2026-10-30). Measured
    before the fix: no `content-encoding` on any response from either the app or Caddy, and
    `GET /api/questions` alone is ~1.1 MB of JSON that `static/app.js` refetches **every 30 s
    per open tab**; gzip -9 takes this instance's own API payloads to ~31 % of their size
    (1,000,000 → 313,029 bytes on a real capture). Compression is the half that needs no
    behaviour change: nothing is hidden, no payload is capped, and removing the line restores
    the old bytes exactly.

    Why it is safe on EVERY response here, checked rather than assumed:

    * There is no SSE anywhere in this app — `grep` for `StreamingResponse` /
      `text/event-stream` finds nothing; the live streams are WebSockets, and a websocket
      scope never reaches an http middleware (the same reason `slowsample.py`'s timing
      middleware cannot count one). Starlette 1.8's GZip also excludes `text/event-stream` and the
      binary types (images, fonts, video, zip) by default, so artifact and screenshot
      responses pass through untouched.
    * `minimum_size=1024` keeps small bodies byte-identical — including the 9-byte body
      `tests/test_served_file_containment.py` pins by `content-length`.

    WHERE THIS IS CALLED IS LOAD-BEARING, and it was measured the hard way. `app.py` calls it
    BEFORE its two `@app.middleware("http")` layers — `fresh_ui`, then `slow_requests`, which
    `slowsample.install` adds — which makes
    gzip the INNERMOST of the four. Those two are `BaseHTTPMiddleware`, which re-emits every
    response as a STREAM, and starlette's gzip skips a small body only when it arrives in one
    chunk with `more_body=False` (`starlette/middleware/gzip.py`). Wired outside them, every
    response therefore looked streamed: a 9-byte artifact download was gzipped and had its
    `Content-Length` DELETED, which broke
    `tests/test_served_file_containment.py::test_served_files_keep_their_bytes_type_and_freshness`
    — a contract about artifact downloads, nothing to do with compression. Inside them, the
    threshold works as documented. If a later edit moves this call after those layers, that
    test is the one that fails and this paragraph is the reason.

    Gzip is added AFTER `SecurityHeaders` here, so it sits inside it and compresses a response
    that already carries the policy; it rewrites `Content-Length` itself when it does compress.
    """
    app.add_middleware(SecurityHeaders)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
