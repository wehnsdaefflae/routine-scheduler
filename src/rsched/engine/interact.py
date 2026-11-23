"""The ASK protocol — turning a run's need for a human into a decision record and back.

EVERY kind of required user feedback funnels into the same decision record
(inbox.file_question): plain asks and util approvals, deferred and blocking. A blocking
decision waits up to the routine's ask_timeout_min; on timeout the run CONTINUES on the
model's stated `default` and the question stays open as deferred so a late answer still
reaches the next run. Waiting time is credited back to the wall-clock budget.

An ASK BACK (an `intermediate` answer) decides nothing: it ends the wait at once and comes back
as a DIALOG result that the asking action's observation carries (`askback.still_pending`,
worded by `obs_admin.dialog_reply`). The record stays open until the re-submission for the
same SUBJECT supersedes it; nothing is written, granted or run on its strength.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from ..ids import question_id
from . import (
    askback,
    askproof,
    askprose,
    autoask,
    availability,
    config_bridge,
    grantsecrets,
    inbox,
    requests,
    runkind,
)
from .control import RunAborted

# Natural affirmatives count: approval answers arrive as free text ("Do it. The mail is …").
_APPROVE_WORDS = ("approve", "approved", "yes", "y", "ok", "okay", "go", "accept", "confirm",
                  "do", "sure", "yep", "yeah", "proceed", "ja")
# Explicit declines only: an approval is settled by a clear yes OR a clear no; anything else
# is held as a delayed user message and the question stays open (D38).
_DECLINE_WORDS = ("decline", "declined", "no", "n", "deny", "denied", "reject", "rejected",
                  "stop", "cancel", "don't", "dont", "nein", "nope", "never", "skip")


def _head_word(text: str) -> str:
    return text.strip().lower().split()[0].strip(".,!:;") if text.strip() else ""


def is_approval(text: str) -> bool:
    return _head_word(text) in _APPROVE_WORDS


#: Question types settled ONLY by a clear approve/decline (D38) — every authoring approval, and
#: a root command the risk check held (engine/rootcheck.py).
_APPROVAL_QTYPES = frozenset({"util-approval", "rule-approval", "reminder-approval",
                              "command-approval"})


def _settles_approval(text: str) -> bool:
    """True when the text is a clear approve OR a clear decline — the only two replies
    that may settle a blocking util-approval (D38).
    """
    head = _head_word(text)
    return head in _APPROVE_WORDS or head in _DECLINE_WORDS


def _held_not_settled(qtype: str, answer: dict) -> bool:
    """D38 across record types: does this reply fail to SETTLE the question?

    An APPROVAL settles only on a clear approve/decline, an access request only on a typed
    decision. Defer markers and ask-backs are never held (an ask-back is owed its answer now).
    A held reply becomes a delayed user message and the wait continues. An unlisted approval
    qtype would fall through to "settled", so every one is in `_APPROVAL_QTYPES`.
    """
    if not answer.get("text") or answer.get("defer") or answer.get("intermediate"):
        return False
    if qtype in _APPROVAL_QTYPES:
        return not _settles_approval(str(answer["text"]))
    if qtype == "request":
        return answer.get("decision") not in requests.DECISIONS
    return False


def _free_qid(ctx) -> str:
    """A decision-record id no OPEN record already uses.

    The id is `q-<run-ts>-<turn>`, but one turn can file two records (e.g. a global `remind`
    op on an `ask_user`) and `file_question` overwrites unconditionally, so a suffix is added.
    A settled record frees its id again, so only pending ones are checked.
    """
    base = question_id(ctx.run_ts, ctx.turn)
    # the ROOT routine's pending dir, not `ctx.routine.dir`: a child shares its parent's
    # run_ts, so a parent and a child asking on the same turn derive the same base id
    pending = ctx.root_routine_dir / "questions" / "pending"
    qid, n = base, 1
    while (pending / f"{qid}.json").exists():
        n += 1
        qid = f"{base}-{n}"
    return qid


def file_report_decision(loop, *, question: str, options: list[str],
                         report_id: str, target: str = "") -> str:
    """File the decision record a REPORT carrying options is (F621) — returns its qid.

    Naming `options` on a report says its question is the OPERATOR's, so the filing also leaves
    the decision record every operator surface renders. Same `_free_qid`, transcript event and
    writer as an `ask_user`; only `type` and the `report`/`report_target` relay keys differ.
    Always DEFERRED: the run is not parked on it.
    """
    ctx = loop.ctx
    qdir = ctx.root_routine_dir
    qid = _free_qid(ctx)
    picks = list(options)
    ctx.transcript.event("question", {"qid": qid, "mode": "deferred", "question": question,
                                      "options": picks, "type": "report",
                                      "subject": report_id})
    inbox.file_question(qdir, qid, question, picks, ctx.run_ts, qtype="report",
                        report=report_id, report_target=target)
    ctx.asks_deferred += 1
    return qid


def _await_reply(loop, qdir: Path, qid: str, qtype: str, *, deadline: float,
                 poll_s: float) -> dict | None:
    """Wait on a BLOCKING record until a reply ends the wait — None when the deadline passes
    first. Raises RunAborted the moment the run is stopped.

    D38: a reply that does not settle the record (`_held_not_settled`) is user INPUT, held as
    a delayed message while the wait goes on. A settling answer, a defer marker or an ask-back
    ends the wait.
    """
    ctx = loop.ctx
    while time.monotonic() < deadline:
        if loop._aborted():
            raise RunAborted
        answer = inbox.take_answer(qdir, qid, loop.consumed_dir)
        if answer is not None and _held_not_settled(qtype, answer):
            src = str(answer.get("source", "web"))
            ctx.transcript.event("answer", {"qid": qid, "text": str(answer["text"]),
                                            "source": src, "held": True})
            ctx.user_replies += 1     # held or not, the user spoke (R1310)
            inbox.file_message(qdir, str(answer["text"]), source=src,
                               via="web")   # the user's own reply to THIS run — live
            continue
        if answer:
            return answer
        time.sleep(poll_s)
    return None


def handle_ask(loop, action: dict, poll_s: float, qtype: str = "question",
               subject: str = "", batch: str = "") -> dict:
    """File one decision record and, for a blocking ask, wait for its answer.

    `batch` (D212) is the id the records of ONE `ask_user` action share — set by
    `askbatch.handle`, which files each of its questions through here, and empty for the
    single ask every other caller makes.

    `subject` is what the decision is ABOUT (the util, rule or reminder an approval names; an
    access request's entity ids; none for a plain question). `(type, subject)` keys a record a
    DIALOG reply kept open (`loop.dialog_qids`): only the next decision for the same pair
    supersedes it.
    """
    ctx = loop.ctx
    # every write of this record goes to the ROUTINE's dir, where a person reads it — a
    # child's `routine.dir` is a workspace no surface scans
    qdir = ctx.root_routine_dir
    qid = _free_qid(ctx)
    mode = action.get("mode") or "deferred"
    if ctx.depth > 0 or runkind.is_detached_run(ctx):
        mode = "deferred"  # subruns / detached tasks cannot block the run on the user
    options = list(action.get("options") or [])
    default = str(action.get("default") or "").strip()
    # config bridge: a proposed routine.yaml change the run can't make itself — rides the
    # decision record for the Decisions page's one-click apply (see engine/revise.py).
    cpatch = action.get("config_patch") if isinstance(action.get("config_patch"), dict) else None
    # target and keys are checked at the seam that writes the record, so a patch the apply
    # would refuse never reaches the Decisions page with an apply button (config_bridge)
    ctarget, cterr = config_bridge.resolve(ctx, cpatch)
    if cterr:
        return {"kind": "ask_user", "error": cterr}
    # A typed access request rides the same record and is settled by the answer's `decision`.
    # Ids are validated (availability), then WIDENED: a util grant carries its declared secrets.
    req_ids, question = grantsecrets.widen(ctx, availability.request_ids(action),
                                           str(action["question"]))
    if req_ids:
        qtype, subject = "request", " ".join(sorted(req_ids))
    question, default = askprose.normalize_plain(qtype, question, default)
    blocks = bool(action.get("blocks_work"))   # D212: the run's own CLASS claim (askclass)
    # D220: per option, what shows its answer landed — shape checked by askfields already
    proof = action.get("proof") if isinstance(action.get("proof"), dict) else None
    proof_note = {"proof_note": note} if (note := askproof.note(proof)) else {}
    if ctx.depth > 0:
        # a child has no page of its own, so the record names who is asking
        question = f"[child task #{ctx.sub_n} {ctx.sub_label!r}] {question}"
    extra = {"type": qtype, **({"default": default} if default else {})}
    # `subject` rides the event so a resumed leg can re-key an open dialog (askback.rebuild_dialogs)
    ctx.transcript.event("question", {"qid": qid, "mode": mode, "question": question,
                                      "options": options, **extra,
                                      **({"subject": subject} if subject else {}),
                                      **({"request": req_ids} if req_ids else {})})

    def leave_open(*, churn: bool = True) -> None:
        """(Re)file the record as DEFERRED — every ending that leaves it open.
        `churn` counts it in `asks_deferred`; a dialog reply does not.
        """
        inbox.file_question(qdir, qid, question, options, ctx.run_ts, qtype=qtype,
                            default=default, config_patch=cpatch, config_target=ctarget,
                            request=req_ids, batch=batch, blocks_work=blocks, proof=proof)
        if churn:
            ctx.asks_deferred += 1

    if mode == "deferred":
        # D191: a patch the routine's own `config_confirm` dial permits is queued for the web's
        # validated writer instead of filed as a card (engine/autoask.py)
        taken, why = autoask.taken(ctx, cpatch, qid=qid, question=question, mode=mode,
                                   options=options, request_ids=req_ids, config_target=ctarget)
        if taken:
            return {"kind": "ask_user", "qid": qid, "mode": mode, **taken}
        leave_open()
        askback.supersede(loop, qdir, (qtype, subject))
        return {"kind": "ask_user", "qid": qid, "mode": mode, **proof_note,
                **({"request": req_ids} if req_ids else {}),
                **({"config_not_applied": why} if why else {})}

    timeout_min = ctx.budgets.ask_timeout_min
    expires = ((datetime.now().astimezone() + timedelta(minutes=timeout_min))
               .isoformat(timespec="seconds"))
    # blocking decisions are durable records too — the Decisions page never depends on a
    # live status.json to show one, and an aborted run leaves it behind as deferred
    inbox.file_question(qdir, qid, question, options, ctx.run_ts,
                        mode="blocking", qtype=qtype, default=default, expires=expires,
                        config_patch=cpatch, config_target=ctarget,
                        request=req_ids, batch=batch, blocks_work=blocks, proof=proof)
    askback.supersede(loop, qdir, (qtype, subject))
    ctx.write_status("waiting_user",
                     question={"qid": qid, "question": question, "options": options,
                               "asked": ctx.run_ts, "expires": expires, **extra,
                               **config_bridge.live_fields(ctx, cpatch, ctarget),
                               **({"request": req_ids} if req_ids else {})})
    started = time.monotonic()
    try:
        answer = _await_reply(loop, qdir, qid, qtype, deadline=started + timeout_min * 60,
                              poll_s=poll_s)
    except RunAborted:
        # the run dies but the decision survives — as a deferred question for the next run
        leave_open()
        raise
    finally:
        ctx.credit_suspended(time.monotonic() - started)
        ctx.write_status("running", question=None)
    if answer and answer.get("defer"):
        # The user parked the decision from the Decisions page — continue exactly like a
        # timeout: on the stated default, the record staying open as deferred.
        leave_open()
        return {"kind": "ask_user", "qid": qid, "mode": mode, "deferred_by_user": True,
                **proof_note, **({"default": default} if default else {})}
    if answer:
        source = answer.get("source", "web")
        ctx.transcript.event("answer", {"qid": qid, "text": answer["text"], "source": source,
                                        "intermediate": bool(answer.get("intermediate")),
                                        **({"decision": answer["decision"]}
                                           if answer.get("decision") else {})})
        ctx.user_replies += 1             # a blocking answer IS the user's next message
        if answer.get("intermediate"):
            # An ASK-BACK: nothing is settled. The record stays open (deferred) until the
            # re-submission under its subject supersedes it, or for the next run.
            leave_open(churn=False)
            loop.dialog_qids[(qtype, subject)] = qid
            return {"kind": "ask_user", "qid": qid, "mode": mode, "dialog": True,
                    "user_message": answer["text"],
                    **({"request": req_ids} if req_ids else {})}
        inbox.resolve_question(qdir, qid)
        if req_ids:
            # a typed decision: seed the run overlay and rebuild policy + schema; forever-
            # decisions were persisted by the web at click time (the engine writes no config)
            decision = str(answer["decision"])
            requests.apply_decision(loop, req_ids, decision,
                                    account=str(answer.get("account") or ""))
            return {"kind": "ask_user", "qid": qid, "mode": mode, "answered": True,
                    "request": req_ids, "decision": decision,
                    "result": requests.observation_text(req_ids, decision)}
        return {"kind": "ask_user", "qid": qid, "mode": mode, "answered": True,
                "answer": answer["text"], "source": source, **proof_note}
    # timeout: continue WITHOUT the decision — on the stated default when there is one.
    # The record stays open (now deferred) so a late answer still reaches a future run.
    leave_open()
    return {"kind": "ask_user", "qid": qid, "mode": mode, "timed_out": True, **proof_note,
            "timeout_min": timeout_min, **({"default": default} if default else {})}
