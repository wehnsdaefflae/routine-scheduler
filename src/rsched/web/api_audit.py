"""Audit tab: read the self-audit routine's latest report + changelog, and write reviewer
feedback (comments / decisions / free notes) into that routine's inbox as tagged messages.

Feedback reuses the standard inbox message channel (`msg-*.json`, drained as a user message),
so the routine consumes it on its next/current run with no engine changes. The report and
changelog are routine-owned artifacts under `<routine>/audit/` — this layer only reads them.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..daemon import registry
from ..ids import now_iso
from ..paths import atomic_write_json, read_json

router = APIRouter(tags=["audit"])

SELF_AUDIT_SLUG = "self-audit"
_ACTIVE = ("queued", "running", "waiting_user", "paused", "starting")


def _routine_dir(request: Request) -> Path:
    return request.app.state.server.routines_home / SELF_AUDIT_SLUG


def _read_changelog(path: Path, limit: int = 100) -> list[dict]:
    """Append-only JSONL of code changes the routine made, returned newest-first."""
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    except OSError:
        return []
    out.reverse()
    return out[:limit]


def _pending_feedback(routine_dir: Path) -> list[dict]:
    """Web-submitted feedback still sitting in the inbox (not yet consumed by a run) — the UI
    shows these so a reviewer can see exactly what the next self-audit run will pick up."""
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return []
    out = []
    for path in sorted(inbox.glob("msg-*.json")):
        obj = read_json(path)
        if isinstance(obj, dict) and obj.get("via") == "web-audit":
            out.append({"text": str(obj.get("text") or ""), "ts": str(obj.get("ts") or "")})
    return out


@router.get("/audit")
def audit(request: Request) -> dict:
    routine_dir = _routine_dir(request)
    exists = routine_dir.is_dir()
    report = read_json(routine_dir / "audit" / "report.json") if exists else None
    if not isinstance(report, dict):
        report = None
    changelog = _read_changelog(routine_dir / "audit" / "changelog.jsonl") if exists else []
    last_run = None
    runs = registry.run_index(routine_dir, SELF_AUDIT_SLUG) if exists else []
    if runs:
        r = runs[0]
        last_run = {"run_id": r.run_id, "ts": r.ts, "state": r.state, "summary": r.summary[:400]}
    return {"exists": exists, "routine": SELF_AUDIT_SLUG, "report": report,
            "changelog": changelog, "last_run": last_run,
            "pending_feedback": _pending_feedback(routine_dir) if exists else []}


class Feedback(BaseModel):
    kind: str                       # "comment" | "decision" | "general"
    target: str | None = None       # finding/decision id (F1, D1, …) for comment/decision
    text: str | None = None         # the comment / free note / optional decision note
    choice: str | None = None       # the chosen option, for a decision


def _format_feedback(fb: Feedback) -> str:
    text = (fb.text or "").strip()
    target = (fb.target or "").strip()
    if fb.kind == "comment":
        if not target or not text:
            raise HTTPException(400, "a comment needs a target finding and text")
        return f"[AUDIT feedback · finding {target}] {text}"
    if fb.kind == "decision":
        choice = (fb.choice or "").strip()
        if not target or not (choice or text):
            raise HTTPException(400, "a decision needs a target and a choice or note")
        base = f"[AUDIT decision · {target}] selected: {choice or '(free text)'}"
        return f"{base} — {text}" if text else base
    if fb.kind == "general":
        if not text:
            raise HTTPException(400, "empty note")
        return f"[AUDIT note] {text}"
    raise HTTPException(400, f"unknown feedback kind {fb.kind!r}")


@router.post("/audit/feedback")
def audit_feedback(request: Request, body: Feedback) -> dict:
    routine_dir = _routine_dir(request)
    if not routine_dir.is_dir():
        raise HTTPException(404, "the self-audit routine is not set up yet")
    text = _format_feedback(body)
    # unique suffix: now_iso() is only second-resolution, so several submissions in the same
    # second would otherwise share a filename and clobber each other (lost feedback).
    fname = f"msg-{now_iso().replace(':', '')}-{uuid.uuid4().hex[:8]}.json"
    atomic_write_json(routine_dir / "inbox" / fname,
                      {"text": text, "ts": now_iso(), "via": "web-audit"})
    active = request.app.state.runner.is_active(SELF_AUDIT_SLUG)
    return {"ok": True, "delivery": "mid-run" if active else "next-run"}
