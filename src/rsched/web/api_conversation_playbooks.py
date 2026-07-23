"""Save-as / update playbook: distil a conversation's transcript into a reusable brief
(the one-shot save/use-instruction analog) — split from api_conversations.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import playbook_distill
from .conversations_common import conversation_info

router = APIRouter(tags=["conversations"])

@router.post("/conversations/{slug}/playbook")
def save_playbook(request: Request, slug: str) -> dict:
    """Distil this conversation (its intent + the procedure that satisfied it) into a NEW library
    playbook via the system model, committed to the library. Always creates a new playbook (slug
    suffixed on collision) — use PUT to refine the one a conversation was seeded from. A sync def:
    FastAPI runs it in a worker thread, so the blocking inference never stalls the event loop.
    """
    from .. import playbooks
    from ..workflows import library

    info = conversation_info(request, slug)
    server = request.app.state.server
    home = server.libraries_home
    try:
        pb = playbook_distill.distill_playbook(server, info.cfg.dir)
    except Exception as exc:
        raise HTTPException(502, f"could not distil a playbook: {exc}") from exc
    pb["slug"] = playbooks.unique_slug(home, pb["slug"])
    main_text, details = playbook_distill.materialize(pb)
    playbooks.write_playbook(home, pb["slug"], main=main_text, details=details)
    library.git_commit(home, f"save playbook {pb['slug']} (from conversation {slug})",
                       paths=[f"playbooks/{pb['slug']}"])
    return {"ok": True, "slug": pb["slug"], "title": pb["title"], "when": pb["when"],
            "axis": pb["axis"]}


@router.put("/conversations/{slug}/playbook")
def update_playbook(request: Request, slug: str) -> dict:
    """Revise the playbook this conversation was SEEDED from, folding in the deltas the user made
    by adjusting/intervening in the conversation (committed). 400 if the conversation has no bound
    playbook; 404 if that playbook was since deleted (Save a new one instead).
    """
    from .. import playbooks
    from ..workflows import library

    info = conversation_info(request, slug)
    server = request.app.state.server
    home = server.libraries_home
    bound = info.cfg.playbook_slug
    if not bound:
        raise HTTPException(400, "this conversation was not created from a playbook")
    existing = playbooks.read_playbook(home, bound)
    if existing is None:
        raise HTTPException(
            404, f"playbook {bound!r} no longer exists — use Save as playbook instead")
    try:
        pb = playbook_distill.revise_playbook(server, info.cfg.dir, existing["content"], bound)
    except Exception as exc:
        raise HTTPException(502, f"could not revise the playbook: {exc}") from exc
    main_text, details = playbook_distill.materialize(pb)
    playbooks.write_playbook(home, bound, main=main_text, details=details)
    library.git_commit(home, f"update playbook {bound} (from conversation {slug})",
                       paths=[f"playbooks/{bound}"])
    return {"ok": True, "slug": bound, "title": pb["title"], "axis": pb["axis"]}
