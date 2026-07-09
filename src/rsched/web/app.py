"""FastAPI app factory: bearer-token auth, API routers, SSE, static frontend, and the
scheduler running as a startup task — one process serves everything."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import ServerConfig, load_server_config
from ..daemon.events import EventBus
from ..daemon.runner import Runner
from ..daemon.scheduler import Scheduler

log = logging.getLogger("rsched.web")

STATIC_DIR = Path(__file__).resolve().parents[3] / "static"


def require_auth(request: Request) -> None:
    token = request.app.state.server.token
    if not token:
        return  # auth disabled (empty token in config)
    header = request.headers.get("authorization", "")
    if header == f"Bearer {token}" or request.query_params.get("token") == token:
        return
    raise HTTPException(status_code=401, detail="missing or invalid token")


def create_app(server: ServerConfig | None = None, *, with_scheduler: bool = True) -> FastAPI:
    if server is None:
        server, problems = load_server_config()
        for pr in problems:
            log.warning("config: %s", pr)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        from .. import fragments_lib, utils_lib

        # bootstrap the libraries (clone from remote if configured + absent, else init/leave).
        for ensure, home, remote in (
            (fragments_lib.ensure_library, server.fragments_home, server.fragments_remote),
            (utils_lib.ensure_library, server.utils_home, server.utils_remote),
        ):
            try:
                ensure(home, remote=remote)
            except Exception as exc:  # never block startup on a library hiccup
                log.warning("library bootstrap %s: %s", home, exc)
        task = None
        if with_scheduler and not os.environ.get("RSCHED_NO_SCHEDULER"):
            task = asyncio.create_task(app.state.scheduler.run_forever())
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="routine-scheduler", lifespan=lifespan)
    bus = EventBus()
    runner = Runner(server, bus)
    scheduler = Scheduler(server, runner, bus)
    app.state.server = server
    app.state.bus = bus
    app.state.runner = runner
    app.state.scheduler = scheduler

    from . import (api_audit, api_questions, api_routines, api_runs, api_settings,
                   api_wizard, api_workflows)

    deps = [Depends(require_auth)]
    app.include_router(api_routines.router, prefix="/api", dependencies=deps)
    app.include_router(api_runs.router, prefix="/api", dependencies=deps)
    app.include_router(api_questions.router, prefix="/api", dependencies=deps)
    app.include_router(api_audit.router, prefix="/api", dependencies=deps)
    app.include_router(api_settings.router, prefix="/api", dependencies=deps)
    app.include_router(api_workflows.router, prefix="/api", dependencies=deps)
    app.include_router(api_wizard.router, prefix="/api", dependencies=deps)

    def _setup_marker():
        return (server.source.parent / ".setup-complete") if server.source else None

    @app.get("/api/status", dependencies=deps)
    def status() -> dict:
        from .. import __version__
        from ..schedule import server_tz

        marker = _setup_marker()
        needs_setup = not (marker and marker.exists())
        return {"version": __version__, "server_tz": server_tz(),
                "needs_setup": needs_setup, **scheduler.snapshot()}

    @app.post("/api/setup/complete", dependencies=deps)
    def setup_complete() -> dict:
        """The first-run setup flow calls this once the user has configured (or chosen to skip)
        providers + repos — it stops the first-launch redirect to Settings."""
        marker = _setup_marker()
        if marker:
            marker.write_text("done\n", encoding="utf-8")
        return {"ok": True}

    @app.get("/api/events", dependencies=deps)
    async def global_events():
        from .sse import bus_stream, sse_response

        return sse_response(bus_stream(bus))

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
