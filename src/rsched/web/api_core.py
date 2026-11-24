"""The app's own routes — status, first-run setup, the stream ticket, the global event socket and
the console's root files — as MODULE-LEVEL handlers that read their app from `request.app`.

They used to be closures inside `create_app`, and that is a leak, not a style point. FastAPI
classifies every endpoint and dependency callable through process-lifetime `lru_cache`s
(`fastapi.dependencies.models`, `_CALLABLE_CLASSIFICATION_CACHE_SIZE` = 4096, keyed on the
callable's identity), so a closure defined per app is a new key per app, and each key holds its
closure, its cells, and through them the WHOLE app: ~560 routes' pydantic models, ~5 MB. The
daemon builds one app and never noticed; the test suites build one per test and kept every one
(2026-11-16: 40 apps alive after 40 browser tests, every browser-suite worker grown to ~1.9 GB
and OOM-killed inside the `uitest` cgroup's 4 GiB, twelve times since 2026-11-13). A
module-level function is one key for the life of the process, whatever number of apps it serves.

So nothing here may close over an app: what a handler needs is on `request.app.state` (or
`ws.app.state`), which `create_app` fills. `tests/test_app_release.py` pins that an app
`create_app` built is freed once it is dropped.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import FileResponse

from . import actionlimits, assetskew

STATIC_DIR = Path(__file__).resolve().parents[3] / "static"

#: Token-gated: `create_app` includes it with the bearer dependency.
authed = APIRouter()
#: The console's root files and the event socket. A WebSocket route takes no `deps`: an HTTP
#: dependency fails on a websocket scope (it has no Request), so the socket is admitted by its
#: stream ticket inside `serve`.
public = APIRouter()


def setup_marker(server) -> Path | None:
    return (server.source.parent / ".setup-complete") if server.source else None


@authed.get("/api/status")
def status(request: Request) -> dict:
    from .. import __version__, registry
    from ..schedule import server_tz

    state = request.app.state
    server = state.server
    marker = setup_marker(server)
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
    return {"version": __version__, "build": state.build, "server_tz": server_tz(),
            # F696: `build` is the process; `disk_build` is the checkout /static is served
            # from NOW, and `build_skew` says they disagree — the console then tells the
            # operator to reload instead of posting new fields at an old process.
            **assetskew.status_fields(server.source_repo, state.build),
            "needs_setup": needs_setup, "llm_ready": llm_ready,
            # Where the shared browser can be WATCHED (empty = this deployment never
            # published a screen). The console shows the Browser section only when it
            # is set, because a dead link to a port nobody opened is worse than no link.
            "browser_view_url": server.browser_view_url,
            # The agent desktops' two addresses, and whether BOTH are set — the Desktops
            # page, its nav link and its dock show only then, on the same reasoning.
            "desktop_broker_url": server.desktop_broker_url,
            "desktop_view_url": server.desktop_view_url,
            "desktops": server.desktops_configured,
            # Whether the Playground page and its nav entry exist in this console
            # (config `playground_enabled`) — experimental features, off by default.
            "playground_enabled": server.playground_enabled,
            "action_limits": actionlimits.LIMITS,   # web/actionlimits.py — D252
            "meta_routines": meta_routines, **state.scheduler.snapshot()}


@authed.post("/api/setup/complete")
def setup_complete(request: Request) -> dict:
    """The first-run setup flow calls this once the user has configured (or chosen to skip)
    providers + repos — it stops the first-launch redirect to Settings.
    """
    marker = setup_marker(request.app.state.server)
    if marker:
        marker.write_text("done\n", encoding="utf-8")
    return {"ok": True}


@authed.post("/api/stream-ticket")
def stream_ticket(request: Request) -> dict:
    """A short-lived, unguessable query-string credential for ONE live-stream socket (the
    WebSocket API cannot send an Authorization header). `streams.admits` spends it on the
    handshake, so every connection and every reconnect mints its own; expired, unspent
    tickets are purged here.
    """
    from .streams import STREAM_TICKET_TTL_S

    now = time.monotonic()
    tickets = request.app.state.stream_tickets
    # A sync route runs on a worker thread, so two mints can purge at once — every
    # browser tab reconnecting after a restart asks together. Both see the same expired
    # ticket, and a bare `del` made the slower one a 500; the snapshot keeps the
    # iteration off a dict another thread is growing.
    for stale, exp in list(tickets.items()):
        if exp < now:
            tickets.pop(stale, None)
    ticket = secrets.token_urlsafe(24)
    tickets[ticket] = now + STREAM_TICKET_TTL_S
    return {"ticket": ticket, "ttl": STREAM_TICKET_TTL_S}


@public.websocket("/api/events")
async def global_events(ws: WebSocket) -> None:
    from .streams import bus_stream, serve

    bus = ws.app.state.bus
    await serve(ws, lambda: bus_stream(bus))


@public.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@public.get("/sw.js", include_in_schema=False)
def service_worker():
    # served from the root (not /static/) so the worker's scope covers the whole console
    return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript")


@public.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest():
    # served from the root so an installed PWA's scope covers the whole console — the
    # manifest (display:standalone) is what lets the console be added to a phone's home
    # screen, which iOS Safari requires before it will deliver Web Push notifications.
    return FileResponse(STATIC_DIR / "manifest.webmanifest",
                        media_type="application/manifest+json")
