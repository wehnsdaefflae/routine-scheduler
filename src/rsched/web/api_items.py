"""Items tab: the system-maintenance index — every finding, decision and bug report with
its status, purpose, origin and the changelog rows that addressed it (docs/items.md).

This is the READ half; `api_audit` keeps the one write channel the page has (reviewer
feedback into the self-audit routine's inbox). The endpoint also carries the report header
the page shows — the current window, the summary, the last self-audit run — which is why
the old `GET /api/audit` is gone rather than kept beside it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request

from .. import registry
from ..paths import read_json
from ..readmodels import items as items_model
from ..readmodels.items import SELF_AUDIT_SLUG
from .api_audit import answered_decisions, pending_feedback

router = APIRouter(tags=["items"])


def _routine_dir(request: Request) -> Path:
    return request.app.state.server.routines_home / SELF_AUDIT_SLUG


def _report_header(routine_dir: Path) -> dict | None:
    """The report's own meta, without the findings/decisions arrays — those are items now."""
    report = read_json(routine_dir / "audit" / "report.json")
    if not isinstance(report, dict):
        return None
    return {"run_id": str(report.get("run_id") or ""),
            "generated": str(report.get("generated") or ""),
            "since": report.get("since") if isinstance(report.get("since"), dict) else {},
            "summary": str(report.get("summary") or "")}


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
                "pending_feedback": [], "answered_decisions": []}

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
            "pending_feedback": pending_feedback(routine_dir),
            "answered_decisions": answered_decisions(routine_dir, report)}
