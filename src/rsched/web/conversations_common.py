"""Shared conversation-endpoint plumbing: home/info lookups, streamed attachment
saving, and the list-item shaping — used by api_conversations and its playbook sibling.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException, Request, UploadFile

from .. import registry
from ..config import load_routine
from ..ids import run_ts

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


def _home(request: Request) -> Path:
    return request.app.state.server.conversations_home


def conversation_info(request: Request, slug: str) -> registry.RoutineInfo:
    d = _home(request) / slug
    if not (d / "routine.yaml").exists():
        raise HTTPException(404, f"no conversation {slug!r}")
    cfg, problems = load_routine(d)
    if cfg is None:
        raise HTTPException(500, "; ".join(problems))
    return registry.RoutineInfo(cfg=cfg, problems=problems,
                                runs=registry.run_index(d, cfg.slug),
                                open_questions=[])


MAX_ATTACHMENTS_PER_MESSAGE = 16
_UPLOAD_CHUNK = 1 << 20


async def _save_attachments(conv_dir: Path, files: list[UploadFile]) -> list[str]:
    """Store uploads under attachments/ (timestamped, safe basenames); returns the
    conversation-relative paths for the message's attachment block. STREAMED to disk in
    chunks with the size cap enforced as it arrives — `await f.read()` buffered the whole
    upload first, so an oversized body OOM'd the box before the 413. Same-name files in
    one message get a numbered suffix instead of overwriting each other.
    """
    if files and len(files) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise HTTPException(413, f"at most {MAX_ATTACHMENTS_PER_MESSAGE} attachments "
                                 "per message")
    rels: list[str] = []
    taken: set[str] = set()
    stamp = run_ts()
    for i, f in enumerate(files or []):
        base = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(f.filename or f"file-{i}").name).strip("-.") \
            or f"file-{i}"
        rel = f"attachments/{stamp}-{base}"
        n = 2
        while rel in taken or (conv_dir / rel).exists():
            rel = f"attachments/{stamp}-{n}-{base}"
            n += 1
        taken.add(rel)
        (conv_dir / "attachments").mkdir(exist_ok=True)
        dest = conv_dir / rel
        written = 0
        try:
            with dest.open("wb") as out:
                while chunk := await f.read(_UPLOAD_CHUNK):
                    written += len(chunk)
                    if written > MAX_ATTACHMENT_BYTES:
                        raise HTTPException(
                            413, f"attachment {base!r} exceeds "
                                 f"{MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB")
                    out.write(chunk)
        except HTTPException:
            dest.unlink(missing_ok=True)
            raise
        rels.append(rel)
    return rels


def _snippet(info: registry.RoutineInfo) -> str:
    last = info.last_run
    return (last.summary.strip().splitlines()[0][:160]
            if last and last.summary else "")


def _item(info: registry.RoutineInfo) -> dict:
    last = info.last_run
    return {
        "slug": info.slug,
        "title": info.cfg.name,
        "tags": info.cfg.tags,
        "state": last.state if last else "new",
        "updated": (last.updated or last.ts) if last else "",
        "snippet": _snippet(info),
        "run_id": last.run_id if last else None,
        "turns": last.turn if last else 0,
        "usage": last.usage if last else {},
        "question": bool(last and last.question),
    }
