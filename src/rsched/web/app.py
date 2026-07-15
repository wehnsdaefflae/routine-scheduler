"""FastAPI app factory: bearer-token auth, API routers, SSE, static frontend, and the
scheduler running as a startup task — one process serves everything.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import ServerConfig, load_server_config
from ..daemon.events import EventBus
from ..daemon.runner import Runner
from ..daemon.scheduler import Scheduler
from ..endpoints.instrument import set_sink
from ..llm_tasks import DaemonSink, TaskCenter

log = logging.getLogger("rsched.web")

STATIC_DIR = Path(__file__).resolve().parents[3] / "static"


def build_stamp(repo: Path | None) -> str:
    """Short commit + date of the running checkout ('46e48e3 2026-07-13'), '' if unknown.

    Computed once at boot: deploys always restart the daemon, so the stamp can't go stale.
    """
    if not repo:
        return ""
    try:
        import subprocess

        out = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%h %cs"],
                             capture_output=True, text=True, timeout=5, check=False)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


SSE_TICKET_TTL_S = 60


def require_auth(request: Request) -> None:
    token = request.app.state.server.token
    if not token:
        return  # auth disabled (empty token in config)
    header = request.headers.get("authorization", "")
    if header == f"Bearer {token}":
        return
    # EventSource cannot send headers, and the bearer token in a query string would leak
    # into access logs — a SHORT-LIVED ticket (POST /api/sse-ticket) rides there instead.
    ticket = request.query_params.get("ticket") or ""
    expiry = request.app.state.sse_tickets.get(ticket)
    if ticket and expiry is not None and expiry >= time.monotonic():
        return
    raise HTTPException(status_code=401, detail="missing or invalid token")


def _make_lifespan(server: ServerConfig, bus: EventBus, task_center: TaskCenter,
                   *, with_scheduler: bool):
    """The app's startup/shutdown seam, built before the FastAPI instance exists (it only
    needs the shared server/bus/center; the scheduler is reached via app.state at runtime).
    """

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        from .. import library_docs, utils_lib
        from ..docs_build import ensure_docs

        # bootstrap the library repo (clone from remote if configured + absent, else init/leave),
        # then make sure its traits/ + permissions/ subdirs exist.
        try:
            utils_lib.ensure_library(server.libraries_home, remote=server.libraries_remote)
            library_docs.ensure_dir(server.traits_home)
            library_docs.ensure_dir(server.permissions_home)
        except Exception as exc:  # never block startup on a library hiccup
            log.warning("library bootstrap %s: %s", server.libraries_home, exc)
        # Reconcile wizard builds orphaned by a restart/crash: finalize.json stuck at 'building'
        # (a self-restart drains engine runs but not in-flight web-process builds) → mark them a
        # recoverable error + clean the half-built dir, so a routine setup never hangs forever.
        try:
            from . import wizard_store

            recovered = wizard_store.recover_orphan_builds(server)
            if recovered:
                log.warning("recovered %d orphaned wizard build(s): %s", len(recovered), recovered)
                for wid in recovered:
                    bus.publish({"event": "routine_failed", "wid": wid,
                                 "error": "build interrupted by a server restart — please retry"})
        except Exception as exc:  # recovery must never block startup
            log.warning("wizard build recovery: %s", exc)
        # regenerate the Help tab's content (pdoc + guides) when the source changed — in a
        # thread, and ensure_docs never raises, so startup is never blocked on it
        docs_task = asyncio.create_task(asyncio.to_thread(ensure_docs, server.source_repo))
        task = None
        if with_scheduler and not os.environ.get("RSCHED_NO_SCHEDULER"):
            task = asyncio.create_task(app.state.scheduler.run_forever())
        # Web Push sender: idles until a browser subscribes, then pushes new decisions
        from . import push as push_mod

        push_task = asyncio.create_task(push_mod.bus_listener(server, bus))
        # The LLM task manager sink: every instrumented complete() (run in threadpool/to_thread
        # workers) marshals its lifecycle records onto THIS loop, where the task center + bus live.
        set_sink(DaemonSink(task_center, asyncio.get_running_loop()))
        yield
        set_sink(None)
        docs_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await docs_task
        push_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await push_task
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return lifespan


def _include_api_routers(app: FastAPI, deps: list) -> None:
    from . import (
        api_audit,
        api_background,
        api_conversations,
        api_llm_tasks,
        api_playbooks,
        api_push,
        api_questions,
        api_routines,
        api_runs,
        api_schedule,
        api_stats,
        api_traces,
        api_wizard,
        api_workflows,
        settings,
    )

    for module in (api_push, api_routines, api_conversations, api_background, api_runs,
                   api_schedule, api_stats, api_questions, api_audit, api_traces, settings,
                   api_workflows, api_playbooks, api_wizard, api_llm_tasks):
        app.include_router(module.router, prefix="/api", dependencies=deps)


def create_app(server: ServerConfig | None = None, *, with_scheduler: bool = True) -> FastAPI:
    if server is None:
        server, problems = load_server_config()
        for pr in problems:
            log.warning("config: %s", pr)

    bus = EventBus()
    task_center = TaskCenter(bus)
    app = FastAPI(title="routine-scheduler",
                  lifespan=_make_lifespan(server, bus, task_center,
                                          with_scheduler=with_scheduler))
    runner = Runner(server, bus, task_center)   # runs are processes; llm-calls their children
    scheduler = Scheduler(server, runner, bus)
    app.state.server = server
    app.state.sse_tickets = {}   # ticket → monotonic expiry (see require_auth / sse-ticket)
    app.state.bus = bus
    app.state.runner = runner
    app.state.scheduler = scheduler
    app.state.detached = scheduler.detached   # detached-background-task manager (Phase 2 API)
    app.state.llm_tasks = task_center

    deps = [Depends(require_auth)]
    _include_api_routers(app, deps)

    def _setup_marker():
        return (server.source.parent / ".setup-complete") if server.source else None

    build = build_stamp(server.source_repo)

    @app.get("/api/status", dependencies=deps)
    def status() -> dict:
        from .. import __version__
        from ..daemon import registry
        from ..schedule import server_tz

        marker = _setup_marker()
        needs_setup = not (marker and marker.exists())
        # llm_ready: the system_model (used by the clarify wizard + workflow generation) names a
        # catalog model whose endpoint is configured. Until then nothing that needs an LLM to
        # CREATE a routine works — the UI disables those. (Routines pick their own models to run.)
        mc = server.models.get(server.system_model) if server.system_model else None
        llm_ready = bool(mc and mc.endpoint in server.endpoints)
        # the seeded meta routines install disabled and carry the "meta" tag — the UI uses this
        # to notice that self-improvement is off on a fresh instance
        meta_routines = [{"slug": info.slug, "enabled": info.cfg.enabled}
                         for info in registry.scan(server).values() if "meta" in info.cfg.tags]
        return {"version": __version__, "build": build, "server_tz": server_tz(),
                "needs_setup": needs_setup, "llm_ready": llm_ready,
                "meta_routines": meta_routines, **scheduler.snapshot()}

    @app.post("/api/setup/complete", dependencies=deps)
    def setup_complete() -> dict:
        """The first-run setup flow calls this once the user has configured (or chosen to skip)
        providers + repos — it stops the first-launch redirect to Settings.
        """
        marker = _setup_marker()
        if marker:
            marker.write_text("done\n", encoding="utf-8")
        return {"ok": True}

    @app.post("/api/sse-ticket", dependencies=deps)
    def sse_ticket() -> dict:
        """A short-lived, unguessable query-string credential for EventSource connections
        (which cannot send an Authorization header). Multi-use within its TTL so the
        browser's automatic reconnects keep working; expired tickets are purged here.
        """
        now = time.monotonic()
        tickets = app.state.sse_tickets
        for stale in [t for t, exp in tickets.items() if exp < now]:
            del tickets[stale]
        ticket = secrets.token_urlsafe(24)
        tickets[ticket] = now + SSE_TICKET_TTL_S
        return {"ticket": ticket, "ttl": SSE_TICKET_TTL_S}

    @app.get("/api/events", dependencies=deps)
    async def global_events():
        from sse_starlette import EventSourceResponse

        from .sse import bus_stream

        return EventSourceResponse(bus_stream(bus))

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        # served from the root (not /static/) so the worker's scope covers the whole console
        return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript")

    @app.middleware("http")
    async def fresh_ui(request, call_next):
        # The daemon self-updates and restarts; without this, browsers heuristically cache the
        # ES modules and keep rendering the pre-update console. no-cache = revalidate (cheap 304s).
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith(("/static", "/docs")):
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    # Generated Help content (see docs_build.py) — static like /static and served with the
    # same posture (only /api/* is token-gated). The dir may not exist before the first
    # build finishes; check_dir=False lets the mount come up regardless.
    from ..docs_build import docs_out_dir

    app.mount("/docs", StaticFiles(directory=docs_out_dir(), check_dir=False, html=True),
              name="docs")
    return app
