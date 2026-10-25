"""Approving a run's RECIPE-REVISION proposal (D200): arm the revise leg that already exists.

A separate module from `api_pending.py` for two reasons — that file is at its size ceiling, and
this is the one proposal whose approval is not a config write. The other five kinds materialize
something (a routine, a lane change, `enabled: false`, a swap, a report routing); this one hands
the RUN THAT PROPOSED IT the capability it lacked at its finish.

Why that, and not a web-side recipe write: the proposal is PROSE a run wrote about its own
recipe — "the scan stage should read the cursor before the feed, this run wasted two turns on
it" — not a diff. Nothing out here can turn that into an edit without re-deriving the reasoning
the run already did, and the reasoning is in that run's transcript. So approving resumes the
proposing run through `engine/revise.py`, exactly as the run view's "editable recipe" checkbox
does: the leg boots with recipe self-write granted (`recipe_unlocked`), the one-shot marker is
cleared on read, and the engine commits the recipe edit at that leg's end. One unlock path, not
a second one that can drift from it.

The run dir is the limit, and it is named rather than hidden: retention prunes runs
(`keep_runs`), and a proposal outlives the run that made it. Once the dir is gone there is no
transcript to resume, so this answers 409 and says to apply the revision from the routine page
instead — better than silently resuming a run whose reasoning no longer exists.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import pending
from ..engine import inbox, recipeproposal
from ..registry import TERMINAL_STATES
from .api_runs import run_state

router = APIRouter(tags=["pending"])


def _instruction(lines: list[str]) -> str:
    """What the resumed leg is told. The run's OWN words, handed back as the operator's
    go-ahead: it reasoned the revision at its finish and could not write it, so the leg needs
    the authorization, not an explanation of the change.
    """
    body = "\n".join(f"- {line}" for line in lines)
    return ("The operator approved the recipe revision(s) you proposed when you finished. Your "
            "recipe is writable to you on THIS leg only, so make the edits now and nothing "
            "else:\n\n" + body + "\n\nEdit main.md / stages/ / tuning.yaml with edit_file (a "
            "narrow anchor, not a rewrite), check what you wrote by reading it back, and finish "
            "with what you changed. If a line turns out to be wrong on re-reading, say so in "
            "the summary and leave that file alone.")


@router.post("/pending-creations/{pid}/revise")
async def apply_recipe_revision(request: Request, pid: str) -> dict:
    """Approve the proposal: resume the proposing run with its recipe writable."""
    server = request.app.state.server
    rec = pending.load(server.routines_home, pid)
    if rec is None:
        raise HTTPException(404, f"no pending creation {pid!r}")
    if rec.get("kind") != recipeproposal.KIND:
        raise HTTPException(400, f"proposal {pid!r} is a {rec.get('kind')!r}, not a recipe "
                                 "revision — approve it on its own card")
    fields = rec.get("fields") or {}
    lines = [str(x) for x in (fields.get("lines") or []) if str(x).strip()]
    if not lines:
        raise HTTPException(400, "this proposal names no revision — discard it")

    slug = str(rec.get("routine") or "")
    run_id = str(fields.get("run_id") or rec.get("run_id") or "")
    ts = run_id.split(":", 1)[1] if ":" in run_id else ""
    routine_dir = server.routines_home / slug
    run_dir = routine_dir / "runs" / ts
    if not ts or not run_dir.is_dir():
        raise HTTPException(409, f"the run that proposed this ({run_id or 'unknown'}) is no "
                                 "longer on disk, so there is no leg to resume — apply the "
                                 f"revision from {slug}'s own recipe editor and discard this "
                                 "proposal")
    if run_state(run_dir) not in TERMINAL_STATES:
        raise HTTPException(409, f"{run_id} is running again — recipe editing unlocks on a "
                                 "FINISHED run; approve this once it ends")
    from ..config import load_routine

    cfg, _problems = load_routine(routine_dir)
    if cfg is None:
        raise HTTPException(404, f"routine {slug!r} is gone — discard this proposal")

    from ..engine.revise import clear_revise_marker, write_revise_marker

    text = _instruction(lines)
    # The same pair the run view's checkbox files: the marker unlocks the leg, the message is
    # what the leg reads. `web-converse` is a LIVE via, so the resumed leg's boot drains it —
    # freight on any other channel would wait for a fresh run that a resume never is.
    write_revise_marker(run_dir, text)
    inbox.file_message(routine_dir, text, via="web-converse")
    rid = await request.app.state.runner.resume_terminal(cfg, ts, reason="converse")
    if not rid:
        # Nothing was unlocked, so leave nothing unlocked behind: a marker left by a resume
        # that did not happen would hand the NEXT leg — a plain converse — recipe write it
        # never presented for (the same cleanup api_run_control does).
        clear_revise_marker(run_dir)
        why = request.app.state.runner.resume_blocker(cfg, ts) or "the run could not be resumed"
        raise HTTPException(409, f"could not resume {run_id}: {why}")
    pending.drop(server.routines_home, pid)
    return {"ok": True, "id": pid, "resumed": rid, "routine": slug, "lines": lines}
