"""Webhook ingest (the ONE unauthenticated API route) + trigger CRUD for the routine page.

POST /api/hooks/<slug>/<token> is called by THIRD PARTIES (CI, monitors, IFTTT-style
services), so it deliberately takes no global bearer: the per-trigger URL token —
server-generated, compared constant-time — IS the auth. The handler's only job is to
RECORD the event durably in the trigger spool (rsched.triggers.write_event — the same
request-file idiom restart.request and the background .requests/ use); FIRING is the
daemon's job (daemon/triggers.py at the scheduler tick), which keeps one-run-per-routine,
max_concurrent_runs, and coalescing in one place. Hardening: one generic 404 for unknown
slug / wrong token / disabled routine (no existence oracle), a payload size cap, a
per-slug accept rate limit + a durable spool cap so a leaked URL can't fill the disk,
the payload is never echoed back, and every rejection is logged (never the payload).

`hooks_router` is wired in app.py WITHOUT the auth dependency; `router` (the trigger
CRUD the routine page uses) rides the normal authed include like every other module.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections import deque
from typing import Literal, NoReturn

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import registry, triggers
from ..paths import atomic_write
from .routines_common import _git_commit, _info, _state, guard_not_active, guard_template

log = logging.getLogger("rsched.hooks")

hooks_router = APIRouter(tags=["hooks"])   # unauthenticated ingest (see app.py wiring)
router = APIRouter(tags=["triggers"])      # authed CRUD

RATE_WINDOW_S = 60.0
RATE_MAX_ACCEPTS = 30   # accepted events per slug per window; the spool cap backstops it
# Burned on the no-candidates path so an unknown slug costs the same comparison a wrong
# token does — response timing never says whether the slug exists.
_DUMMY_TOKEN = secrets.token_urlsafe(24)


def _reject(status: int, slug: str, client: str, reason: str, detail: str) -> NoReturn:
    log.warning("hook rejected routine=%s client=%s status=%d (%s)",
                slug, client, status, reason)
    raise HTTPException(status, detail)


def _match_webhook(info: registry.RoutineInfo | None, token: str) -> dict | None:
    """Constant-time token match over the slug's webhook triggers: every candidate is
    compared (no early exit), and a slug with no candidates burns one comparison too.
    """
    candidates = ([t for t in info.cfg.triggers if t.get("type") == "webhook"]
                  if info is not None else [])
    matched: dict | None = None
    for t in candidates:
        if secrets.compare_digest(str(t.get("token") or ""), token):
            matched = t
    if not candidates:
        secrets.compare_digest(_DUMMY_TOKEN, token)
    return matched


async def _read_capped(request: Request) -> bytes | None:
    """Read the body streaming, aborting once it exceeds the cap — so a chunked request
    with no (or a lying) Content-Length can't buffer an unbounded body into memory before
    the size check. Returns the bytes, or None if the stream ran past MAX_PAYLOAD_BYTES.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > triggers.MAX_PAYLOAD_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _rate_window(request: Request, slug: str) -> deque[float]:
    """Sliding accept-window per slug, kept on app.state (in-memory is right: one process
    serves web + daemon, and the durable backstop is the spool cap, not this).
    """
    if not hasattr(request.app.state, "hook_accepts"):
        request.app.state.hook_accepts = {}
    window: deque[float] = request.app.state.hook_accepts.setdefault(slug, deque())
    now = time.monotonic()
    while window and now - window[0] > RATE_WINDOW_S:
        window.popleft()
    return window


@hooks_router.post("/hooks/{slug}/{token}", status_code=202)
async def receive_hook(request: Request, slug: str, token: str) -> dict:
    """Record one webhook event. Accepts any body (stored as text, capped); replies
    202 {"ok": true} and NOTHING else — the payload is never echoed. The daemon picks
    the event up at its next tick (≤~5s) and fires/coalesces per docs/triggers.md.
    """
    server = request.app.state.server
    client = request.client.host if request.client else "?"
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > triggers.MAX_PAYLOAD_BYTES:
        _reject(413, slug, client, f"declared content-length {declared}", "payload too large")
    body = await _read_capped(request)
    if body is None:
        # streamed past the cap (a missing/lying content-length can't sneak a huge body in)
        _reject(413, slug, client, "streamed body over cap", "payload too large")
    info = registry.scan(server).get(slug)
    trigger = _match_webhook(info, token)
    if info is None or trigger is None or not info.cfg.enabled:
        # one generic answer for unknown slug / wrong token / disabled — no oracle
        why = ("unknown routine" if info is None
               else "no matching token" if trigger is None else "routine disabled")
        _reject(404, slug, client, why, "unknown hook")
    window = _rate_window(request, slug)
    if len(window) >= RATE_MAX_ACCEPTS:
        _reject(429, slug, client, "rate limit", "too many events — slow down")
    if len(triggers.pending_events(server.routines_home, slug)) >= triggers.MAX_PENDING_EVENTS:
        _reject(429, slug, client, "spool full", "too many pending events — slow down")
    window.append(time.monotonic())
    triggers.write_event(server.routines_home, slug,
                         trigger_id=str(trigger["id"]),
                         payload=body.decode("utf-8", "replace"),
                         content_type=request.headers.get("content-type", ""),
                         client=client)
    return {"ok": True}


# -- trigger CRUD (authed; the routine page's Triggers card) ------------------------------


class TriggerCreate(BaseModel):
    # only webhooks are creatable today — imap/watch_path are reserved shape, no watcher
    type: Literal["webhook"] = "webhook"
    cooldown_s: int = Field(triggers.DEFAULT_COOLDOWN_S, ge=0)


@router.post("/routines/{slug}/triggers")
def create_trigger(request: Request, slug: str, body: TriggerCreate) -> dict:
    """Append a server-generated webhook trigger to routine.yaml (user config: 409 while
    a run is active, like every config edit). The response carries the one place the
    full hook URL path is handed out.
    """
    info = _info(request, slug)
    guard_template(slug, "it never runs, so nothing can trigger it")
    guard_not_active(request, info)
    trigger = triggers.new_webhook_trigger(cooldown_s=body.cooldown_s)
    path = info.cfg.dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = [t for t in raw.get("triggers") or [] if isinstance(t, dict)]
    entries.append(trigger)
    raw["triggers"] = entries
    atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    _git_commit(info.cfg.dir, f"add webhook trigger {trigger['id']}")
    _state(request).scheduler.rescan()
    return {"ok": True, "trigger": {**trigger, "url_path": triggers.hook_path(slug, trigger)}}


@router.delete("/routines/{slug}/triggers/{trigger_id}")
def delete_trigger(request: Request, slug: str, trigger_id: str) -> dict:
    """Remove a trigger; its hook URL stops matching immediately, and the daemon drops
    any still-spooled events for it at the next tick.
    """
    info = _info(request, slug)
    guard_not_active(request, info)
    path = info.cfg.dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = [t for t in raw.get("triggers") or [] if isinstance(t, dict)]
    kept = [t for t in entries if str(t.get("id")) != trigger_id]
    if len(kept) == len(entries):
        raise HTTPException(404, f"no trigger {trigger_id!r} on {slug!r}")
    raw["triggers"] = kept
    atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    _git_commit(info.cfg.dir, f"remove trigger {trigger_id}")
    _state(request).scheduler.rescan()
    return {"ok": True}
