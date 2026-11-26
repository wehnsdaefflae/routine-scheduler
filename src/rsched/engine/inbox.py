"""Routine inbox, MESSAGE half: freight for a run, consumed by rename (never partial reads).

Two filename shapes live in <routine>/inbox/, written atomically and never by hand:

  msg-<stem>.json   {"text", "ts", "via", …}   — freight for a run, written by `file_message`
  answer-<qid>.json {"qid", "text", "source"}  — a question's answer, matched by qid

This module owns the FIRST shape; `file_message` below is its one writer, so every channel
shares one `ts` spelling and one uniqueness rule. The second shape's lifecycle lives in
`engine/inbox_questions` and its one WRITER in `rsched/answerfile` (D143) — a separate seam,
because an answer carries no `via`, has no delivery policy and must never buy a run.
`tests/test_policy_inbox_writer.py` allow-lists exactly those two writers. Every public question
name is re-exported at the foot of this module, so `inbox.file_question(...)` resolves.

A fresh run's boot drains every message; live turn boundaries and a resumed leg's boot deliver
only the LIVE_MESSAGE_VIAS set (the user talking to this run, plus the results of work this run
started). Every scanner selects `msg-*.json` — never "any file that is not answer-*", which
would also match `atomic_write`'s in-flight `.msg-….json.XXXX.tmp` and consume it.

Consumed files move to <run_dir>/consumed/ for the audit trail. The drain and an in-place
`rewrite_message` hold one lock (`inbox/.lock`), so a drain cannot land between a rewrite's
check and its write and let the write re-create a consumed message.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from ..ids import now_iso
from ..paths import atomic_write_json, file_lock, read_json

# The delivery channels and their policy live in `channels.py`; re-exported so every
# `inbox.VIAS` caller resolves.
from .channels import (
    LIVE_MESSAGE_VIAS,
    MACHINE_VIAS,
    USER_MESSAGE_VIAS,
    VIAS,
    user_authored,
)

log = logging.getLogger("rsched.inbox")

#: The lock the drain and `rewrite_message` share. A dotfile, so no `msg-*` scan sees it.
LOCK_NAME = ".lock"

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

    A HAND-BACK — a branch's (`rsched/branches.hand_back`) or a detached task's
    (`daemon/detached_delivery`) — also carries `handback` ({slug, name, paths}): the child's
    identity and the files that landed in this routine's `artifacts/`. It rides the injection
    event so the chat can render the delivery as a CARD with real file links instead of
    re-reading the paths out of the message's prose.

    A message the operator RE-SENT by flagging a conversation reply as a refusal
    (web/api_refusal_flag) carries `refusal_flag` — what the engine records, as the `refusal`
    event of the `operator` seam, when it delivers the message (control.inject_user_message).
    """
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return []
    with file_lock(inbox / LOCK_NAME):
        return _drain_locked(inbox, consumed_dir, vias)


def _drain_locked(inbox: Path, consumed_dir: Path, vias: tuple[str, ...] | None) -> list[dict]:
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
                        **({"handback": dict(obj["handback"])}
                           if isinstance(obj.get("handback"), dict) else {}),
                        **({"refusal_flag": dict(obj["refusal_flag"])}
                           if isinstance(obj.get("refusal_flag"), dict) else {}),
                        **({"report": str(obj["report"]),
                            "from": str(obj.get("from") or ""),
                            **({"closes": True} if obj.get("closes") is True else {}),
                            **({"no_reply": True} if obj.get("no_reply") is True else {})}
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

    Every "is anything waiting" caller asks here; they differ only on three axes:

    - `vias` — the delivery channels that count. None counts every channel; a live leg passes
      LIVE_MESSAGE_VIAS, the reap USER_MESSAGE_VIAS.
    - `include_closures` — whether a report that only CLOSES a thread counts. The run gate
      says no: a closure is the end of work, and firing runs for closures chains.
    - `on_unparseable` — what an unreadable file counts AS. The run gate is fail-OPEN (a
      spurious run costs a run, a missed one is silence); every live-run predicate is
      fail-CLOSED, mirroring `drain_messages`, because counting freight the drain
      never consumes makes a wait-yield or finish-deferral spin forever.

    `answer-*` files are never counted here: they have their own pass
    (`collect_deferred_answers`), and an answer must not buy a run.
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


def queued_freight(routine_dir: Path, *, exclude_vias: tuple[str, ...]) -> list[dict]:
    """What is WAITING in this routine's inbox that the current leg may not consume — one
    line per file, consuming nothing.

    A leg that is not a fresh boot drains only `LIVE_MESSAGE_VIAS` (D92/D93): other freight is
    addressed to the routine's NEXT FRESH run. This is the read half of that rule — the leg is
    TOLD what waits, never handed it, so it does not treat an answered question as still open.
    The caller renders it as a digest section (boot composes AFTER the drain, so a fresh run
    sees nothing here). `text` is the first line only: enough to recognise, not to act on —
    the run is told to go and look.
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
        # `next(…, "")`, not `[0]`: a whitespace-only text has no first line
        out.append({"file": path.name, "via": via, "ts": str(obj.get("ts") or ""),
                    "text": next(iter(str(obj["text"]).strip().splitlines()), ""),
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
    conversation message's `attachments`/`command`/`refusal_flag`, a report delivery's
    `report`/`from`/`closes`/`no_reply`, the audit editor's feedback fields.

    `name` is the message's STEM without the `msg-` prefix, for channels whose filename is an
    idempotency KEY: a report delivery is `msg-rep-<id>` (the sender can retract it while
    queued), a background result `msg-bg-<task>` (a re-delivery replaces the pending one). The
    default is the unique `msg-<ts>-<rand>` form, because `now_iso()` is second-resolution.
    """
    import uuid

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


def rewrite_message(path: Path, edit: Callable[[dict], dict]) -> dict:
    """Rewrite one still-QUEUED message in place — the same file, so its queue position
    holds — and return what was written. `edit` maps the record as it is NOW to its new form.

    Raises LookupError once a drain has taken the file. The check and the write sit under the
    drain's own lock (`LOCK_NAME`), because `atomic_write` CREATES a missing path and would
    resurrect a just-consumed message. Withdrawing needs no lock: `unlink` fails on its own.
    """
    with file_lock(path.parent / LOCK_NAME):
        prev = read_json(path)
        if isinstance(prev, dict):
            rec = edit(prev)
            atomic_write_json(path, rec)
            return rec
    raise LookupError(f"{path.name} is no longer queued")


# ---- the question half ------------------------------------------------------------------
# Questions live in `engine/inbox_questions` (D143), re-exported here because callers reach
# them as `inbox.<name>`. The import sits at the FOOT, after `_consume` is defined, because
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
    "drain_messages",
    "file_message",
    "file_question",
    "has_pending_messages",
    "open_questions",
    "queued_freight",
    "resolve_question",
    "revise_answer",
    "rewrite_message",
    "take_answer",
    "user_authored",
]
