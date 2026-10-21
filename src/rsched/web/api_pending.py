"""Queued proposals: list, materialize, discard (F328).

The WEB layer materializes, exactly as it already applies forever-grants — that is the whole
reason this file exists rather than the engine doing it. A run never writes `routine.yaml`, so a
scheduled run's proposal has to cross into the config-writing half somewhere — this is the one
place it does. Materializing goes through the SAME `workflows.scaffold` / `rsched.lanes`
calls a conversation's confirmed creation uses — one materializer, not a second path that can
drift from it.

Five kinds ride this queue. Two are creations (`create_routine`, `manage_lane`); one is a SWAP
(`replace-routine`: a replacement candidate the improver created and proved takes the old
routine's place — engine/candidate.py, `_materialize_replace`); one is a DECISION with a choice
(`unowned-report`: a report nobody has owned for a week, routed or closed by the operator —
daemon/owed_watch.py, `_decide_unowned`, the only kind whose materialize takes a body). The fifth,
`goal-reached`, is the opposite: a routine reporting that it is FINISHED. It is queued by
`engine/goalreached.py` when its finish line is reached — by a run, by the calendar, or by the
operator's own tick; by then the routine has already stopped running — that half is derived
from its finish line and needs no click. Approving writes `enabled: false` through the ordinary
PATCH; discarding reopens the finish line, which puts the routine back on the schedule (a line
the calendar reached is changed on the routine page instead). Doing nothing leaves it paused
with the card standing.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import lanes, library_drift, pending, schedule
from ..engine import finishline
from ..paths import read_yaml

router = APIRouter(tags=["pending"])


class Discard(BaseModel):
    reason: str = ""


class Choice(BaseModel):
    """What the operator decided for an `unowned-report` card: route it to a routine, or close
    it. Other kinds carry no body.
    """

    choice: Literal["route", "close"] = "route"
    to: str = ""


def _decide_unowned(server, rec: dict, body: Choice | None) -> dict:
    """An UNOWNED report becomes the operator's decision (daemon/owed_watch.py): routed to a
    routine as a report FROM the operator that takes the row over, or closed by one that settles
    it — so the ledger says who decided, and nothing is answered in anybody else's name.
    """
    from .. import reports
    from ..daemon.owed_watch import runs

    f = rec.get("fields") or {}
    rid, title = str(f.get("report") or ""), str(f.get("title") or "")
    home = server.routines_home
    if body is None or body.choice == "close":
        filed = reports.file_report(
            home, routine="operator", run_id="decisions-page", title=f"Closed: {title}",
            detail=f"{rid} had no owner for {f.get('days')} days; the operator closed it.",
            disposal=reports.Disposal(settles=(rid,), closes=True))
        if filed is None:
            raise HTTPException(503, "the report ledger is busy — try again")
        return {"closed": rid}
    if not runs(home, body.to):
        raise HTTPException(400, f"{body.to!r} is not a routine that runs")
    filed = reports.file_report(
        home, routine="operator", run_id="decisions-page", target=body.to,
        target_dir=home / body.to, supersedes=(rid,), title=f"Yours now: {title}",
        detail=(f"{rid} had no owner for {f.get('days')} days, so the operator decided on the "
                f"Decisions page that it is {body.to}'s. Its full text is {rid} in the ledger."))
    if filed is None:
        raise HTTPException(503, "the report ledger is busy — try again")
    return {"routed": rid, "to": body.to, "report": filed[1]}


@router.get("/pending-creations")
def list_pending(request: Request) -> list[dict]:
    """Everything a run has proposed and nobody has decided yet, oldest first."""
    from .decisions_read import open_proposals

    return open_proposals(request.app.state.server)


def _materialize_routine(server, fields: dict) -> dict:
    from ..workflows.scaffold import scaffold

    slug = str(fields.get("slug") or "").strip()
    if (server.routines_home / slug).exists():
        raise HTTPException(409, f"a routine {slug!r} already exists — discard this proposal or "
                                 "rename it before creating")
    name = str(fields.get("name") or "").strip()
    instruction = str(fields.get("instruction") or "")
    workflow_slug = str(fields.get("workflow") or "")

    def lines(key: str) -> list[str]:
        raw = fields.get(key)
        return [t for t in raw if isinstance(t, str) and t.strip()] \
            if isinstance(raw, list) else []

    from ..engine.outcome_words import contract
    from ..workflows.suggest import generate_description

    declared = contract(lines("produces"))
    if isinstance(declared, str):      # the queue refuses this, so only a hand-made record
        raise HTTPException(422, f"this proposal carries no usable outcome contract "
                                 f"({declared}) — discard it")
    routine_dir = scaffold(server, slug=slug, name=name,
                           instruction=instruction, workflow_slug=workflow_slug,
                           # a COMPREHENSIVE generated description — the same one a
                           # conversation's confirmed create gets; falls back to the name
                           # when no endpoint answers, so materialization never fails on it
                           description=generate_description(server, name=name,
                                                            instruction=instruction,
                                                            workflow_slug=workflow_slug),
                           # the queued proposal carries what the run settled the same way a
                           # conversation's confirmed call does — one materializer, one path
                           pattern=str(fields.get("pattern") or ""), setup=lines("setup"),
                           done_when=lines("done_when"), finish_line=lines("finish_line"),
                           never=lines("never"), outcome=declared.model_dump())
    return {"created": "routine", "slug": slug, "dir": str(routine_dir)}


def _materialize_lane(server, fields: dict) -> dict:
    verb = str(fields.get("verb") or "").strip()
    home = server.routines_home
    lane_id = str(fields.get("target") or "").strip()
    name = str(fields.get("name") or "").strip()
    raw_members = fields.get("members")
    # The action's flat ordered slugs become the store's member RECORDS — the same wrap
    # engine/manage_lane does, so a proposal builds the lane a conversation would have.
    members = ([{"slug": str(m)} for m in raw_members]
               if isinstance(raw_members, list) else None)
    on_failure = fields.get("on_failure")
    cron = str(fields.get("cron") or "").strip()
    # A schedule is saved WITH the zone it was written in — the server's, exactly as the lane
    # PATCH and the conversation's own manage_lane record it. Without it the lane read the
    # server zone at every rescan instead, so a later zone change silently moved its fires.
    tz = schedule.server_tz() if cron else ""
    if verb == "create":
        rec = lanes.create(home, name=name, members=members,
                           on_failure=str(on_failure) if on_failure else None,
                           cron=cron, tz=tz)
        return {"created": "lane", "lane_id": rec["id"], "name": rec.get("name")}
    if verb == "update":
        paused = fields.get("paused")
        # `on_failure` is a TRI-STATE in the store (omit = unchanged, None = inherit, value =
        # override) and `cron` "" clears a schedule, so both are only passed when the proposal
        # actually carried them — a default would silently rewrite a field nobody proposed.
        extra: dict[str, Any] = {}
        if on_failure:
            extra["on_failure"] = str(on_failure)
        if "cron" in fields:
            extra.update(cron=cron, tz=tz)
        updated = lanes.update(
            home, lane_id, name=name or None, members=members,
            paused=bool(paused) if paused is not None else None, **extra)
        if updated is None:
            raise HTTPException(404, f"no lane {lane_id!r} — it was deleted since the proposal")
        return {"updated": "lane", "lane_id": lane_id}
    if verb == "delete":
        if not lanes.delete(home, lane_id):
            raise HTTPException(404, f"no lane {lane_id!r} — it is already gone")
        return {"deleted": "lane", "lane_id": lane_id}
    if verb == "set-default":
        value = lanes.set_default_on_failure(home, str(fields.get("on_failure") or ""))
        return {"default_on_failure": value}
    # Only config verbs (create/update/delete/set-default) are proposable: a fire is ephemeral,
    # not a creation, so `run` is never queued. Anything else is a record this queue cannot
    # honour — tell the operator to discard it and fire the lane live instead.
    raise HTTPException(
        400, f"a lane {verb!r} is not a creation and cannot be approved here — discard this "
             "proposal and fire the lane live from its row instead")


def _materialize_goal_reached(request: Request, rec: dict) -> dict:
    """Confirm a retirement: write `enabled: false` through the ONE config writer.

    The routine has ALREADY stopped running — the scheduler builds no fire table entry for a
    routine that reached its finish line (registry.RoutineInfo.retired), which is what lets it
    retire itself without anything writing config. This click is what makes that permanent and
    legible: after it, the routine reads as switched off in every surface that has ever meant
    it, so it survives someone editing the finish line later.

    Nothing else is touched — not the LANE it fires in, not the shared stores among its roots.
    A retired member is skipped by its chains without counting as a failure
    (daemon/lane_runs.py), so dropping it from a lane would buy nothing; and its roots are keys
    in its own routine.yaml, so clearing them would strip what it would need back the moment
    someone reopens the finish line. Retirement answers "does this still run?", which is neither of
    those questions.
    """
    from .api_routine_patch import RoutinePatch, patch_routine

    slug = str(rec.get("routine") or "")
    out = patch_routine(request, slug, RoutinePatch(enabled=False))
    return {"retired": slug, "updated": out.get("updated", [])}


def _materialize_replace(request: Request, rec: dict) -> dict:
    """The SWAP a replacement candidate was created for (engine/candidate.py) — one accept:
    the candidate takes the old routine's place in its lane, its own cron and its grants (the
    secrets a candidate never held), and the old routine is switched off. Every config write goes
    through the ordinary PATCH writer; the lane through the lane store. The old routine is kept,
    switched off, never deleted: its runs and its history stay readable.
    """
    from ..engine import candidate
    from .api_routine_patch import RoutinePatch, patch_routine

    server = request.app.state.server
    home = server.routines_home
    f = rec.get("fields") or {}
    new, old = str(f.get("candidate") or ""), str(f.get("replaces") or "")
    for slug in (new, old):
        if not (home / slug / "routine.yaml").is_file():
            raise HTTPException(409, f"{slug!r} no longer exists — discard this proposal")
    old_raw = read_yaml(home / old / "routine.yaml", {})
    sched = old_raw.get("schedule") if isinstance(old_raw.get("schedule"), dict) else {}
    lane = lanes.lane_of(home, old)
    if lane is not None:
        members = [{**m, "slug": new} if m.get("slug") == old else m for m in lane["members"]]
        lanes.update(home, lane["id"], members=members)
    patch_routine(request, new, RoutinePatch.model_validate({
        "enabled": True, "grants": old_raw.get("grants") or {},
        **({"schedule": {"cron": sched["cron"], "tz": sched.get("tz") or schedule.server_tz()}}
           if sched.get("cron") else {})}))
    patch_routine(request, old, RoutinePatch(enabled=False))
    candidate.forget(home, new)
    return {"swapped": new, "retired": old, "lane": lane["id"] if lane else None}


def _discard_replace(request: Request, rec: dict) -> None:
    """Keep the old routine: the candidate is switched off and stops being a candidate."""
    from ..engine import candidate
    from .api_routine_patch import RoutinePatch, patch_routine

    home = request.app.state.server.routines_home
    new = str((rec.get("fields") or {}).get("candidate") or "")
    if (home / new / "routine.yaml").is_file():
        patch_routine(request, new, RoutinePatch(enabled=False))
    candidate.forget(home, new)


@router.post("/pending-creations/{pid}/materialize")
def materialize(request: Request, pid: str, body: Choice | None = None) -> dict:
    """Build what the run proposed. The record is dropped only once the proposal has
    materialized; one that fails stays on the page with the reason in the 4xx — a proposal
    the operator must look at again, to fix or discard, rather than a button that silently
    does nothing twice.
    """
    server = request.app.state.server
    rec = pending.load(server.routines_home, pid)
    if rec is None:
        raise HTTPException(404, f"no pending creation {pid!r}")
    fields = rec.get("fields") or {}
    try:
        if rec.get("kind") == "create_routine":
            out = _materialize_routine(server, fields)
        elif rec.get("kind") == "manage_lane":
            out = _materialize_lane(server, fields)
        elif rec.get("kind") == "goal-reached":
            out = _materialize_goal_reached(request, rec)
        elif rec.get("kind") == "replace-routine":
            out = _materialize_replace(request, rec)
        elif rec.get("kind") == "unowned-report":
            out = _decide_unowned(server, rec, body)
        else:
            raise HTTPException(400, f"unknown proposal kind {rec.get('kind')!r}")
    except ValueError as exc:
        # a bad slug or an unknown pattern: the proposal is wrong, not the click — keep it on
        # the page with a legible reason so the operator can discard it deliberately
        raise HTTPException(400, str(exc)) from exc
    # A new routine or a lane change moves the fire table and the member-suppression set
    # (D71): rescan now, as the lane routes and the routine PATCH do, rather than leaving the
    # first fire to the next periodic pass (`registry_rescan_s`).
    request.app.state.scheduler.rescan()
    pending.drop(server.routines_home, pid)
    told = pending.notify_proposer(server, rec, "approved and materialized")
    return {"ok": True, "id": pid, "notified": told, **out}


@router.post("/pending-creations/{pid}/discard")
def discard(request: Request, pid: str, body: Discard) -> dict:
    """Throw the proposal away. The proposing routine is told, so its next run stops waiting on
    something that is never coming.

    For a `library-drift` card this is not a discard but the only ANSWER it has — the card is a
    notice with nothing to materialize — so it also records the gap as settled, and the watcher
    stops asking about that gap until the gap itself changes (`library_drift`, F604).
    """
    server = request.app.state.server
    rec = pending.load(server.routines_home, pid)
    if rec is None:
        raise HTTPException(404, f"no pending creation {pid!r}")
    reopened: list[str] = []
    settled = ""
    if rec.get("kind") == "library-drift":
        # A drift card has nothing to materialize, so `dismiss` IS its answer — and dropping
        # the record was all it did, which left the answer nowhere (F604). The gap is written
        # down as settled, so the next library commit does not re-ask a question he answered.
        settled = library_drift.settle(server.routines_home, rec, body.reason.strip())
    if rec.get("kind") == "replace-routine":
        _discard_replace(request, rec)
    if rec.get("kind") == "goal-reached":
        # Discarding a retirement means "not yet — keep going", so that has to change the finish
        # line, because retirement is DERIVED from it. Dropping the record alone would leave the
        # routine unscheduled with nothing left on the page to act on. A finish line the CALENDAR
        # reached is refused here: reopening changes nothing a date decides, so the next tick
        # would queue the same card again — its date is changed on the routine page; that
        # save withdraws the card (engine/goalreached.withdraw).
        routine_dir = server.routines_home / str(rec.get("routine") or "")
        if (routine_dir / "routine.yaml").is_file():
            if why := finishline.reached_by_calendar(finishline.load(routine_dir)):
                raise HTTPException(409, f"this finish line was reached by the calendar ({why}) "
                                         "— change its date in the routine's Goal settings, or "
                                         "retire it")
            reopened = finishline.reopen(routine_dir)
            request.app.state.scheduler.rescan()
    pending.drop(server.routines_home, pid)
    outcome = f"discarded ({body.reason.strip()})" if body.reason.strip() else "discarded"
    if reopened:
        outcome = (f"declined — the finish line is not reached, so {', '.join(reopened)} "
                   "were reopened and the routine is scheduled again")
    return {"ok": True, "id": pid, "reopened": reopened, "settled": settled,
            "notified": pending.notify_proposer(server, rec, outcome)}
