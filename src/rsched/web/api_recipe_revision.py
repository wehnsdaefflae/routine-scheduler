"""Approving a RECIPE-REVISION proposal: arm the revise leg that already exists (D200), or hand
another routine's proposed revision to the routine that owns the recipe (D198).

A separate module from `api_pending.py` for two reasons — that file is at its size ceiling, and
these are the proposals whose approval is not a config write. The other four kinds materialize
something (a routine, a lane change, `enabled: false`, a swap, a report routing); these two
hand a RUN the capability it lacked: D200's card resumes the run that proposed the revision to
its OWN recipe, D198's (`materialize_revise`, dispatched from `api_pending.materialize`) files
an invitation for the TARGET routine's next run, because a revision one routine proposes for
another's recipe is still edited by a run of the routine that owns it.

Why that, and not a web-side recipe write: the proposal is PROSE a run wrote about its own
recipe — "the scan stage should read the cursor before the feed, this run wasted two turns on
it" — not a diff. Nothing out here can turn that into an edit without re-deriving the reasoning
the run already did, and the reasoning is in that run's transcript. So approving resumes the
proposing run through `engine/revise.py`, exactly as the run view's "editable recipe" checkbox
does: the leg boots with recipe self-write granted (`recipe_unlocked`), the one-shot marker is
cleared on read, and the engine commits the recipe edit at that leg's end. One unlock path, not
a second one that can drift from it. What differs is the LEG: the card's marker makes it NARROW
(`engine/reviseleg.py` — the recipe edit kinds, a small turn backstop, a finish that owes
nothing, the run's verdict and result kept), because the operator approved an edit, not another
run. D198's invitation arms a full run, which does its task as well, and stays full.

The run dir is the limit, and it is named rather than hidden: retention prunes runs
(`keep_runs`), and a proposal outlives the run that made it. Once the dir is gone there is no
transcript to resume, so this answers 409 and says to apply the revision from the routine page
instead — better than silently resuming a run whose reasoning no longer exists.

Both refusals — the pruned run, and a run that is LIVE again (the unlock needs a finished run) —
are ONE predicate, `revise_refusal`, and the CARD is given it (`with_revise_state`, read by
`api_pending.list_pending`). The card used to have no way to know, so it served an enabled
button this route answers 409 to, and finding out cost a click (F885). The view is told rather
than deriving it: a second copy of `registry.TERMINAL_STATES` out there would drift from the one
here, which is what actually decides.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import pending, reciperevise
from ..engine import inbox, recipeproposal
from ..registry import TERMINAL_STATES
from .api_runs import run_state

router = APIRouter(tags=["pending"])


class Edited(BaseModel):
    """The operator's OWN wording for the revision (F790), posted by the card's `edit & apply`.
    Absent — the plain `apply it` — the run's own lines stand, and this route behaves exactly as
    it did before. The optional-body shape is `api_pending.materialize`'s `Choice | None = None`.
    """

    lines: list[str] = []


def materialize_revise(server, rec: dict) -> dict:
    """Approve a revision one routine proposed for ANOTHER's recipe (D198): hand the prose to
    the target, whose own next run makes the edit under the one-leg recipe unlock.

    Called from the proposal queue's dispatch (`api_pending.materialize`) and living HERE for
    this module's own reason — the two recipe-revision approvals belong together, and
    `api_pending.py` is at its size ceiling. Not a web-side recipe write, for the same reason
    as D200's card above: the proposal is PROSE about a recipe, not a diff, and only a run that
    can read what that recipe is FOR can turn it into an edit. Unlike D200's card there is no
    leg to resume — the reasoning is the PROPOSER's and the recipe is the TARGET's — so the
    invitation waits for the target's next fire (`rsched/reciperevise.py`) instead of resuming
    anybody.
    """
    fields = rec.get("fields") or {}
    target = str(fields.get("target") or "")
    lines = [str(x) for x in (fields.get("lines") or []) if str(x).strip()]
    if not lines:
        raise ValueError("this proposal names no revision — discard it")
    if not (server.routines_home / target / "routine.yaml").is_file():
        raise ValueError(f"routine {target!r} is gone — discard this proposal")
    entry = reciperevise.invite(server.routines_home, target, lines=lines,
                                by=str(rec.get("routine") or ""),
                                run_id=str(fields.get("run_id") or rec.get("run_id") or ""),
                                approved_by=str(rec.get("id") or ""))
    if entry is None:
        raise ValueError(f"{target} already has {reciperevise.MAX_PENDING} revisions waiting "
                         "for its next run — approve this once it has run")
    return {"revises": target, "lines": lines, "armed": True}


def revise_refusal(server, rec: dict) -> tuple[str, str]:
    """The proposing run's state, and the 409 detail `apply it` would be refused with — the
    refusal is "" exactly when the route below would accept the click.

    ONE predicate, read by that route AND by the CARD (`api_pending.list_pending` carries both
    onto a `recipe-revision` row, `static/components/recipe-card.js` renders them). What decides
    is `registry.TERMINAL_STATES`; a second copy of that list in the view would drift from this
    one, so the view is TOLD rather than deriving it.

    F885: the card could not know, so it served an enabled button the server refuses. The
    operator clicked `apply it` on a live run at 09:22:33, read "…is running again — recipe
    editing unlocks on a FINISHED run", waited 93 seconds, clicked again and read the identical
    refusal — the only two UI error events in 2 998 trace rows across two days, on a control he
    had clicked 13 times in that window.
    """
    fields = rec.get("fields") or {}
    slug = str(rec.get("routine") or "")
    run_id = str(fields.get("run_id") or rec.get("run_id") or "")
    ts = run_id.split(":", 1)[1] if ":" in run_id else ""
    run_dir = server.routines_home / slug / "runs" / ts
    if not ts or not run_dir.is_dir():
        return "", (f"the run that proposed this ({run_id or 'unknown'}) is no "
                    "longer on disk, so there is no leg to resume — apply the "
                    f"revision from {slug}'s own recipe editor and discard this "
                    "proposal")
    state = run_state(run_dir) or ""
    if state not in TERMINAL_STATES:
        return state, (f"{run_id} is running again — recipe editing unlocks on a "
                       "FINISHED run; approve this once it ends")
    return state, ""


def with_revise_state(server, rec: dict) -> dict:
    """A `recipe-revision` row plus `run_state` and `revise_blocked`, for the card to read.

    Attached per REQUEST rather than inside `decisions_read.open_proposals`, which is memoized
    on the queue directory: that directory does not move when a RUN finishes, so a state folded
    into the memo would pin "running" until something wrote to the queue — the same defect
    pointing the other way. Other kinds are returned untouched; only this card asks.
    """
    if rec.get("kind") != recipeproposal.KIND:
        return rec
    state, refusal = revise_refusal(server, rec)
    return {**rec, "run_state": state, "revise_blocked": refusal}


def _instruction(lines: list[str], *, edited: bool = False) -> str:
    """What the resumed leg is told. Two framings, because the words are not always the run's.

    Unedited, the lines ARE the run's own, handed back as the operator's go-ahead: it reasoned
    the revision at its finish and could not write it, so the leg needs the authorization, not
    an explanation of the change. EDITED, the operator rewrote them on the card — and a leg told
    that his sentence is its own has no way to see where the two differ, so it would implement
    and defend wording it never chose. The branch says whose words these are.

    Both end on the same tail, because the leg is NARROW (`engine/reviseleg.py`): it holds the
    recipe edit kinds and a small turn backstop, and its finish owes nothing — so the words say
    exactly that: edit, read back once, finish. No orientation, no records, no accounting.
    """
    body = "\n".join(f"- {line}" for line in lines)
    head = ("The operator approved the recipe revision(s) you proposed when you finished. Your "
            "recipe is writable to you on THIS leg only, and this leg exists only to make them:")
    if edited:
        head = ("The operator decided a revision to your recipe and EDITED THE WORDING before "
                "approving it — THE LINES BELOW ARE HIS, not the ones you proposed when you "
                "finished. Where they differ from what you wrote, his version is the decision: "
                "implement it as given rather than restoring your own phrasing. Your recipe is "
                "writable to you on THIS leg only, and this leg exists only to make them:")
    return (head + "\n\n" + body + "\n\nEdit main.md / stages/ / tuning.yaml with edit_file (a "
            "narrow anchor, not a rewrite; independent edits may ride one reply), read each "
            "changed file back ONCE, then finish with one line per change. Nothing else: no "
            "orientation (the transcript above is your context), no records (ledger, notes, "
            "memory, state files) and no accounting — the run's work was accounted for when it "
            "finished, its verdict stands, and your summary is appended to its result. If a "
            "line turns out to be wrong on re-reading, say so in the summary and leave that "
            "file alone.")


@router.post("/pending-creations/{pid}/revise")
async def apply_recipe_revision(request: Request, pid: str, body: Edited | None = None) -> dict:
    """Approve the proposal: resume the proposing run with its recipe writable.

    With an `Edited` body the OPERATOR's lines are what the leg is told to implement (F790).
    They are capped here at `recipeproposal.MAX_LINES`, the proposal's own ceiling, because a
    cap the card enforces is not a cap: this route is reachable without the card.
    """
    server = request.app.state.server
    rec = pending.load(server.routines_home, pid)
    if rec is None:
        raise HTTPException(404, f"no pending creation {pid!r}")
    if rec.get("kind") != recipeproposal.KIND:
        raise HTTPException(400, f"proposal {pid!r} is a {rec.get('kind')!r}, not a recipe "
                                 "revision — approve it on its own card")
    fields = rec.get("fields") or {}
    lines = [str(x) for x in (fields.get("lines") or []) if str(x).strip()]
    edited = [str(x).strip() for x in (body.lines if body else []) if str(x).strip()]
    if body is not None and body.lines and not edited:
        # He cleared the box. That is not a request for the wording he just deleted, so it is
        # refused instead of falling back to it — the card keeps the row and he can retype.
        raise HTTPException(400, "the edited revision is empty — type the change you want, or "
                                 "discard the proposal")
    if edited:
        lines = edited[:recipeproposal.MAX_LINES]
    if not lines:
        raise HTTPException(400, "this proposal names no revision — discard it")

    slug = str(rec.get("routine") or "")
    run_id = str(fields.get("run_id") or rec.get("run_id") or "")
    ts = run_id.split(":", 1)[1] if ":" in run_id else ""
    routine_dir = server.routines_home / slug
    run_dir = routine_dir / "runs" / ts
    # The predicate the CARD is given too (`revise_refusal` → `with_revise_state`), so the
    # button is disabled exactly where this 409 is raised, and the operator reads the reason
    # on the card instead of spending a click to discover it (F885).
    if refusal := revise_refusal(server, rec)[1]:
        raise HTTPException(409, refusal)
    from ..config import load_routine

    cfg, _problems = load_routine(routine_dir)
    if cfg is None:
        raise HTTPException(404, f"routine {slug!r} is gone — discard this proposal")

    from ..engine.revise import NARROW, clear_revise_marker, write_revise_marker

    text = _instruction(lines, edited=bool(edited))
    # The same pair the run view's checkbox files: the marker unlocks the leg, the message is
    # what the leg reads. `web-converse` is a LIVE via, so the resumed leg's boot drains it —
    # freight on any other channel would wait for a fresh run that a resume never is. `leg`
    # makes it a NARROW leg (engine/reviseleg.py): the operator approved an edit, not a run.
    write_revise_marker(run_dir, text, leg=NARROW)
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
    return {"ok": True, "id": pid, "resumed": rid, "routine": slug, "lines": lines,
            "edited": bool(edited)}
