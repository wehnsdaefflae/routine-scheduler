"""Webhook ingest (the ONE unauthenticated API route) + the routine page's trigger list.

POST /api/hooks/<slug>/<token> is called by THIRD PARTIES (CI, monitors, IFTTT-style
services), so it deliberately takes no global bearer: the per-trigger URL token —
server-generated, compared constant-time — IS the auth. The handler's only job is to
RECORD the event durably in the trigger spool (rsched.triggers.write_event — the same
request-file idiom restart.request and the background .requests/ use); FIRING is the
daemon's job (daemon/triggers.py at the scheduler tick), which keeps one-run-per-routine,
max_concurrent_runs, and coalescing in one place. Hardening: one generic 404 for unknown
slug / wrong token / a routine that may not fire (no existence oracle), a payload size cap,
a per-slug accept rate limit + a durable spool cap so a leaked URL can't fill the disk,
the payload is never echoed back, and every rejection is logged (never the payload).

`hooks_router` is wired in app.py WITHOUT the auth dependency. The trigger list itself is a
field of the settings page (0.369.0): added, re-bounded and removed in the draft and landed by
its one accept (api_settings), which asks `reconciled_triggers` here what the new list is.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import deque
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request

from .. import registry, triggers
from ..patterns import fields

log = logging.getLogger("rsched.hooks")

hooks_router = APIRouter(tags=["hooks"])   # unauthenticated ingest (see app.py wiring)

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

    Bytes on both sides, as `require_auth` compares the bearer: given a `str`,
    `compare_digest` RAISES on a non-ASCII character, and the token is whatever the caller
    put in the URL — `%C3%A9` was a 500 instead of the 404 every other wrong token gets.
    """
    candidates = ([t for t in info.cfg.triggers if t.get("type") == "webhook"]
                  if info is not None else [])
    presented = token.encode()
    matched: dict | None = None
    for t in candidates:
        if secrets.compare_digest(str(t.get("token") or "").encode(), presented):
            matched = t
    if not candidates:
        secrets.compare_digest(_DUMMY_TOKEN.encode(), presented)
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
    # The whole catalog, on purpose — a per-slug lookup would answer a known slug faster than
    # an unknown one, and timing would say what the 404 refuses to. In a worker thread,
    # because this handler is async and nobody has authenticated yet: on the event loop, every
    # garbage POST stalled each SSE stream and async route for the length of the walk.
    info = (await asyncio.to_thread(registry.scan, server)).get(slug)
    trigger = _match_webhook(info, token)
    # `fireable`, the registry's ONE fire predicate: a switched-off routine and a RETIRED one
    # (its finish line reached) alike. On `enabled` alone a retired routine's hook answered 202
    # for an event the daemon then dropped unread.
    if info is None or trigger is None or not info.fireable:
        # one generic answer for unknown slug / wrong token / not fireable — no oracle
        why = ("unknown routine" if info is None
               else "no matching token" if trigger is None else "routine switched off or retired")
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


# -- the trigger list (the settings page's Triggers field) --------------------------------

#: What the Triggers card can CREATE. `imap` and `watch_path` are reserved shape with no
#: watcher yet (rsched/triggers.py): kept when a file already names one, never minted here.
CREATABLE_TRIGGERS = ("webhook", "report")
_BOUNDS = ("cooldown_s", "max_fires_per_day")


def reconciled_triggers(current: list[dict], target: object) -> list[dict]:
    """The trigger list that makes `current` (routine.yaml's entries) equal `target` (canonical
    rows: type + bounds, no identity). A trigger that is already there keeps its id and — for
    a webhook — its token, so a URL a third party holds keeps working; only a trigger the
    target lacks is removed, only one it adds is created. A row whose bounds changed is
    therefore a NEW trigger, under a new URL — the card says so before the accept.

    Writes nothing: a row that cannot become a trigger is refused here (422), before the
    accept has written anything at all.
    """
    wanted = fields.canonical("triggers", target)
    want = list(wanted) if isinstance(wanted, list) else []
    kept: list[dict] = []
    for entry in current:
        rows = fields.canonical("triggers", [entry])
        row = rows[0] if isinstance(rows, list) and rows else {}
        if row in want:
            want.remove(row)
            kept.append(entry)
    added = [_new_trigger(row) for row in want]
    if any(t["type"] == "report" for t in added) \
            and sum(t.get("type") == "report" for t in kept + added) > 1:
        raise HTTPException(422, "triggers: one report trigger per routine — one inbox, one "
                                 "watcher; change the bounds of the one it has instead")
    return kept + added


def _new_trigger(row: dict) -> dict:
    """The trigger a row the accepted list GAINS stands for: identity minted server-side (the
    id and, for a webhook, the token that is the hook's only auth), configuration exactly as
    the row carries it. `fields.canonical` spells both bounds out, and `0` is a value — no
    wait, no cap — never an absence to fill with the type's default.
    """
    ttype = row.get("type")
    if ttype not in CREATABLE_TRIGGERS:
        raise HTTPException(422, f"triggers: a {ttype!r} trigger cannot be created — one of "
                                 f"{', '.join(CREATABLE_TRIGGERS)}")
    if stray := sorted(set(row) - {"type", *_BOUNDS}):
        raise HTTPException(422, f"triggers: a {ttype} trigger has no {', '.join(stray)}")
    for key in _BOUNDS:
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HTTPException(422, f"triggers: {key} is a whole number, 0 or more "
                                     f"(got {value!r})")
    entry = (triggers.new_report_trigger() if ttype == "report"
             else triggers.new_webhook_trigger())
    entry.update({key: row[key] for key in _BOUNDS})
    return entry
