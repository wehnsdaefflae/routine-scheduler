"""Playbook library API: list (catalog), read MAIN.md + its on-demand detail files, lint-gated
MAIN edits, delete. Playbooks are captured from conversations (Save/Update-playbook — see
api_conversations.py) and reused to seed new ones; here they are browsed and hand-edited like any
library doc. Git lives at the library root (workflows.library.*)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import playbooks
from ..workflows import library
from ..workflows.lint import lint_playbook_text

router = APIRouter(tags=["playbooks"])


def _home(request: Request):
    home = request.app.state.server.library_home
    if not home.is_dir():
        raise HTTPException(503, f"library not found at {home} — run deploy/install.sh")
    return home


@router.get("/playbooks")
def list_playbooks(request: Request) -> dict:
    """The catalog — feeds the new-conversation playbook picker AND the Library tab."""
    home = _home(request)
    items = playbooks.list_playbooks(home)
    for it in items:
        pb = playbooks.read_playbook(home, it["slug"])
        it["problems"] = lint_playbook_text(pb["content"], filename=f"{it['slug']}/MAIN.md") if pb else []
    return {"playbooks": items, "head": library.head_commit(home)}


@router.get("/playbooks/{slug}")
def playbook_detail(request: Request, slug: str) -> dict:
    home = _home(request)
    pb = playbooks.read_playbook(home, slug)
    if pb is None:
        raise HTTPException(404, f"no playbook {slug!r}")
    return {"slug": slug, "content": pb["content"], "details": sorted(pb["details"]),
            "log": library.git_log(home, f"playbooks/{slug}")}


@router.get("/playbooks/{slug}/detail/{name}")
def playbook_detail_file(request: Request, slug: str, name: str) -> dict:
    body = playbooks.read_detail(_home(request), slug, name)
    if body is None:
        raise HTTPException(404, f"no detail file {name!r} in playbook {slug!r}")
    return {"slug": slug, "name": name, "content": body}


class PlaybookBody(BaseModel):
    content: str


@router.put("/playbooks/{slug}")
def put_playbook(request: Request, slug: str, body: PlaybookBody) -> dict:
    """Edit a playbook's MAIN.md (lint-gated, committed). Detail files are left untouched — they
    are managed by the Update-playbook distillation, not hand-edited here."""
    home = _home(request)
    if playbooks.read_playbook(home, slug) is None:
        raise HTTPException(404, f"no playbook {slug!r}")
    problems = lint_playbook_text(body.content, filename=f"{slug}/MAIN.md")
    if problems:
        raise HTTPException(422, "; ".join(problems))
    playbooks.write_playbook(home, slug, main=body.content)
    library.git_commit(home, f"edit playbook {slug} via web")
    return {"ok": True, "head": library.head_commit(home)}


@router.delete("/playbooks/{slug}")
def delete_playbook(request: Request, slug: str) -> dict:
    home = _home(request)
    if not playbooks.delete_playbook(home, slug):
        raise HTTPException(404, f"no playbook {slug!r}")
    library.git_commit(home, f"delete playbook {slug} via web")
    return {"ok": True, "head": library.head_commit(home)}
