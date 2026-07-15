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


@router.post("/ui-trace")
def ingest(request: Request, body: TraceBatch) -> dict:
    server = request.app.state.server
    now = dt.datetime.now(dt.UTC)
    lines = []
    for ev in body.events:
        if ev.kind not in KINDS:
            continue
        lines.append(json.dumps({
            "ts": now.isoformat(timespec="seconds"),
            "kind": ev.kind,
            "view": ev.view[:MAX_FIELD],
            "target": ev.target[:MAX_FIELD],
            "detail": ev.detail[:MAX_FIELD],
        }, ensure_ascii=False))
    if not lines:
        return {"recorded": 0}
    d = traces_dir(server)
    d.mkdir(parents=True, exist_ok=True)
    day_file = d / f"{now.strftime('%Y%m%d')}.jsonl"
    try:
        with day_file.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        _prune(d)
    except OSError as exc:
        # Tracing must never 500 the console, but a dead trace store shouldn't be silent
        # either — and the count must be honest.
        log.warning("ui-trace write failed: %s", exc)
        return {"recorded": 0}
    return {"recorded": len(lines)}
