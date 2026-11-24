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

from .. import branches, registry
from ..config import load_routine
from ..engine import inbox
from ..paths import read_yaml
from ..registry import TERMINAL_STATES
from . import branch_lineage
from .conversations_common import _item, conversation_info

router = APIRouter(tags=["branches"])


class ForkBody(BaseModel):
    turn: int          # fork through THIS turn — the branch inherits the history up to it
    name: str = ""     # optional display name; defaults to "<parent> (branch)"
    instruction: str = ""   # optional first message — given, the branch starts working at once


class HandBackBody(BaseModel):
    summary: str       # what the branch concluded — the parent reads this, not the transcript


def _config(conv_dir: Path) -> dict:
    """A conversation's routine.yaml as a mapping, for READING ONLY — `{}` when it is missing,
    does not parse, or is not a mapping. Lineage reads every conversation's file to find the
    branches, so `read_yaml`'s deliberate raise (it guards read-modify-writes) turned one
    hand-broken config into a 500 for every conversation; the catalog lists such a file as an
    unloadable conversation and carries on, and so does this.
    """
    try:
        raw = read_yaml(conv_dir / "routine.yaml", {})
    except (OSError, yaml.YAMLError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _parent_record(conv_dir: Path) -> dict:
    rec = _config(conv_dir).get("parent")
    return rec if isinstance(rec, dict) else {}


@router.post("/conversations/{slug}/branch")
async def fork(request: Request, slug: str, body: ForkBody) -> dict:
    """Fork this conversation at `turn` into a new one that inherits its config and its
    history up to that point. The original is untouched — a branch cannot mutate it.

    With an `instruction`, the branch also STARTS: the text is filed as its first message
    through the same `inbox.file_message` + `resume_terminal` path
    `POST /conversations/{slug}/message` uses, so a branch is one click instead of "fork,
    land in an empty conversation, now type what you came to ask" (D306 pt 4). The start is
    the operator's own click — one of the three things allowed to start a run. Without one
    the branch opens idle, exactly as before.
    """
    info = conversation_info(request, slug)
    last = info.last_run
    if last and last.state not in TERMINAL_STATES:
        raise HTTPException(409, "this conversation is mid-reply — branch it once the reply "
                                 "has finished, so the fork point is a settled turn")
    runner = request.app.state.runner
    text = body.instruction.replace("\r\n", "\n").strip()
    # R81, the branch door: in the restart's exit window the wake is refused, so refuse BEFORE
    # the fork — a branch made and never started is a conversation in the list holding the
    # instruction nobody is reading, and the retry makes a second one.
    if text and getattr(runner, "draining", False):
        raise HTTPException(
            503, "the server is restarting — the branch was NOT made. Try again in a moment.")
    try:
        made = branches.fork_conversation(
            request.app.state.server, parent_dir=info.cfg.dir, parent_slug=slug,
            at_turn=body.turn, name=body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    out = {"ok": True, "slug": made["slug"], "at_turn": made["at_turn"],
           "kept_events": made["kept_events"], "started": False}
    if not text:
        return out
    inbox.file_message(made["dir"], text, via="conversation")
    cfg, _problems = load_routine(made["dir"])
    # The fork left a TERMINAL run beside the copied transcript (branches.fork_conversation
    # says why), so starting the branch is the ordinary terminal resume a typed message takes
    # — not a fresh fire, which would discard the inherited history.
    rid = await runner.resume_terminal(cfg, reason="converse")
    # A filed-but-unwoken instruction is not lost: it stays queued and the branch's next reply
    # drains it. Say which happened rather than claim a start that did not take.
    out["started"] = bool(rid)
    if rid:
        out["run_id"] = rid
    return out


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
    home = server.conversations_home
    # ONE catalog read for the whole answer (memoized and single-flighted, `readmodels/memo`):
    # both the parent's live fields and every branch's come out of it, so D306 pt 2's state
    # columns cost no walk of their own on top of the loop below.
    rows = {s: _item(i) for s, i in registry.scan(server, home).items()} if home.is_dir() else {}
    if parent.get("slug"):
        raw = _config(home / str(parent["slug"]))
        # A deleted parent leaves the record standing: the branch's history still came from
        # somewhere, and saying so beats silently reading as a root conversation.
        parent = {**parent, "name": str(raw.get("name") or parent["slug"]),
                  "exists": bool(raw),
                  **branch_lineage.live_fields(rows, str(parent["slug"]))}
    kids = []
    if home.is_dir():
        # D306 pt 2: each branch carries what BECAME of it — state, last update, whether it has
        # handed back — because a list of names with no state is the gap the operator named
        # ("no way to tell a branch working from one long finished"). `branch_lineage` owns what
        # one row says; the catalog it reads was taken once, above.
        for d in sorted(home.iterdir()):
            raw = _config(d)
            rec = raw.get("parent")
            if not isinstance(rec, dict) or rec.get("slug") != slug:
                continue
            kids.append(branch_lineage.branch_row(
                {"slug": d.name, "name": str(raw.get("name") or d.name),
                 "turn": rec.get("turn"), "forked": rec.get("forked")},
                info.cfg.dir, rows))
    return {"parent": parent or None, "branches": kids}
