"""One route: the operator says NO to a decision — `POST /questions/{qid}/decline` (R2197).

Beside the three lifecycle operations that already existed, none of which is a refusal:
`answer` closes an item only by SAYING something, `snooze` is UI noise control that leaves the
question open in every run's digest, `defer` releases a parked run onto its stated default with
the record still open. So the Decisions page had no "no": an operator who had read a proposal
and did not want it could type prose, snooze in circles, or leave it open.

The decline writes the ORDINARY answer payload (`inbox/answer-<qid>.json`, built by
`rsched/decline.py`), so three things follow for free and nothing new consumes anything:
the item leaves the open set exactly as an answer does (`decisions_read._mark_answered`),
`engine.inbox_questions.collect_deferred_answers` drains it unchanged, and the archived
receipt the Decisions page reads afterwards is the same file. What the decline adds is the
VALUE `decision: "declined"` — carried onto the answer pair, so the asking routine matches a
token instead of parsing a sentence.

TWO RECORDS IT REFUSES, both because the engine would mis-handle them rather than out of
caution:

* An ACCESS REQUEST (400). That vocabulary is `engine/requests.DECISIONS`, its values are what
  seeds a run's grant overlay, and a blocking request's wait is settled ONLY by one of them
  (`engine/interact._held_not_settled`) — a question-scoped word would be held as chatter and
  the parked run would keep waiting. `deny_now` / `deny_forever` are that record's "no".
* A qid with no open file-backed record (404, `_record_match`) — which is also how an AUDIT
  decision lands here: those live in the self-audit report, not as records, exactly as they
  cannot be snoozed or deferred either.

Its own module because `api_questions.py` is at its size ceiling (tests/size_ratchet.json),
where the house rule is that an addition goes in its own file — the same reason
`api_patch_applied.py` exists.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import decline, patchreceipt
from ..paths import atomic_write_json
from .api_questions import (
    Answer,
    _announce_answer,
    _record_match,
    _relay_report_answer,
    _resume_terminal_conversation,
)
from .decisions_read import _record_dir

router = APIRouter(tags=["questions"])


class Decline(BaseModel):
    reason: str = Field(default="", max_length=600)   # optional; the phrase stands alone


@router.post("/questions/{qid}/decline")
async def decline_question(request: Request, qid: str, body: Decline) -> dict:
    """Decline the decision behind `qid`: file the refusal, then — on the loop, where the
    runner lives — resume a finished conversation so the refusal is actually consumed.

    The split mirrors `api_questions.answer`: FILING walks the decision catalog of all three
    homes and writes, so it belongs on a worker thread; resuming a run is the loop's.
    """
    match, routine_dir, payload = await run_in_threadpool(_file_decline, request, qid, body)
    resumed = await _resume_terminal_conversation(request, match, routine_dir)
    return {"ok": True, "routine": match["routine"], "mode": match["mode"],
            "declined": True, "decision": payload["decision"], "text": payload["text"],
            **({"resumed": True} if resumed else {})}


def _file_decline(request: Request, qid: str, body: Decline) -> tuple[dict, object, dict]:
    """Write the refusal into the asking routine's inbox and announce it.

    Returns (record, its dir, the payload) for the loop half — `_file_answer`'s contract, so
    the two paths stay recognisably one protocol.
    """
    server = request.app.state.server
    match = _record_match(server, qid)
    if match.get("request"):
        raise HTTPException(400, "this question is an access request — decline it with the "
                                 "typed decision `deny_now` or `deny_forever`, which is what "
                                 "the engine reads to deny the grant")
    routine_dir = _record_dir(server, match)
    marker = routine_dir / "inbox" / f"answer-{qid}.json"
    if marker.exists():
        raise HTTPException(409, "an answer for this question is already queued")
    payload = decline.payload(qid, body.reason)
    # A declined proposal is the clearest possible case of a `config_patch` that was NOT
    # applied, and the record states it in the words the asking run already reads
    # (rsched/patchreceipt.py) — declining never applies anything.
    payload.update(patchreceipt.receipt(match, applied=False))
    atomic_write_json(marker, payload)
    # A report-born decision routed work to another routine; that routine has no way to learn
    # an answer filed into someone else's inbox, and a refusal is exactly what it must not miss
    # (F621, `api_questions._relay_report_answer`).
    # The phrase alone, never `Answer.decision`: that field is the ACCESS-REQUEST vocabulary
    # by its own contract, and the relay reads the text when there is text.
    _relay_report_answer(server, match, Answer(text=payload["text"]))
    _announce_answer(request, qid, match["routine"])
    return match, routine_dir, payload
