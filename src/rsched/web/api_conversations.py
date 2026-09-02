"""Conversations API: list/create/detail/message/delete, config edits, artifact +
attachment serving.

A conversation is a routine-shaped dir under conversations_home (see conversations.py);
its ONE run is continued in place — a message to a live run is an ordinary injection, a
message to a finished run resumes it (converse semantics). Transcript/SSE/abort ride the
existing /api/runs endpoints (run resolution is home-aware). Attachments upload as
multipart files into <conv>/attachments/ and travel as an `[attached files]` block in the
message text; deliverables the model writes into <conv>/artifacts/ are listed and served
here for the chat's artifact panel. Its detached background tasks live in api_background.
"""

from __future__ import annotations

import shutil
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from .. import conversations as conv_mod
from .. import registry
from ..engine import inbox
from . import artifacts
from .api_background import teardown_background
from .routines_common import (
    guard_not_active,
)

router = APIRouter(tags=["conversations"])


from .conversations_common import (  # noqa: E402
    _home,
    _item,
    _save_attachments,
    conversation_info,
)


@router.get("/conversations")
def list_conversations(request: Request) -> list[dict]:
    catalog = registry.scan(request.app.state.server, _home(request))
    items = [_item(info) for info in catalog.values()]
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items


@router.get("/conversations/{slug}/commands")
def commands(request: Request, slug: str) -> dict:
    """The chat composer's command reference + autocomplete feed: the slash-command kinds
    this conversation's capability surface allows (the engine still enforces exactly at
    execution) and the util catalog (name + summary + usage).
    """
    from .. import utils_lib
    from ..engine.commands import command_catalog
    from ..policyload import load_policy

    info = conversation_info(request, slug)
    server = request.app.state.server
    policy = load_policy(server.permissions_home, info.cfg.permissions,
                         info.cfg.capabilities)
    return command_catalog(policy, utils_lib.list_utils(server.libraries_home))


@router.post("/conversations/{slug}/message")
async def message(request: Request, slug: str, text: Annotated[str, Form()],
                  command: Annotated[str, Form()] = "",
                  files: Annotated[list[UploadFile] | None, File()] = None) -> dict:
    """Append a user message (with optional attachments): a live reply picks it up at the
    next turn boundary; a finished conversation is resumed in place. `command` marks a
    slash command — the engine EXECUTES it as a user-authored action instead of handing
    it to the model as prose.
    """
    info = conversation_info(request, slug)
    text = text.replace("\r\n", "\n")   # multipart encodes newlines CRLF; \n is canonical
    if not text.strip():
        raise HTTPException(400, "empty message")
    conv_dir = info.cfg.dir
    is_command = bool(command.strip())
    last = info.last_run
    is_mid_run = bool(last and last.state not in registry.TERMINAL_STATES)
    # R81: a terminal/new conversation must be WOKEN (resume_terminal / fire), and both refuse
    # while the daemon is draining for a self-update restart — with nothing re-driving a pending
    # inbox after relaunch. Filing first then failing the wake strands the message and returns a
    # 409 that reads as total failure, so the operator blind-resends (the observed duplicate
    # spam). Refuse up front, BEFORE filing, unless the run is live (mid-run drains its own inbox
    # at the next turn boundary; draining does not kill an already-running run).
    if not is_mid_run and getattr(request.app.state.runner, "draining", False):
        raise HTTPException(
            503, "the server is restarting — your message was NOT saved. Resend it once, in a "
                 "moment, after the server is back (repeated resends only pile up duplicates).")
    rels = await _save_attachments(conv_dir, files or [])
    full = text.rstrip() + conv_mod.attachment_note(rels)
    inbox.file_message(conv_dir, full, via="conversation",
                       extra={**({"command": True} if command.strip() else {}),
                              **({"attachments": rels} if rels else {})})
    if is_mid_run:
        # R108 residual (F268): the liveness snapshot above predates the file write — a
        # run that finished inside that window would leave this message queued with
        # nothing waking it (the engine's own finish-defer only covers messages that
        # landed BEFORE its final check). Re-read the LIVE state now that the message is
        # durably down: still live → mid-run delivery as before (the next turn boundary
        # drains it); finished in between → fall through to the terminal-resume below,
        # exactly as if the run had already been terminal when the message arrived.
        fresh = conversation_info(request, slug).last_run
        if fresh and fresh.state not in registry.TERMINAL_STATES:
            return {"ok": True, "delivery": "mid-run", "run_id": fresh.run_id,
                    "command": is_command}
        last = fresh or last
    runner = request.app.state.runner
    # D62/D63: an ADMIN resume from the Conversations composer — the SAME web-layer-only token
    # check as /runs/{id}/converse. On a valid token, unlock capability gating for THIS resumed
    # leg via the one-shot marker (never persisted, never inherited by a sub-workflow). Scoped to
    # a resume of an existing terminal conversation — a fresh fire has no run dir to mark yet, and
    # the mid-run branch already returned above. The marker is cleared again if the wake fails, so
    # a stale marker can never grant admin to a LATER, tokenless resume of the same run.
    from ..engine.admin import (
        ADMIN_HEADER,
        admin_token_valid,
        clear_admin_marker,
        write_admin_marker,
    )
    admin_run_dir = None
    if last and admin_token_valid(request.headers.get(ADMIN_HEADER)):
        admin_run_dir = conv_dir / "runs" / last.run_id.split(":", 1)[1]
        write_admin_marker(admin_run_dir)
    # A command wakes the engine to EXECUTE it and return to idle without a reply (the loop's
    # command-only gate) — same resume, but the model never takes a turn.
    rid = (await runner.resume_terminal(info.cfg, reason="converse") if last
           else await runner.fire(info.cfg, reason="conversation"))
    if not rid:
        if admin_run_dir is not None:
            clear_admin_marker(admin_run_dir)
        raise HTTPException(
            409, "could not wake the conversation (draining, or a reply just started)")
    return {"ok": True, "delivery": "command" if is_command else "resumed",
            "run_id": rid, "command": is_command}


@router.delete("/conversations/{slug}")
async def delete_conversation(request: Request, slug: str) -> dict:
    """A conversation is unversioned by design — delete means gone. Also cancels + removes any
    detached background tasks it launched (the manager's 'owner missing at delivery' branch is
    the safety net, but tearing them down here frees the pool and stops wasted compute).
    """
    info = conversation_info(request, slug)
    guard_not_active(request, info, noun="conversation")
    await teardown_background(request, slug)
    shutil.rmtree(info.cfg.dir)
    return {"ok": True}

@router.get("/conversations/{slug}/stategraph")
def stategraph(request: Request, slug: str) -> dict:
    """The conversation's lifecycle graph (working ⇄ waiting for you) with the CURRENT node
    lit from the live run state — same shape as the routines endpoint so the artifact rail
    renders it. A conversation is a loop, so its state IS its reply cycle, not the single
    converse workflow phase (which is never written to phase.json, so the generic routine
    state graph would never highlight a node).
    """
    info = conversation_info(request, slug)
    last = info.last_run
    return {"states": [dict(s) for s in conv_mod.CONVERSATION_STATES],
            "current": conv_mod.conversation_phase(last.state if last else None)}


@router.get("/conversations/{slug}/artifacts")
def list_artifacts(request: Request, slug: str) -> list[dict]:
    info = conversation_info(request, slug)
    return artifacts.list_artifacts(info.cfg.dir)


@router.delete("/conversations/{slug}/artifacts")
def delete_artifact(request: Request, slug: str, path: str) -> dict:
    """Remove one artifact from the sidebar (user order 2026-08-14). artifacts/ only —
    attachments are the USER'S uploads and stay.
    """
    info = conversation_info(request, slug)
    return artifacts.delete_artifact(info.cfg.dir, path)


@router.get("/conversations/{slug}/file")
def get_file(request: Request, slug: str, path: str):
    """Serve one artifact or attachment (the chat panel fetches these with the auth header
    and renders from blob URLs). Only artifacts/ and attachments/ are servable.
    """
    info = conversation_info(request, slug)
    return artifacts.serve_file(info.cfg.dir, path,
                                subdirs=(*artifacts.ARTIFACT_DIRS, "attachments"))
