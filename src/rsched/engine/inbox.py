"""Routine inbox: user messages and question answers, consumed by rename (never partial reads).

The daemon/web write files into <routine>/inbox/ atomically:
  msg-<ts>.json     {"text": ..., "ts": ...}          — injected user message
  answer-<qid>.json {"qid": ..., "text": ..., "source": ...}
A fresh run's boot drains every message; live turn boundaries deliver only the
LIVE_MESSAGE_VIAS set (the live run view + background results — user order 2026-08-20);
answers are matched by qid.
Consumed files move to <run_dir>/consumed/ for the audit trail.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

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
#: (daemon/detached._wake). Everything else — reports, audit feedback, routine-page queued
#: messages, trigger/one-shot texts — is addressed to the routine's NEXT FRESH run and is
#: consumed only by that run's boot, never mid-flight. Mid-run injection into a running run
#: is the live run view's channel, by design.
LIVE_MESSAGE_VIAS = (*USER_MESSAGE_VIAS, "background")


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
    {"text": str, "attachments": [rel, ...], "command": bool} — attachments (recorded by
    the web layer for a conversation message) drive auto-attach of images/PDFs; `command`
    marks a slash command the engine EXECUTES instead of injecting as prose.

    A report a sibling ROUTINE addressed here (`reports.file_report` with a target) also
    carries `report` (its `R<n>` id) and `from` (the sending slug). Those two keys are what
    keep it out of the prompt's user-message channel: a report is not something the user said,
    and rendering it as though it were invites the run to answer the wrong party.
    """
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(p for p in inbox.iterdir()
                       if p.is_file() and not p.name.startswith("answer-")):
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
            out.append({"text": str(obj["text"]),
                        "attachments": [str(a) for a in (obj.get("attachments") or [])],
                        **({"command": True} if obj.get("command") else {}),
                        **({"report": str(obj["report"]),
                            "from": str(obj.get("from") or "")}
                           if obj.get("report") else {})})
        else:
            # every writer produces {"text": …} JSON (web layer, daemon managers) — a
            # readable file that is not that is corrupt; consume it so it can't loop
            log.warning("inbox: %s is not a message file — consumed without injection",
                        path.name)
        _consume(path, consumed_dir)
    return out


def has_pending_messages(routine_dir: Path, *, vias: tuple[str, ...] | None = None) -> bool:
    """True if an unconsumed injected user message (a `msg-*` file, not an `answer-*`) is
    waiting. A responsive `wait` polls this so a child-wait YIELDS to the user — hands control
    back to the turn loop, which drains the message and lets the parent respond — instead of
    freezing the conversation while a subtask/subrun runs.
    """
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return False
    for p in inbox.iterdir():
        if not p.is_file() or p.name.startswith("answer-"):
            continue
        if vias is None:
            return True
        # mirror drain_messages' filter EXACTLY: counting a message the drain would
        # skip would make a live leg's wait-yield / finish-deferral spin forever on
        # freight it is never going to consume
        obj = read_json(p)
        if (isinstance(obj, dict) and obj.get("text")
                and str(obj.get("via") or "") in vias):
            return True
    return False


def file_message(routine_dir: Path, text: str, *, source: str = "",
                 via: str = "") -> Path:
    """Queue TEXT as an injected user message — the exact msg-* shape the web layer
    writes. `via` decides WHEN it is consumed: a via in LIVE_MESSAGE_VIAS reaches the
    running run at its next turn boundary (the D38 held-reply path stamps "web" — the
    text IS the user talking to this run); no via means queued freight, consumed only
    by the next fresh run's boot (the routine-page queue).
    """
    import uuid
    from datetime import datetime

    from ..paths import atomic_write_json

    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    path = (routine_dir / "inbox"
            / f"msg-{ts.replace(':', '')}-{uuid.uuid4().hex[:8]}.json")
    atomic_write_json(path, {"text": text, "ts": ts,
                             **({"source": source} if source else {}),
                             **({"via": via} if via else {})})
    return path


def take_answer(routine_dir: Path, qid: str, consumed_dir: Path) -> dict | None:
    """The answer file for a specific question, if present (consumed on read). A defer
    marker (`{"defer": true}`, written by the Decisions page's defer-to-next-run action)
    is returned like an answer — the caller unblocks without a decision.
    """
    path = routine_dir / "inbox" / f"answer-{qid}.json"
    obj = read_json(path)
    if not isinstance(obj, dict) or ("text" not in obj and not obj.get("defer")):
        return None
    _consume(path, consumed_dir)
    return obj


def collect_deferred_answers(routine_dir: Path, consumed_dir: Path,
                             *, own_run_ts: str | None = None) -> list[dict]:
    """Match stray answer files against questions/pending/, consume both, and return
    [{question, answer}] — called at run start (boot digest) AND at every live turn
    boundary (control.drain_injections, the F195 delivery). An access-request pair also
    carries `request` (the record's entity ids) + `decision` (+ `account`), so both
    consumers can seed the run overlay (requests.apply_deferred_decisions) — an
    "allow now" decided between runs grants exactly the run that consumes it, and a
    decision landing mid-run reaches the running run's policy at once (R118).
    """
    inbox = routine_dir / "inbox"
    pending = routine_dir / "questions" / "pending"
    if not inbox.is_dir():
        return []
    pairs: list[dict] = []
    for path in sorted(inbox.glob("answer-*.json")):
        obj = read_json(path)
        if isinstance(obj, dict) and obj.get("defer") and "text" not in obj:
            # a defer marker that outlived the run it targeted — its purpose is spent
            _consume(path, consumed_dir)
            continue
        if not isinstance(obj, dict) or "text" not in obj:
            log.warning("inbox: answer file %s is unreadable or has no text — skipping it",
                        path.name)
            continue
        qid = str(obj.get("qid") or path.stem.removeprefix("answer-"))
        if own_run_ts is not None and not qid.startswith(f"q-{own_run_ts}-"):
            # F359: a RESUMED leg (the caller sets own_run_ts) takes only answers to ITS
            # OWN questions; an answer to another run's question waits for the next fresh
            # run, whose boot digest presents it with full context.
            continue
        qfile = pending / f"{qid}.json"
        q = read_json(qfile)
        if not isinstance(q, dict):
            # No matching pending question — the answer belongs to someone else (e.g. a
            # blocking ask later in this very run). Leave it alone.
            continue
        pair = {"qid": qid, "question": q.get("question", "?"), "answer": str(obj["text"])}
        if q.get("request") and obj.get("decision"):
            pair["request"] = [str(r) for r in q["request"]]
            pair["decision"] = str(obj["decision"])
            if obj.get("account"):
                pair["account"] = str(obj["account"])
        pairs.append(pair)
        _consume(path, consumed_dir)
        try:
            qfile.unlink()
        except OSError:
            pass
    return pairs


def file_question(routine_dir: Path, qid: str, question: str, options: list[str],  # noqa: PLR0913 — the ONE record shape: every field is a documented key of it, keyword-only
                  asked_ts: str, *, mode: str = "deferred", qtype: str = "question",
                  default: str = "", expires: str = "", config_patch: dict | None = None,
                  request: list[str] | None = None) -> Path:
    """The ONE decision record every kind of required user feedback funnels into —
    plain asks, util approvals and access requests, deferred and blocking alike. Blocking
    records carry `expires` (when the run continues without an answer) and are rewritten
    as deferred on timeout/abort; `config_patch` (a proposed routine.yaml change a revise
    run can't make itself) rides along for the Decisions page's one-click apply; `request`
    (grant-entity ids, entities.py) makes the record an ACCESS REQUEST — the Decisions
    page renders the four allow/deny × now/forever buttons and the answer carries a
    `decision`. Every surface (Decisions page, run view, Discord mirror) renders from
    this shape.
    """
    from ..paths import atomic_write_json

    path = routine_dir / "questions" / "pending" / f"{qid}.json"
    record: dict = {"qid": qid, "question": question, "options": options,
                    "asked": asked_ts, "mode": mode, "type": qtype}
    if default:
        record["default"] = default
    if expires:
        record["expires"] = expires
    if config_patch:
        record["config_patch"] = config_patch
    if request:
        record["request"] = list(request)
    atomic_write_json(path, record)
    return path


def resolve_question(routine_dir: Path, qid: str) -> None:
    """Drop the pending record — the decision was made (or superseded by a re-ask)."""
    try:
        (routine_dir / "questions" / "pending" / f"{qid}.json").unlink(missing_ok=True)
    except OSError:
        pass


def open_questions(routine_dir: Path) -> list[dict]:
    """Pending questions. A question whose answer already waits in the inbox (answered on
    the Decisions page, not yet drained by a run) is flagged `answered: True` so every
    surface can show it as answered-and-queued instead of still-open.
    """
    pending = routine_dir / "questions" / "pending"
    if not pending.is_dir():
        return []
    inbox = routine_dir / "inbox"
    out = []
    for path in sorted(pending.glob("*.json")):
        obj = read_json(path)
        if isinstance(obj, dict) and obj.get("question"):
            qid = str(obj.get("qid") or path.stem)
            if (inbox / f"answer-{qid}.json").exists():
                obj = {**obj, "answered": True}
            out.append(obj)
    return out
