"""Routine inbox, MESSAGE half: freight for a run, consumed by rename (never partial reads).

Two filename shapes live in <routine>/inbox/, written atomically and never by hand:

  msg-<stem>.json   {"text", "ts", "via", …}   — freight for a run, written by `file_message`
  answer-<qid>.json {"qid", "text", "source"}  — a question's answer, matched by qid

This module owns the FIRST shape. The second lives in `engine/inbox_questions` (D143,
2026-09-24), split out when this file reached 530 lines against the repo's ~350 budget — two
record shapes sharing a directory and a consume-by-rename discipline, and nothing else. Every
public question name is re-exported at the foot of this module, so `inbox.file_question(...)`
still resolves and no caller moved.

`file_message` is the ONE writer of the `msg-*` shape. Every channel goes through it — the
web layer, a sibling routine's report delivery, a daemon manager's trigger or one-shot text —
because the alternative is what the codebase had: seven `atomic_write_json(... / "inbox" /
f"msg-…")` call sites, each choosing its own `ts` spelling and its own uniqueness rule, and
one of them a character-for-character copy of the line below. A channel that needs a
DETERMINISTIC filename (so it can read its own pending delivery back, or replace it instead
of queuing a second) passes `name=`; extra keys ride in `extra=`.

A fresh run's boot drains every message; live turn boundaries deliver only the
LIVE_MESSAGE_VIAS set (the live run view + background results — user order 2026-08-20).
Every scanner selects `msg-*.json` — the stem the one writer produces — never "any file that
is not answer-*", which also matched `atomic_write`'s in-flight `.msg-….json.XXXX.tmp`: on a
fresh boot that temp file reached the unparseable branch below, was logged "not a message
file" and RENAMED into consumed/, so the writer's `replace()` failed and the message was lost.

Consumed files move to <run_dir>/consumed/ for the audit trail.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..ids import now_iso
from ..paths import read_json

log = logging.getLogger("rsched.inbox")

#: The injection channels that mean "the user is talking to THIS run": the conversation
#: composer and the run page. The daemon's post-finish sweep re-opens a finished run only
#: for these (R108/F268), and a RESUMED leg's boot drains ONLY these (F359, user order
#: 2026-08-17): everything else waiting in an inbox — audit feedback, report deliveries,
#: routine-page queued messages, trigger/background/one-shot texts, answers to other runs'
#: questions — is addressed to the routine's NEXT fresh run, and a follow-up leg draining
#: it wholesale silently ate decision answers meant for that night's run (D92/D93).
USER_MESSAGE_VIAS = ("conversation", "web", "web-converse")

#: What a LIVE run may consume at a mid-run turn boundary, and what a RESUMED leg's boot
#: drains (user order 2026-08-20, generalizing F359): the user talking to THIS run
#: (USER_MESSAGE_VIAS) plus a detached background task's result delivery — the task was
#: started by this very conversation, so its result IS this conversation's freight, and the
#: daemon's delivery contract counts on the live owner draining it at the next boundary
#: (daemon/detached_delivery.wake). Everything else — reports, audit feedback, routine-page queued
#: messages, trigger/one-shot texts — is addressed to the routine's NEXT FRESH run and is
#: consumed only by that run's boot, never mid-flight. Mid-run injection into a running run
#: is the live run view's channel, by design.
LIVE_MESSAGE_VIAS = (*USER_MESSAGE_VIAS, "background", "branch")

#: The CLOSED set of delivery channels. `via` is not a label: it is the switch that decides
#: when a message is consumed (the two tuples above), whether the reap counts it as a user
#: talking to a finished run (daemon/runner_state), and whether the conversation surface
#: offers it as the user's own editable text (web/api_conversations). It was chosen freehand
#: at every writer, which is how a branch hand-back came to be filed on the USER channel and
#: was read by the parent as the operator speaking. A writer now names a channel this set
#: knows, or `file_message` refuses the write.
#:
#: "" is a channel too — the routine page's queued message, which is the operator typing with
#: no surface of its own to name.
VIAS = frozenset({
    "",               # web/api_messages — the routine page's queued message
    "conversation",   # web/api_conversations — the composer
    "web",            # web/api_run_control, engine/interact — the live run view
    "web-converse",   # web/api_run_control — the run view's converse box
    "web-audit",      # web/api_audit — self-audit feedback and decisions
    "report",         # reports.file_report — a sibling routine's addressed report
    "background",     # daemon/detached_delivery — a detached task's result
    "branch",         # branches.hand_back — a forked conversation's result
    "trigger",        # daemon/triggers — a configured event's text
    "schedule_once",  # daemon/schedule_once — a one-shot fire's provenance
    "pending",        # pending.notify_proposer — the outcome of a queued proposal
})

#: The channels whose freight a MACHINE authored. `ctx.user_replies` — read by the
#: create_routine confirm gate (has the user spoken since the draft?) and by the `user-spoke`
#: assist predicate — counts the user talking, so a report, a background result or a branch
#: hand-back must not advance it. Everything else on VIAS is a person at a console.
MACHINE_VIAS = frozenset({"report", "background", "branch", "trigger", "schedule_once",
                          "pending"})


def user_authored(via: str) -> bool:
    """Is freight on this channel the USER talking? The one answer, for every consumer."""
    return via not in MACHINE_VIAS


def _consume(path: Path, consumed_dir: Path) -> None:
    consumed_dir.mkdir(parents=True, exist_ok=True)
    target = consumed_dir / path.name
    n = 1
    while target.exists():
        target = consumed_dir / f"{path.stem}.{n}{path.suffix}"
        n += 1
    path.rename(target)


def drain_messages(routine_dir: Path, consumed_dir: Path,
                   *, vias: tuple[str, ...] | None = None) -> list[dict]:
    """Injected messages, oldest first; answer-* files are left alone. With `vias` (a live
    turn boundary or a RESUMED leg's boot — LIVE_MESSAGE_VIAS) only messages whose `via` is
    in that set are consumed — everything else stays queued, untouched, for the next fresh
    run's boot (`vias=None`), which drains all. Each item is
    {"text": str, "via": str, "attachments": [rel, ...], "command": bool} — `via` is the
    channel it arrived on, which is what tells the consumer whether a PERSON wrote it
    (`user_authored`); attachments (recorded by the web layer for a conversation message)
    drive auto-attach of images/PDFs; `command` marks a slash command the engine EXECUTES
    instead of injecting as prose.

    A report a sibling ROUTINE addressed here (`reports.file_report` with a target) also
    carries `report` (its `R<n>` id) and `from` (the sending slug). Those two keys are what
    keep it out of the prompt's user-message channel: a report is not something the user said,
    and rendering it as though it were invites the run to answer the wrong party.
    """
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(inbox.glob("msg-*.json")):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:   # transiently unreadable (fs blip) — never consume blind
            log.warning("inbox: cannot read %s (%s) — leaving it for the next drain",
                        path.name, exc)
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            obj = None
        if vias is not None and not (isinstance(obj, dict)
                                     and str(obj.get("via") or "") in vias):
            continue   # not live-deliverable — stays queued for the next fresh run's boot
        if isinstance(obj, dict) and obj.get("text"):
            out.append({"text": str(obj["text"]), "via": str(obj.get("via") or ""),
                        "attachments": [str(a) for a in (obj.get("attachments") or [])],
                        **({"command": True} if obj.get("command") else {}),
                        **({"report": str(obj["report"]),
                            "from": str(obj.get("from") or ""),
                            **({"closes": True} if obj.get("closes") is True else {})}
                           if obj.get("report") else {})})
        else:
            # every writer produces {"text": …} JSON (web layer, daemon managers) — a
            # readable file that is not that is corrupt; consume it so it can't loop
            log.warning("inbox: %s is not a message file — consumed without injection",
                        path.name)
        _consume(path, consumed_dir)
    return out


def _waiting(routine_dir: Path, *, vias: tuple[str, ...] | None,
             include_closures: bool, on_unparseable: bool):
    """The unconsumed messages a caller counts as "something is waiting", one at a time.

    Five callers asked this question with five hand-rolled loops, and the differences between
    them were real but undocumented — so the next message class to need an exemption would be
    taught to one loop and not the other four. The differences are these three, and nothing
    else:

    - `vias` — the delivery channels that count. None counts every channel; a live leg passes
      LIVE_MESSAGE_VIAS, the reap USER_MESSAGE_VIAS.
    - `include_closures` — whether a report that only CLOSES a thread counts. The report
      trigger says no: a closure is the end of work, not work, and firing a run for one is how
      a fleet of routines answering each other chains into runs nobody asked for.
    - `on_unparseable` — what an unreadable or unrecognised file counts AS, and the axis that
      cannot be collapsed. The trigger manager is fail-OPEN (anything it cannot read WAKES a
      run: the cost of a spurious run is a run, the cost of a missed one is silence); every
      live-run predicate is fail-CLOSED, mirroring `drain_messages` exactly, because counting
      freight the drain will never consume makes a wait-yield or a finish-deferral spin
      forever on it.

    `answer-*` files are never counted here by anyone. They have their own matching pass
    (`collect_deferred_answers`), and an answer is the one thing that must not buy a run.
    """
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return
    for path in sorted(inbox.glob("msg-*.json")):
        obj = read_json(path)
        if not isinstance(obj, dict) or not obj.get("text"):
            if on_unparseable:
                yield path
            continue
        if vias is not None and str(obj.get("via") or "") not in vias:
            continue
        if not include_closures and obj.get("closes") is True:
            continue
        yield path


def has_pending_messages(routine_dir: Path, *, vias: tuple[str, ...] | None = None,
                         include_closures: bool = True,
                         on_unparseable: bool = False) -> bool:
    """Is anything waiting in this routine's inbox? The ONE predicate — see `_waiting` for
    what each flag admits.

    A responsive `wait` polls this so a child-wait YIELDS to the user — hands control back to
    the turn loop, which drains the message and lets the parent respond — instead of freezing
    the conversation while a subtask/subrun runs.
    """
    return next(_waiting(routine_dir, vias=vias, include_closures=include_closures,
                         on_unparseable=on_unparseable), None) is not None


def count_pending(routine_dir: Path, *, vias: tuple[str, ...] | None = None,
                  include_closures: bool = True, on_unparseable: bool = False) -> int:
    """How many, for the surfaces that show a number (the routine page's report-trigger row).
    The same filter as `has_pending_messages`, so a page can never disagree with the engine
    about whether there is freight.
    """
    return sum(1 for _ in _waiting(routine_dir, vias=vias,
                                   include_closures=include_closures,
                                   on_unparseable=on_unparseable))


def queued_freight(routine_dir: Path, *, exclude_vias: tuple[str, ...]) -> list[dict]:
    """What is WAITING in this routine's inbox that the current leg may not consume — one
    line per file, consuming nothing.

    A leg that is not a fresh boot drains only `LIVE_MESSAGE_VIAS` (D92/D93: an audit
    decision answer, a sibling's report delivery, a routine-page queued message is addressed
    to the routine's NEXT FRESH run, and a follow-up leg draining it wholesale silently ate
    answers meant for that night's run). That exclusion is right and stays. What was missing
    is that the leg was not TOLD: on 2026-09-21 a decision was answered at 16:01, the run
    finished at 16:02, and three continuation legs later the operator asked what that decision
    meant — the run explained it as still OPEN, with a recommendation, because nothing in its
    context said an answer was sitting in `inbox/` it was not allowed to read. "did you lose my
    answer again?!"

    So this is the read half of the same rule: the freight is named, never delivered. The
    caller renders it as a digest section (boot composes AFTER the drain, so a fresh run —
    which drains everything — sees nothing here and a resumed leg sees exactly what it may
    not take). `text` is the first line only: enough to recognise, not enough to act on
    without reading the file, which is the point — the run is told to go and look.
    """
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(inbox.glob("msg-*.json")):
        obj = read_json(path)
        if not isinstance(obj, dict) or not obj.get("text"):
            continue
        via = str(obj.get("via") or "")
        if via in exclude_vias:
            continue
        out.append({"file": path.name, "via": via, "ts": str(obj.get("ts") or ""),
                    "text": str(obj["text"]).strip().splitlines()[0],
                    **({"report": str(obj["report"]), "from": str(obj.get("from") or "")}
                       if obj.get("report") else {})})
    return out


def file_message(routine_dir: Path, text: str, *, source: str = "",
                 via: str = "", extra: dict | None = None, name: str = "") -> Path:
    """Queue TEXT as freight for a run — the ONE writer of the msg-* shape, for the engine,
    the daemon and the web layer alike. `via` decides WHEN it is consumed: a via in
    LIVE_MESSAGE_VIAS reaches the running run at its next turn boundary (the D38 held-reply
    path stamps "web" — the text IS the user talking to this run); anything else is queued
    freight, consumed only by the next fresh run's boot. It must name a channel `VIAS`
    knows — the value is the delivery-policy switch, not a label, and this refuses an
    unknown one rather than filing freight on a policy nobody chose.

    `extra` is merged into the record for the keys ONE channel adds on top of that shape — a
    conversation message's `attachments` rels and `command` flag, a report delivery's
    `report`/`from`/`closes`, the audit editor's structured feedback fields — so such a
    caller still files through this writer instead of hand-rolling the filename beside it,
    which is how the unique-suffix rule and the `ts` spelling start differing per endpoint.

    `name` is the message's STEM without the `msg-` prefix, for the channels whose filename
    is a KEY rather than a timestamp: a report delivery is `msg-rep-<id>` so the sender can
    see whether its own delivery is still queued and retract it, a background result is
    `msg-bg-<task>` so a re-delivery replaces the pending one instead of queuing a second.
    Those are idempotency keys and must stay deterministic; the default is the unique
    `msg-<ts>-<rand>` form, because two submissions in one second must not clobber each
    other (`now_iso()` is second-resolution).
    """
    import uuid

    from ..paths import atomic_write_json

    if via not in VIAS:
        raise ValueError(f"unknown inbox via {via!r} — delivery policy is decided by this "
                         f"value, so a writer names one of {sorted(VIAS)} (see inbox.VIAS)")
    ts = now_iso()
    stem = name or f"{ts.replace(':', '')}-{uuid.uuid4().hex[:8]}"
    path = routine_dir / "inbox" / f"msg-{stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {"text": text, "ts": ts,
                             **({"source": source} if source else {}),
                             **({"via": via} if via else {}),
                             **(extra or {})})
    return path


# ---- the question half ------------------------------------------------------------------
# Questions live in `engine/inbox_questions` (D143, 2026-09-24): same directory, same
# consume-by-rename discipline, a different record shape. They are re-exported HERE because
# every caller reaches them as `inbox.<name>` — the split is an internal seam and moved
# nobody. The import sits at the FOOT of the module, after `_consume` is defined, because
# inbox_questions imports it from here.
from .inbox_questions import (  # noqa: E402 — re-export at the foot; see the comment above
    answered_questions,
    collect_deferred_answers,
    file_question,
    open_questions,
    resolve_question,
    revise_answer,
    take_answer,
)

__all__ = [
    "LIVE_MESSAGE_VIAS",
    "MACHINE_VIAS",
    "USER_MESSAGE_VIAS",
    "VIAS",
    "answered_questions",
    "collect_deferred_answers",
    "count_pending",
    "drain_messages",
    "file_message",
    "file_question",
    "has_pending_messages",
    "open_questions",
    "queued_freight",
    "resolve_question",
    "revise_answer",
    "take_answer",
    "user_authored",
]
