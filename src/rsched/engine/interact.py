"""Action handlers that converse with the user: ask_user (blocking/deferred questions)
and write_util, whose approval gate is the routine's write_util capability level.

EVERY kind of required user feedback funnels into the same decision record
(inbox.file_question): plain asks and util approvals, deferred and blocking. A blocking
decision waits up to the routine's ask_timeout_min (configurable on the routine page),
mirrors to Discord when the routine holds the communication permission, and — whichever
surface answers first — the other one is told. On timeout the run CONTINUES on the
model's stated `default`; the question stays open as deferred so a late answer still
reaches the next run. Waiting time is credited back to the wall-clock budget.
"""

from __future__ import annotations

import difflib
import time
from datetime import datetime, timedelta

from .. import bug_reports, sandbox, schedule_once, utils_lib
from ..ids import is_slug, question_id
from . import decisions, detach, inbox
from .control import RunAborted

# Natural affirmatives count: approval answers arrive as free text (Discord mirrors the
# question to a phone), and "Do it. The mail is …" must not read as a decline (F161 —
# two real approvals were recorded DECLINED because "do" was missing here).
_APPROVE_WORDS = ("approve", "approved", "yes", "y", "ok", "okay", "go", "accept", "confirm",
                  "do", "sure", "yep", "yeah", "proceed", "ja")


def _is_approval(text: str) -> bool:
    head = text.strip().lower().split()[0].strip(".,!:;") if text.strip() else ""
    return head in _APPROVE_WORDS


def handle_ask(loop, action: dict, poll_s: float, qtype: str = "question") -> dict:
    ctx = loop.ctx
    if qtype == "question" and loop.dialog_qid:
        # a re-ask after a dialog reply supersedes the still-open previous record
        inbox.resolve_question(ctx.routine.dir, loop.dialog_qid)
        loop.dialog_qid = None
    qid = question_id(ctx.run_ts, ctx.turn)
    mode = action.get("mode") or "deferred"
    if ctx.depth > 0 or detach.is_detached_run(ctx):
        mode = "deferred"  # subruns / detached tasks cannot block the run on the user
    options = list(action.get("options") or [])
    default = str(action.get("default") or "").strip()
    # config bridge: a proposed routine.yaml change the run can't make itself — rides the
    # decision record for the Decisions page's one-click apply (see engine/revise.py).
    cpatch = action.get("config_patch") if isinstance(action.get("config_patch"), dict) else None
    question = action["question"]
    extra = {"type": qtype, **({"default": default} if default else {})}
    ctx.transcript.event("question", {"qid": qid, "mode": mode, "question": question,
                                      "options": options, **extra})
    if mode == "deferred":
        inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                            qtype=qtype, default=default, config_patch=cpatch)
        ctx.asks_deferred += 1   # churn telemetry: a decision thrown over the wall
        return {"kind": "ask_user", "qid": qid, "mode": mode}

    timeout_min = ctx.budgets.ask_timeout_min
    expires = ((datetime.now().astimezone() + timedelta(minutes=timeout_min))
               .isoformat(timespec="seconds"))
    # blocking decisions are durable records too — the Decisions page never depends on a
    # live status.json to show one, and an aborted run leaves it behind as deferred
    inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                        mode="blocking", qtype=qtype, default=default, expires=expires,
                        config_patch=cpatch)
    mirror = decisions.mirror_blocking(ctx, qid, question, options, default, timeout_min)
    ctx.write_status("waiting_user",
                     question={"qid": qid, "question": question, "options": options,
                               "asked": ctx.run_ts, "expires": expires,
                               "mirrored": mirror is not None, **extra})
    deadline = time.monotonic() + timeout_min * 60
    started = time.monotonic()
    answer = None
    try:
        while time.monotonic() < deadline:
            if loop._aborted():
                raise RunAborted
            answer = inbox.take_answer(ctx.routine.dir, qid, loop.consumed_dir)
            if answer:
                break
            if mirror and (reply := mirror.poll()):
                answer = {"text": reply, "source": "discord"}
                break
            time.sleep(poll_s)
    except RunAborted:
        # the run dies but the decision survives — as a deferred question for the next run
        inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                            qtype=qtype, default=default, config_patch=cpatch)
        ctx.asks_deferred += 1
        raise
    finally:
        ctx.credit_suspended(time.monotonic() - started)
        ctx.write_status("running", question=None)
    if answer and answer.get("defer"):
        # The user parked the decision from the Decisions page — continue exactly like a
        # timeout: on the stated default, the record staying open as deferred.
        inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                            qtype=qtype, default=default, config_patch=cpatch)
        ctx.asks_deferred += 1
        if mirror:
            mirror.notify_deferred(default)
        return {"kind": "ask_user", "qid": qid, "mode": mode, "deferred_by_user": True,
                **({"default": default} if default else {})}
    if answer:
        source = answer.get("source", "web")
        ctx.transcript.event("answer", {"qid": qid, "text": answer["text"], "source": source,
                                        "intermediate": bool(answer.get("intermediate"))})
        if answer.get("intermediate"):
            # A dialog reply, not the answer: the user needs some back-and-forth before they
            # can decide. The decision record STAYS OPEN (deferred — the run is no longer
            # parked on it): the model's re-ask supersedes it, and a finish without a re-ask
            # leaves it live for the next run instead of silently dropping it. Discord gets
            # no "resolved" note — the follow-up question is the reply.
            inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                                qtype=qtype, default=default, config_patch=cpatch)
            loop.dialog_qid = qid
            return {"kind": "ask_user", "qid": qid, "mode": mode, "dialog": True,
                    "user_message": answer["text"],
                    "note": "This is a dialog reply, NOT the final answer — the user needs "
                            "more back-and-forth first. Address their message, then ask again "
                            "with ask_user (the original question, or a sharper version)."}
        inbox.resolve_question(ctx.routine.dir, qid)
        # The run's record of decided asks: guards an explicit user yes unblocks consult it
        # (recreate_denial). In-memory on purpose — a resumed leg starts empty and re-asks.
        ctx.user_answers.append({"qid": qid, "question": question, "answer": answer["text"]})
        if mirror:
            mirror.notify_resolved(answer["text"], source)
        return {"kind": "ask_user", "qid": qid, "mode": mode, "answered": True,
                "answer": answer["text"], "source": source}
    # timeout: continue WITHOUT the decision — on the stated default when there is one.
    # The record stays open (now deferred) so a late answer still reaches a future run.
    inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                        qtype=qtype, default=default, config_patch=cpatch)
    ctx.asks_deferred += 1
    if mirror:
        mirror.notify_timeout(default)
    return {"kind": "ask_user", "qid": qid, "mode": mode, "timed_out": True,
            "timeout_min": timeout_min, **({"default": default} if default else {})}


def recreate_denial(loop, action: dict) -> list[str]:
    """The never-recreate rule, checked INSIDE the schema-retry cycle (a denied call is
    corrected and never becomes a turn, like every permission gate): a write_util for a
    slug that once existed and was deleted from the util library — the user's deliberate
    act, per the library's git history — must not proceed silently. An explicit user yes
    THIS run (an answered blocking ask naming the util) unblocks it; the routine's normal
    write_util approval level still applies afterwards.
    """
    if action.get("kind") != "write_util":
        return []
    ctx = loop.ctx
    name = str(action.get("name") or "")
    if ctx.depth > 0 or not name or not is_slug(name):
        return []   # subruns can't write utils at all — handle_write_util declines them
    home = ctx.server.libraries_home
    if utils_lib.exists(home, name) or not utils_lib.was_deleted(home, name):
        return []   # a revision, or a slug that never existed — no recreation involved
    if any(name.lower() in str(a.get("question") or "").lower()
           and _is_approval(str(a.get("answer") or "")) for a in ctx.user_answers):
        return []   # the user explicitly said yes to this recreate, this run
    return [f"util {name!r} existed before and was DELETED from the util library by the "
            f"user — a user-deleted util is never recreated without asking. First ask_user "
            f'with mode "blocking", naming util {name!r} and why it is needed (e.g. '
            f"'Recreate the deleted util {name!r}? <reason>'); recreate only after an "
            f"explicit yes this run. On a no or a timeout, work without it and note the "
            f"gap in your finish summary."]


def handle_write_util(loop, action: dict, poll_s: float) -> dict:
    ctx = loop.ctx
    name, content = action["name"], action["content"]
    if ctx.depth > 0:
        return {"kind": "write_util", "name": name, "declined": True,
                "reason": "sub-workflows cannot create/revise utils — use existing ones"}
    # Doc-standard gate BEFORE the approval ask: a util without tags or with undeclared
    # secrets never reaches the user or the library — the observation names the fix.
    problems = utils_lib.header_problems(content)
    if problems:
        return {"kind": "write_util", "name": name, "header_ok": False,
                "problems": problems}
    home = ctx.server.libraries_home
    utils_lib.ensure_library(home, remote=ctx.server.libraries_remote)
    creating = not utils_lib.exists(home, name)
    # Approval policy is the routine's write_util capability level (always: every change;
    # creations: new utils only; never). No grants on the ctx = confirm everything.
    if ctx.grants is None or ctx.grants.needs_confirm(creating):
        verb = "create" if creating else "revise"
        ask = handle_ask(loop, {
            "question": f"Approve {verb} of global util '{name}'? First lines:\n"
                        f"{content.strip()[:400]}",
            "mode": "blocking", "options": ["approve", "decline"],
            "default": "the util is NOT applied until approved"}, poll_s,
            qtype="util-approval")
        if not ask.get("answered"):
            return {"kind": "write_util", "name": name, "pending_approval": True,
                    "qid": ask.get("qid")}
        if not _is_approval(ask["answer"]):
            # carry the verbatim answer: a decline that hides WHAT was said reads as a
            # contradiction when the user meant to approve in other words (F161)
            return {"kind": "write_util", "name": name, "declined": True,
                    "answer": str(ask["answer"])[:200]}
    # Selftest gates the LIBRARY, not just the observation: on failure the write is rolled
    # back — a new util's dir removed, a revision restored to the previous working text —
    # so a broken script is never left live for concurrent `gu` callers.
    previous = None if creating else utils_lib.read_util(home, name)
    utils_lib.write_util_file(home, name, content)
    ok, output = utils_lib.selftest(home, name, policy=sandbox.base_policy(ctx.server))
    if not ok:
        if previous is None:
            utils_lib.remove_util_file(home, name)
        else:
            utils_lib.write_util_file(home, name, previous)
        return {"kind": "write_util", "name": name, "created": creating,
                "selftest_ok": False, "reverted": True, "output": output[:2000]}
    utils_lib.git_commit(home, f"{'create' if creating else 'revise'} {name}",
                         paths=[f"utils/{name}"])
    return {"kind": "write_util", "name": name, "created": creating, "selftest_ok": True}


def handle_remove_util(loop, action: dict, poll_s: float) -> dict:
    """Delete a global util (curation) — the write_util counterpart, gated by the same
    util-authoring capability. Refuses if any sibling still declares it on a `calls:` line
    (mirrors the `gu remove` no-callers refusal); asks for approval unless the routine's
    write_util policy is 'never'; the removal itself runs un-sandboxed engine-side (like
    write_util's library write), committed so it is recoverable from git history.
    """
    ctx = loop.ctx
    name = action["name"]
    if ctx.depth > 0:
        return {"kind": "remove_util", "name": name, "declined": True,
                "reason": "sub-workflows cannot remove utils — curation is a top-level action"}
    home = ctx.server.libraries_home
    utils_lib.ensure_library(home, remote=ctx.server.libraries_remote)
    if not utils_lib.exists(home, name):
        return {"kind": "remove_util", "name": name, "missing": True}
    if callers := utils_lib.referenced_by(home, name):
        return {"kind": "remove_util", "name": name, "callers": callers}
    # Removal is destructive — approve it unless write_util is fully autonomous ('never').
    if ctx.grants is None or ctx.grants.needs_confirm(creating=True):
        ask = handle_ask(loop, {
            "question": f"Approve removal of global util '{name}'? It is deleted from the "
                        f"library (recoverable from git history).",
            "mode": "blocking", "options": ["approve", "decline"],
            "default": "the util is NOT removed until approved"}, poll_s,
            qtype="util-approval")
        if not ask.get("answered"):
            return {"kind": "remove_util", "name": name, "pending_approval": True,
                    "qid": ask.get("qid")}
        if not _is_approval(ask["answer"]):
            return {"kind": "remove_util", "name": name, "declined": True}
    utils_lib.remove_util_file(home, name)
    utils_lib.git_commit(home, f"remove {name}", paths=[f"utils/{name}"])
    return {"kind": "remove_util", "name": name, "removed": True}


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


def handle_report_bug(loop, action: dict) -> dict:
    """File a bug report against the scheduler — the ungated, default-on channel every
    routine holds. Appends a structured entry to <routines_home>/.control/bug-reports.jsonl
    (routine, run_id, ts, title, detail); self-audit's gather-evidence reads that stream
    each run and turns unresolved entries into findings. Best-effort like the health log —
    a failed write never aborts the reporting run; it just reports filed=False. Works at any
    depth (subruns report too — the report carries the run that saw the bug).
    """
    ctx = loop.ctx
    title = str(action.get("title") or "").strip()
    detail = str(action.get("detail") or "").strip()
    path = bug_reports.file_bug_report(
        ctx.server.routines_home, routine=ctx.routine.slug, run_id=ctx.run_id,
        title=title, detail=detail)
    return {"kind": "report_bug", "title": title, "filed": path is not None}
