"""The MESSAGES surface of one routine (D74): the four-folder read model plus the write
half the operator ordered. The per-folder write-surface decision (recorded in
docs/messages.md) is:

- **inbox** — the user's queue, full write access: create (a message the next run reads at
  boot), edit (rewrite `text` in place — same file, so the queue position holds), delete.
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
kept — delivery stamping matches on them.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..ids import now_iso
from ..paths import atomic_write_json
from ..readmodels import messages
from ..reports import read_reports, reports_path, retract_report
from .routines_common import _info, _state, queued_message

router = APIRouter(tags=["messages"])

#: Structured reviewer-feedback fields (api_audit) — stale after a free-text edit.
_FEEDBACK_FIELDS = ("kind", "target", "choice", "raw")


class MessageBody(BaseModel):
    text: str = ""


def _clean_text(body: MessageBody) -> str:
    text = body.text.replace("\r\n", "\n").strip()
    if not text:
        raise HTTPException(400, "empty message")
    return text


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
def create_message(request: Request, slug: str, body: MessageBody) -> dict:
    """Queue a free-text message for the routine's NEXT run (F233/D74). It lands in the
    routine's inbox (`<routine>/inbox/msg-*.json`) and is drained at the next turn boundary
    of a live run, or at the start of the next one — scheduled or manual.
    """
    from ..engine import inbox

    info = _info(request, slug)
    path = inbox.file_message(info.cfg.dir, _clean_text(body), source="web-routine-queue")
    return {"ok": True, "id": path.stem, "delivery": _delivery(request, slug)}


@router.put("/routines/{slug}/messages/{msg_id}")
def edit_message(request: Request, slug: str, msg_id: str, body: MessageBody) -> dict:
    """Rewrite a queued message's text in place (same file, so its inbox position holds);
    the original `ts` is kept and `edited` stamped. Gone from the inbox = consumed =
    immutable — the transcript now owns it.
    """
    info = _info(request, slug)
    path, prev = queued_message(info.cfg.dir / "inbox", msg_id)
    rec = {k: v for k, v in prev.items() if k not in _FEEDBACK_FIELDS}
    rec.update(text=_clean_text(body), edited=now_iso())
    atomic_write_json(path, rec)
    return {"ok": True, "id": msg_id, "delivery": _delivery(request, slug)}


@router.delete("/routines/{slug}/messages/{msg_id}")
def delete_message(request: Request, slug: str, msg_id: str) -> dict:
    info = _info(request, slug)
    path, _ = queued_message(info.cfg.dir / "inbox", msg_id)
    try:
        path.unlink()
    except FileNotFoundError:  # a run consumed it between the check and now — same outcome
        raise HTTPException(
            404, "this message is no longer queued — a run already consumed it") from None
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
