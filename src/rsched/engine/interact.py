"""The ASK protocol — every kind of required user feedback.

Library authoring moved to `authoring.py`, the secret-exposure gate to `secretgate.py` and
the two handlers that merely RODE this file — `schedule_run` and `report` — to
`admin_handlers.py` (F393): four responsibilities had accumulated in one file; the
`config_patch` filing check followed to `config_bridge.py`. What is left is one — turning a
run's need for a human into a decision record and back into an answer.

EVERY kind of required user feedback funnels into the same decision record
(inbox.file_question): plain asks and util approvals, deferred and blocking. A blocking
decision waits up to the routine's ask_timeout_min (configurable on the routine page)
and is answered on the web console — the Decisions page, with browser push carrying it to
a phone. On timeout the run CONTINUES on the model's stated `default`; the question stays
open as deferred so a late answer still reaches the next run. Waiting time is credited
back to the wall-clock budget.

The console's ASK BACK (an `intermediate` answer) is a reply to ANY blocking decision that
decides nothing — the operator needs some back-and-forth first. It ends the wait at once and
comes back as a DIALOG result, which every consumer (the authoring approvals, the curated
reminder gate, the secret-exposure gate, `ask_user` itself) puts on the observation of the
action that asked (`still_pending`), worded once (`obs_admin.dialog_reply`): the operator's
words, and the instruction to answer them and re-submit that action. The record stays open;
the re-submission SUPERSEDES it, because it is the next decision filed for the same SUBJECT
(`handle_ask`). Nothing is written, granted or run on the strength of a reply that decided
nothing.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from ..ids import question_id
from . import availability, config_bridge, inbox, requests, runkind
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
    decisions; defer markers and ask-backs pass through to their own paths — an ask-back is
    never held, whatever the record type, because the operator is asking the run something
    and is owed the answer now, not after the decision they could not yet make. A held
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
    # the ROOT routine's pending dir, not `ctx.routine.dir`: a child shares its parent's
    # run_ts, so a parent and a child asking on the same turn derive the same base id
    pending = ctx.root_routine_dir / "questions" / "pending"
    qid, n = base, 1
    while (pending / f"{qid}.json").exists():
        n += 1
        qid = f"{base}-{n}"
    return qid


def still_pending(ask: dict) -> dict:
    """The fields an UNSETTLED decision puts on the observation of the action that asked: it is
    pending under its record — and after an ask-back the operator's words ride along, for the
    renderer to put in front of the model (`obs_admin.dialog_reply`). ONE shape for every
    consumer, so no renderer can find the words under one key and miss them under another.
    """
    out: dict = {"pending_approval": True, "qid": ask.get("qid")}
    if ask.get("dialog"):
        out.update(dialog=True, user_message=str(ask.get("user_message") or ""))
    return out


def _supersede(loop, qdir: Path, key: tuple[str, str]) -> None:
    """The decision just filed IS the re-submission an ask-back asked for when one left a
    record open on the same `(type, subject)` — so that record goes, once the new one exists.
    After the filing, never before: an ask refused before it files anything must not cost the
    open decision either.
    """
    if old := loop.dialog_qids.pop(key, None):
        inbox.resolve_question(qdir, old)


def _await_reply(loop, qdir: Path, qid: str, qtype: str, *, deadline: float,
                 poll_s: float) -> dict | None:
    """Wait on a BLOCKING record until a reply ends the wait — None when the deadline passes
    first. Raises RunAborted the moment the run is stopped.

    D38: an approval is settled ONLY by a clear approve/decline, and an access REQUEST only by
    one of the typed decisions (the web's buttons). Any other plain reply ("Bin hier", an
    unrelated instruction) is user INPUT that arrived while the question blocks — it is held
    as a normal delayed message (drained at the next turn boundary, i.e. after this decision)
    and the wait goes on; the question stays open. Everything else ends the wait: a settling
    answer, the Decisions page's defer marker, and an ask-back — the one reply that ends it
    without settling anything, answered by the caller as a dialog.
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
               subject: str = "") -> dict:
    """File one decision record and, for a blocking ask, wait for its answer.

    `subject` is what the decision is ABOUT, beside its type: the util a util-approval would
    write or remove, the rule a rule-approval would change, the curated reminder a
    reminder-approval names. An access request's subject is its entity ids, whoever files it —
    the model's own `request` or the secret gate's — and a plain question has none. The pair
    keys the one thing an ask leaves behind: a record a DIALOG reply kept open
    (`loop.dialog_qids`). The next decision filed for the same `(type, subject)` is the
    re-submission that reply asked for and supersedes it; nothing else can. A single slot was
    resolved by WHATEVER plain question came next, so an approval the operator had asked back
    on was deleted by an unrelated ask.
    """
    ctx = loop.ctx
    # EVERY write of this decision record — the first filing and each re-filing that leaves it
    # open — goes to the ROUTINE's own dir, where a person reads it; a child's `routine.dir` is
    # its runs/<ts>/sub/<n>/ workspace, which no surface scans. One name for that dir keeps the
    # re-files from drifting back to the per-run dir the first filing had to be moved off.
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
    # D123/F458: the patch may name ANOTHER routine as its target. Until 0.326.0 the apply
    # button always PATCHed the ASKING routine, so a config-optimizer proposal for suedlink-wlf
    # silently rewrote config-optimizer's own budgets and reported success (R1343, R1407). The
    # target — and the body's keys — are checked HERE, at the seam that writes the durable
    # record, so a patch the apply would refuse can never reach the Decisions page wearing an
    # apply button. An unresolvable target is refused loudly rather than falling back to the
    # asker — a silent fallback is precisely the defect.
    ctarget, cterr = config_bridge.resolve(ctx, cpatch)
    if cterr:
        return {"kind": "ask_user", "error": cterr}
    # A typed access request (entities.py) rides the same record; the Decisions page
    # renders the allow/deny × now/forever buttons for it (plus allow-once for
    # turn-action classes, D65), and the answer's `decision` is what settles it (free
    # text is held, D38). Validation already ran in the schema-retry cycle
    # (availability.request_denial), so the ids here are requestable.
    req_ids = availability.request_ids(action)
    if req_ids:
        qtype, subject = "request", " ".join(sorted(req_ids))
    question, default = _normalize_plain(qtype, str(action["question"]), default)
    if ctx.depth > 0:
        # The record lands in the ROUTINE's pending dir, where a person reads it — so it has
        # to say who is asking. A child has no page of its own and its label is the only
        # thing that makes the question answerable in context.
        question = f"[child task #{ctx.sub_n} {ctx.sub_label!r}] {question}"
    extra = {"type": qtype, **({"default": default} if default else {})}
    ctx.transcript.event("question", {"qid": qid, "mode": mode, "question": question,
                                      "options": options, **extra,
                                      **({"request": req_ids} if req_ids else {})})

    def leave_open(*, churn: bool = True) -> None:
        """(Re)file the record as DEFERRED — the shape every ending that leaves it open shares.
        `churn` counts it as a decision thrown over the wall (`asks_deferred` telemetry); a
        dialog reply is not one — the conversation about it is still going.
        """
        inbox.file_question(qdir, qid, question, options, ctx.run_ts, qtype=qtype,
                            default=default, config_patch=cpatch, config_target=ctarget,
                            request=req_ids)
        if churn:
            ctx.asks_deferred += 1

    if mode == "deferred":
        leave_open()
        _supersede(loop, qdir, (qtype, subject))
        return {"kind": "ask_user", "qid": qid, "mode": mode,
                **({"request": req_ids} if req_ids else {})}

    timeout_min = ctx.budgets.ask_timeout_min
    expires = ((datetime.now().astimezone() + timedelta(minutes=timeout_min))
               .isoformat(timespec="seconds"))
    # blocking decisions are durable records too — the Decisions page never depends on a
    # live status.json to show one, and an aborted run leaves it behind as deferred
    inbox.file_question(qdir, qid, question, options, ctx.run_ts,
                        mode="blocking", qtype=qtype, default=default, expires=expires,
                        config_patch=cpatch, config_target=ctarget,
                        request=req_ids)
    _supersede(loop, qdir, (qtype, subject))
    ctx.write_status("waiting_user",
                     question={"qid": qid, "question": question, "options": options,
                               "asked": ctx.run_ts, "expires": expires, **extra,
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
                **({"default": default} if default else {})}
    if answer:
        source = answer.get("source", "web")
        ctx.transcript.event("answer", {"qid": qid, "text": answer["text"], "source": source,
                                        "intermediate": bool(answer.get("intermediate")),
                                        **({"decision": answer["decision"]}
                                           if answer.get("decision") else {})})
        ctx.user_replies += 1             # a blocking answer IS the user's next message
        if answer.get("intermediate"):
            # An ASK-BACK, not the answer — on any record type: the operator needs some
            # back-and-forth before they can decide. Nothing is settled, so nothing is
            # written, granted or run. The record STAYS OPEN (deferred — the run is no longer
            # parked on it): the re-submission supersedes it under its subject, and a finish
            # without one leaves it live for the next run instead of silently dropping it.
            # A consumer puts this result on its own kind's observation (`still_pending`); what
            # the model is told — their words and what to do with them — is obs_admin's
            # wording.
            leave_open(churn=False)
            loop.dialog_qids[(qtype, subject)] = qid
            return {"kind": "ask_user", "qid": qid, "mode": mode, "dialog": True,
                    "user_message": answer["text"],
                    **({"request": req_ids} if req_ids else {})}
        inbox.resolve_question(qdir, qid)
        if req_ids:
            # One of the typed decisions (guaranteed by the settle rule, `_await_reply`):
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
    leave_open()
    return {"kind": "ask_user", "qid": qid, "mode": mode, "timed_out": True,
            "timeout_min": timeout_min, **({"default": default} if default else {})}
