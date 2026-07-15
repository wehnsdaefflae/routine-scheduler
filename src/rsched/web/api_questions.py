"""Open questions across routines (blocking from live status.json, deferred from
questions/pending/) PLUS wizard clarify sessions (dot-hidden, so the registry skips them)
PLUS the self-audit report's open decisions (meta-badged) — the Decisions page is the ONE
answering surface. Answers land as an atomic inbox file either way; an audit decision's
answer takes the same [AUDIT decision · id] form the audit feedback channel uses, so the
routine consumes both identically.

Question STATE is derived, never stored twice: a question is `answered` the moment its
inbox/answer-<qid>.json exists — even though the pending file lives on until the routine's
next run consumes it. Every surface (Decisions page, run view, badges) reads that one
derivation, and each answer POST publishes a bus event so open views resync at once.
"""

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
    with SETTLED).
    """
    from .api_audit import SELF_AUDIT_SLUG, _pending_feedback

    rdir = server.routines_home / SELF_AUDIT_SLUG
    report = read_json(rdir / "audit" / "report.json")
    if not isinstance(report, dict):
        return []
    queued = {m.group(1).strip() for p in _pending_feedback(rdir)
              if (m := _DECISION_RE.match(p.get("text") or ""))}
    # Durable answered markers: a mid-run delivery consumes the inbox message instantly,
    # so `queued` alone cannot keep an answered decision out of the inbox while the report
    # still lists it open — the user would be asked the same decision again and again.
    # A decision answered at-or-after this report's `generated` stays hidden until a NEWER
    # report explicitly lists it open again.
    answered = read_json(rdir / "audit" / "decisions-answered.json")
    if not isinstance(answered, dict):
        answered = {}
    out = []
    for d in report.get("decisions") or []:
        did = str(d.get("id") or "").strip()
        settled = (str(d.get("status") or "").lower() in ("settled", "closed", "done")
                   or str(d.get("detail") or "").lstrip().upper().startswith("SETTLED"))
        if not did or did in queued or settled:
            continue
        marker = str(answered.get(did) or "")
        if marker and marker >= str(report.get("generated") or ""):
            # answered since this report was written — not open again until a newer
            # report says so
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
    rides along so every surface can say WHERE the decision was made (web / discord).
    """
    ans = read_json(routine_dir / "inbox" / f"answer-{item.get('qid')}.json")
    if isinstance(ans, dict) and "text" in ans:
        item["answered"] = True
        item["answer"] = ans["text"]
        item["answer_source"] = ans.get("source", "web")
    return item


def _all_questions(server, *, conversations: bool = False) -> list[dict]:
    """Open questions of one home's catalog. Conversation questions carry
    `conversation: True` so the answer endpoint (and the UI) can tell the homes apart.
    """
    home = server.conversations_home if conversations else None
    out: list[dict] = []
    for info in registry.scan(server, home).values():
        runs = {r.ts: r for r in info.runs}
        seen: set[str] = set()
        active = info.active_run
        if active and active.question:
            seen.add(str(active.question.get("qid")))
            out.append(_mark_answered(info.cfg.dir,
                       {**active.question, "routine": info.slug, "mode": "blocking",
                        "run_id": active.run_id, "run_state": active.state,
                        "asked": active.question.get("asked") or active.ts,
                        **({"conversation": True} if conversations else {})}))
        for q in info.open_questions:
            if str(q.get("qid")) in seen:
                continue   # a live blocking question also has a durable pending record
            # a blocking record with no live run behind it (crash/kill) is just deferred now
            mode = "deferred" if q.get("mode") == "blocking" else q.get("mode", "deferred")
            item = {**q, "routine": info.slug, "mode": mode,
                    **({"conversation": True} if conversations else {})}
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
    other decision, answerable from either surface.
    """
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
    tab-open notifier, and the Web Push sender all read this.
    """
    return (_all_questions(server) + _all_questions(server, conversations=True)
            + _wizard_questions(server) + _audit_decisions(server))


@router.get("/questions")
def list_questions(request: Request) -> list[dict]:
    return open_decisions(request.app.state.server)


class Answer(BaseModel):
    text: str
    intermediate: bool = False   # dialog reply to a BLOCKING question — it stays open


@router.post("/questions/{qid}/answer")
async def answer(request: Request, qid: str, body: Answer) -> dict:
    if not body.text.strip():
        raise HTTPException(400, "empty answer")
    if qid.startswith("audit:"):
        from .api_audit import Feedback, write_feedback

        match = next((q for q in _audit_decisions(request.app.state.server)
                      if q["qid"] == qid), None)
        if match is None:
            raise HTTPException(404, f"no open audit decision {qid!r}")
        text = body.text.strip()
        choice = text if text in match["options"] else ""
        routine_dir = request.app.state.server.routines_home / match["routine"]
        write_feedback(routine_dir, Feedback(kind="decision", target=qid.removeprefix("audit:"),
                                             choice=choice, text="" if choice else text))
        _announce_answer(request, qid, match["routine"])
        return {"ok": True, "routine": match["routine"], "mode": "deferred", "meta": True}
    server = request.app.state.server
    match = next((q for q in _all_questions(server)
                  + _all_questions(server, conversations=True)
                  + _wizard_questions(server)
                  if q.get("qid") == qid), None)
    if match is None:
        raise HTTPException(404, f"no open question {qid!r}")
    home = server.conversations_home if match.get("conversation") else server.routines_home
    routine_dir = home / match["routine"]
    atomic_write_json(routine_dir / "inbox" / f"answer-{qid}.json",
                      {"qid": qid, "text": body.text, "source": "web",
                       "intermediate": body.intermediate and match["mode"] == "blocking",
                       "ts": now_iso()})
    _announce_answer(request, qid, match["routine"])
    # A conversation is a one-shot run with no scheduled "next run": an answer filed on a
    # FINISHED conversation would sit in the inbox forever (F39). Resume it in place — as
    # api_conversations.message() does — so the engine's collect_deferred_answers drains the
    # answer at run start. A LIVE conversation reply needs no resume (it drains the answer at
    # its next turn boundary); a scheduled routine has its own next run.
    resumed = await _resume_terminal_conversation(request, match, routine_dir)
    return {"ok": True, "routine": match["routine"], "mode": match["mode"],
            **({"resumed": True} if resumed else {})}


def _announce_answer(request: Request, qid: str, routine: str) -> None:
    """One bus event per answer: every open view (Decisions page, run views, badges)
    resyncs its question state immediately instead of waiting for a reload.
    """
    bus = getattr(request.app.state, "bus", None)
    if bus is not None:
        bus.publish({"event": "question_answered", "qid": qid, "routine": routine})


async def _resume_terminal_conversation(request: Request, match: dict, routine_dir) -> bool:
    """Resume a FINISHED conversation so a just-filed answer is actually consumed (F39).
    No-op for a scheduled routine (it has its own next run), for a LIVE conversation (the
    answer drains at the next turn boundary), or when the run cannot be resumed.
    """
    if not match.get("conversation"):
        return False
    from ..config import load_routine

    runner = getattr(request.app.state, "runner", None)
    if runner is None:
        return False
    cfg, _ = load_routine(routine_dir)
    if cfg is None:
        return False
    return bool(await runner.resume_terminal(cfg, reason="converse"))
