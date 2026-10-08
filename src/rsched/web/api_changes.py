"""The Changes endpoints — the DEVELOPMENT view's data: what changed in each routine and whether
it helped (readmodels/change_effects.py), and how each model served it (readmodels/model_fit.py).

Read-only and open to the routine token: the config-optimizer reads these to propose a pending
change, the way every maintenance routine reads the console's read models.

The console keeps production and development apart (operator, 2026-10-08): the fleet view is
#/changes, one routine's is #/changes/<slug>, and the PRODUCTION routine page carries exactly one
line about any of it — `GET /changes/{slug}/summary`, small enough to re-read when that routine's
run finishes.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ..ids import is_slug
from ..readmodels import change_effects
from ..readmodels.model_fit import model_fit
from ..readmodels.run_health import recipe_regression

router = APIRouter(tags=["changes"])


def _routine_home(request: Request, slug: str) -> Path:
    """The routines home, once `slug` is known to name a routine there — 400/404 otherwise."""
    if not is_slug(slug):
        raise HTTPException(400, "not a routine slug")
    home: Path = request.app.state.server.routines_home
    if not (home / slug / "routine.yaml").is_file():
        raise HTTPException(404, f"no routine {slug!r}")
    return home


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
    home = _routine_home(request, slug)
    return {"routine": slug, "changes": change_effects.changes(home)["routines"].get(slug, []),
            "model_fit": model_fit(home, slug)}


@router.get("/changes/{slug}/summary")
def routine_summary(request: Request, slug: str) -> dict:
    """The production routine page's ONE development line: how many measured changes the
    routine has, the newest one's verdict and when it came, and the recipe-health regression
    flag on its newest recipe change (`recipe_regression`: the version and its subject while
    flagged, else null). Everything behind those numbers lives at #/changes/<slug>.
    """
    home = _routine_home(request, slug)
    chs = change_effects.changes(home)["routines"].get(slug, [])
    reg = recipe_regression(request.app.state.server, home / slug, slug)
    return {"routine": slug, "changes": len(chs),
            "latest": chs[0]["verdict"] if chs else None,
            "at": chs[0]["at"] if chs else None,
            "recipe_regression": ({"short": reg.get("short", ""), "subject": reg.get("subject", "")}
                                  if reg.get("flagged") else None)}
