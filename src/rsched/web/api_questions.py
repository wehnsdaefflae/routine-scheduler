"""Open questions across routines (blocking from live status.json, deferred from
questions/pending/) and the single answer path — an atomic inbox file either way."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..daemon import registry
from ..ids import now_iso
from ..paths import atomic_write_json

router = APIRouter(tags=["questions"])


def _all_questions(request: Request) -> list[dict]:
    out: list[dict] = []
    for info in registry.scan(request.app.state.server).values():
        active = info.active_run
        if active and active.question:
            out.append({**active.question, "routine": info.slug, "mode": "blocking",
                        "run_id": active.run_id})
        for q in info.open_questions:
            out.append({**q, "routine": info.slug, "mode": q.get("mode", "deferred")})
    return out


@router.get("/questions")
def list_questions(request: Request) -> list[dict]:
    return _all_questions(request)


class Answer(BaseModel):
    text: str


@router.post("/questions/{qid}/answer")
def answer(request: Request, qid: str, body: Answer) -> dict:
    if not body.text.strip():
        raise HTTPException(400, "empty answer")
    match = next((q for q in _all_questions(request) if q.get("qid") == qid), None)
    if match is None:
        raise HTTPException(404, f"no open question {qid!r}")
    routine_dir = request.app.state.server.routines_home / match["routine"]
    atomic_write_json(routine_dir / "inbox" / f"answer-{qid}.json",
                      {"qid": qid, "text": body.text, "source": "web", "ts": now_iso()})
    return {"ok": True, "routine": match["routine"], "mode": match["mode"]}
