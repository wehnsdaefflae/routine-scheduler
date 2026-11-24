"""One route: a decision AS AN A2UI SURFACE — `GET /questions/{qid}/a2ui` (D310 step 4).

The operator's order behind this is `use https://a2ui.org/ for all decision / ask elements`, with
his correction that it must bring NEW interaction elements rather than wrap the cards that
already exist. Steps 1-3 built the surface store, the catalogs and the renderer; this is the
route that hands the renderer a real decision, which is what makes the protocol exercised by the
console rather than only by its tests.

WHAT IT SERVES: the `createSurface` envelope `a2ui_decision.decision_surface` composes — one
message, the shape an agent would stream, so the console's own surface and an agent's are
indistinguishable to the renderer. That is the point of serving it as a message rather than as a
bespoke JSON view: the moment a routine wants to draw its own ask, it sends this same envelope
and nothing in the console has to learn a second shape.

WHAT IT DOES NOT DO: it does not answer. The surface's submit is an A2UI agent EVENT, and the
console routes that event to `POST /questions/{qid}/answer` — the one answer route every surface
posts through, whose writer (`answerfile.write`) exists precisely so there is one shape of the
inbox's `answer-*` file. A route here that took an answer would be the second writer.

Its own module rather than more of `api_questions.py`, which is at its size ceiling: the same
house rule `api_question_decline.py` and `api_patch_applied.py` follow.

THE RECORD IT NEEDS is the one every other decision surface reads — `decisions_read.find_
question`, the catalog of all three homes — so a qid that resolves on the Decisions page resolves
here, and one that does not is a 404 here as well.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from . import a2ui_decision
from .decisions_read import find_question

router = APIRouter(tags=["questions"])


@router.get("/questions/{qid}/a2ui")
def decision_as_a2ui(request: Request, qid: str) -> dict:
    """The A2UI `createSurface` message for one open decision.

    404 when no record carries that qid — including an AUDIT decision, which lives in the
    self-audit report rather than as a question record, exactly as it cannot be declined or
    snoozed either.
    """
    match = find_question(request.app.state.server, qid)
    if match is None:
        raise HTTPException(404, f"no question {qid!r}")
    return a2ui_decision.decision_surface(match)
