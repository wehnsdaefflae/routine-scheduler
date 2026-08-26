"""Open questions across routines (blocking from live status.json, deferred from
questions/pending/) PLUS the self-audit report's open decisions (meta-badged) — the ONE
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
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import registry
from ..ids import now_iso
from ..paths import atomic_write_json, read_json

router = APIRouter(tags=["questions"])

_DECISION_RE = re.compile(r"\[AUDIT decision · ([^\]]+)\]")


#: Statuses that mean the decision is already MADE — the report carries them so the Items
#: page can show progress, but they are not asks. `in_progress` is the big one: self-audit
#: is already building the thing, so queueing it for an answer is a card with nothing to
#: choose (2026-08-06: D74/D76/D77/D78/D80 all reached the operator this way in one night).
_DECIDED_STATUSES = frozenset({"settled", "closed", "done", "in_progress", "in progress",
                               "authorized", "shipped", "building"})


def _audit_decisions(server) -> list[dict]:
    """The self-audit report's OPEN decisions as meta-badged question items. A decision
    leaves the inbox when an answer is queued for it, when the report marks it decided
    (`_DECIDED_STATUSES` — or the routine's prose convention, a detail starting with
    SETTLED), or when it offers EXACTLY ONE option.

    The one-option rule is the load-bearing half: an already-decided item re-presented as
    a card whose only option restates the decision ("phase 1 next run") costs the operator
    a read and a click for no choice, and it is the shape self-audit reaches for whenever
    it wants an acknowledgment. Zero options stays open on purpose — that is a free-text
    ask, which the answer POST accepts. Nothing is hidden by this: every decision, at every
    status, is still listed on the Messages page (`readmodels.items`) with its status.
    """
    from ..readmodels.items import SELF_AUDIT_SLUG
    from .api_audit import queued_messages

    rdir = server.routines_home / SELF_AUDIT_SLUG
    report = read_json(rdir / "audit" / "report.json")
    if not isinstance(report, dict):
        return []
    queued = {m.group(1).strip() for p in queued_messages(rdir)
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
        options = [str(o) for o in (d.get("options") or [])]
        settled = (str(d.get("status") or "").strip().lower() in _DECIDED_STATUSES
                   or str(d.get("detail") or "").lstrip().upper().startswith("SETTLED"))
        if not did or did in queued or settled or len(options) == 1:
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
                    "meta": True, "question": text, "options": options,
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


def _record_dir(server, match: dict):
    """The dir whose inbox/ and questions/pending/ the engine behind a decision actually
    polls — the decision's own routine/conversation/background-task dir.
    """
    if match.get("background"):
        return server.background_home / match["routine"]
    home = server.conversations_home if match.get("conversation") else server.routines_home
    return home / match["routine"]


def _all_questions(server, home_kind: str = "routine") -> list[dict]:
    """Open questions of one home's catalog. Conversation questions carry
    `conversation: True`, detached-task questions `background: True` (+ the owning
    conversation's slug as `owner`), so the answer endpoint and the UI can tell the
    homes apart. Background asks are deferred-only by design — surfacing them here is
    what lets the user see and answer them at all (the answer lands durably in the
    task's inbox).
    """
    home = {"routine": None, "conversation": server.conversations_home,
            "background": server.background_home}[home_kind]
    marker = {} if home_kind == "routine" else {home_kind: True}
    out: list[dict] = []
    for info in registry.scan(server, home).values():
        runs = {r.ts: r for r in info.runs}
        seen: set[str] = set()
        active = info.active_run
        if active and active.question:
            seen.add(str(active.question.get("qid")))
            item = {**active.question, "routine": info.slug, "mode": "blocking",
                    "run_id": active.run_id, "run_state": active.state,
                    "asked": active.question.get("asked") or active.ts, **marker}
            if home_kind == "background" and isinstance(info.cfg.owner, dict):
                item["owner"] = str(info.cfg.owner.get("slug") or "")
            out.append(_mark_answered(_record_dir(server, item), item))
        for q in info.open_questions:
            if str(q.get("qid")) in seen:
                continue   # a live blocking question also has a durable pending record
            # a blocking record with no live run behind it (crash/kill) is just deferred now
            mode = "deferred" if q.get("mode") == "blocking" else q.get("mode", "deferred")
            item = {**q, "routine": info.slug, "mode": mode, **marker}
            if home_kind == "background" and isinstance(info.cfg.owner, dict):
                item["owner"] = str(info.cfg.owner.get("slug") or "")
            # a deferred question's `asked` is the run_ts it was filed from — link back to
            # that run (with its live state) when the run dir still exists, so a stale
            # question is recognizable against what its run actually did.
            run = runs.get(str(q.get("asked") or ""))
            if run:
                item.setdefault("run_id", run.run_id)
                item["run_state"] = run.state
            out.append(_mark_answered(info.cfg.dir, item))
    return out


def open_decisions(server) -> list[dict]:
    """Every decision across the instance, one shape — the Decisions page, the badge, the
    tab-open notifier, and the Web Push sender all read this. A record snoozed into the
    future carries `snoozed: True` (still open, still visible to runs — hidden by default
    on the user surfaces only).
    """
    items = (_all_questions(server) + _all_questions(server, "conversation")
             + _all_questions(server, "background") + _audit_decisions(server))
    now = datetime.now(UTC)
    for item in items:
        if _snooze_active(item.get("snoozed_until"), now):
            item["snoozed"] = True
    return items


def _snooze_active(snoozed_until: object, now: datetime) -> bool:
    if not snoozed_until:
        return False
    try:
        until = datetime.fromisoformat(str(snoozed_until))
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    return until > now


@router.get("/questions")
def list_questions(request: Request) -> list[dict]:
    return open_decisions(request.app.state.server)


class Answer(BaseModel):
    text: str = ""
    intermediate: bool = False   # dialog reply to a BLOCKING question — it stays open
    # Access requests only: one of the typed decisions (allow/deny × now/forever,
    # plus allow_once for once-grantable classes — D65, widened to secret/fs by D76).
    # A forever-decision is APPLIED to routine.yaml right here, at click time — the
    # engine only bridges it into a live run's overlay and never writes config.
    decision: str | None = None


@router.post("/questions/{qid}/answer")
async def answer(request: Request, qid: str, body: Answer) -> dict:
    if not body.text.strip() and not body.decision:
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
                  + _all_questions(server, "conversation")
                  + _all_questions(server, "background")
                  if q.get("qid") == qid), None)
    if match is None:
        raise HTTPException(404, f"no open question {qid!r}")
    routine_dir = _record_dir(server, match)
    payload: dict = {"qid": qid, "text": body.text, "source": "web",
                     "intermediate": body.intermediate and match["mode"] == "blocking",
                     "ts": now_iso()}
    if body.decision:
        payload.update(_decide_request(request, match, routine_dir, body.decision))
    atomic_write_json(routine_dir / "inbox" / f"answer-{qid}.json", payload)
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


def _decide_request(request: Request, match: dict, routine_dir,
                    decision: str) -> dict:
    """Settle an ACCESS REQUEST with one of the four typed decisions: validate it against
    the record, persist a forever-decision to routine.yaml NOW (this is the user's click —
    the one sanctioned config write; a live run only bridges it), resolve a connection
    grant's account, and return the answer-file fields. The `text` becomes the shared
    decision phrase so every surface (digest, Discord note, settled card) reads one
    vocabulary.
    """
    from ..engine.requests import DECISION_PHRASES, DECISIONS
    from . import grants_apply
    from .routines_common import _git_commit

    if decision not in DECISIONS:
        raise HTTPException(400, f"unknown decision {decision!r} — expected one of "
                                 f"{', '.join(DECISIONS)}")
    req_ids = [str(r) for r in match.get("request") or []]
    if not req_ids:
        raise HTTPException(400, "this question is not an access request — answer it "
                                 "with text")
    out: dict = {"decision": decision, "text": DECISION_PHRASES[decision],
                 "intermediate": False}
    if decision.endswith("_forever"):
        out.update(grants_apply.apply_forever(request.app.state.server, routine_dir,
                                              req_ids, decision))
        _git_commit(routine_dir, f"grant decision via web ({decision}: "
                                 f"{', '.join(req_ids)})")
        try:
            request.app.state.scheduler.rescan()
        except AttributeError:
            pass   # test apps without a scheduler — config on disk is already right
    elif decision == "allow_now":
        # a one-run connection grant still needs its account resolved at decision time
        for eid in req_ids:
            if eid.startswith("connection:"):
                out["account"] = grants_apply.resolve_account(eid.partition(":")[2])
    elif decision == "allow_once":
        # D65 scoped `allow once` to turn-action classes (exact spend). D76 (operator,
        # 2026-08-06) widened it to secret:/fs-*: with an explicitly coarser spend: the
        # next util invocation that RECEIVES the entity (declared-env injection, mounted
        # roots) or a file action under the fs root — engine/requests._once_match.
        # connection:/machine:/recreate: still cannot be once-granted.
        from .. import entities
        bad = [e for e in req_ids
               if (p := entities.parse_entity(e)) is None
               or p[0] not in entities.ONCE_CLASSES]
        if bad:
            raise HTTPException(
                400, f"allow_once applies only to once-grantable classes "
                     f"({', '.join(sorted(entities.ONCE_CLASSES))}:*) — not to "
                     f"{', '.join(bad)}; use allow now / allow forever for those")
    return out


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


# ---- lifecycle: snooze (hide until) + defer-to-next-run (unblock without deciding) ---------


def _record_match(server, qid: str) -> dict:
    """The open FILE-BACKED decision for qid (audit decisions live in the report, not as
    records — they can't be snoozed or deferred).
    """
    match = next((q for q in _all_questions(server)
                  + _all_questions(server, "conversation")
                  + _all_questions(server, "background")
                  if q.get("qid") == qid), None)
    if match is None:
        raise HTTPException(404, f"no open question {qid!r}")
    return match


class Snooze(BaseModel):
    minutes: int = 0   # > 0 hides the record until now+minutes; <= 0 clears the snooze


@router.post("/questions/{qid}/snooze")
def snooze_question(request: Request, qid: str, body: Snooze) -> dict:
    """Hide a decision until a timestamp — UI noise control, NOT an answer. The record
    keeps its one shape (a `snoozed_until` field rides along); runs still see the open
    question in their state digest.
    """
    server = request.app.state.server
    match = _record_match(server, qid)
    if match["mode"] == "blocking":
        raise HTTPException(400, "a blocking question parks a live run — answer it, or "
                                 "defer it to the next run instead of snoozing it")
    qfile = _record_dir(server, match) / "questions" / "pending" / f"{qid}.json"
    record = read_json(qfile)
    if not isinstance(record, dict):
        raise HTTPException(404, f"decision record for {qid!r} is gone")
    until = ""
    if body.minutes > 0:
        until = (datetime.now(UTC) + timedelta(minutes=body.minutes)).isoformat(
            timespec="seconds")
        record["snoozed_until"] = until
    else:
        record.pop("snoozed_until", None)
    atomic_write_json(qfile, record)
    _announce_answer(request, qid, match["routine"])   # every open view resyncs
    return {"ok": True, "snoozed_until": until or None}


@router.post("/questions/{qid}/defer")
def defer_question(request: Request, qid: str) -> dict:
    """Unblock a run parked on a blocking question WITHOUT deciding: a defer marker in
    the inbox releases the wait, the engine continues on the action's stated default,
    and the record stays open as deferred — exactly the timeout path, chosen by the user.
    """
    server = request.app.state.server
    match = _record_match(server, qid)
    if match["mode"] != "blocking":
        raise HTTPException(400, "only a blocking question can be deferred — a deferred "
                                 "one already waits for a future run")
    routine_dir = _record_dir(server, match)
    marker = routine_dir / "inbox" / f"answer-{qid}.json"
    if marker.exists():
        raise HTTPException(409, "an answer for this question is already queued")
    atomic_write_json(marker, {"qid": qid, "defer": True, "ts": now_iso(), "source": "web"})
    _announce_answer(request, qid, match["routine"])
    return {"ok": True, "deferred": True}
