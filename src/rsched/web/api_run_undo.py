"""UNDOING a run — the two routes behind the run page's ⤺ control (D269).

Its own module rather than two more routes in `api_run_control.py`: that file is at 350 lines
against this repo's one-responsibility budget, and undoing a FINISHED run's work is a different
job from steering a live one. Everything about what an undo may touch, what refuses it and why
lives in `engine/rundo.py`; these routes only expose it.

The pair is deliberate, and the GET is the half that makes the control honest: the page asks
what an undo WOULD do before it offers the button, so a refusal arrives as the server's own
sentence — *"a later commit has touched the same files: a1b2c3d (operator: fix the recipe by
hand)"* — rather than as a disabled button with no reason. A person can act on the first and not
on the second.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..engine.rundo import plan, undo
from .api_runs import _run_dir, require_terminal

router = APIRouter()


@router.get("/runs/{run_id}/undo")
def undo_plan(request: Request, run_id: str) -> dict:
    """What an UNDO of this run would revert, and why it would be refused. Read-only."""
    _, run_dir = _run_dir(request, run_id)
    return plan(run_dir.parent.parent, run_id).as_dict()


@router.post("/runs/{run_id}/undo")
def undo_run(request: Request, run_id: str) -> dict:
    """UNDO this run: revert its own commits in the routine's repo, limited to its deliverables
    and the recipe, with `state/` left exactly as it is — and REFUSED when a later commit
    touched the same paths (D269, decided 2026-10-08).

    It reverts rather than rewrites, so the undo is itself a commit and itself undoable, and
    nothing already pushed has to be force-updated. A LIVE run is refused by
    `require_terminal`: its own autocommit has not happened yet, so there is nothing of it in
    the repo to revert.
    """
    _, run_dir = _run_dir(request, run_id)
    require_terminal(run_dir, "undo")
    out = undo(run_dir.parent.parent, run_id)
    if not out.get("ok"):
        raise HTTPException(409, out.get("why") or "the undo was refused")
    return out
