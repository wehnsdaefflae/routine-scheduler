"""FastAPI app factory: bearer-token auth, API routers, live streams, static frontend, and the
scheduler running as a startup task — one process serves everything.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette._utils import get_route_path

from ..config import ServerConfig, load_server_config
from ..daemon.events import EventBus
from ..daemon.runner import Runner
from ..daemon.scheduler import Scheduler
from ..llm_tasks import TaskCenter
from . import api_core, assetskew, routers, slowsample
from .api_core import STATIC_DIR
from .appwiring import _add_middlewares, _make_lifespan
from .ioload import attach_state
from .screen_proxy import screen_for_path

log = logging.getLogger("rsched.web")


# R94 (operator decision 2026-08-05: ENFORCE — this supersedes decision D68's 2026-08-03
# "leave as-is"): two bearer tiers. The PRIMARY token (config.yaml `token:`) is the
# human/web credential and authorizes everything. The ROUTINE token (`routine_token:`,
# injected into util subprocesses as RSCHED_API_TOKEN) authorizes READ-ONLY methods plus
# the explicit non-config mutations below — so no run can rewrite ANY routine's config
# (schedule, permissions, capabilities, grants, connections, settings, triggers, roots)
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
# routine's transcripts, notes and ledgers (a util's output is redacted only of the secret
# VALUES the engine itself injected, `captured_output.read_capped`; a token it fetched or read
# from a file and printed once is still queryable by every other routine forever). Nothing
# logs it as a boundary crossing, because it is an authorized GET. The 2026-08-05 rsched-api
# usage survey found runs reading items, questions, the routine cards, the runs index, status
# and stats — none of these, and no live util or recipe targets one today.
#
# `/api/routines/*/secrets` is the per-routine half of the secret-name read (D103): every
# routine's OWN secret names, the central names each one shadows, and where the store file
# lives on the host. A scoped secret is "invisible to every other routine" by design, and was
# one GET away from every other routine's util. A `*` segment matches any one segment.
#
# Cross-routine FILE reads (`/api/routines/{slug}/file`, `/api/runs/{id}/file`) are the same
# class and are deliberately NOT here yet: at least one routine was granted another's
# transcripts on purpose, and closing that door needs the grant re-expressed as an fs-read
# root first, or a run loses a channel with no error it can act on.
#
# `/api/desktops` is the operator's view of the agent-desktop fleet, and it carries the random
# token that OPENS each desktop's screen — a keyboard and a mouse on another routine's computer.
# The broker keeps that view behind a second secret no routine is granted
# (DESKTOP_OPERATOR_TOKEN), and this route would hand it to any run's RSCHED_API_TOKEN if it
# were an ordinary read. The relayed screens themselves (`/browser-view`, `/desktop-view`) are
# the operator's for the same reason: their sockets already need a pass only the primary token
# mints, and their assets have no reader a run needs.
ROUTINE_TOKEN_DENIED_READS: tuple[str, ...] = ("/api/fs", "/api/debug", "/api/settings",
                                               "/api/search", "/api/desktops",
                                               "/api/routines/*/secrets", "/browser-view",
                                               "/api/conversations/*/secrets", "/desktop-view")


def _in_subtree(path: str, prefix: str) -> bool:
    """The path itself or something genuinely under it, compared segment by segment — never a
    bare startswith, which would let "/api/foo" swallow "/api/foo-bar" and silently catch (or
    open) any future sibling route that shares the prefix. A `*` segment in `prefix` matches
    any one segment of `path`.
    """
    want, have = prefix.split("/"), path.split("/")
    return len(have) >= len(want) and all(w in ("*", h) for w, h in zip(want, have,
                                                                         strict=False))


def _routine_token_allowed(method: str, path: str) -> bool:
    if any(_in_subtree(path, prefix) for prefix in ROUTINE_TOKEN_DENIED_READS):
        return False
    return method in ("GET", "HEAD", "OPTIONS") or any(
        method == allowed and _in_subtree(path, prefix)
        for allowed, prefix in ROUTINE_TOKEN_MUTATIONS)


def _route_path(request: Request) -> str:
    """The path the ROUTER dispatches this request on — the only string a tier decision may
    read.

    Not `request.url.path`: that is a URL Starlette RE-PARSES out of the already-decoded path,
    so an encoded `?` or `#` inside a segment ends the path early. `/api/runs/events%3Fx/file`
    routes to `/runs/{run_id}/file` while its `url.path` reads `/api/runs/events` — once a
    stream path, which a URL-carriable ticket then admitted. A credential is only as narrow as
    the path it is checked against, so that path has to be the one that runs.
    """
    return get_route_path(request.scope)


def require_auth(request: Request) -> None:
    server = request.app.state.server
    token = server.token
    if not token:
        return  # auth disabled (empty token in config)
    header = request.headers.get("authorization", "")
    path = _route_path(request)
    # constant-time, like the webhook token (api_hooks._match_webhook): one credential
    # class, one standard — and the weaker half was guarding the PRIMARY token.
    if secrets.compare_digest(header.encode(), f"Bearer {token}".encode()):
        return
    routine_token = server.routine_token
    if routine_token and secrets.compare_digest(header.encode(),
                                                f"Bearer {routine_token}".encode()):
        if _routine_token_allowed(request.method, path):
            return
        # RFC 6750 §3.1: the console tells a TIER refusal apart from an ordinary 403
        # (a protected template, the credentials dir, a denied path) by this header alone
        # — on seeing it, static/api.js drops the stored token and re-opens the gate, so a
        # browser holding the routine token is never stranded with an unactionable toast.
        raise HTTPException(
            status_code=403,
            detail="the routine API token is read-only and reads no wider than the "
                   "sandbox (R94): config-mutating endpoints, the filesystem picker, the "
                   "settings surface, a routine's secret names, cross-routine search, the "
                   "daemon's stacks, the desktop fleet and the relayed screens take the "
                   "operator's primary token. A run that needs a "
                   "config change proposes it via ask_user with config_patch; a file it may "
                   "read is reached with read_file, and one outside its jail is an fs-read "
                   "access request.",
            headers={"WWW-Authenticate": 'Bearer error="insufficient_scope"'})
    # No HTTP route accepts a stream ticket: the live streams are WebSockets, admitted by
    # `streams.admits` on their own handshake (F606), so a ticket that leaks into a URL can
    # never stand in for the bearer here.
    # A relayed noVNC screen (F527) carries its own PASS, in a cookie (F530). A query ticket
    # cannot work here and shipping one was the bug: an <iframe src> is a naked GET, and
    # noVNC then builds its own asset URLs (app/ui.js, app/styles/base.css, the images), so
    # no parameter the embedding page chooses ever reaches those requests. A cookie is the
    # one credential the browser attaches to every sub-resource of the frame by itself — and
    # each screen's is path-scoped to its own relay, so only THAT screen's pass admits it.
    screen = screen_for_path(path) if request.method == "GET" else None
    if screen is not None:
        from .api_screen_view import pass_is_valid

        if pass_is_valid(request.app, screen, request.cookies.get(screen.cookie) or ""):
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
    app.state.stream_tickets = {}   # ticket → monotonic expiry (see streams.admits / stream-ticket)
    attach_state(app)   # the live-socket ceiling + the io_pool slot the lifespan fills (ioload)
    # pass → monotonic expiry, one store per relayed screen (F530). Separate from the stream
    # tickets on purpose: a different lifetime, a different scope, and a different failure if
    # one is ever mistaken for the other.
    from .api_screen_view import new_pass_stores

    app.state.screen_passes = new_pass_stores()
    app.state.bus = bus
    app.state.runner = runner
    app.state.scheduler = scheduler
    app.state.detached = scheduler.detached   # detached-background-task manager (Phase 2 API)
    app.state.llm_tasks = task_center
    from ..search import SearchIndex

    app.state.search = SearchIndex(server)    # the FTS cache; this process is its one writer

    deps = [Depends(require_auth)]
    routers.include_api_routers(app, deps)

    # The build this PROCESS is, read once at boot. /static is served live from the checkout,
    # which can move under us — `assetskew` compares the two and /api/status carries the verdict
    # so the console can say so (F696).
    app.state.build = assetskew.read_stamp(server.source_repo)
    # The app's own routes are MODULE-LEVEL and read this app from `request.app.state`: as
    # closures here, FastAPI's process-wide callable cache kept every app ever built (api_core).
    app.include_router(api_core.authed, dependencies=deps)
    app.include_router(api_core.public)

    _add_middlewares(app)   # CSP + compression, INSIDE the two layers below (see appwiring)

    @app.middleware("http")
    async def fresh_ui(request, call_next):
        # The daemon self-updates and restarts; without this, browsers heuristically cache the
        # ES modules and keep rendering the pre-update console. no-cache = revalidate (cheap 304s).
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith(("/static", "/docs")):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # Slow-request evidence (2026-09-12): every sync handler took 20-50 s for an hour while five
    # runs were active, and nothing recorded it — no access log, no stack. The timing middleware,
    # the in-flight register and the SELF-SAMPLE taken while a request is still stalled (F871)
    # live in `slowsample.py`, whose docstring holds the measurements; the durable row is
    # api_traces'. Live streams reach none of it: they are WebSockets, and an http middleware
    # never sees a websocket scope.
    slowsample.install(app)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    # Generated Help content (see docs_build.py) — static like /static and served with the
    # same posture (only /api/* is token-gated). The dir may not exist before the first
    # build finishes; check_dir=False lets the mount come up regardless.
    from ..docs_build import docs_out_dir

    app.mount("/docs", StaticFiles(directory=docs_out_dir(), check_dir=False, html=True),
              name="docs")
    return app
