"""One route: the apply button applied the patch of a question that was ALREADY answered.

R2189's ruling keeps a patch-carrying card — and its `approve & apply` button — alive after a
prose answer, so *answer, read the warning, then click apply* is the DESIGNED order. On that
order the button posts no answer at all: re-filing one would overwrite the operator's own
words and resurrect a settled record. So the answer already on disk kept saying
`config_patch_applied: false` while the patch was live.

That was harmless while nothing read the field. R2778 makes the asking RUN read it
(`engine/inbox_questions` → `engine/composer`), and a run told "NOT APPLIED" about a patch
that IS applied is worse off than the run that was told nothing. Hence this: the button
reports the apply, and the record is corrected.

It corrects the record and nothing else — no answer text is touched, no settled card is
re-opened, no config is written (the PATCH route the button already called is the only
config writer). Both copies move: the queued answer file a run has yet to consume, and the
durable archive (`questions/answered/`) the Decisions page reads once a run has.

Its own module because `api_questions.py` is at its size ceiling (tests/size_ratchet.json),
where the house rule is that an addition goes in its own file — and because this is one
narrow act with its own reason, not part of the answer protocol.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import patchreceipt
from ..paths import atomic_write_json, read_json
from .api_questions import _announce_answer, _answered_record_dir
from .decisions_read import find_question

router = APIRouter(tags=["questions"])


@router.post("/questions/{qid}/patch-applied")
def record_patch_applied(request: Request, qid: str) -> dict:
    """Record that `qid`'s `config_patch` was applied, on an answer that is already filed."""
    server = request.app.state.server
    routine, routine_dir = _answered_record_dir(server, qid)
    archive = routine_dir / "questions" / "answered" / f"{qid}.json"
    found = find_question(server, qid)
    match = found if isinstance(found, dict) else read_json(archive)
    if not isinstance(match, dict) or not match.get("config_patch"):
        raise HTTPException(400, f"question {qid!r} carries no config_patch to report on")
    fields = patchreceipt.receipt(match, applied=True)
    touched: list[str] = []
    for path in (routine_dir / "inbox" / f"answer-{qid}.json", archive):
        record = read_json(path)
        if isinstance(record, dict) and record:
            atomic_write_json(path, {**record, **fields})
            touched.append(path.name)
    if not touched:
        raise HTTPException(404, f"no answer on record for {qid!r}")
    _announce_answer(request, qid, routine)
    return {"ok": True, "routine": routine, "recorded": touched, **fields}
