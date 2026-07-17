"""Instance-wide full-text search endpoint (the global header search box's backend).

GET /api/search?q=… returns ranked, snippet-carrying hits over both homes' searchable
prose (see rsched.search). Each hit carries enough metadata (home, slug, run_ts, sub,
kind, turn, phase) for the client to group results routine → run → matching doc and
deep-link into the run / conversation / decisions views. The index is a pure cache under
`<routines_home>/.control/` — a bounded query-time refresh keeps results honest (deleted
runs never surface, new prose appears), and the lifespan's maintainer task keeps the
backlog drained so that top-up stays cheap.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Request

from ..search import SearchIndex

log = logging.getLogger("rsched.search")

router = APIRouter(tags=["search"])

# The query-time freshness top-up: long enough to fold in a routine's latest run,
# short enough that a search over a cold/backlogged index still answers promptly
# (the maintainer drains the rest).
QUERY_REFRESH_BUDGET_S = 2.0
MAINTAIN_INTERVAL_S = 60.0
MAINTAIN_BUDGET_S = 15.0


@router.get("/search")
def search(request: Request, q: str = "", limit: int = 50) -> dict:
    """Ranked full-text hits. `index.pending` > 0 in the response means the index is
    still catching up (cold boot, deep backlog) — results are valid but may be
    incomplete; the client surfaces that.
    """
    index: SearchIndex = request.app.state.search
    if not q.strip():
        raise HTTPException(400, "empty query — pass ?q=<terms>")
    stats = index.refresh(budget_s=QUERY_REFRESH_BUDGET_S)
    try:
        hits = index.search(q, limit=limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except sqlite3.OperationalError as exc:
        raise HTTPException(400, f"unsupported query syntax: {exc}") from exc
    return {"hits": hits, "index": stats}


async def maintain(index: SearchIndex) -> None:
    """The lifespan's index-warming loop: bounded refresh passes in a worker thread
    (the event loop never blocks), back-to-back while a backlog remains (boot, bulk
    changes), then one pass a minute. Errors are logged and retried — search freshness
    must never take the daemon down.
    """
    while True:
        pending = 0
        try:
            pending = (await asyncio.to_thread(index.refresh, MAINTAIN_BUDGET_S))["pending"]
        except Exception as exc:
            log.warning("search index maintainer: %s", exc)
        await asyncio.sleep(1.0 if pending else MAINTAIN_INTERVAL_S)
