"""`schedule_run` and `report` — the two handlers that address something OUTSIDE this run.

Split out of `interact.py` (F393), whose own docstring admitted they merely rode it: one is
the ASK protocol, these two are a run reaching another routine's future (a one-shot fire) or
another owner's queue (a report). They sit beside their renderer, `obs_admin`.

Neither asks the user anything, and neither writes config. `schedule_run` writes the daemon's
request spool; `report` appends one row to the single append-only ledger. Both are best-effort
in the same sense: the run's real job is elsewhere.
"""

from __future__ import annotations

import difflib

from .. import report_threads, reports, schedule_once


def handle_schedule_run(loop, action: dict) -> dict:
    """Arm or cancel a one-shot time trigger on a routine — the cross-routine setter the
    `scheduling` capability gates. The engine writes the request spool un-sandboxed (like
    write_util's library write); the daemon's OneShotManager fires the request once at
    fire_at then CONSUMES it (auto-deactivate). Scope (a): any scheduling-holder may target
    ANY routine; self-targeting (a run arming its own follow-up) is the common case.
    """
    ctx = loop.ctx
    target = str(action.get("target") or "")
    home = ctx.server.routines_home
    # Self-target is ALWAYS allowed (the schema promises it) — including for a
    # CONVERSATION, which lives outside routines_home: its spool entry is namespaced
    # (`conv--<slug>`) so a same-named routine can never be mis-fired, and the daemon's
    # OneShotManager resolves that namespace back to conversations_home (waking the
    # conversation by RESUMING it — the "remind me in 3 days" flow).
    spool_slug = target
    if target == ctx.routine.slug and not (home / target / "routine.yaml").is_file() \
            and (ctx.routine.dir / "routine.yaml").is_file():
        spool_slug = f"conv--{target}"
    elif not (home / target / "routine.yaml").is_file():
        # Discoverability: a scheduling routine guessing a sibling's slug (the train-seat
        # friction) should get the valid slugs + close matches back, not a bare rejection.
        slugs = sorted(p.name for p in home.iterdir()
                       if not p.name.startswith(".") and (p / "routine.yaml").is_file())
        return {"kind": "schedule_run", "target": target, "unknown_target": True,
                "suggestions": difflib.get_close_matches(target, slugs, n=3, cutoff=0.5),
                "valid_targets": slugs}
    if action.get("cancel"):
        req_id = str(action.get("id")).strip() if action.get("id") else None
        removed = schedule_once.cancel(home, spool_slug, req_id)
        return {"kind": "schedule_run", "target": target, "cancelled": removed, "id": req_id}
    try:
        fire_at = schedule_once.parse_fire_at(str(action.get("fire_at") or ""))
    except ValueError as exc:
        return {"kind": "schedule_run", "target": target, "bad_fire_at": str(exc)}
    rec = schedule_once.arm(home, spool_slug, fire_at=fire_at,
                            reason=str(action.get("reason") or ""),
                            requested_by=ctx.run_id)
    return {"kind": "schedule_run", "target": target, "armed": rec["id"],
            "fire_at": rec["fire_at"]}


def handle_report(loop, action: dict) -> dict:
    """File a REPORT — the ungated channel every routine holds for work that is not its own
    task. Appends to <routines_home>/.control/reports.jsonl under an `R<n>` id.

    UNADDRESSED (no `target`): the entry waits in the stream for self-audit's triage. Filing
    it is best-effort like the health log — a failed write never aborts the reporting run,
    whose real job is elsewhere; it just reports filed=False.

    ADDRESSED (`target`): the report is ALSO delivered into that routine's inbox, and its next
    scheduled run reads it. Nothing is fired and nothing is woken — another routine's schedule
    is its own.

    Self-targeting is refused: a note to yourself is `note` or `memory_write`, and queueing
    prose into your own next prompt is a loop with no reader in between. Works at any depth —
    subruns report too, and the row carries the run that saw the problem.

    Three things can REFUSE a report here, each returning an observation instead of filing:
    an unknown or self target; `supersedes` naming a row that cannot be taken over; and the
    OPEN-THREAD CAP, which names the ids already open to this owner so the run has something
    to fold into rather than just a count (docs/items.md § Reports).
    """
    ctx = loop.ctx
    title = str(action.get("title") or "").strip()
    detail = str(action.get("detail") or "").strip()
    target = str(action.get("target") or "").strip()
    home = ctx.server.routines_home
    target_dir = None
    if target:
        if target == ctx.routine.slug:
            return {"kind": "report", "target": target, "self_target": True}
        target_dir = home / target
        if not (target_dir / "routine.yaml").is_file():
            # Discoverability: a routine guessing a sibling's slug should get the valid slugs
            # and close matches back, not a bare rejection (as schedule_run does).
            slugs = sorted(p.name for p in home.iterdir()
                           if not p.name.startswith(".") and (p / "routine.yaml").is_file())
            return {"kind": "report", "target": target, "unknown_target": True,
                    "suggestions": difflib.get_close_matches(target, slugs, n=3, cutoff=0.5),
                    "valid_targets": slugs}
    answers = str(action.get("answers") or "").strip()
    wanted = [str(i).strip().upper() for i in (action.get("supersedes") or [])]
    folded: list[str] = []
    if wanted:
        rows = reports.read_reports(reports.reports_path(home))
        folded, unusable = report_threads.supersedable(rows, wanted)
        if unusable:
            # Naming them beats folding what it can: a run told "3 of 5 taken over" has to
            # work out which two it still owns, and that is the bookkeeping this field exists
            # to remove.
            return {"kind": "report", "target": target, "supersedes": wanted,
                    "unusable": unusable, "reason": "unknown to the ledger, retracted, or "
                    "already folded into another thread — a row belongs to exactly one"}
    settles = [str(i).strip().upper() for i in (action.get("settles") or []) if str(i).strip()]
    try:
        filed = reports.file_report(home, routine=ctx.routine.slug, run_id=ctx.run_id,
                                    title=title, detail=detail, target=target,
                                    target_dir=target_dir,
                                    disposal=reports.Disposal(
                                        answers=answers,
                                        closes=bool(action.get("closes")),
                                        settles=tuple(settles)),
                                    supersedes=tuple(folded))
    except report_threads.ThreadCapError as cap:
        return {"kind": "report", "target": target, "thread_cap": report_threads.OPEN_THREAD_CAP,
                "open_to_target": cap.open_ids, "oldest": cap.open_ids[0]}
    out = {"kind": "report", "title": title, "filed": filed is not None,
           "id": filed[1] if filed else ""}
    if target:
        out["target"] = target
        out["delivery"] = "the target reads it on its next scheduled run"
    if filed and filed[2]:
        out["supersedes"] = filed[2]        # what the LEDGER folded, under its own lock
    if settles and filed:
        out["settles"] = settles
    if filed and (answers or settles):
        # This run has now disposed of those threads — the pre-finish assist reads what is
        # LEFT. `answers` ends one exchange, `settles` ends every row it names, and both have
        # to leave the open set or the assist keeps asking for work that is already done.
        done = {answers.upper(), *settles} - {""}
        ctx.reports_open = [r for r in ctx.reports_open if r not in done]
    return out
