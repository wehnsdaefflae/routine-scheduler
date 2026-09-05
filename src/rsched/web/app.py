"""FastAPI app factory: bearer-token auth, API routers, SSE, static frontend, and the
scheduler running as a startup task — one process serves everything.
"""

from __future__ import annotations

import logging
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
from ..llm_tasks import TaskCenter
from .appwiring import _include_api_routers, _make_lifespan

log = logging.getLogger("rsched.web")

STATIC_DIR = Path(__file__).resolve().parents[3] / "static"


def build_stamp(repo: Path | None) -> str:
    """Short commit + date of the running checkout ('46e48e3 2026-07-13'), '' if unknown.

    Computed once at boot: deploys always restart the daemon, so the stamp can't go stale.
    """
    if not repo:
        return ""
    try:
        from ..libgit import git

        out = git(repo, "log", "-1", "--format=%h %cs")
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


SSE_TICKET_TTL_S = 60


def _is_sse_path(path: str) -> bool:
    """The ONLY endpoints an SSE ticket may authenticate: the global event stream and a
    run's transcript stream — the two EventSource surfaces the frontend opens. A ticket is
    a URL-carriable credential (it leaks into logs/history far more easily than a bearer
    header), so it must never be a full-API bearer substitute: scoped to these read-only
    streams, a leaked ticket can at worst read events for its 60s TTL.
    """
    return path == "/api/events" or (path.startswith("/api/runs/") and path.endswith("/events"))


# R94 (operator decision 2026-08-05: ENFORCE — this supersedes decision D68's 2026-08-03
# "leave as-is"): two bearer tiers. The PRIMARY token (config.yaml `token:`) is the
# human/web credential and authorizes everything. The ROUTINE token (`routine_token:`,
# injected into util subprocesses as RSCHED_API_TOKEN) authorizes READ-ONLY methods plus
# the explicit non-config mutations below — so no run can rewrite ANY routine's config
# (schedule, permissions, capabilities, grants, connections, settings, triggers, domain)
# through the HTTP API around the engine's "config is the user's" seal. Mutating routes
# are therefore primary-only BY DEFAULT: a new endpoint is born sealed, and opening one to
# routines is an explicit allowlist entry here, with its reason.
# ("METHOD", "/api/path-prefix") pairs — add a pair here, with its reason, the day a run
# legitimately needs a non-config mutation. The wild rsched-api usage survey (2026-08-05)
# found only reads plus the config mutations this seal exists to stop. Empty today —
# no run legitimately needs a mutation.
ROUTINE_TOKEN_MUTATIONS: tuple[tuple[str, str], ...] = ()


def _routine_token_allowed(request: Request) -> bool:
    # exact path or a real subtree — a bare startswith would let "/api/foo" swallow
    # "/api/foo-bar", silently opening any future sibling route that shares the prefix
    return request.method in ("GET", "HEAD", "OPTIONS") or any(
        request.method == method
        and (request.url.path == prefix or request.url.path.startswith(prefix + "/"))
        for method, prefix in ROUTINE_TOKEN_MUTATIONS)


def require_auth(request: Request) -> None:
    server = request.app.state.server
    token = server.token
    if not token:
        return  # auth disabled (empty token in config)
    header = request.headers.get("authorization", "")
    if header == f"Bearer {token}":
        return
    routine_token = server.routine_token
    if routine_token and header == f"Bearer {routine_token}":
        if _routine_token_allowed(request):
            return
        # RFC 6750 §3.1: the console tells a TIER refusal apart from an ordinary 403
        # (a protected template, the credentials dir, a denied path) by this header alone
        # — on seeing it, static/api.js drops the stored token and re-opens the gate, so a
        # browser holding the routine token is never stranded with an unactionable toast.
        raise HTTPException(
            status_code=403,
            detail="the routine API token is read-only — config-mutating endpoints "
                   "take the operator's primary token (R94). A run that needs a config "
                   "change proposes it via ask_user with config_patch instead.",
            headers={"WWW-Authenticate": 'Bearer error="insufficient_scope"'})
    # EventSource cannot send headers, and the bearer token in a query string would leak
    # into access logs — a SHORT-LIVED ticket (POST /api/sse-ticket) rides there instead,
    # valid ONLY for the SSE GET endpoints themselves (never a general API credential).
    if request.method == "GET" and _is_sse_path(request.url.path):
        ticket = request.query_params.get("ticket") or ""
        expiry = request.app.state.sse_tickets.get(ticket)
        if ticket and expiry is not None and expiry >= time.monotonic():
            return
    raise HTTPException(status_code=401, detail="missing or invalid token")


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
    from ..search import SearchIndex

    app.state.search = SearchIndex(server)    # the FTS cache; this process is its one writer

    deps = [Depends(require_auth)]
    _include_api_routers(app, deps)

    def _setup_marker():
        return (server.source.parent / ".setup-complete") if server.source else None

    build = build_stamp(server.source_repo)

    @app.get("/api/status", dependencies=deps)
    def status() -> dict:
        from .. import __version__, registry
        from ..schedule import server_tz

        marker = _setup_marker()
        needs_setup = not (marker and marker.exists())
        # llm_ready: the system_model (used by clarify + workflow generation) names a
        # catalog model whose endpoint is configured. Until then nothing that needs an LLM to
        # CREATE a routine works — the UI disables those. (Routines pick their own models to run.)
        mc = server.models.get(server.system_model) if server.system_model else None
        llm_ready = bool(mc and mc.endpoint in server.endpoints)
        # the seeded meta routines install disabled and carry the "meta" tag — the UI uses this
        # to notice that self-improvement is off on a fresh instance
        meta_routines = [{"slug": info.slug, "enabled": info.cfg.enabled}
                         for info in registry.scan(server).values()
                         if "meta" in info.cfg.tags]
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

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def web_manifest():
        # served from the root so an installed PWA's scope covers the whole console — the
        # manifest (display:standalone) is what lets the console be added to a phone's home
        # screen, which iOS Safari requires before it will deliver Web Push notifications.
        return FileResponse(STATIC_DIR / "manifest.webmanifest",
                            media_type="application/manifest+json")

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
