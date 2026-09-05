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
from ..readmodels import summaries
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


@router.post("/items/orphans/{report_id}/discard")
def discard_orphan(request: Request, report_id: str) -> dict:
    """Discard an "addressed, never delivered" orphan (readmodels/orphans.find_undelivered): a
    row with a target but no inbox message, which no run can ever drain. Appends a `retracted`
    event so it reads `dropped` and leaves the banner + backlog. 404 unknown id, 409 when the
    row is not an undelivered orphan (delivered, already retracted, unaddressed, or its delivery
    is still waiting — that one is retracted from the routine's outbox instead).
    """
    from ..reports import discard_undelivered_report

    home = request.app.state.server.routines_home
    try:
        discard_undelivered_report(home, report_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"ok": True, "id": report_id}


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
    # A run's finish summary is an item too, and it comes from `registry.scan` rather than from
    # the maintenance record — so it is merged in BEFORE filtering, and it is served on the
    # `exists: False` branch as well: an instance without self-audit has no findings, but its
    # routines still have things to tell you.
    summary_rows = summaries.build(server)
    if not exists:
        only = items_model.filter_items(summary_rows, type_=type_, status=status,
                                        routine=routine, search=search)
        return {"exists": False, "routine": SELF_AUDIT_SLUG,
                "items": only[:max(1, limit)], "total": len(only),
                "counts": items_model.counts(summary_rows), "report": None, "last_run": None,
                "queued": [], "answered_decisions": []}

    merged = items_model.build(routine_dir, server.routines_home)
    all_items = summary_rows + merged["items"]
    shown = items_model.filter_items(all_items, type_=type_, status=status,
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
            "items": shown[:max(1, limit)], "total": len(shown),
            # recomputed over the MERGED set: `counts` is documented as always being over the
            # unfiltered whole, so it has to include the summaries the page can now filter to
            "counts": items_model.counts(all_items),
            "changelog": list(reversed(changelog))[:60],
            "report": report, "last_run": last_run,
            "queued": queued_messages(routine_dir),
            "answered_decisions": answered_decisions(routine_dir, report)}


@router.post("/items/{item_id}/read")
def set_item_read(request: Request, item_id: str, body: dict) -> dict:
    """Dismiss (or un-dismiss) a routine's latest finish message (`{"read": true|false}`).

    Summaries only — the maintenance items have their own status vocabulary and are settled by
    the work, not by being looked at. The store is a WATERMARK per routine, so a newer run
    resurfaces on its own.
    """
    server = request.app.state.server
    if ":" not in item_id:
        raise HTTPException(400, "only a summary can be marked read — its id is a run id")
    read = bool((body or {}).get("read", True))
    slug = summaries.mark_read(server.routines_home, item_id, read=read)
    return {"ok": True, "id": item_id, "routine": slug, "read": read}


@router.post("/items/read-all")
def mark_all_summaries_read(request: Request) -> dict:
    """Dismiss every currently-shown summary at once (F303 — without it, clearing the backlog
    is one click per routine).
    """
    server = request.app.state.server
    return {"ok": True, "marked": summaries.mark_all_read(server.routines_home, server)}


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
