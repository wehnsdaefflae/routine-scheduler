"""UI interaction traces — evidence for the improve-ui improvement lens.

The frontend batches lightweight events (view navigations, control clicks by label,
error toasts, stream reconnects) to POST /api/ui-trace; they land as daily JSONL under
routines_home/.ui-traces/ (a dot-dir the registry scan ignores), where the routine-improver's
improve-ui lens (and the self-audit's evidence pass) read them. Values are truncated here so
no free-text user content grows unbounded; files beyond the retention window are pruned
on write.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

log = logging.getLogger("rsched.traces")
router = APIRouter(tags=["traces"])

KINDS = ("nav", "click", "submit", "error", "reconnect")
MAX_BATCH = 100
MAX_FIELD = 200
KEEP_DAYS = 14


class TraceEvent(BaseModel):
    kind: str
    view: str = ""
    target: str = ""
    detail: str = ""


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


def _append(server, records: list[dict]) -> int:
    """Append vetted trace records to today's day file; returns the count written."""
    now = dt.datetime.now(dt.UTC)
    lines = [json.dumps({
        "ts": now.isoformat(timespec="seconds"),
        "kind": str(r.get("kind", ""))[:MAX_FIELD],
        "view": str(r.get("view", ""))[:MAX_FIELD],
        "target": str(r.get("target", ""))[:MAX_FIELD],
        "detail": str(r.get("detail", ""))[:MAX_FIELD],
    }, ensure_ascii=False) for r in records]
    if not lines:
        return 0
    d = traces_dir(server)
    d.mkdir(parents=True, exist_ok=True)
    day_file = d / f"{now.strftime('%Y%m%d')}.jsonl"
    try:
        with day_file.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        _prune(d)
    except OSError as exc:
        # Tracing must never take its caller down, but a dead trace store shouldn't be
        # silent either — and the count must be honest.
        log.warning("ui-trace write failed: %s", exc)
        return 0
    return len(lines)


def record_server_trace(server, *, kind: str, target: str = "", detail: str = "",
                        view: str = "server") -> None:
    """A SERVER-originated trace event, appended to the same day file the browser batches
    land in — one evidence stream for the audits. First use: `sse-close` (F175), so client
    `reconnect` traces can be matched against what the server saw at the same moment.
    """
    _append(server, [{"kind": kind, "view": view, "target": target, "detail": detail}])


@router.post("/ui-trace")
def ingest(request: Request, body: TraceBatch) -> dict:
    server = request.app.state.server
    return {"recorded": _append(server, [ev.model_dump() for ev in body.events
                                         if ev.kind in KINDS])}
