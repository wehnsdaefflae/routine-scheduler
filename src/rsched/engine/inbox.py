"""Routine inbox: user messages and question answers, consumed by rename (never partial reads).

The daemon/web write files into <routine>/inbox/ atomically:
  msg-<ts>.json     {"text": ..., "ts": ...}          — injected user message
  answer-<qid>.json {"qid": ..., "text": ..., "source": ...}
The engine drains messages at every turn boundary and matches answers by qid.
Consumed files move to <run_dir>/consumed/ for the audit trail.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..paths import read_json

log = logging.getLogger("rsched.inbox")


def _consume(path: Path, consumed_dir: Path) -> None:
    consumed_dir.mkdir(parents=True, exist_ok=True)
    target = consumed_dir / path.name
    n = 1
    while target.exists():
        target = consumed_dir / f"{path.stem}.{n}{path.suffix}"
        n += 1
    path.rename(target)


def drain_messages(routine_dir: Path, consumed_dir: Path) -> list[dict]:
    """Injected user messages, oldest first; answer-* files are left alone. Each item is
    {"text": str, "attachments": [rel, ...]} — the attachments (recorded by the web layer for
    a conversation message) drive auto-attach of images/PDFs to the injected message.
    """
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(p for p in inbox.iterdir()
                       if p.is_file() and not p.name.startswith("answer-")):
        obj = read_json(path)
        if isinstance(obj, dict) and obj.get("text"):
            out.append({"text": str(obj["text"]),
                        "attachments": [str(a) for a in (obj.get("attachments") or [])]})
        else:
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                log.warning("inbox: cannot read %s (%s) — leaving it for the next drain",
                            path.name, exc)
                continue
            if text:
                out.append({"text": text, "attachments": []})
            else:
                log.warning("inbox: %s carried no text — consumed without injection", path.name)
        _consume(path, consumed_dir)
    return out


def has_pending_messages(routine_dir: Path) -> bool:
    """True if an unconsumed injected user message (a `msg-*` file, not an `answer-*`) is
    waiting. A responsive `wait` polls this so a child-wait YIELDS to the user — hands control
    back to the turn loop, which drains the message and lets the parent respond — instead of
    freezing the conversation while a subtask/subrun runs.
    """
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return False
    return any(p.is_file() and not p.name.startswith("answer-") for p in inbox.iterdir())


def take_answer(routine_dir: Path, qid: str, consumed_dir: Path) -> dict | None:
    """The answer file for a specific question, if present (consumed on read)."""
    path = routine_dir / "inbox" / f"answer-{qid}.json"
    obj = read_json(path)
    if not isinstance(obj, dict) or "text" not in obj:
        return None
    _consume(path, consumed_dir)
    return obj


def collect_deferred_answers(routine_dir: Path, consumed_dir: Path) -> list[dict]:
    """At run start: match stray answer files against questions/pending/, consume both,
    and return [{question, answer}] for the state digest.
    """
    inbox = routine_dir / "inbox"
    pending = routine_dir / "questions" / "pending"
    if not inbox.is_dir():
        return []
    pairs: list[dict] = []
    for path in sorted(inbox.glob("answer-*.json")):
        obj = read_json(path)
        if not isinstance(obj, dict) or "text" not in obj:
            log.warning("inbox: answer file %s is unreadable or has no text — skipping it",
                        path.name)
            continue
        qid = str(obj.get("qid") or path.stem.removeprefix("answer-"))
        qfile = pending / f"{qid}.json"
        q = read_json(qfile)
        if not isinstance(q, dict):
            # No matching pending question — the answer belongs to someone else (e.g. a
            # blocking ask later in this very run). Leave it alone.
            continue
        pairs.append({"qid": qid, "question": q.get("question", "?"), "answer": str(obj["text"])})
        _consume(path, consumed_dir)
        try:
            qfile.unlink()
        except OSError:
            pass
    return pairs


def file_question(routine_dir: Path, qid: str, question: str, options: list[str],
                  asked_ts: str, *, mode: str = "deferred", qtype: str = "question",
                  default: str = "", expires: str = "") -> Path:
    """The ONE decision record every kind of required user feedback funnels into —
    plain asks and util approvals, deferred and blocking alike. Blocking records carry
    `expires` (when the run continues without an answer) and are rewritten as deferred
    on timeout/abort; every surface (Decisions page, run view, Discord mirror) renders
    from this shape.
    """
    from ..paths import atomic_write_json

    path = routine_dir / "questions" / "pending" / f"{qid}.json"
    record = {"qid": qid, "question": question, "options": options,
              "asked": asked_ts, "mode": mode, "type": qtype}
    if default:
        record["default"] = default
    if expires:
        record["expires"] = expires
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
