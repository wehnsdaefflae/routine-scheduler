"""Shared surface for the new-routine wizard routes: the one APIRouter every wizard module
attaches to, plus the helpers used by both the session/clarify half (wizard_sessions) and the
build half (api_wizard). No route handlers live here — see wizard_sessions and api_wizard.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["wizard"])


def _wizard_pid(wid: str) -> str:
    """The LLM-task-manager process id for a whole routine-creation flow — shared across the
    separate wizard requests (start → suggest → generate → finalize) so their calls group.
    """
    return f"create:{wid.lstrip('.')}"


def _center(state):
    return getattr(state, "llm_tasks", None)


def _wizard_recorder(center, pid: str, rid: str):
    """Fold the clarify subprocess's LLM records into the create process (cross-process link)."""
    def _on(rec: dict) -> None:
        rec["run_id"] = rid
        rec["process_id"] = pid
        center.ingest(rec)
    return _on


def _stop_tailer(sess) -> None:
    t = (sess or {}).get("tailer")
    if t is not None:
        t.cancel()


def _wizard_dir(request: Request, wid: str) -> Path:
    d = request.app.state.server.routines_home / wid
    if not wid.startswith(".wizard-") or not d.is_dir():
        raise HTTPException(404, f"no wizard session {wid!r}")
    return d


