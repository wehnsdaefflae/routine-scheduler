"""Open questions across routines (blocking from live status.json, deferred from
questions/pending/) PLUS wizard clarify sessions (dot-hidden, so the registry skips them)
PLUS the self-audit report's open decisions (meta-badged) — the Decisions page is the ONE
answering surface. Answers land as an atomic inbox file either way; an audit decision's
answer takes the same [AUDIT decision · id] form the audit feedback channel uses, so the
routine consumes both identically.

Question STATE is derived, never stored twice: a question is `answered` the moment its
inbox/answer-<qid>.json exists — even though the pending file lives on until the routine's
next run consumes it. Every surface (Decisions page, run view, badges) reads that one
derivation, and each answer POST publishes a bus event so open views resync at once."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..daemon import registry
from ..ids import now_iso
from ..paths import atomic_write_json, read_json

router = APIRouter(tags=["questions"])

_DECISION_RE = re.compile(r"\[AUDIT decision · ([^\]]+)\]")


def _audit_decisions(server) -> list[dict]:
    """The self-audit report's OPEN decisions as meta-badged question items. A decision
    leaves the inbox when an answer is queued for it, or when the report marks it
    settled (`status: settled` — or the routine's prose convention, a detail starting
    with SETTLED)."""
    from .api_audit import SELF_AUDIT_SLUG, _pending_feedback

    rdir = server.routines_home / SELF_AUDIT_SLUG
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


def _mark_answered(routine_dir, item: dict) -> dict:
    """The single answered-state derivation: an inbox answer file means the user has
    spoken, even while the pending file waits for the next run to consume it. Without
    this, an answered decision re-appears as open on every reload. The answer's source
    rides along so every surface can say WHERE the decision was made (web / discord)."""
    ans = read_json(routine_dir / "inbox" / f"answer-{item.get('qid')}.json")
    if isinstance(ans, dict) and "text" in ans:
        item["answered"] = True
        item["answer"] = ans["text"]
        item["answer_source"] = ans.get("source", "web")
    return item


def _all_questions(server) -> list[dict]:
    out: list[dict] = []
    for info in registry.scan(server).values():
        runs = {r.ts: r for r in info.runs}
        seen: set[str] = set()
        active = info.active_run
        if active and active.question:
            seen.add(str(active.question.get("qid")))
            out.append(_mark_answered(info.cfg.dir,
                       {**active.question, "routine": info.slug, "mode": "blocking",
                        "run_id": active.run_id, "run_state": active.state,
                        "asked": active.question.get("asked") or active.ts}))
        for q in info.open_questions:
            if str(q.get("qid")) in seen:
                continue   # a live blocking question also has a durable pending record
            # a blocking record with no live run behind it (crash/kill) is just deferred now
            mode = "deferred" if q.get("mode") == "blocking" else q.get("mode", "deferred")
            item = {**q, "routine": info.slug, "mode": mode}
            # a deferred question's `asked` is the run_ts it was filed from — link back to
            # that run (with its live state) when the run dir still exists, so a stale
            # question is recognizable against what its run actually did.
            run = runs.get(str(q.get("asked") or ""))
            if run:
                item.setdefault("run_id", run.run_id)
                item["run_state"] = run.state
            out.append(_mark_answered(info.cfg.dir, item))
    return out


def _wizard_questions(server) -> list[dict]:
    """Clarify-session questions. Wizard sessions are dot-hidden pseudo-routines the
    registry deliberately skips — but their questions belong in the same inbox as every
    other decision, answerable from either surface."""
    from . import wizard_store

    home = server.routines_home
    out: list[dict] = []
    for d in sorted(home.glob(".wizard-*")) if home.is_dir() else []:
        if not d.is_dir():
            continue
        ts = wizard_store.latest_run_ts(d)
        run = (registry.read_run(d / "runs" / ts, d.name)
               if ts and (d / "runs" / ts).is_dir() else None)
        if run and run.question and run.state == "waiting_user":
            out.append(_mark_answered(d, {**run.question, "routine": d.name, "wizard": True,
                                          "mode": "blocking", "run_state": run.state,
                                          "asked": run.question.get("asked") or run.ts}))
        pending = d / "questions" / "pending"
        for path in sorted(pending.glob("*.json")) if pending.is_dir() else []:
            q = read_json(path)
            if isinstance(q, dict) and q.get("question"):
                out.append(_mark_answered(d, {**q, "routine": d.name, "wizard": True,
                                              "mode": q.get("mode", "deferred")}))
    return out


def open_decisions(server) -> list[dict]:
    """Every decision across the instance, one shape — the Decisions page, the badge, the
    tab-open notifier, and the Web Push sender all read this."""
    return _all_questions(server) + _wizard_questions(server) + _audit_decisions(server)


@router.get("/questions")
def list_questions(request: Request) -> list[dict]:
    return open_decisions(request.app.state.server)


class Answer(BaseModel):
    text: str
    intermediate: bool = False   # dialog reply to a BLOCKING question — it stays open


@router.post("/questions/{qid}/answer")
def answer(request: Request, qid: str, body: Answer) -> dict:
    if not body.text.strip():
        raise HTTPException(400, "empty answer")
    if qid.startswith("audit:"):
        from .api_audit import Feedback, write_feedback

        match = next((q for q in _audit_decisions(request.app.state.server) if q["qid"] == qid), None)
        if match is None:
            raise HTTPException(404, f"no open audit decision {qid!r}")
        text = body.text.strip()
        choice = text if text in match["options"] else ""
        routine_dir = request.app.state.server.routines_home / match["routine"]
        write_feedback(routine_dir, Feedback(kind="decision", target=qid.removeprefix("audit:"),
                                             choice=choice, text="" if choice else text))
        _announce_answer(request, qid, match["routine"])
        return {"ok": True, "routine": match["routine"], "mode": "deferred", "meta": True}
    match = next((q for q in _all_questions(request.app.state.server)
                  + _wizard_questions(request.app.state.server)
                  if q.get("qid") == qid), None)
    if match is None:
        raise HTTPException(404, f"no open question {qid!r}")
    routine_dir = request.app.state.server.routines_home / match["routine"]
    atomic_write_json(routine_dir / "inbox" / f"answer-{qid}.json",
                      {"qid": qid, "text": body.text, "source": "web",
                       "intermediate": body.intermediate and match["mode"] == "blocking",
                       "ts": now_iso()})
    _announce_answer(request, qid, match["routine"])
    return {"ok": True, "routine": match["routine"], "mode": match["mode"]}


def _announce_answer(request: Request, qid: str, routine: str) -> None:
    """One bus event per answer: every open view (Decisions page, run views, badges)
    resyncs its question state immediately instead of waiting for a reload."""
    bus = getattr(request.app.state, "bus", None)
    if bus is not None:
        bus.publish({"event": "question_answered", "qid": qid, "routine": routine})
