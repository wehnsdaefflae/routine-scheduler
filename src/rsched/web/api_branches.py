"""Conversation branching endpoints — fork, hand-back, and the lineage a page shows (F325).

The web layer only: `rsched/branches.py` owns the mechanics and states the design. These three
routes exist because branching is a USER decision at both ends — where to split, and whether a
branch's result is worth handing back — so neither half may happen implicitly.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import branches
from ..registry import TERMINAL_STATES
from .conversations_common import conversation_info

router = APIRouter(tags=["branches"])


class ForkBody(BaseModel):
    turn: int          # fork through THIS turn — the branch inherits the history up to it
    name: str = ""     # optional display name; defaults to "<parent> (branch)"


class HandBackBody(BaseModel):
    summary: str       # what the branch concluded — the parent reads this, not the transcript


def _parent_record(conv_dir: Path) -> dict:
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text(encoding="utf-8")) or {}
    rec = raw.get("parent")
    return rec if isinstance(rec, dict) else {}


@router.post("/conversations/{slug}/branch")
def fork(request: Request, slug: str, body: ForkBody) -> dict:
    """Fork this conversation at `turn` into a new one that inherits its config and its
    history up to that point. The original is untouched — a branch cannot mutate it.
    """
    info = conversation_info(request, slug)
    last = info.last_run
    if last and last.state not in TERMINAL_STATES:
        raise HTTPException(409, "this conversation is mid-reply — branch it once the reply "
                                 "has finished, so the fork point is a settled turn")
    try:
        made = branches.fork_conversation(
            request.app.state.server, parent_dir=info.cfg.dir, parent_slug=slug,
            at_turn=body.turn, name=body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "slug": made["slug"], "at_turn": made["at_turn"],
            "kept_events": made["kept_events"]}


@router.post("/conversations/{slug}/handback")
def handback(request: Request, slug: str, body: HandBackBody) -> dict:
    """Hand this branch's result back to its parent: the summary as a message, its artefacts
    as files. Deliberately NOT a transcript merge — two divergent histories cannot be
    interleaved into one coherent conversation, so the parent receives a RESULT and decides
    what to do with it, exactly as it would from a detached background task.
    """
    info = conversation_info(request, slug)
    if not body.summary.strip():
        raise HTTPException(400, "a hand-back needs a summary — that is what the parent reads")
    try:
        out = branches.hand_back(request.app.state.server, branch_dir=info.cfg.dir, slug=slug,
                                 summary=body.summary.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **out}


@router.get("/conversations/{slug}/lineage")
def lineage(request: Request, slug: str) -> dict:
    """This conversation's place in its branch family: the parent it was forked from (with the
    fork point) and the branches forked off it. Both directions, because a page needs to offer
    the way back as readily as the way down.
    """
    info = conversation_info(request, slug)
    server = request.app.state.server
    parent = _parent_record(info.cfg.dir)
    if parent.get("slug"):
        pdir = server.conversations_home / str(parent["slug"])
        raw = (yaml.safe_load((pdir / "routine.yaml").read_text(encoding="utf-8")) or {}
               if (pdir / "routine.yaml").is_file() else {})
        # A deleted parent leaves the record standing: the branch's history still came from
        # somewhere, and saying so beats silently reading as a root conversation.
        parent = {**parent, "name": str(raw.get("name") or parent["slug"]),
                  "exists": bool(raw)}
    kids = []
    home = server.conversations_home
    if home.is_dir():
        for d in sorted(home.iterdir()):
            if not (d / "routine.yaml").is_file():
                continue
            rec = _parent_record(d)
            if rec.get("slug") != slug:
                continue
            raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8")) or {}
            kids.append({"slug": d.name, "name": str(raw.get("name") or d.name),
                         "turn": rec.get("turn"), "forked": rec.get("forked")})
    return {"parent": parent or None, "branches": kids}
