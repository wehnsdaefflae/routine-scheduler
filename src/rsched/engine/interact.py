"""The ASK protocol — every kind of required user feedback.

Library authoring moved to `authoring.py`, the secret-exposure gate to `secretgate.py` and
the two handlers that merely RODE this file — `schedule_run` and `report` — to
`admin_handlers.py` (F393): four responsibilities had accumulated in one file. What is left
is one — turning a run's need for a human into a decision record and back into an answer.

EVERY kind of required user feedback funnels into the same decision record
(inbox.file_question): plain asks and util approvals, deferred and blocking. A blocking
decision waits up to the routine's ask_timeout_min (configurable on the routine page)
and is answered on the web console — the Decisions page, with browser push carrying it to
a phone. On timeout the run CONTINUES on the model's stated `default`; the question stays
open as deferred so a late answer still reaches the next run. Waiting time is credited
back to the wall-clock budget.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from ..ids import question_id
from . import availability, detach, inbox, requests
from .control import RunAborted

# Natural affirmatives count: approval answers arrive as free text, and "Do it. The mail
# is …" must not read as a decline (F161 — two real approvals were recorded DECLINED
# because "do" was missing here).
_APPROVE_WORDS = ("approve", "approved", "yes", "y", "ok", "okay", "go", "accept", "confirm",
                  "do", "sure", "yep", "yeah", "proceed", "ja")
# Explicit declines only. An approval question is settled by a clear yes OR a clear no —
# anything else is NOT an answer (D38): the wait loop holds it as a delayed user message
# and keeps the question open instead of letting a presence ping decline a util.
_DECLINE_WORDS = ("decline", "declined", "no", "n", "deny", "denied", "reject", "rejected",
                  "stop", "cancel", "don't", "dont", "nein", "nope", "never", "skip")


def _head_word(text: str) -> str:
    return text.strip().lower().split()[0].strip(".,!:;") if text.strip() else ""


def is_approval(text: str) -> bool:
    return _head_word(text) in _APPROVE_WORDS


#: Question types settled ONLY by a clear approve/decline (D38). Every authoring approval
#: belongs here — `authoring.py` files util- and rule-approvals, `remind.py` the global
#: reminder one.
_APPROVAL_QTYPES = frozenset({"util-approval", "rule-approval", "reminder-approval"})


def _settles_approval(text: str) -> bool:
    """True when the text is a clear approve OR a clear decline — the only two replies
    that may settle a blocking util-approval (D38).
    """
    head = _head_word(text)
    return head in _APPROVE_WORDS or head in _DECLINE_WORDS


def _held_not_settled(qtype: str, answer: dict) -> bool:
    """D38 across record types: does this reply fail to SETTLE the question? An APPROVAL
    settles only on a clear approve/decline, an access request only on one of the typed
    decisions; defer markers and dialog replies pass through to their own paths. A held
    reply becomes a delayed user message and the wait continues.

    Every approval qtype is listed, not just the first one: the check reads the type, so a
    type it has never heard of falls through to "settled" and an ambiguous reply lands as a
    decision. `rule-approval` and `reminder-approval` write to the LIBRARY — a copy every
    holder reads at its next run — which is the last place a "hmm, maybe" should count as
    yes.
    """
    if not answer.get("text") or answer.get("defer") or answer.get("intermediate"):
        return False
    if qtype in _APPROVAL_QTYPES:
        return not _settles_approval(str(answer["text"]))
    if qtype == "request":
        return answer.get("decision") not in requests.DECISIONS
    return False


def _unescape_newlines(text: str) -> str:
    r"""Literal backslash-n sequences ("\n") become real newlines — see _normalize_plain."""
    if "\\n" not in text:
        return text
    return text.replace("\\r\\n", "\n").replace("\\n", "\n")


def _normalize_plain(qtype: str, question: str, default: str) -> tuple[str, str]:
    r"""D85-A (F291/R242): some models double-escape newlines in PLAIN question text, so a
    literal backslash-n reached the UI as "\n" (the renderer and store are correct —
    mdInline handles real newlines). Normalize question + default at intake, but ONLY for
    plain questions: util-approvals embed util SOURCE and access requests carry typed ids,
    where the two-character sequence is intended verbatim.
    """
    if qtype != "question":
        return question, default
    return _unescape_newlines(question), _unescape_newlines(default)


def _free_qid(ctx) -> str:
    """A decision-record id no OPEN record already uses.

    The id is `q-<run-ts>-<turn>`, which is unique per turn — and a turn could only ever file
    one question until a side field that rides ANY kind gained its own approval (a global
    `remind` op on an `ask_user` action files two). `file_question` is an unconditional write,
    so the second record silently replaced the first and the user answered a question the run
    was no longer waiting on. A settled record frees its id again, which is why this checks
    only what is still pending.
    """
    base = question_id(ctx.run_ts, ctx.turn)
    # the ROOT routine's pending dir, not `ctx.routine.dir`: a child shares its parent's
    # run_ts, so a parent and a child asking on the same turn derive the same base id
    pending = ctx.root_routine_dir / "questions" / "pending"
    qid, n = base, 1
    while (pending / f"{qid}.json").exists():
        n += 1
        qid = f"{base}-{n}"
    return qid


def _config_patch_shape(patch: dict | None, home: str = "") -> str:
    """Refuse a `config_patch` whose KEYS the apply route would reject, at the moment it is
    filed rather than when the operator clicks.

    The target was already checked above; the BODY was not, so a run could invent a shape and
    the record reached the Decisions page wearing an apply button that 422s. Measured
    2026-09-23: a proposed filesystem grant arrived as `{"filesystem": {"read": [...]}}`, the
    apply answered `filesystem: Extra inputs are not permitted`, and the operator had a
    decision he could read and not take — the one state a decision surface must never be in.

    `configflow.CLASSIFICATION` is the field list to check against because it is the one the
    project already keeps honest: `tests/test_configflow.py` fails on a patch field missing
    from it, so this can never drift behind the model it stands in for. Routing keys are not
    fields, and an empty patch is left to the caller above.
    """
    if not patch:
        return ""
    from ..configflow import CLASSIFICATION
    # `_config_target` has already run and POPPED the routing key, so the surface is read
    # from the target it resolved, never from the body — which no longer says.
    if home == "domains" or patch.get("domain"):
        # A DOMAIN patch is a different surface with its own body (D140): the shared block
        # merges under `config`, and dropping a key from it has to be said out loud.
        known, surface = {"domain", "name", "config", "remove"}, "domain config"
    else:
        known, surface = set(CLASSIFICATION) | {"routine"}, "routine config"
    stray = sorted(k for k in patch if k not in known)
    if not stray:
        return ""
    return (f"config_patch key(s) {', '.join(repr(k) for k in stray)} are not {surface} — the "
            f"apply would refuse them and the user would be left with a decision he can read "
            f"and not take. The body IS the PATCH body, so filesystem roots are "
            f"`fs_read_roots` and `fs_write_roots` (flat lists of paths), not a nested object. "
            f"Valid keys here: {', '.join(sorted(known))}.")


def _config_target(ctx, cpatch: dict | None) -> tuple[str, str, str]:
    """What a `config_patch` is FOR — its own asker unless the patch names another target.

    Returns `(target, home, error)`. `target` is `""` when the patch is for the asker itself;
    `home` is the API surface the apply must PATCH — `""` for the asker, `"routines"` for
    another routine, `"domains"` for a domain (R1488).

    D123/F458. A run proposes a config change it cannot make itself; the Decisions page applies
    it. Before 0.326.0 that apply was hardwired to the ASKING routine, so config-optimizer —
    whose whole job is per-routine config — could not land a single change on a target: its
    suedlink-wlf budget proposal rewrote config-optimizer's own budgets and reported success
    (R1343, R1407). The slug rides INSIDE the patch as `routine`, which keeps `config_patch`
    one object in the action schema, and is popped here so the remaining keys stay a clean
    PATCH body the endpoint's `extra="forbid"` accepts.

    R1488 adds the other target a config proposal can legitimately have: a DOMAIN, named as
    `domain`. A domain's shared block is config exactly as a routine's file is, and the daemon
    has always exposed `PATCH /api/domains/{id}` — but with the target able to name only a
    routine, every domain-level finding ended as prose telling the operator to go and click it
    themselves, which is the delegation of mechanical work the ask-policy rule forbids.
    Naming both in one patch is refused rather than guessed at: they are two different surfaces,
    and a patch body valid for one is not valid for the other.

    Resolution happens at this seam, not at apply time, because an unresolvable target must
    never reach the user wearing a working button. A name matching no installed routine or no
    domain is refused on the turn that asked. Falling back to the asker is the defect itself and
    is never done.
    """
    if not cpatch:
        return "", "", ""
    want = str(cpatch.pop("routine", "") or "").strip()
    want_domain = str(cpatch.pop("domain", "") or "").strip()
    if want and want_domain:
        return "", "", (f"config_patch names both routine {want!r} and domain {want_domain!r}: "
                        "a patch applies to ONE config surface — a routine's routine.yaml or a "
                        "domain's shared block. Send the two changes as two proposals.")
    home = ctx.routine.dir.parent
    if want_domain:
        from .. import domains

        if domains.get(home, want_domain) is None:
            return "", "", (f"config_patch domain {want_domain!r}: no such domain under {home} "
                            "— the target must name a domain by the id the Domains page lists, "
                            "not by its display name.")
        return want_domain, "domains", ""
    if not want or want == ctx.routine.slug:
        return "", "", ""
    if not (home / want / "routine.yaml").exists():
        return "", "", (f"config_patch routine {want!r}: no such routine under {home} — the "
                        "target must name an installed routine by its slug, as the Routines "
                        "page lists it. Omit `routine` to propose the change for yourself.")
    return want, "routines", ""


def handle_ask(loop, action: dict, poll_s: float, qtype: str = "question") -> dict:
    ctx = loop.ctx
    if qtype == "question" and loop.dialog_qid:
        # a re-ask after a dialog reply supersedes the still-open previous record
        inbox.resolve_question(ctx.routine.dir, loop.dialog_qid)
        loop.dialog_qid = None
    qid = _free_qid(ctx)
    mode = action.get("mode") or "deferred"
    if ctx.depth > 0 or detach.is_detached_run(ctx):
        mode = "deferred"  # subruns / detached tasks cannot block the run on the user
    options = list(action.get("options") or [])
    default = str(action.get("default") or "").strip()
    # config bridge: a proposed routine.yaml change the run can't make itself — rides the
    # decision record for the Decisions page's one-click apply (see engine/revise.py).
    cpatch = action.get("config_patch") if isinstance(action.get("config_patch"), dict) else None
    # D123/F458: the patch may name ANOTHER routine as its target. Until 0.326.0 the apply
    # button always PATCHed the ASKING routine, so a config-optimizer proposal for suedlink-wlf
    # silently rewrote config-optimizer's own budgets and reported success (R1343, R1407). The
    # target is resolved HERE, at the seam that writes the durable record, so a slug naming no
    # routine can never reach the Decisions page wearing an apply button. An unresolvable
    # target is refused loudly rather than falling back to the asker — a silent fallback is
    # precisely the defect.
    ctarget, chome, cterr = _config_target(ctx, cpatch)
    if cterr:
        return {"kind": qtype if qtype != "question" else "ask_user", "error": cterr}
    cterr = _config_patch_shape(cpatch, chome)
    if cterr:
        return {"kind": qtype if qtype != "question" else "ask_user", "error": cterr}
    # A typed access request (entities.py) rides the same record; the Decisions page
    # renders the allow/deny × now/forever buttons for it (plus allow-once for
    # turn-action classes, D65), and the answer's `decision` is what settles it (free
    # text is held, D38). Validation already ran in the schema-retry cycle
    # (availability.request_denial), so the ids here are requestable.
    req_ids = availability.request_ids(action)
    if req_ids:
        qtype = "request"
    question, default = _normalize_plain(qtype, str(action["question"]), default)
    if ctx.depth > 0:
        # The record lands in the ROUTINE's pending dir, where a person reads it — so it has
        # to say who is asking. A child has no page of its own and its label is the only
        # thing that makes the question answerable in context.
        question = f"[child task #{ctx.sub_n} {ctx.sub_label!r}] {question}"
    extra = {"type": qtype, **({"default": default} if default else {})}
    ctx.transcript.event("question", {"qid": qid, "mode": mode, "question": question,
                                      "options": options, **extra,
                                      **({"request": req_ids} if req_ids else {})})
    if mode == "deferred":
        inbox.file_question(ctx.root_routine_dir, qid, question, options, ctx.run_ts,
                            qtype=qtype, default=default, config_patch=cpatch,
                            config_target=ctarget, config_home=chome, request=req_ids)
        ctx.asks_deferred += 1   # churn telemetry: a decision thrown over the wall
        return {"kind": "ask_user", "qid": qid, "mode": mode,
                **({"request": req_ids} if req_ids else {})}

    timeout_min = ctx.budgets.ask_timeout_min
    expires = ((datetime.now().astimezone() + timedelta(minutes=timeout_min))
               .isoformat(timespec="seconds"))
    # blocking decisions are durable records too — the Decisions page never depends on a
    # live status.json to show one, and an aborted run leaves it behind as deferred
    inbox.file_question(ctx.root_routine_dir, qid, question, options, ctx.run_ts,
                        mode="blocking", qtype=qtype, default=default, expires=expires,
                        config_patch=cpatch, config_target=ctarget, config_home=chome,
                        request=req_ids)
    ctx.write_status("waiting_user",
                     question={"qid": qid, "question": question, "options": options,
                               "asked": ctx.run_ts, "expires": expires, **extra,
                               **({"request": req_ids} if req_ids else {})})
    deadline = time.monotonic() + timeout_min * 60
    started = time.monotonic()
    answer = None
    try:
        while time.monotonic() < deadline:
            if loop._aborted():
                raise RunAborted
            answer = inbox.take_answer(ctx.routine.dir, qid, loop.consumed_dir)
            # D38: an approval is settled ONLY by a clear approve/decline, and an access
            # REQUEST only by one of the typed decisions (the web's buttons). Any
            # other reply ("Bin hier", an unrelated instruction) is user INPUT that
            # arrived while the question blocks — hold it as a normal delayed message
            # (drained at the next turn boundary, i.e. after this decision) and keep
            # waiting; the question stays open.
            if answer is not None and _held_not_settled(qtype, answer):
                src = str(answer.get("source", "web"))
                ctx.transcript.event("answer", {"qid": qid, "text": str(answer["text"]),
                                                "source": src, "held": True})
                ctx.user_replies += 1     # held or not, the user spoke (R1310)
                inbox.file_message(ctx.routine.dir, str(answer["text"]), source=src,
                                   via="web")   # the user's own reply to THIS run — live
                answer = None
                continue
            if answer:
                break
            time.sleep(poll_s)
    except RunAborted:
        # the run dies but the decision survives — as a deferred question for the next run
        inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                            qtype=qtype, default=default, config_patch=cpatch,
                            config_target=ctarget, config_home=chome, request=req_ids)
        ctx.asks_deferred += 1
        raise
    finally:
        ctx.credit_suspended(time.monotonic() - started)
        ctx.write_status("running", question=None)
    if answer and answer.get("defer"):
        # The user parked the decision from the Decisions page — continue exactly like a
        # timeout: on the stated default, the record staying open as deferred.
        inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                            qtype=qtype, default=default, config_patch=cpatch,
                            config_target=ctarget, config_home=chome, request=req_ids)
        ctx.asks_deferred += 1
        return {"kind": "ask_user", "qid": qid, "mode": mode, "deferred_by_user": True,
                **({"default": default} if default else {})}
    if answer:
        source = answer.get("source", "web")
        ctx.transcript.event("answer", {"qid": qid, "text": answer["text"], "source": source,
                                        "intermediate": bool(answer.get("intermediate")),
                                        **({"decision": answer["decision"]}
                                           if answer.get("decision") else {})})
        ctx.user_replies += 1             # a blocking answer IS the user's next message
        if answer.get("intermediate"):
            # A dialog reply, not the answer: the user needs some back-and-forth before they
            # can decide. The decision record STAYS OPEN (deferred — the run is no longer
            # parked on it): the model's re-ask supersedes it, and a finish without a re-ask
            # leaves it live for the next run instead of silently dropping it.
            inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                                qtype=qtype, default=default, config_patch=cpatch,
                                config_target=ctarget, config_home=chome, request=req_ids)
            loop.dialog_qid = qid
            return {"kind": "ask_user", "qid": qid, "mode": mode, "dialog": True,
                    "user_message": answer["text"],
                    "note": "This is a dialog reply, NOT the final answer — the user needs "
                            "more back-and-forth first. Address their message, then ask again "
                            "with ask_user (the original question, or a sharper version)."}
        inbox.resolve_question(ctx.routine.dir, qid)
        if req_ids:
            # One of the typed decisions (guaranteed by the settle rule above):
            # seed the run overlay, rebuild the live policy + transport schema, and
            # teach the outcome. Forever-decisions were persisted by the web layer at
            # click time — the engine bridges them into this run and writes no config.
            decision = str(answer["decision"])
            requests.apply_decision(loop, req_ids, decision,
                                    account=str(answer.get("account") or ""))
            return {"kind": "ask_user", "qid": qid, "mode": mode, "answered": True,
                    "request": req_ids, "decision": decision,
                    "result": requests.observation_text(req_ids, decision)}
        return {"kind": "ask_user", "qid": qid, "mode": mode, "answered": True,
                "answer": answer["text"], "source": source}
    # timeout: continue WITHOUT the decision — on the stated default when there is one.
    # The record stays open (now deferred) so a late answer still reaches a future run.
    inbox.file_question(ctx.routine.dir, qid, question, options, ctx.run_ts,
                        qtype=qtype, default=default, config_patch=cpatch,
                        config_target=ctarget, config_home=chome, request=req_ids)
    ctx.asks_deferred += 1
    return {"kind": "ask_user", "qid": qid, "mode": mode, "timed_out": True,
            "timeout_min": timeout_min, **({"default": default} if default else {})}
