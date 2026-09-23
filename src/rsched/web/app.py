"""FastAPI app factory: bearer-token auth, API routers, SSE, static frontend, and the
scheduler running as a startup task — one process serves everything.
"""

from __future__ import annotations

import collections
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
from ..ids import now_iso
from ..llm_tasks import TaskCenter
from .appwiring import _include_api_routers, _make_lifespan

log = logging.getLogger("rsched.web")

#: A request slower than this is recorded (`app.state.slow_requests`, `/api/debug/slow`) and
#: logged. Two seconds: every console read model measures under 0.3 s on this instance, so a
#: request over two is queueing or contention — the thing worth a stack sample.
SLOW_REQUEST_S = 2.0
SLOW_KEEP = 50

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


def _is_browser_view_path(path: str) -> bool:
    """The relayed noVNC screen (F527), authenticated by its own PASS cookie (F530).

    It shipped on the SSE ticket and that could never have worked: an `<iframe src>` is a
    naked GET, and noVNC then requests its own siblings (`app/ui.js`, `app/styles/base.css`,
    the images) with URLs the page builds itself — so no query parameter the console chooses
    reaches them. Every one of those requests 401'd, and the frame rendered this app's own
    error body at the user. A cookie is the only credential a browser attaches to a frame's
    sub-resources unasked; it is path-scoped to this prefix, HttpOnly, and minted only for a
    caller holding the console token.
    """
    return path == "/browser-view" or path.startswith("/browser-view/")


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

# The reads a run may NOT make either. "Read-only" is not the same as "may read anything":
# a util subprocess runs inside a Landlock jail scoped to its routine's granted roots, and
# these subtrees hand it exactly what that jail forbids — any directory listing on the
# host (api_fs's own docstring: "names only is still reconnaissance"), every secret NAME with
# the utils that declare it, the daemon's own stacks, and full-text search over EVERY
# routine's transcripts, notes and ledgers (observations are not redacted, so a util that
# printed a token once is queryable by every other routine forever). Nothing logs it as a
# boundary crossing, because it is an authorized GET. The 2026-08-05 rsched-api usage survey
# found runs reading items, questions, the routine cards, the runs index, status and stats —
# none of these, and no live util or recipe targets one today.
#
# Cross-routine FILE reads (`/api/routines/{slug}/file`, `/api/runs/{id}/file`) are the same
# class and are deliberately NOT here yet: at least one routine was granted another's
# transcripts on purpose, and closing that door needs the grant re-expressed as an fs-read
# root first, or a run loses a channel with no error it can act on.
ROUTINE_TOKEN_DENIED_READS: tuple[str, ...] = ("/api/fs", "/api/debug", "/api/settings",
                                               "/api/search")


def _in_subtree(path: str, prefix: str) -> bool:
    """The path itself or something genuinely under it — never a bare startswith, which
    would let "/api/foo" swallow "/api/foo-bar" and silently catch (or open) any future
    sibling route that shares the prefix.
    """
    return path == prefix or path.startswith(prefix + "/")


def _routine_token_allowed(request: Request) -> bool:
    path = request.url.path
    if any(_in_subtree(path, prefix) for prefix in ROUTINE_TOKEN_DENIED_READS):
        return False
    return request.method in ("GET", "HEAD", "OPTIONS") or any(
        request.method == method and _in_subtree(path, prefix)
        for method, prefix in ROUTINE_TOKEN_MUTATIONS)


def require_auth(request: Request) -> None:
    server = request.app.state.server
    token = server.token
    if not token:
        return  # auth disabled (empty token in config)
    header = request.headers.get("authorization", "")
    # constant-time, like the webhook token (api_hooks._match_webhook): one credential
    # class, one standard — and the weaker half was guarding the PRIMARY token.
    if secrets.compare_digest(header.encode(), f"Bearer {token}".encode()):
        return
    routine_token = server.routine_token
    if routine_token and secrets.compare_digest(header.encode(),
                                                f"Bearer {routine_token}".encode()):
        if _routine_token_allowed(request):
            return
        # RFC 6750 §3.1: the console tells a TIER refusal apart from an ordinary 403
        # (a protected template, the credentials dir, a denied path) by this header alone
        # — on seeing it, static/api.js drops the stored token and re-opens the gate, so a
        # browser holding the routine token is never stranded with an unactionable toast.
        raise HTTPException(
            status_code=403,
            detail="the routine API token is read-only and reads no wider than the "
                   "sandbox (R94): config-mutating endpoints, the filesystem picker, the "
                   "settings surface, cross-routine search and the daemon's stacks take the "
                   "operator's primary token. A run that needs a config change proposes it "
                   "via ask_user with config_patch; a file it may read is reached with "
                   "read_file, and one outside its jail is an fs-read access request.",
            headers={"WWW-Authenticate": 'Bearer error="insufficient_scope"'})
    # EventSource cannot send headers, and the bearer token in a query string would leak
    # into access logs — a SHORT-LIVED ticket (POST /api/sse-ticket) rides there instead,
    # valid ONLY for the SSE GET endpoints themselves (never a general API credential).
    if request.method == "GET" and _is_sse_path(request.url.path):
        ticket = request.query_params.get("ticket") or ""
        expiry = request.app.state.sse_tickets.get(ticket)
        if ticket and expiry is not None and expiry >= time.monotonic():
            return
    # The relayed browser screen carries its own PASS, in a cookie (F530). A query ticket
    # cannot work here and shipping one was the bug: an <iframe src> is a naked GET, and
    # noVNC then builds its own asset URLs (app/ui.js, app/styles/base.css, the images), so
    # no parameter the embedding page chooses ever reaches those requests. A cookie is the
    # one credential the browser attaches to every sub-resource of the frame by itself.
    if request.method == "GET" and _is_browser_view_path(request.url.path):
        from .api_browser_view import SCREEN_COOKIE, pass_is_valid

        if pass_is_valid(request.app, request.cookies.get(SCREEN_COOKIE) or ""):
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
    # pass → monotonic expiry for the relayed browser screen (F530). Separate from the SSE
    # tickets on purpose: a different lifetime, a different scope, and a different failure if
    # one is ever mistaken for the other.
    app.state.browser_view_passes = {}
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
                # Where the shared browser can be WATCHED (empty = this deployment never
                # published a screen). The console shows the Browser section only when it
                # is set, because a dead link to a port nobody opened is worse than no link.
                "browser_view_url": server.browser_view_url,
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

    # Slow-request evidence (2026-09-12): every sync handler took 20-50 s for an hour while five
    # runs were active, and nothing recorded it — no access log, no stack. The in-flight count
    # and a ring of the slow ones are what /api/debug reads; the WARNING is what `docker logs`
    # shows the morning after. SSE streams are excluded: they are slow by design.
    app.state.in_flight = 0
    app.state.slow_request_s = SLOW_REQUEST_S
    app.state.slow_requests = collections.deque(maxlen=SLOW_KEEP)

    @app.middleware("http")
    async def slow_requests(request, call_next):
        if _is_sse_path(request.url.path):
            return await call_next(request)
        app.state.in_flight += 1
        started = time.monotonic()
        try:
            return await call_next(request)
        finally:
            app.state.in_flight -= 1
            took = time.monotonic() - started
            if took >= app.state.slow_request_s:
                entry = {"method": request.method, "path": str(request.url.path),
                         "seconds": round(took, 2), "in_flight": app.state.in_flight,
                         "ts": now_iso()}
                app.state.slow_requests.appendleft(entry)
                log.warning("slow request: %s %s took %.1fs (in_flight=%d)",
                            request.method, request.url.path, took, app.state.in_flight)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    # Generated Help content (see docs_build.py) — static like /static and served with the
    # same posture (only /api/* is token-gated). The dir may not exist before the first
    # build finishes; check_dir=False lets the mount come up regardless.
    from ..docs_build import docs_out_dir

    app.mount("/docs", StaticFiles(directory=docs_out_dir(), check_dir=False, html=True),
              name="docs")
    return app
