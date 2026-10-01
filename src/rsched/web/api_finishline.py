"""The routine page's GOAL: its finish line (editable) and what a finished run delivers (read).

The finish line is the operator's (`engine/finishline.py`): this PUT is its only writer for
what a person sets; a run can never write the file. Saving an outcome only the operator
judges as reached, or a date that has already passed, completes the finish line at once — so
the save queues the same retirement card a run or the calendar would; a save that makes a reached
finish line open again withdraws that card.

What a finished run delivers is the recipe's `## Done when`: shown here read-only, beside the
verdicts the last runs gave each line, because the recipe owns it and is changed where the
recipe is (Recipe, Revise recipe).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from ..engine import accounting, donewhen, finishline, goalreached
from ..ids import now_iso
from ..paths import read_json
from .routines_common import _info, active_run_dir

router = APIRouter(tags=["finish-line"])

#: How many recent runs the Done-when verdict strip shows.
VERDICT_RUNS = 10


class Outcome(BaseModel):
    """One outcome as the operator sets it. The engine's fields (distance, evidence, which run)
    are deliberately not accepted: a save carries them forward, so a client can neither invent
    a verdict nor erase one by editing an outcome's text.
    """

    model_config = ConfigDict(extra="forbid")
    id: str = ""
    text: str
    judge: str = "you"
    date: str = ""
    status: str = "open"


class FinishLineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcomes: list[Outcome] = []
    until: str = ""


def verdict_history(routine_dir: Path, limit: int = VERDICT_RUNS) -> list[dict]:
    """`[{run, accounting: {id: [verdict, note]}}]`, newest first, over the runs that carried
    an accounting.
    """
    runs = sorted((routine_dir / "runs").glob("*/status.json"),
                  reverse=True) if (routine_dir / "runs").is_dir() else []
    out = []
    for status in runs:
        doc = read_json(status)
        if isinstance(doc, dict) and doc.get("accounting"):
            out.append({"run": status.parent.name,
                        "accounting": {i: list(v) for i, v in
                                       accounting.parse(doc["accounting"]).items()}})
            if len(out) >= limit:
                break
    return out


def payload(routine_dir: Path) -> dict:
    doc = finishline.load(routine_dir)
    return {**doc, "reached": finishline.reached(doc),
            "done_when": donewhen.read(routine_dir),
            "verdicts": verdict_history(routine_dir)}


def checked(body: dict) -> dict:
    """The operator's document as `finishline.normalize` reads it — or the 400 naming what is
    wrong with it. Apart from `save` because the settings page's one accept must refuse a bad
    finish line BEFORE it writes the fields it carries beside it, not after.
    """
    doc = finishline.normalize(body)
    if body.get("until") and not doc["until"]:
        raise HTTPException(400, "until is a date, YYYY-MM-DD")
    for o in body.get("outcomes") or []:
        if o.get("judge") not in finishline.JUDGES:
            raise HTTPException(400, f"judge must be one of {list(finishline.JUDGES)}")
    if found := finishline.problems(doc):
        raise HTTPException(400, "; ".join(found))
    return doc


def save(routine_dir: Path, slug: str, name: str, body: dict) -> dict:
    """Validate and write the operator's finish line; queue the retirement card when this save
    is what completes it. Shared by this route and the settings page's one accept.
    """
    finishline.save(routine_dir, checked(body), now=now_iso())
    goalreached.propose(routine_dir.parent, slug, name)
    goalreached.withdraw(routine_dir.parent, slug)
    return payload(routine_dir)


@router.get("/routines/{slug}/finish-line")
def get_finish_line(request: Request, slug: str) -> dict:
    return payload(_info(request, slug).cfg.dir)


@router.put("/routines/{slug}/finish-line")
def set_finish_line(request: Request, slug: str, body: FinishLineBody) -> dict:
    """Not guarded by an active run: no run writes this file, so the web is its only writer
    and there is no two-writer race. A run already going keeps what it booted with.
    """
    info = _info(request, slug)
    out = save(info.cfg.dir, slug, info.cfg.name, body.model_dump())
    return {"ok": True, **out, "live_run": active_run_dir(info) is not None}
