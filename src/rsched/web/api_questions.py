"""Open questions across routines (blocking from live status.json, deferred from
questions/pending/) PLUS the self-audit report's open decisions (meta-badged) — the
Decisions page is the ONE answering surface. Answers land as an atomic inbox file either
way; an audit decision's answer takes the same [AUDIT decision · id] form the audit
feedback channel uses, so the routine consumes both identically."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..daemon import registry
from ..ids import now_iso
from ..paths import atomic_write_json, read_json

router = APIRouter(tags=["questions"])

_DECISION_RE = re.compile(r"\[AUDIT decision · ([^\]]+)\]")


def _audit_decisions(request: Request) -> list[dict]:
    """The self-audit report's OPEN decisions as meta-badged question items. A decision
    leaves the inbox when an answer is queued for it, or when the report marks it
    settled (`status: settled` — or the routine's prose convention, a detail starting
    with SETTLED)."""
    from .api_audit import SELF_AUDIT_SLUG, _pending_feedback

    rdir = request.app.state.server.routines_home / SELF_AUDIT_SLUG
    report = read_json(rdir / "audit" / "report.json")
    if not isinstance(report, dict):
        return []
    queued = {m.group(1).strip() for p in _pending_feedback(rdir)
              if (m := _DECISION_RE.match(p.get("text") or ""))}
    out = []
    for d in report.get("decisions") or []:
        did = str(d.get("id") or "").strip()
        settled = (str(d.get("status") or "").lower() in ("settled", "closed", "done")
                   or str(d.get("detail") or "").lstrip().upper().startswith("SETTLED"))
        if not did or did in queued or settled:
            continue
        text = str(d.get("title") or did)
        if d.get("detail"):
            text += "\n\n" + str(d["detail"])
        out.append({"qid": f"audit:{did}", "routine": SELF_AUDIT_SLUG, "mode": "deferred",
                    "meta": True, "question": text,
                    "options": [str(o) for o in (d.get("options") or [])],
                    "asked": report.get("generated") or ""})
    return out


def _all_questions(request: Request) -> list[dict]:
    out: list[dict] = []
    for info in registry.scan(request.app.state.server).values():
        runs = {r.ts: r for r in info.runs}
        active = info.active_run
        if active and active.question:
            out.append({**active.question, "routine": info.slug, "mode": "blocking",
                        "run_id": active.run_id, "run_state": active.state,
                        "asked": active.question.get("asked") or active.ts})
        for q in info.open_questions:
            item = {**q, "routine": info.slug, "mode": q.get("mode", "deferred")}
            # a deferred question's `asked` is the run_ts it was filed from — link back to
            # that run (with its live state) when the run dir still exists, so a stale
            # question is recognizable against what its run actually did.
            run = runs.get(str(q.get("asked") or ""))
            if run:
                item.setdefault("run_id", run.run_id)
                item["run_state"] = run.state
            out.append(item)
    return out


@router.get("/questions")
def list_questions(request: Request) -> list[dict]:
    return _all_questions(request) + _audit_decisions(request)


class Answer(BaseModel):
    text: str


@router.post("/questions/{qid}/answer")
def answer(request: Request, qid: str, body: Answer) -> dict:
    if not body.text.strip():
        raise HTTPException(400, "empty answer")
    if qid.startswith("audit:"):
        from .api_audit import Feedback, write_feedback

        match = next((q for q in _audit_decisions(request) if q["qid"] == qid), None)
        if match is None:
            raise HTTPException(404, f"no open audit decision {qid!r}")
        text = body.text.strip()
        choice = text if text in match["options"] else ""
        routine_dir = request.app.state.server.routines_home / match["routine"]
        write_feedback(routine_dir, Feedback(kind="decision", target=qid.removeprefix("audit:"),
                                             choice=choice, text="" if choice else text))
        return {"ok": True, "routine": match["routine"], "mode": "deferred", "meta": True}
    match = next((q for q in _all_questions(request) if q.get("qid") == qid), None)
    if match is None:
        raise HTTPException(404, f"no open question {qid!r}")
    routine_dir = request.app.state.server.routines_home / match["routine"]
    atomic_write_json(routine_dir / "inbox" / f"answer-{qid}.json",
                      {"qid": qid, "text": body.text, "source": "web", "ts": now_iso()})
    return {"ok": True, "routine": match["routine"], "mode": match["mode"]}
