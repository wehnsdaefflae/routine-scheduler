"""The records an ASK-BACK left open — the one shape a consumer reports them in, the rule that
supersedes them, and their re-keying when a run resumes.

The console's "ask back" is a reply to a blocking decision that decides nothing
(`interact.handle_ask` turns it into a DIALOG result). The record stays open, keyed in
`loop.dialog_qids` by what the decision is ABOUT — `(type, subject)` — and the next decision
filed for the same pair is the re-submission the ask-back asked for: it supersedes the open
record, and nothing else can. Split out of `interact.py`, which is the ask protocol alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import inbox

if TYPE_CHECKING:
    from pathlib import Path


def still_pending(ask: dict) -> dict:
    """The fields an UNSETTLED decision puts on the observation of the action that asked: it is
    pending under its record — and after an ask-back the operator's words ride along, for the
    renderer to put in front of the model (`obs_admin.dialog_reply`). ONE shape for every
    consumer, so no renderer can find the words under one key and miss them under another.
    """
    out: dict = {"pending_approval": True, "qid": ask.get("qid")}
    if ask.get("dialog"):
        out.update(dialog=True, user_message=str(ask.get("user_message") or ""))
    return out


def supersede(loop, qdir: Path, key: tuple[str, str]) -> None:
    """The decision just filed IS the re-submission an ask-back asked for when one left a
    record open on the same `(type, subject)` — so that record goes, once the new one exists.
    After the filing, never before: an ask refused before it files anything must not cost the
    open decision either.
    """
    if old := loop.dialog_qids.pop(key, None):
        inbox.resolve_question(qdir, old)


def rebuild_dialogs(loop, events: list[dict]) -> None:
    """Re-key, on a resumed leg, every record an ask-back left OPEN — so the re-submission
    supersedes it whatever leg it lands in.

    `loop.dialog_qids` is built by the leg that took the ask-back, and a leg that resumed
    after it (a restart between the ask-back and the re-submission, a conversation whose next
    reply re-submits) started it empty: the re-submission filed a SECOND record and the first
    stayed open beside it — two cards on the Decisions page for one decision. Read from the
    WHOLE transcript, not the guard scope (engine/guardscope.py): a record is open until the
    operator settles it or its re-submission supersedes it, whichever reply that falls in.
    A question event names the record's type and subject; an `intermediate` answer to it is
    the ask-back; the newest per `(type, subject)` wins, and only a record still pending on
    disk is kept — a settled or superseded one is gone from there.
    """
    asked: dict[str, tuple[str, str]] = {}
    for ev in events:
        payload = ev.get("payload") or {}
        qid = str(payload.get("qid") or "")
        if ev.get("type") == "question" and qid:
            asked[qid] = (str(payload.get("type") or "question"), str(payload.get("subject") or ""))
        elif ev.get("type") == "answer" and payload.get("intermediate") and qid in asked:
            loop.dialog_qids[asked[qid]] = qid
    pending = loop.ctx.root_routine_dir / "questions" / "pending"
    loop.dialog_qids = {key: qid for key, qid in loop.dialog_qids.items()
                        if (pending / f"{qid}.json").is_file()}
