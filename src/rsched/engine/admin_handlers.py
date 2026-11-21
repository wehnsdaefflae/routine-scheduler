"""`schedule_run` and `report` — the two handlers that address something OUTSIDE this run.

A run reaching another routine's future (a one-shot fire) or another owner's queue (a
report); `interact.py` is the ASK protocol. They sit beside their renderer, `obs_admin`.

Neither asks the user anything, and neither writes config. `schedule_run` writes the daemon's
request spool; `report` appends one row to the single append-only ledger. Both are best-effort
in the same sense: the run's real job is elsewhere.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from .. import recipients, registry, report_threads, reports, schedule_once
from ..hooks import scheduleproj
from . import runtree


def _unknown_target(kind: str, target: str, home: Path,
                    valid: list[str] | None = None) -> dict:
    """The refusal for a target slug naming no routine — with the valid slugs and the close
    matches, so a run guessing a sibling's slug can correct it next turn. `valid` narrows those
    (a report offers only routines that would READ it); absent, every routine is valid.
    """
    slugs = valid if valid is not None else sorted(
        p.name for p in home.iterdir()
        if not p.name.startswith(".") and (p / "routine.yaml").is_file())
    return {"kind": kind, "target": target, "unknown_target": True,
            "suggestions": difflib.get_close_matches(target, slugs, n=3, cutoff=0.5),
            "valid_targets": slugs}


def _target_cannot_read(kind: str, target: str, server, home: Path,
                        sender: str = "") -> dict | None:
    """The refusal for a target that exists but will never READ what is filed to it — or None.

    A targeted report is read on that routine's next SCHEDULED run, so one that starts no run
    would read nothing (F614); filing is where the sender can still redirect it. The refusal
    says WHICH "off" (`enabled: false` may be a pause, `retired` is permanent) and the last run
    date, and offers SUGGESTIONS (`recipients.suggest`) — or triage, which is always read.
    Nothing is written. A target off only because its routine.yaml could not be READ passes.
    """
    info = registry.info(server, home, target)
    if info is None or recipients.reads(info):
        return None
    state, because = recipients.why_off(info)
    last = info.last_run
    return {"kind": kind, "target": target, "target_unreachable": True,
            "state": state, "because": because,
            "last_run": last.ts if last else "",
            "last_run_state": last.state if last else "it has never run",
            "suggestions": [s.as_dict() for s in recipients.suggest(server, home, target,
                                                                    exclude=(sender,))],
            "reason": f"a report filed to {target!r} would be delivered to its inbox and read on "
                      "its next scheduled run — and it starts none, so nothing would ever read "
                      "it. Nothing was filed and nothing was lost: re-file it to the routine that "
                      "owns the problem now, or leave `target` out entirely to send it to triage, "
                      "which is read."}


def handle_schedule_run(loop, action: dict) -> dict:
    """Arm or cancel a one-shot time trigger on a routine — the cross-routine setter the
    `scheduling` capability gates. The engine writes the request spool un-sandboxed (like
    write_util's library write); the daemon's OneShotManager fires the request once at
    fire_at then CONSUMES it. Any scheduling-holder may target ANY routine; self-targeting is
    the common case.
    """
    ctx = loop.ctx
    target = str(action.get("target") or "")
    home = ctx.server.routines_home
    # Self-target is ALWAYS allowed, including for a CONVERSATION (outside routines_home): its
    # spool entry is namespaced `conv--<slug>` so a same-named routine is never mis-fired.
    spool_slug = target
    if target == ctx.routine.slug and not (home / target / "routine.yaml").is_file() \
            and (ctx.routine.dir / "routine.yaml").is_file():
        spool_slug = f"conv--{target}"
    elif not (home / target / "routine.yaml").is_file():
        return _unknown_target("schedule_run", target, home)
    if action.get("cancel"):
        req_id = str(action.get("id")).strip() if action.get("id") else None
        removed = schedule_once.cancel(home, spool_slug, req_id)
        if removed and req_id:
            scheduleproj.record_event(home, hook_id=req_id, event="cancelled",
                                      detail=f"cancelled by {ctx.run_id}")
        return {"kind": "schedule_run", "target": target, "cancelled": removed, "id": req_id}
    try:
        fire_at = schedule_once.parse_fire_at(str(action.get("fire_at") or ""))
    except ValueError as exc:
        return {"kind": "schedule_run", "target": target, "bad_fire_at": str(exc)}
    rec = schedule_once.arm(home, spool_slug, fire_at=fire_at,
                            reason=str(action.get("reason") or ""),
                            requested_by=ctx.run_id)
    # also a `schedule`-triggered hook (D152); best-effort — the spool is the source
    scheduleproj.record_armed(home, rec, spool_slug)
    return {"kind": "schedule_run", "target": target, "armed": rec["id"],
            "fire_at": rec["fire_at"]}


def _decision_question(report_id: str, title: str, detail: str, target: str) -> str:
    """The prose the OPERATOR reads on the card for a report-born decision (F621).

    The report's own words plus which report this is and who acts on the answer. The detail
    rides WHOLE: only the delivered message is clipped for a run's prompt budget.
    """
    owner = (f"**{target}** owns the work and will be told what you decide"
             if target else "it is in triage — whoever picks it up will be told what you decide")
    return f"**{title}**\n\n{detail}\n\n*({report_id} — {owner}.)*" if detail else \
           f"**{title}**\n\n*({report_id} — {owner}.)*"


def handle_report(loop, action: dict) -> dict:
    """File a REPORT — the ungated channel every routine holds for work that is not its own
    task. Appends to <routines_home>/.control/reports.jsonl under an `R<n>` id.

    UNADDRESSED: waits for self-audit's triage; best-effort, a failed write reports
    filed=False. ADDRESSED (`target`): ALSO delivered into that routine's inbox for its next
    scheduled run — nothing is fired or woken. Self-targeting is refused (use `note` or
    `memory_write`). Works at any depth.

    Refusals come back as observations, never a filing: unknown/self target, a target that would
    never read it, an unusable `supersedes`, a disposal of a row not delivered to this routine,
    and the OPEN-THREAD CAP (which names the open ids to fold into; docs/items.md § Reports).
    """
    ctx = loop.ctx
    title = str(action.get("title") or "").strip()
    detail = str(action.get("detail") or "").strip()
    target = str(action.get("target") or "").strip()
    home = ctx.server.routines_home
    target_dir = None
    # A RUN-TREE address (`parent`, `child:<label>`) is a hook within this run tree, delivered
    # at that run's next turn boundary with no `R<n>` (engine/runtree.py says why).
    if runtree.is_runtree(target):
        # disposal fields travel WITH it so `send` names them back rather than swallowing them
        return runtree.send(loop, target=target, title=title, detail=detail,
                            disposal={f: action.get(f) for f in runtree.DISPOSAL_FIELDS})
    if target:
        if target == ctx.routine.slug:
            return {"kind": "report", "target": target, "self_target": True}
        target_dir = home / target
        if not (target_dir / "routine.yaml").is_file():
            return _unknown_target("report", target, home,
                                   recipients.reader_slugs(ctx.server, home))
        if (unreadable := _target_cannot_read("report", target, ctx.server, home,
                                              ctx.routine.slug)) is not None:
            return unreadable
    answers = str(action.get("answers") or "").strip()
    wanted = [str(i).strip().upper() for i in (action.get("supersedes") or [])]
    settles = [str(i).strip().upper() for i in (action.get("settles") or []) if str(i).strip()]
    # one ledger read answers both preconditions below (what may be folded, and disposed of)
    rows = (reports.read_reports(reports.reports_path(home))
            if wanted or settles or answers else [])
    folded: list[str] = []
    if wanted:
        folded, unusable = report_threads.supersedable(rows, wanted)
        if unusable:
            # all-or-nothing, worded PER ID by `supersedable` (a folded row names its live row)
            return {"kind": "report", "target": target, "supersedes": wanted,
                    "unusable": unusable}
    # D182: `answers` and `settles` dispose of a row only FOR THE ROUTINE IT WAS DELIVERED TO;
    # checked here so the author can act on the refusal
    if wanted_disposals := ([answers.upper()] if answers else []) + settles:
        _ok, elsewhere, unrouted = report_threads.disposable(rows, wanted_disposals,
                                                            replier=ctx.routine.slug)
        if elsewhere or unrouted:
            return {"kind": "report", "target": target, "undeliverable_disposal": True,
                    "elsewhere": [(rid, report_threads.owner_of(rows, rid) or "")
                                  for rid in elsewhere],
                    "unrouted": unrouted}
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
    # tell the filer the detail cap bit, while it can still re-file the rest
    if filed and (dropped := reports.clip_detail(detail)[1]):
        out["detail_truncated"] = dropped
    # prose claiming to close a row no field disposes of closes nothing; said now, while the
    # author can still add the field — not a refusal, since the id may merely be cited
    if filed and (claimed := report_threads.undisposed_claims(
            title=title, detail=detail,
            disposed=(answers, *settles, *(filed[2] or ())))):
        out["claimed_not_disposed"] = claimed
    if target:
        out["target"] = target
        out["delivery"] = "the target reads it on its next scheduled run"
    # options make it ALSO a decision for the operator — filed only once the report landed
    if filed and (picks := [str(o) for o in (action.get("options") or []) if str(o).strip()]):
        from . import interact

        out["decision_qid"] = interact.file_report_decision(
            loop, question=_decision_question(filed[1], title, detail, target),
            options=picks, report_id=filed[1], target=target)
    if filed and filed[2]:
        out["supersedes"] = filed[2]        # what the LEDGER folded, under its own lock
    if settles and filed:
        out["settles"] = settles
    if filed and (answers or settles or filed[2]):
        # answered, settled and folded rows leave the open set the pre-finish assist reads
        done = {str(i).strip().upper() for i in (answers, *settles, *filed[2])} - {""}
        ctx.reports_open = [r for r in ctx.reports_open if str(r).upper() not in done]
    return out
