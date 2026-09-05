"""The ASK protocol — every kind of required user feedback, and the two small handlers that
ride it (`schedule_run`, `report`).

Library authoring moved to `authoring.py` and the secret-exposure gate to `secretgate.py`
(F393): three responsibilities had accumulated in one file. What is left is one — turning a
run's need for a human into a decision record and back into an answer.

EVERY kind of required user feedback funnels into the same decision record
(inbox.file_question): plain asks and util approvals, deferred and blocking. A blocking
decision waits up to the routine's ask_timeout_min (configurable on the routine page)
and is answered on the web console — the Decisions page, with browser push carrying it to
a phone. On timeout the run CONTINUES on the model's stated `default`; the question stays
open as deferred so a late answer still reaches the next run. Waiting time is credited
back to the wall-clock budget.
"""

from __future__ import annotations

import difflib
import time
from datetime import datetime, timedelta

from .. import reports, schedule_once
from ..ids import question_id
from . import availability, detach, inbox, requests
from .control import RunAborted

# Natural affirmatives count: approval answers arrive as free text, and "Do it. The mail
# is …" must not read as a decline (F161 — two real approvals were recorded DECLINED
# because "do" was missing here).
_APPROVE_WORDS = ("approve", "approved", "yes", "y", "ok", "okay", "go", "accept", "confirm",
                  "do", "sure", "yep", "yeah", "proceed", "ja")
# Explicit declines only. An approval question is settled by a clear yes OR a clear no —
# anything else is NOT an answer (D38): the wait loop holds it as a delayed user message
# and keeps the question open instead of letting a presence ping decline a util.
_DECLINE_WORDS = ("decline", "declined", "no", "n", "deny", "denied", "reject", "rejected",
                  "stop", "cancel", "don't", "dont", "nein", "nope", "never", "skip")


def _head_word(text: str) -> str:
    return text.strip().lower().split()[0].strip(".,!:;") if text.strip() else ""


def is_approval(text: str) -> bool:
    return _head_word(text) in _APPROVE_WORDS


#: Question types settled ONLY by a clear approve/decline (D38). Every authoring approval
#: belongs here — `authoring.py` files util- and rule-approvals, `remind.py` the global
#: reminder one.
_APPROVAL_QTYPES = frozenset({"util-approval", "rule-approval", "reminder-approval"})


def _settles_approval(text: str) -> bool:
    """True when the text is a clear approve OR a clear decline — the only two replies
    that may settle a blocking util-approval (D38).
    """
    head = _head_word(text)
    return head in _APPROVE_WORDS or head in _DECLINE_WORDS


def _held_not_settled(qtype: str, answer: dict) -> bool:
    """D38 across record types: does this reply fail to SETTLE the question? An APPROVAL
    settles only on a clear approve/decline, an access request only on one of the typed
    decisions; defer markers and dialog replies pass through to their own paths. A held
    reply becomes a delayed user message and the wait continues.

    Every approval qtype is listed, not just the first one: the check reads the type, so a
    type it has never heard of falls through to "settled" and an ambiguous reply lands as a
    decision. `rule-approval` and `reminder-approval` write to the LIBRARY — a copy every
    holder reads at its next run — which is the last place a "hmm, maybe" should count as
    yes.
    """
    if not answer.get("text") or answer.get("defer") or answer.get("intermediate"):
        return False
    if qtype in _APPROVAL_QTYPES:
        return not _settles_approval(str(answer["text"]))
    if qtype == "request":
        return answer.get("decision") not in requests.DECISIONS
    return False


def _unescape_newlines(text: str) -> str:
    r"""Literal backslash-n sequences ("\n") become real newlines — see _normalize_plain."""
    if "\\n" not in text:
        return text
    return text.replace("\\r\\n", "\n").replace("\\n", "\n")


def _normalize_plain(qtype: str, question: str, default: str) -> tuple[str, str]:
    r"""D85-A (F291/R242): some models double-escape newlines in PLAIN question text, so a
    literal backslash-n reached the UI as "\n" (the renderer and store are correct —
    mdInline handles real newlines). Normalize question + default at intake, but ONLY for
    plain questions: util-approvals embed util SOURCE and access requests carry typed ids,
    where the two-character sequence is intended verbatim.
    """
    if qtype != "question":
        return question, default
    return _unescape_newlines(question), _unescape_newlines(default)


def _free_qid(ctx) -> str:
    """A decision-record id no OPEN record already uses.

    The id is `q-<run-ts>-<turn>`, which is unique per turn — and a turn could only ever file
    one question until a side field that rides ANY kind gained its own approval (a global
    `remind` op on an `ask_user` action files two). `file_question` is an unconditional write,
    so the second record silently replaced the first and the user answered a question the run
    was no longer waiting on. A settled record frees its id again, which is why this checks
    only what is still pending.
    """
    base = question_id(ctx.run_ts, ctx.turn)
    pending = ctx.routine.dir / "questions" / "pending"
    qid, n = base, 1
    while (pending / f"{qid}.json").exists():
        n += 1
        qid = f"{base}-{n}"
    return qid


def handle_ask(loop, action: dict, poll_s: float, qtype: str = "question") -> dict:
    ctx = loop.ctx
    if qtype == "question" and loop.dialog_qid:
        # a re-ask after a dialog reply supersedes the still-open previous record
        inbox.resolve_question(ctx.routine.dir, loop.dialog_qid)
        loop.dialog_qid = None
    qid = _free_qid(ctx)
    mode = action.get("mode") or "deferred"
    if ctx.depth > 0 or detach.is_detached_run(ctx):
        mode = "deferred"  # subruns / detached tasks cannot block the run on the user
    options = list(action.get("options") or [])
    default = str(action.get("default") or "").strip()
    # config bridge: a proposed routine.yaml change the run can't make itself — rides the
    # decision record for the Decisions page's one-click apply (see engine/revise.py).
    cpatch = action.get("config_patch") if isinstance(action.get("config_patch"), dict) else None
    # A typed access request (entities.py) rides the same record; the Decisions page
    # renders the allow/deny × now/forever buttons for it (plus allow-once for
    # turn-action classes, D65), and the answer's `decision` is what settles it (free
    # text is held, D38). Validation already ran in the schema-retry cycle
    # (availability.request_denial), so the ids here are requestable.
    req_ids = availability.request_ids(action)
    if req_ids:
        qtype = "request"
    question, default = _normalize_plain(qtype, str(action["question"]), default)
    extra = {"type": qtype, **({"default": default} if default else {})}
    ctx.transcript.event("question", {"qid": qid, "mode": mode, "question": question,
                                      "options": options, **extra,
                                      **({"request": req_ids} if req_ids else {})})
    if mode == "deferred":
        inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                            qtype=qtype, default=default, config_patch=cpatch,
                            request=req_ids)
        ctx.asks_deferred += 1   # churn telemetry: a decision thrown over the wall
        return {"kind": "ask_user", "qid": qid, "mode": mode,
                **({"request": req_ids} if req_ids else {})}

    timeout_min = ctx.budgets.ask_timeout_min
    expires = ((datetime.now().astimezone() + timedelta(minutes=timeout_min))
               .isoformat(timespec="seconds"))
    # blocking decisions are durable records too — the Decisions page never depends on a
    # live status.json to show one, and an aborted run leaves it behind as deferred
    inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                        mode="blocking", qtype=qtype, default=default, expires=expires,
                        config_patch=cpatch, request=req_ids)
    ctx.write_status("waiting_user",
                     question={"qid": qid, "question": question, "options": options,
                               "asked": ctx.run_ts, "expires": expires, **extra,
                               **({"request": req_ids} if req_ids else {})})
    deadline = time.monotonic() + timeout_min * 60
    started = time.monotonic()
    answer = None
    try:
        while time.monotonic() < deadline:
            if loop._aborted():
                raise RunAborted
            answer = inbox.take_answer(ctx.routine.dir, qid, loop.consumed_dir)
            # D38: an approval is settled ONLY by a clear approve/decline, and an access
            # REQUEST only by one of the typed decisions (the web's buttons). Any
            # other reply ("Bin hier", an unrelated instruction) is user INPUT that
            # arrived while the question blocks — hold it as a normal delayed message
            # (drained at the next turn boundary, i.e. after this decision) and keep
            # waiting; the question stays open.
            if answer is not None and _held_not_settled(qtype, answer):
                src = str(answer.get("source", "web"))
                ctx.transcript.event("answer", {"qid": qid, "text": str(answer["text"]),
                                                "source": src, "held": True})
                ctx.user_replies += 1     # held or not, the user spoke (R1310)
                inbox.file_message(ctx.routine.dir, str(answer["text"]), source=src,
                                   via="web")   # the user's own reply to THIS run — live
                answer = None
                continue
            if answer:
                break
            time.sleep(poll_s)
    except RunAborted:
        # the run dies but the decision survives — as a deferred question for the next run
        inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                            qtype=qtype, default=default, config_patch=cpatch,
                            request=req_ids)
        ctx.asks_deferred += 1
        raise
    finally:
        ctx.credit_suspended(time.monotonic() - started)
        ctx.write_status("running", question=None)
    if answer and answer.get("defer"):
        # The user parked the decision from the Decisions page — continue exactly like a
        # timeout: on the stated default, the record staying open as deferred.
        inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                            qtype=qtype, default=default, config_patch=cpatch,
                            request=req_ids)
        ctx.asks_deferred += 1
        return {"kind": "ask_user", "qid": qid, "mode": mode, "deferred_by_user": True,
                **({"default": default} if default else {})}
    if answer:
        source = answer.get("source", "web")
        ctx.transcript.event("answer", {"qid": qid, "text": answer["text"], "source": source,
                                        "intermediate": bool(answer.get("intermediate")),
                                        **({"decision": answer["decision"]}
                                           if answer.get("decision") else {})})
        ctx.user_replies += 1             # a blocking answer IS the user's next message
        if answer.get("intermediate"):
            # A dialog reply, not the answer: the user needs some back-and-forth before they
            # can decide. The decision record STAYS OPEN (deferred — the run is no longer
            # parked on it): the model's re-ask supersedes it, and a finish without a re-ask
            # leaves it live for the next run instead of silently dropping it.
            inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                                qtype=qtype, default=default, config_patch=cpatch,
                                request=req_ids)
            loop.dialog_qid = qid
            return {"kind": "ask_user", "qid": qid, "mode": mode, "dialog": True,
                    "user_message": answer["text"],
                    "note": "This is a dialog reply, NOT the final answer — the user needs "
                            "more back-and-forth first. Address their message, then ask again "
                            "with ask_user (the original question, or a sharper version)."}
        inbox.resolve_question(ctx.routine.dir, qid)
        if req_ids:
            # One of the typed decisions (guaranteed by the settle rule above):
            # seed the run overlay, rebuild the live policy + transport schema, and
            # teach the outcome. Forever-decisions were persisted by the web layer at
            # click time — the engine bridges them into this run and writes no config.
            decision = str(answer["decision"])
            requests.apply_decision(loop, req_ids, decision,
                                    account=str(answer.get("account") or ""))
            return {"kind": "ask_user", "qid": qid, "mode": mode, "answered": True,
                    "request": req_ids, "decision": decision,
                    "result": requests.observation_text(req_ids, decision)}
        return {"kind": "ask_user", "qid": qid, "mode": mode, "answered": True,
                "answer": answer["text"], "source": source}
    # timeout: continue WITHOUT the decision — on the stated default when there is one.
    # The record stays open (now deferred) so a late answer still reaches a future run.
    inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                        qtype=qtype, default=default, config_patch=cpatch, request=req_ids)
    ctx.asks_deferred += 1
    return {"kind": "ask_user", "qid": qid, "mode": mode, "timed_out": True,
            "timeout_min": timeout_min, **({"default": default} if default else {})}


def handle_schedule_run(loop, action: dict) -> dict:
    """Arm or cancel a one-shot time trigger on a routine — the cross-routine setter the
    `scheduling` capability gates. The engine writes the request spool un-sandboxed (like
    write_util's library write); the daemon's OneShotManager fires the request once at
    fire_at then CONSUMES it (auto-deactivate). Scope (a): any scheduling-holder may target
    ANY routine; self-targeting (a run arming its own follow-up) is the common case.
    """
    ctx = loop.ctx
    target = str(action.get("target") or "")
    home = ctx.server.routines_home
    # Self-target is ALWAYS allowed (the schema promises it) — including for a
    # CONVERSATION, which lives outside routines_home: its spool entry is namespaced
    # (`conv--<slug>`) so a same-named routine can never be mis-fired, and the daemon's
    # OneShotManager resolves that namespace back to conversations_home (waking the
    # conversation by RESUMING it — the "remind me in 3 days" flow).
    spool_slug = target
    if target == ctx.routine.slug and not (home / target / "routine.yaml").is_file() \
            and (ctx.routine.dir / "routine.yaml").is_file():
        spool_slug = f"conv--{target}"
    elif not (home / target / "routine.yaml").is_file():
        # Discoverability: a scheduling routine guessing a sibling's slug (the train-seat
        # friction) should get the valid slugs + close matches back, not a bare rejection.
        slugs = sorted(p.name for p in home.iterdir()
                       if not p.name.startswith(".") and (p / "routine.yaml").is_file())
        return {"kind": "schedule_run", "target": target, "unknown_target": True,
                "suggestions": difflib.get_close_matches(target, slugs, n=3, cutoff=0.5),
                "valid_targets": slugs}
    if action.get("cancel"):
        req_id = str(action.get("id")).strip() if action.get("id") else None
        removed = schedule_once.cancel(home, spool_slug, req_id)
        return {"kind": "schedule_run", "target": target, "cancelled": removed, "id": req_id}
    try:
        fire_at = schedule_once.parse_fire_at(str(action.get("fire_at") or ""))
    except ValueError as exc:
        return {"kind": "schedule_run", "target": target, "bad_fire_at": str(exc)}
    rec = schedule_once.arm(home, spool_slug, fire_at=fire_at,
                            reason=str(action.get("reason") or ""),
                            requested_by=ctx.run_id)
    return {"kind": "schedule_run", "target": target, "armed": rec["id"],
            "fire_at": rec["fire_at"]}


def handle_report(loop, action: dict) -> dict:
    """File a REPORT — the ungated channel every routine holds for work that is not its own
    task. Appends to <routines_home>/.control/reports.jsonl under an `R<n>` id.

    UNADDRESSED (no `target`): the entry waits in the stream for self-audit's triage. Filing
    it is best-effort like the health log — a failed write never aborts the reporting run,
    whose real job is elsewhere; it just reports filed=False.

    ADDRESSED (`target`): the report is ALSO delivered into that routine's inbox, and its next
    scheduled run reads it. Nothing is fired and nothing is woken — another routine's schedule
    is its own.

    Self-targeting is refused: a note to yourself is `note` or `memory_write`, and queueing
    prose into your own next prompt is a loop with no reader in between. Works at any depth —
    subruns report too, and the row carries the run that saw the problem.
    """
    ctx = loop.ctx
    title = str(action.get("title") or "").strip()
    detail = str(action.get("detail") or "").strip()
    target = str(action.get("target") or "").strip()
    home = ctx.server.routines_home
    target_dir = None
    if target:
        if target == ctx.routine.slug:
            return {"kind": "report", "target": target, "self_target": True}
        target_dir = home / target
        if not (target_dir / "routine.yaml").is_file():
            # Discoverability: a routine guessing a sibling's slug should get the valid slugs
            # and close matches back, not a bare rejection (as schedule_run does).
            slugs = sorted(p.name for p in home.iterdir()
                           if not p.name.startswith(".") and (p / "routine.yaml").is_file())
            return {"kind": "report", "target": target, "unknown_target": True,
                    "suggestions": difflib.get_close_matches(target, slugs, n=3, cutoff=0.5),
                    "valid_targets": slugs}
    filed = reports.file_report(home, routine=ctx.routine.slug, run_id=ctx.run_id, title=title,
                                detail=detail, target=target, target_dir=target_dir,
                                answers=str(action.get("answers") or "").strip(),
                                closes=bool(action.get("closes")))
    out = {"kind": "report", "title": title, "filed": filed is not None,
           "id": filed[1] if filed else ""}
    if target:
        out["target"] = target
        out["delivery"] = "the target reads it on its next scheduled run"
    return out
