"""The Changes endpoints — the DEVELOPMENT view's data: what changed in each routine and whether
it helped (readmodels/change_effects.py), and how each model served it (readmodels/model_fit.py).

Read-only and open to the routine token: the config-optimizer reads these to propose a pending
change, the way every maintenance routine reads the console's read models.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..ids import is_slug
from ..readmodels import change_effects
from ..readmodels.model_fit import model_fit

router = APIRouter(tags=["changes"])


@router.get("/changes")
def fleet_changes(request: Request) -> dict:
    """The fleet: engine releases judged across the routines they reached, model and rule
    changes rolled up across routines, and one line per routine with a measured change.
    """
    home = request.app.state.server.routines_home
    data = change_effects.changes(home)
    return {"releases": data["releases"], "fleet": data["fleet"],
            "routines": change_effects.summary(home), "measured_runs": data["measured_runs"]}


@router.get("/changes/{slug}")
def routine_changes(request: Request, slug: str) -> dict:
    """One routine: its changes, newest first, each with its windows, signals and verdict, and
    its runs grouped by the model, effort and deliberation that served them.
    """
    if not is_slug(slug):
        raise HTTPException(400, "not a routine slug")
    home = request.app.state.server.routines_home
    if not (home / slug / "routine.yaml").is_file():
        raise HTTPException(404, f"no routine {slug!r}")
    return {"routine": slug, "changes": change_effects.changes(home)["routines"].get(slug, []),
            "model_fit": model_fit(home, slug)}
