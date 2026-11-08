"""The MESSAGES surface of one routine (D74): the four-folder read model plus the write
half the operator ordered. The per-folder write-surface decision (recorded in
docs/messages.md) is:

- **inbox** — the user's queue, full write access: create (a message the next run reads at
  boot, with FILE ATTACHMENTS since D275 — multipart, the same saver and the same
  `[attached files]` block a conversation message uses), edit (rewrite `text` in place —
  same file, so the queue position holds), delete.
  This covers EVERY `msg-*` file waiting there, engine-filed deliveries included: the inbox
  file is the delivery vehicle, and what a routine's next run gets told is the user's call
  right up until a run drains it. `answer-*` files are unreachable (the id pattern), they
  belong to the Decisions page.
- **outbox** — rows are derived from the append-only report ledger, and a report is the
  RUN's utterance: the user neither authors nor rewrites one. The one write is RETRACTION
  of a not-yet-consumed addressed report (`reports.retract_report`); a correction is a new
  message written to the target's inbox in the user's own voice.
- **read / received** — consumed history; no write endpoint exists at all.

Editing drops any structured reviewer-feedback fields (`kind`/`target`/`choice`/`raw`,
written by `api_audit`): they exist so THAT channel can re-format its tagged text, and after
a free-text rewrite they no longer describe the message. Engine keys (`report`/`from`) are
kept — delivery stamping matches on them — and so is `attachments`: the files the user
uploaded are not a description of the words that were replaced, and the engine reads the rels
off the record to auto-attach them (D275). Rewording a message must not strip its files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from ..ids import now_iso
from ..readmodels import messages
from ..reports import read_reports, reports_path, retract_report
from .conversations_common import _save_attachments
from .routines_common import _info, _state, queued_message, rewrite_queued_message

router = APIRouter(tags=["messages"])

#: Structured reviewer-feedback fields (api_audit) — stale after a free-text edit.
_FEEDBACK_FIELDS = ("kind", "target", "choice", "raw")


class MessageBody(BaseModel):
    text: str = ""


def _clean_text(body: MessageBody, *, allow_empty: bool = False) -> str:
    """The message text as it is stored: CRLF normalized (multipart encodes newlines that
    way), trimmed, and REFUSED when empty — with one exception, `allow_empty`, for a create
    that carries attachments instead of words (D275). The caller then supplies the body.
    """
    text = body.text.replace("\r\n", "\n").strip()
    if not text and not allow_empty:
        raise HTTPException(400, "empty message")
    return text


# The two writes a queued inbox message allows, shared with the conversation surface (D139),
# which edits and withdraws its own queue the same way — `via` narrows which files it may
# address (`routines_common.queued_message`).

def rewrite_queued(inbox: Path, msg_id: str, body: MessageBody, *, via: str = "") -> None:
    """Rewrite a queued message's text in place — the SAME file, so its position in the queue
    holds and its `ts` stands; `edited` is stamped so a run can tell.
    """
    text = _clean_text(body)
    rewrite_queued_message(inbox, msg_id, lambda prev: {
        **{k: v for k, v in prev.items() if k not in _FEEDBACK_FIELDS},
        "text": text, "edited": now_iso()}, via=via)


def withdraw_queued(inbox: Path, msg_id: str, *, via: str = "") -> None:
    """Withdraw a queued message: the delivery is removed and no run ever sees it."""
    path, _ = queued_message(inbox, msg_id, via=via)
    try:
        path.unlink()
    except FileNotFoundError:  # a run consumed it between the check and now — same outcome
        raise HTTPException(
            404, "this message is no longer queued — a run already consumed it") from None


def _delivery(request: Request, slug: str) -> str:
    """Whether the message reaches a RUNNING run's next turn boundary or waits for the
    next run — the toast wording the console shows on create/edit.
    """
    return "mid-run" if _state(request).runner.is_active(slug) else "next-run"


@router.get("/routines/{slug}/messages")
def list_messages(request: Request, slug: str) -> dict:
    """The four-folder MESSAGES view of one routine: inbox (waiting, user-writable) ·
    outbox (addressed reports the recipient has not consumed, retractable) · read
    (consumed by this routine) · received (consumed by the recipient) — the last two
    read-only (docs/messages.md).
    """
    info = _info(request, slug)
    return messages.build(info.cfg.dir, _state(request).server.routines_home)


@router.post("/routines/{slug}/messages")
async def create_message(request: Request, slug: str, text: Annotated[str, Form()] = "",
                         files: Annotated[list[UploadFile] | None, File()] = None) -> dict:
    """Queue a message — with optional file ATTACHMENTS — for the routine's NEXT run
    (F233/D74, attachments D275). It lands in the routine's inbox
    (`<routine>/inbox/msg-*.json`) and is drained at the next turn boundary of a live run,
    or at the start of the next one — scheduled or manual.

    MULTIPART, exactly like a conversation message and the run page's injection: uploads go
    through the one saver (`conversations_common._save_attachments` — its count and size
    caps, its 413s) into the ROUTINE dir's `attachments/`, which is the run's own working
    dir, so each recorded rel resolves for `read_file` / `view_image`. The rels ride the
    record under `attachments`, which is what the engine's drain already reads off any
    `msg-*` file to auto-attach images and PDFs (`engine/inbox.py`), and the message text
    carries the human-readable `[attached files]` block.

    AN ATTACHMENT WITH NO WORDS IS A MESSAGE (D275): dragging a screenshot in and saying
    nothing is the ordinary case, so `text` defaults to empty and the attachment block
    becomes the body. It must not be left literally empty — the drain injects only a record
    with a truthy `text` and logs anything else as "not a message file", so an empty-bodied
    record would be consumed with the run never seeing the file. Nothing at all — no words
    and no file — is still the 400.
    """
    from ..conversations import attachment_note
    from ..engine import inbox

    info = _info(request, slug)
    rels = await _save_attachments(info.cfg.dir, files or [])
    body = _clean_text(MessageBody(text=text), allow_empty=bool(rels))
    # lstrip: the block opens with a blank line to separate it from the words above it, and
    # with no words it IS the message — a body starting with two newlines is not one.
    path = inbox.file_message(info.cfg.dir, (body + attachment_note(rels)).lstrip("\n"),
                              source="web-routine-queue",
                              extra={"attachments": rels} if rels else None)
    return {"ok": True, "id": path.stem, "delivery": _delivery(request, slug),
            "attachments": rels}


@router.put("/routines/{slug}/messages/{msg_id}")
def edit_message(request: Request, slug: str, msg_id: str, body: MessageBody) -> dict:
    """Rewrite a queued message's text in place (same file, so its inbox position holds);
    the original `ts` is kept and `edited` stamped. Gone from the inbox = consumed =
    immutable — the transcript now owns it.
    """
    rewrite_queued(_info(request, slug).cfg.dir / "inbox", msg_id, body)
    return {"ok": True, "id": msg_id, "delivery": _delivery(request, slug)}


@router.delete("/routines/{slug}/messages/{msg_id}")
def delete_message(request: Request, slug: str, msg_id: str) -> dict:
    withdraw_queued(_info(request, slug).cfg.dir / "inbox", msg_id)
    return {"ok": True, "id": msg_id}


@router.delete("/routines/{slug}/outbox/{report_id}")
def retract_outbox_report(request: Request, slug: str, report_id: str) -> dict:
    """Retract an addressed report THIS routine filed, while its delivery still waits in
    the target's inbox. The ledger row stays (append-only; a `retracted` event is added)
    and the item reads `dropped` from then on.
    """
    _info(request, slug)                       # 404 before touching the ledger
    home = _state(request).server.routines_home
    row = next((r for r in read_reports(reports_path(home))
                if str(r.get("id")) == report_id), None)
    if row is None or row.get("routine") != slug:
        raise HTTPException(404, f"{slug!r} has no report {report_id!r}")
    try:
        retract_report(home, report_id)        # re-validates under the ledger lock
    except (LookupError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from None
    return {"ok": True, "id": report_id}
