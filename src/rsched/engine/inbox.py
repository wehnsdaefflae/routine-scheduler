"""Routine inbox: user messages and question answers, consumed by rename (never partial reads).

The daemon/web write files into <routine>/inbox/ atomically:
  msg-<ts>.json     {"text": ..., "ts": ...}          — injected user message
  answer-<qid>.json {"qid": ..., "text": ..., "source": ...}
The engine drains messages at every turn boundary and matches answers by qid.
Consumed files move to <run_dir>/consumed/ for the audit trail.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..paths import read_json


def _consume(path: Path, consumed_dir: Path) -> None:
    consumed_dir.mkdir(parents=True, exist_ok=True)
    target = consumed_dir / path.name
    n = 1
    while target.exists():
        target = consumed_dir / f"{path.stem}.{n}{path.suffix}"
        n += 1
    path.rename(target)


def drain_messages(routine_dir: Path, consumed_dir: Path) -> list[str]:
    """Injected user messages, oldest first; answer-* files are left alone."""
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return []
    out: list[str] = []
    for path in sorted(p for p in inbox.iterdir() if p.is_file() and not p.name.startswith("answer-")):
        obj = read_json(path)
        if isinstance(obj, dict) and obj.get("text"):
            out.append(str(obj["text"]))
        else:
            try:
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    out.append(text)
            except OSError:
                continue
        _consume(path, consumed_dir)
    return out


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
    and return [{question, answer}] for the state digest."""
    inbox = routine_dir / "inbox"
    pending = routine_dir / "questions" / "pending"
    if not inbox.is_dir():
        return []
    pairs: list[dict] = []
    for path in sorted(inbox.glob("answer-*.json")):
        obj = read_json(path)
        if not isinstance(obj, dict) or "text" not in obj:
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


def file_deferred_question(routine_dir: Path, qid: str, question: str, options: list[str],
                           asked_ts: str) -> Path:
    from ..paths import atomic_write_json

    path = routine_dir / "questions" / "pending" / f"{qid}.json"
    atomic_write_json(path, {"qid": qid, "question": question, "options": options,
                             "asked": asked_ts, "mode": "deferred"})
    return path


def open_questions(routine_dir: Path) -> list[dict]:
    pending = routine_dir / "questions" / "pending"
    if not pending.is_dir():
        return []
    out = []
    for path in sorted(pending.glob("*.json")):
        obj = read_json(path)
        if isinstance(obj, dict) and obj.get("question"):
            out.append(obj)
    return out
