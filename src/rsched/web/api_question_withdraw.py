"""Two routes: the asking routine takes its own card back — `POST /questions/{qid}/retract`
and `POST /questions/{qid}/supersede` (F943).

The four lifecycle operations that existed are all the OPERATOR's, and none of them is
available to the run that FILED the card: `answer` and `decline` are him speaking, `revise`
amends an ANSWER (not the question), `snooze` is his noise control, `defer` releases a parked
run onto its default with the record still open. So a run that disproved its own question's
premise had no way to withdraw it, and the only move left was to file a SECOND card correcting
the first — which is what put two cards for one matter on the Decisions page, the stale one
reading exactly as current as the correction.

Both are callable by the asker's own run, because that is where the premise breaks. The
vocabulary and the record move live in `rsched/withdraw.py`; this file is the two routes and
the four refusals, and the refusals are the design:

* An AUDIT decision or an unknown qid (404, `_record_match`) — the same floor the snooze,
  defer and decline routes stand on: an audit decision lives in the self-audit report, not as
  a record, so there is nothing to withdraw.
* A record whose ANSWER IS ALREADY QUEUED (409). The operator has spoken and a run has not
  read him yet; withdrawing would delete the one copy of his words. The asking run reads the
  answer and acts on it — including deciding it no longer applies.
* A BLOCKING record (400). It parks a live run, and that run is the asker: it is sitting in
  `interact._await_reply` and the withdrawal would leave it waiting for a card that no longer
  exists. `defer` is the verb that releases it onto its stated default.
* A SUPERSESSION naming a qid that is not itself an open record, or naming the record it
  would replace (400). The point of the supersede route is that ONE live card is left; a
  carrier that is settled, withdrawn or imaginary leaves the matter with none.

Its own module because `api_questions.py` is at its size ceiling (tests/size_ratchet.json),
where the house rule is that an addition goes in its own file — the same reason
`api_question_decline.py` and `api_patch_applied.py` exist.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import answerfile, withdraw
from .api_questions import _announce_answer, _record_match
from .decisions_read import _record_dir, find_question

router = APIRouter(tags=["questions"])


class Withdrawal(BaseModel):
    reason: str = Field(default="", max_length=600)   # shown in place of the card


class Supersession(Withdrawal):
    by: str = Field(min_length=1, max_length=120)     # the qid that is the live card now


@router.post("/questions/{qid}/retract")
def retract_question(request: Request, qid: str, body: Withdrawal) -> dict:
    """Withdraw the question behind `qid`: its premise is gone and nothing replaces it.

    The record moves to the durable archive carrying `retracted`, so the operator reads WHY
    where the card was instead of finding one fewer card than he remembers. No answer file is
    written — nobody spoke, and a run told its question was answered would act on words that
    do not exist.
    """
    match, routine_dir = _withdrawable(request, qid)
    event = withdraw.stamp(body.reason)
    _move(routine_dir, qid, match, event, key=withdraw.RETRACTED)
    _announce_answer(request, qid, match["routine"])   # every open view resyncs
    return {"ok": True, "routine": match["routine"], "mode": match["mode"],
            "withdrawn": True, withdraw.RETRACTED: event}


@router.post("/questions/{qid}/supersede")
def supersede_question(request: Request, qid: str, body: Supersession) -> dict:
    """Hand this matter to the question `by` names: that card is the live one, this one keeps
    its place in history stamped with the carrier.

    The mirror of a report's `supersedes` fold, and guarded the opposite way round from
    retract: a retraction may leave the matter with no card, a supersession must not — so the
    carrier has to be an OPEN record at the moment of the fold.
    """
    match, routine_dir = _withdrawable(request, qid)
    carrier = str(body.by).strip()
    if carrier == qid:
        raise HTTPException(400, "a question cannot supersede itself — retract it instead if "
                                 "its premise is gone")
    if find_question(request.app.state.server, carrier) is None:
        raise HTTPException(400, f"no open question {carrier!r} to take this matter over — "
                                 f"file the new question first, then supersede this one with "
                                 f"its qid, or retract this one if nothing replaces it")
    event = withdraw.stamp(body.reason, by=carrier)
    _move(routine_dir, qid, match, event, key=withdraw.SUPERSEDED_BY)
    _announce_answer(request, qid, match["routine"])
    return {"ok": True, "routine": match["routine"], "mode": match["mode"],
            "withdrawn": True, withdraw.SUPERSEDED_BY: event}


def _withdrawable(request: Request, qid: str):
    """(record, its dir) for a qid that may still be withdrawn — or the refusal that says
    which other verb applies. The two routes share every guard but the carrier's.
    """
    server = request.app.state.server
    match = _record_match(server, qid)
    if match["mode"] == "blocking":
        raise HTTPException(400, "this question parks a live run, which is waiting on it — "
                                 "withdrawing it would leave that run waiting for a card "
                                 "that no longer exists; defer it to release the run onto "
                                 "its stated default")
    routine_dir = _record_dir(server, match)
    if answerfile.path(routine_dir, qid).exists():
        raise HTTPException(409, "an answer for this question is already queued — the "
                                 "operator has decided it and no run has read him yet; read "
                                 "the answer instead of withdrawing the question")
    return match, routine_dir


def _move(routine_dir, qid: str, match: dict, event: dict, *, key: str) -> None:
    """Archive the record as withdrawn, then take it off the page — in that order, so a
    failure leaves the card standing rather than silently losing it.

    The row is written from the record the catalog holds, minus the keys the read model put
    there: the archive is the question as it was ASKED, and a card rebuilt from it must not
    claim a run state or a pickup that was true only while it was open.
    """
    served = ("run_id", "run_state", "pickup", "answered", "answer", "answer_source",
              "snoozed", "routine", "conversation", "background", "owner")
    question = {k: v for k, v in match.items() if k not in served}
    try:
        withdraw.record(routine_dir, qid, question, event, key=key)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    withdraw.drop_pending(routine_dir, qid)
