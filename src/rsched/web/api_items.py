"""The Messages page's item index (`GET /api/items`): every finding, decision and report
with its status, purpose, origin and the changelog rows that addressed it (docs/items.md;
the page rename is D74 — the ids and the endpoint keep the item vocabulary).

Mostly the READ half; `api_audit` keeps the page's structured write channel (reviewer
feedback into the self-audit routine's inbox) and `api_messages` the free-form one, while
the one write that lives HERE is the ⚑ priority toggle — UI state about an item, not a
message to a run (priorities.py). The GET also carries the report header the page shows —
the current window, the summary, the last self-audit run — which is why the old
`GET /api/audit` is gone rather than kept beside it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from .. import priorities, registry
from ..paths import read_json
from ..readmodels import items as items_model
from ..readmodels.items import SELF_AUDIT_SLUG
from .api_audit import _routine_dir, answered_decisions, queued_messages

router = APIRouter(tags=["items"])


def _report_header(routine_dir: Path) -> dict | None:
    """The report's own meta, without the findings/decisions arrays — those are items now."""
    report = read_json(routine_dir / "audit" / "report.json")
    if not isinstance(report, dict):
        return None
    return {"run_id": str(report.get("run_id") or ""),
            "generated": str(report.get("generated") or ""),
            "since": report.get("since") if isinstance(report.get("since"), dict) else {},
            "summary": str(report.get("summary") or "")}


@router.get("/items/orphans")
def orphans(request: Request) -> list[dict]:
    """The two ways work leaves the ledger without becoming an open item anywhere
    (readmodels/orphans.py). Both are invisible to every filter on the Messages page, so the
    page banners them above the list; both are surfaced rather than gated — a human judges.

    `kind: "deferral"` — an item deferred part of its scope into another, the carrier shipped
    its own scope and closed, and the deferred piece became an open item nowhere.
    `kind: "undelivered"` — an ADDRESSED report whose message never reached its target's inbox,
    so the target can never drain it and it sits open forever (D114).
    """
    from ..readmodels import orphans as orphans_model

    home = request.app.state.server.routines_home
    return orphans_model.load(_routine_dir(request)) + orphans_model.load_undelivered(home)


@router.get("/items")
def items(request: Request,
          type_: Annotated[str, Query(alias="type")] = "",
          status: str = "", routine: str = "", search: str = "",
          limit: int = 500) -> dict:
    """The merged item index, newest origin first. `counts` is always over the UNFILTERED
    set so the filter chips show what is there, not what is left after filtering.
    """
    server = request.app.state.server
    routine_dir = _routine_dir(request)
    exists = routine_dir.is_dir()
    if not exists:
        return {"exists": False, "routine": SELF_AUDIT_SLUG, "items": [],
                "counts": {"type": {}, "status": {}}, "report": None, "last_run": None,
                "queued": [], "answered_decisions": []}

    merged = items_model.build(routine_dir, server.routines_home)
    shown = items_model.filter_items(merged["items"], type_=type_, status=status,
                                     routine=routine, search=search)
    report = _report_header(routine_dir)
    runs = registry.run_index(routine_dir, SELF_AUDIT_SLUG)
    last_run = None
    if runs:
        r = runs[0]
        last_run = {"run_id": r.run_id, "ts": r.ts, "state": r.state, "summary": r.summary[:400]}
    # The changelog as a whole rides along: an item's own history is on its card, but rows
    # that name no item would otherwise be unreachable once the Audit page is gone.
    changelog = items_model.read_changelog(routine_dir / "audit" / "changelog.jsonl")
    return {"exists": True, "routine": SELF_AUDIT_SLUG,
            "items": shown[:max(1, limit)], "total": len(shown), "counts": merged["counts"],
            "changelog": list(reversed(changelog))[:60],
            "report": report, "last_run": last_run,
            "queued": queued_messages(routine_dir),
            "answered_decisions": answered_decisions(routine_dir, report)}


@router.post("/items/{item_id}/priority")
def set_item_priority(request: Request, item_id: str, body: dict) -> dict:
    """Flag or unflag one item as a user priority (`{"on": true|false}`). The ⚑ floats
    the item to the top of the page AND reaches the OWNING routine's next run as a
    state-digest section — ownership resolution lives in priorities.py (D75).
    """
    on = bool((body or {}).get("on", True))
    try:
        priorities.set_priority(request.app.state.server.routines_home, item_id, on)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True, "id": str(item_id).strip().upper(), "on": on}
