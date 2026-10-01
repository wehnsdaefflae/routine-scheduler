"""The operator's REFUSAL FLAG on a conversation reply (⚑) — operator decision 2026-10-01.

A reply the operator reads as a refusal is REDONE on the conversation's uncensored model,
which then CARRIES the conversation: the reply and everything after it are archived (the
D69 rewind's own cut and archive — `rewind.rewind_transcript(before_reply=…)`), the message
that produced it is re-sent verbatim on the composer's own channel, and the uncensored model
becomes the conversation's MAIN model from then on.

This is the one path on which the uncensored model answers and acts. The automatic path
(engine/refusal.py) keeps the honeypot rule — its output is evidence, never an answer —
because nobody decided anything there; here a person did, per reply, behind a warning gate
that says what is discarded and what is NOT undone (files written, messages sent).

Two verbs on one resource. GET says what a flag WOULD do — the message it re-sends, the
model that takes over or that one must be picked — so the gate quotes the server's own
answer instead of a second copy of the leg logic in the browser. POST does it. Both run
`_plan`, and the POST validates everything before its first write, so a refusal leaves the
conversation untouched. The engine stays the transcript's only writer: the flag rides the
re-sent message as inbox metadata, and the engine records it as the `refusal` event of the
`operator` seam when it delivers the message (refusal.record_operator_flag).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from ..config import RoutineConfig, load_routine
from ..conversations import attachment_rels
from ..engine import inbox
from ..engine.rewind import reply_opening, rewind_transcript
from ..engine.transcript import read_events
from ..paths import atomic_write_yaml, read_json, read_yaml
from ..registry import TERMINAL_STATES
from .api_runs import _run_dir, run_state
from .config_fields import validate_models
from .routines_common import merge_control

router = APIRouter(tags=["refusal-flag"])

_REPLY_CAP = 500   # chars of the flagged reply its refusal record keeps


class RefusalFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn: int    # the flagged reply's turn, as the chat's per-reply controls name it …
    ts: str      # … and its finish's own ts: a reply that ran no turn shares the number
    model: str = ""   # the catalog model that takes over — only when none is configured


@dataclass(frozen=True)
class _Plan:
    run_dir: Path
    cfg: RoutineConfig
    text: str               # the message re-sent, verbatim
    attachments: list[str]  # its attachment rels, so the engine shows the files again
    refused_by: str         # the model that wrote the flagged reply
    reply: str              # what that reply said, capped
    takeover: str           # the model that carries on ("" = none configured, none picked)
    roles: dict[str, str]   # what `models:` gains: the takeover as uncensored AND main


def _served_name(server, served: str) -> str:
    """A turn's `<endpoint>/<model>` attribution as the catalog name the operator knows it
    by, when exactly one catalog entry serves it — else the attribution as it stands.
    """
    names = [n for n, m in server.models.items() if f"{m.endpoint}/{m.model}" == served]
    return names[0] if len(names) == 1 else served


def _message(conv_dir: Path, opening: dict) -> tuple[str, list[str]]:
    """The text the flag re-sends and its attachment rels. A first reply's message is the
    conversation's first, instruction.md; anything the user sent before its first turn
    follows it. Several opening messages are re-sent as one, in order.
    """
    texts = [str(e["payload"]["text"]) for e in opening["messages"]]
    rels = [str(r) for e in opening["messages"] for r in e["payload"].get("attachments") or []]
    first = conv_dir / "instruction.md"
    if opening["first"] and first.is_file():
        instruction = first.read_text(encoding="utf-8").rstrip()
        texts.insert(0, instruction)
        rels[:0] = attachment_rels(instruction)
    return "\n\n".join(t for t in texts if t.strip()), list(dict.fromkeys(rels))


def _plan(request: Request, run_id: str, turn: int, ts: str, pick: str) -> _Plan:
    """Everything a flag needs, checked: the conversation, its settled state, the reply, the
    message that opened it, and the model that takes over. Writes nothing.
    """
    server = request.app.state.server
    _, run_dir = _run_dir(request, run_id)
    if run_dir.parent.parent.parent != server.conversations_home:
        raise HTTPException(400, "a reply can be flagged as a refusal in a conversation only")
    if run_state(run_dir) not in TERMINAL_STATES:
        raise HTTPException(409, "this conversation is mid-reply — flag a reply once the "
                                 "reply has finished")
    cfg, _ = load_routine(run_dir.parent.parent)
    if cfg is None:
        raise HTTPException(404, f"conversation {run_dir.parent.parent.name!r} not found")
    events, _ = read_events(run_dir / "transcript.jsonl", 0)
    opening = reply_opening(events, turn, ts)
    if opening is None:
        raise HTTPException(404, f"no reply at turn {turn} with that timestamp — the "
                                 "conversation changed since this view loaded; reload it")
    text, rels = _message(cfg.dir, opening)
    if opening["keep"] is None or not text:
        raise HTTPException(400, "no message of yours opened this reply — it continued the "
                                 "conversation without one, so there is nothing to re-send")
    configured = str(cfg.models.get("uncensored") or "")
    if pick and configured and pick != configured:
        raise HTTPException(400, f"this conversation's uncensored model is {configured!r} and "
                                 "the flag hands over to it — to hand over to another, "
                                 "change the uncensored model first")
    takeover = configured or pick
    # uncensored first, so a configured model the catalog no longer has is refused under the
    # role it was set on; writing it back unchanged is a no-op, and a pick lands in both roles
    roles = {"uncensored": takeover, "main": takeover}
    if takeover:
        validate_models(server, roles)   # the conversation settings' one enforcer
    served = opening["served_by"]
    # no turn of its own to attribute it: the model the conversation was on wrote it
    refused_by = (_served_name(server, served) if served
                  else str(cfg.models.get("main") or server.system_model or ""))
    said = str((opening["finish"].get("payload") or {}).get("summary") or "")
    return _Plan(run_dir=run_dir, cfg=cfg, text=text, attachments=rels, refused_by=refused_by,
                 reply=said[:_REPLY_CAP], takeover=takeover, roles=roles if takeover else {})


@router.get("/runs/{run_id}/flag-refusal")
def preview_refusal_flag(request: Request, run_id: str, turn: int, ts: str = "") -> dict:
    """What flagging this reply as a refusal WOULD do — the warning gate's facts, from the
    same plan the POST executes. `model` is null when the conversation has no uncensored
    model: the gate then asks for one.
    """
    plan = _plan(request, run_id, turn, ts, "")
    return {"turn": turn, "ts": ts, "message": plan.text, "refused_by": plan.refused_by,
            "model": plan.takeover or None}


@router.post("/runs/{run_id}/flag-refusal")
async def flag_refusal(request: Request, run_id: str, body: RefusalFlag) -> dict:
    """Flag a finished conversation reply as a refusal: archive it and everything after it,
    hand the conversation to its uncensored model (the picked one becomes uncensored AND
    main), re-send the message that produced the reply, and wake the conversation.
    """
    plan = _plan(request, run_id, body.turn, body.ts, body.model.strip())
    if not plan.takeover:
        raise HTTPException(400, "this conversation has no uncensored model — pick the model "
                                 "that takes over")
    runner = request.app.state.runner
    if why := runner.resume_blocker(plan.cfg, plan.run_dir.name):
        raise HTTPException(409, f"nothing was changed — the conversation cannot be woken "
                                 f"now: {why}")
    # Everything is checked. From here on, write: the cut, the models, the message, the wake.
    cut = rewind_transcript(plan.run_dir, before_reply=(body.turn, body.ts))
    if cut is None:
        raise HTTPException(409, "the conversation changed while flagging — reload it")
    path = plan.cfg.dir / "routine.yaml"
    raw = read_yaml(path, {})
    raw["models"] = {**(raw.get("models") or {}), **plan.roles}
    atomic_write_yaml(path, raw)
    # A model switch the reply's own leg left pending in control.json would land on the next
    # leg's first boundary and undo the takeover: the takeover is the newer decision.
    ctrl = read_json(plan.run_dir / "control.json")
    if isinstance(ctrl, dict) and ctrl.get("switch_model"):
        merge_control(plan.run_dir, {"switch_model": None})
    flag = {"turn": body.turn, "model": plan.refused_by, "takeover_model": plan.takeover,
            "message": plan.reply, "archive": cut["archive"]}
    queued = inbox.file_message(plan.cfg.dir, plan.text, via="conversation",
                                extra={"refusal_flag": flag,
                                       **({"attachments": plan.attachments}
                                          if plan.attachments else {})})
    rid = await runner.resume(plan.cfg, plan.run_dir.name, reason="refusal-flag")
    if not rid:
        raise HTTPException(409, "flagged and re-sent, but the conversation could not be "
                                 "woken — the message waits for its next wake")
    return {"ok": True, "run_id": rid, "model": plan.takeover, "message_id": queued.stem,
            **cut}
