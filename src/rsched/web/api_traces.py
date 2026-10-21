"""UI interaction traces — evidence for the improve-ui improvement lens.

The frontend batches lightweight events (view navigations, control clicks by label,
error toasts, stream reconnects) to POST /api/ui-trace; they land as daily JSONL under
routines_home/.ui-traces/ (a dot-dir the registry scan ignores), where the routine-improver's
improve-ui lens (and the self-audit's evidence pass) read them. Values are truncated here so
no free-text user content grows unbounded; files beyond the retention window are pruned
on write.

A row carries THREE time fields, because its readers infer friction from inter-event timing
and that inference is only as good as what the store preserves (F577):
  `ts`        when the event happened — the browser's own clock, per event;
  `received`  when the batch carrying it reached the server — shared by a whole flush;
  `ts_src`    which clock `ts` came from: `client`, `server` (none was sent), or `skew` (one
              was sent and was not credible, so `ts` fell back to `received` and the claim is
              kept under `ts_claimed`).
`ts` was the flush time before F577 — one `now` per POST — which quantised every interval
inside a 5 s flush window (or a whole `pagehide` tail) to zero and manufactured co-occurrence
between events that merely shared a batch. Rows written then carry `ts` alone, so a reader
takes `received`/`ts_src` with `.get` and treats a row without `ts_src` as batch-stamped.
"""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..paths import append_jsonl

log = logging.getLogger("rsched.traces")
router = APIRouter(tags=["traces"])

KINDS = ("nav", "click", "submit", "error", "reconnect", "freeze")
MAX_BATCH = 100
MAX_FIELD = 200
KEEP_DAYS = 14
MAX_TS_CHARS = 40          # an ISO instant with milliseconds and an offset fits in ~30

# F577 — how far a CLIENT's own event time may sit from the moment the server received the
# batch and still be trusted. The window is deliberately ASYMMETRIC, because the two
# directions mean opposite things:
#   LAG (the client stamp is OLDER than the receive stamp) is the normal case — an event is
#   queued and flushed up to 5 s later, a backgrounded tab's flush timer is throttled to
#   roughly once a minute, and the `pagehide` flush can carry a whole session's tail. So
#   lateness of many minutes is ordinary traffic, not a broken clock. An hour is far beyond any
#   flush window while still far short of a clock wrong by days, by a timezone, or unset.
#   LEAD (the client stamp is in the FUTURE of the receive stamp) cannot legitimately happen at
#   all: the event precedes the POST that carries it. The only slack needed covers a clock off
#   by seconds plus flight time, so a minute is already generous — anything more is a wrong
#   clock, which is exactly what we must not silently adopt as the friction signal's time base.
# A stamp outside the window is neither trusted nor dropped: the row keeps the server's stamp
# and records that it fell back (`ts_src`), so a skewed client is measurable instead of silent.
MAX_TS_LAG_S = 3600
MAX_TS_LEAD_S = 60


class TraceEvent(BaseModel):
    kind: str
    view: str = ""
    target: str = ""
    detail: str = ""
    #: When the event HAPPENED, by the browser's clock (trace.js stamps it in `trace()`). The
    #: field has to exist here or the schema drops it: that silent drop is what made every
    #: recorded time a flush time (F577). Empty = a client that sends none.
    ts: str = ""


class TraceBatch(BaseModel):
    events: list[TraceEvent] = Field(max_length=MAX_BATCH)


def traces_dir(server):
    return server.routines_home / ".ui-traces"


def _prune(d) -> None:
    # UTC, matching the day-file names below (now.strftime over an aware UTC now)
    cutoff = (dt.datetime.now(dt.UTC).date() - dt.timedelta(days=KEEP_DAYS)).strftime("%Y%m%d")
    for p in d.glob("*.jsonl"):
        if p.stem < cutoff:
            p.unlink(missing_ok=True)


def _stamp(raw: object, received: dt.datetime) -> dict[str, str]:
    """The time fields of ONE record — never of a batch (F577).

    `ts` is when the event happened, `received` is when the batch carrying it arrived, and
    `ts_src` names the clock `ts` came from: `client` (the browser's own stamp, credible), or
    `server` (no stamp was sent — a server-originated trace, or a pre-F577 client), or `skew`
    (one was sent and is not credible, so the row falls back to the server's stamp and keeps
    the claim under `ts_claimed` — how far off the client is stays measurable instead of
    disappearing into a silent fallback).

    Resolution is milliseconds, not seconds: the signal these rows exist for is a burst of
    clicks within a second, which a whole-second stamp quantises away just as the batch stamp
    did.
    """
    server = received.isoformat(timespec="milliseconds")
    fields = {"ts": server, "received": server, "ts_src": "server"}
    text = str(raw or "")[:MAX_TS_CHARS]
    if not text:
        return fields
    try:
        when = dt.datetime.fromisoformat(text)
    except ValueError:
        when = None
    # A naive stamp is a wall clock in an unknown zone, which is no instant at all.
    credible = (when is not None and when.tzinfo is not None
                and -MAX_TS_LEAD_S <= (received - when).total_seconds() <= MAX_TS_LAG_S)
    if not credible:
        return fields | {"ts_src": "skew", "ts_claimed": text}
    return fields | {"ts": when.isoformat(timespec="milliseconds"), "ts_src": "client"}  # type: ignore[union-attr]


def _append(server, records: list[dict]) -> int:
    """Append vetted trace records to today's day file; returns the count written.

    The receive time is a property of the BATCH and is recorded as one (`received`); the event
    time is a property of each RECORD and is taken from each record. Stamping every row of a
    flush with one `now` is what made the store blind to its own subject (F577): every interval
    inside a 5 s flush window — or inside a whole `pagehide` tail — collapsed to zero.

    The day FILE still follows the server clock, because `_prune` compares file stems against a
    server-side cutoff: letting a client stamp pick the file would let a wrong clock write one
    that is pruned on arrival, or never.
    """
    received = dt.datetime.now(dt.UTC)
    rows = [{
        **_stamp(r.get("ts"), received),
        "kind": str(r.get("kind", ""))[:MAX_FIELD],
        "view": str(r.get("view", ""))[:MAX_FIELD],
        "target": str(r.get("target", ""))[:MAX_FIELD],
        "detail": str(r.get("detail", ""))[:MAX_FIELD],
    } for r in records]
    if not rows:
        return 0
    d = traces_dir(server)
    try:
        append_jsonl(d / f"{received.strftime('%Y%m%d')}.jsonl", *rows)
        _prune(d)
    except OSError as exc:
        # Tracing must never take its caller down, but a dead trace store shouldn't be
        # silent either — and the count must be honest.
        log.warning("ui-trace write failed: %s", exc)
        return 0
    return len(rows)


def record_server_trace(server, *, kind: str, target: str = "", detail: str = "",
                        view: str = "server") -> None:
    """A SERVER-originated trace event, appended to the same day file the browser batches
    land in — one evidence stream for the audits. First use: `stream-close` (F175), so client
    `reconnect` traces can be matched against what the server saw at the same moment.
    """
    _append(server, [{"kind": kind, "view": view, "target": target, "detail": detail}])


@router.post("/ui-trace")
def ingest(request: Request, body: TraceBatch) -> dict:
    server = request.app.state.server
    return {"recorded": _append(server, [ev.model_dump() for ev in body.events
                                         if ev.kind in KINDS])}
