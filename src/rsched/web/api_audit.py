"""Audit tab: read the self-audit routine's latest report + changelog, and write reviewer
feedback (comments / decisions / free notes) into that routine's inbox as tagged messages.

Feedback reuses the standard inbox message channel (`msg-*.json`, drained as a user message),
so the routine consumes it on its next/current run with no engine changes. Until a run drains
it, a queued message stays editable and withdrawable (PUT/DELETE by its id) — the message file
keeps the structured fields (kind/target/choice/raw) alongside the formatted text so an edit
can re-format cleanly. The report and changelog are routine-owned artifacts under
`<routine>/audit/` — this layer only reads them.
"""

from __future__ import annotations

import json
import re
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


# messages written before feedback became editable carry only the formatted text —
# recover their structured fields so they stay editable too
_LEGACY_COMMENT_RE = re.compile(r"^\[AUDIT feedback · finding ([^\]]+)\]\s*(.*)$", re.S)
_LEGACY_NOTE_RE = re.compile(r"^\[AUDIT note\]\s*(.*)$", re.S)


def _pending_feedback(routine_dir: Path) -> list[dict]:
    """Web-submitted feedback still sitting in the inbox (not yet consumed by a run) — the UI
    shows these so a reviewer can see exactly what the next self-audit run will pick up, and
    edit or withdraw any of it (by `id`) until then."""
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return []
    out = []
    for path in sorted(inbox.glob("msg-*.json")):
        obj = read_json(path)
        if not (isinstance(obj, dict) and obj.get("via") == "web-audit"):
            continue
        item = {"id": path.stem, "text": str(obj.get("text") or ""), "ts": str(obj.get("ts") or ""),
                "kind": str(obj.get("kind") or ""), "target": str(obj.get("target") or ""),
                "choice": str(obj.get("choice") or ""), "raw": str(obj.get("raw") or "")}
        if not item["kind"]:
            if m := _LEGACY_COMMENT_RE.match(item["text"]):
                item.update(kind="comment", target=m.group(1).strip(), raw=m.group(2))
            elif m := _LEGACY_NOTE_RE.match(item["text"]):
                item.update(kind="general", raw=m.group(1))
        out.append(item)
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


def _message_payload(body: Feedback, ts: str, edited: str | None = None) -> dict:
    """One inbox message: `text` is what the engine injects; the structured fields exist
    only so the web layer can re-open and re-format it while it is still queued."""
    payload = {"text": _format_feedback(body), "ts": ts, "via": "web-audit",
               "kind": body.kind, "target": (body.target or "").strip(),
               "choice": (body.choice or "").strip(), "raw": (body.text or "").strip()}
    if edited:
        payload["edited"] = edited
    return payload


def _record_decision_answer(routine_dir, decision_id: str) -> None:
    """Durable marker that the user answered this decision. The inbox message alone is
    not enough: a mid-run delivery consumes it instantly, and with the report still
    listing the decision open it re-enters the Decisions inbox — the user answers again,
    the routine gets the same injection again, forever. The marker outlives consumption;
    _audit_decisions hides a decision answered at-or-after the report's `generated`
    until a NEWER report explicitly lists it open again."""
    path = routine_dir / "audit" / "decisions-answered.json"
    data = read_json(path)
    if not isinstance(data, dict):
        data = {}
    data[decision_id] = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)


def write_feedback(routine_dir, body: Feedback) -> str:
    """Format + drop one feedback message into the self-audit inbox (shared with the
    Decisions page, which answers audit decisions through the same channel). Returns the
    message id — the handle for editing/withdrawing it until a run consumes it.
    Unique suffix: now_iso() is only second-resolution, so several submissions in the
    same second would otherwise share a filename and clobber each other."""
    fname = f"msg-{now_iso().replace(':', '')}-{uuid.uuid4().hex[:8]}.json"
    atomic_write_json(routine_dir / "inbox" / fname, _message_payload(body, now_iso()))
    if body.kind == "decision" and (body.target or "").strip():
        _record_decision_answer(routine_dir, (body.target or "").strip())
    return Path(fname).stem


_MSG_ID_RE = re.compile(r"^msg-[\w.+-]+$")


def _queued_message(request: Request, msg_id: str) -> tuple[Path, dict]:
    """Resolve a feedback id to its still-queued inbox file, or 404. Only web-audit messages
    are reachable — the id pattern plus the `via` check keep every other inbox file (question
    answers, routine-page injections) out of this endpoint's hands."""
    if not _MSG_ID_RE.fullmatch(msg_id):
        raise HTTPException(404, f"malformed feedback id {msg_id!r}")
    path = _routine_dir(request) / "inbox" / f"{msg_id}.json"
    obj = read_json(path)
    if not (isinstance(obj, dict) and obj.get("via") == "web-audit"):
        raise HTTPException(404, "this feedback is no longer queued — a run already consumed it")
    return path, obj


@router.post("/audit/feedback")
def audit_feedback(request: Request, body: Feedback) -> dict:
    routine_dir = _routine_dir(request)
    if not routine_dir.is_dir():
        raise HTTPException(404, "the self-audit routine is not set up yet")
    msg_id = write_feedback(routine_dir, body)
    active = request.app.state.runner.is_active(SELF_AUDIT_SLUG)
    return {"ok": True, "id": msg_id, "delivery": "mid-run" if active else "next-run"}


@router.put("/audit/feedback/{msg_id}")
def audit_feedback_edit(request: Request, msg_id: str, body: Feedback) -> dict:
    """Rewrite a queued message in place (same file, so its inbox position holds); the
    original ts is kept and `edited` stamped. Gone from the inbox = consumed = immutable."""
    path, prev = _queued_message(request, msg_id)
    atomic_write_json(path, _message_payload(body, str(prev.get("ts") or now_iso()),
                                             edited=now_iso()))
    active = request.app.state.runner.is_active(SELF_AUDIT_SLUG)
    return {"ok": True, "id": msg_id, "delivery": "mid-run" if active else "next-run"}


@router.delete("/audit/feedback/{msg_id}")
def audit_feedback_withdraw(request: Request, msg_id: str) -> dict:
    path, _ = _queued_message(request, msg_id)
    try:
        path.unlink()
    except FileNotFoundError:  # a run consumed it between the check and now — same outcome
        raise HTTPException(404, "this feedback is no longer queued — a run already consumed it")
    return {"ok": True, "id": msg_id}
