"""Workflow library API: list with lint badges, content + git history, lint-gated edits,
meta-routine proposals."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..ids import now_iso
from ..paths import atomic_write_json
from ..workflows import library
from ..workflows.lint import lint_all, lint_fragment_text, lint_workflow_text

router = APIRouter(tags=["workflows"])


def _home(request: Request):
    home = request.app.state.server.library_home
    if not home.is_dir():
        raise HTTPException(503, f"workflow library not found at {home} — run deploy/install.sh")
    return home


@router.get("/workflows")
def list_workflows(request: Request) -> dict:
    home = _home(request)
    lint = lint_all(home)
    return {
        "workflows": [{**w, "problems": lint.get(f"workflows/{w['file']}", [])}
                      for w in library.list_workflows(home)],
        "fragments": [{"slug": s, "problems": lint.get(f"fragments/{s}.md", [])}
                      for s in library.list_fragments(home)],
        "head": library.head_commit(home),
    }


@router.get("/workflows/{slug}")
def workflow_detail(request: Request, slug: str, fragment: bool = False) -> dict:
    home = _home(request)
    rel = f"fragments/{slug}.md" if fragment else f"workflows/{slug}.md"
    path = home / rel
    if not path.exists():
        raise HTTPException(404, f"no {'fragment' if fragment else 'workflow'} {slug!r}")
    return {"slug": slug, "content": path.read_text(encoding="utf-8"),
            "log": library.git_log(home, rel)}


class PutBody(BaseModel):
    content: str
    fragment: bool = False


@router.put("/workflows/{slug}")
def put_workflow(request: Request, slug: str, body: PutBody) -> dict:
    home = _home(request)
    if body.fragment:
        problems = lint_fragment_text(body.content, filename=f"{slug}.md")
        rel = f"fragments/{slug}.md"
    else:
        problems = lint_workflow_text(body.content, filename=f"{slug}.md",
                                      fragment_slugs=library.list_fragments(home))
        rel = f"workflows/{slug}.md"
    if problems:
        raise HTTPException(422, "; ".join(problems))
    (home / rel).write_text(body.content.rstrip() + "\n", encoding="utf-8")
    library.git_commit(home, f"edit {rel} via web")
    return {"ok": True, "head": library.head_commit(home)}


@router.post("/workflows/lint")
def lint(request: Request) -> dict:
    return {"results": lint_all(_home(request))}


@router.get("/proposals")
def proposals(request: Request) -> list[dict]:
    return library.list_proposals(_home(request))


class Decision(BaseModel):
    decision: str  # accepted | declined
    note: str = ""


@router.post("/proposals/{proposal_id}/decide")
def decide(request: Request, proposal_id: str, body: Decision) -> dict:
    home = _home(request)
    if body.decision not in ("accepted", "declined"):
        raise HTTPException(400, "decision must be accepted|declined")
    if not (library.proposals_dir(home) / f"{proposal_id}.md").exists():
        raise HTTPException(404, f"no proposal {proposal_id!r}")
    atomic_write_json(library.proposals_dir(home) / f"{proposal_id}.decision.json",
                      {"decision": body.decision, "note": body.note, "ts": now_iso()})
    library.git_commit(home, f"proposal {proposal_id}: {body.decision}")
    # nudge the meta routine so its next run acts on the decision
    meta_dir = request.app.state.server.routines_home / "meta-workflows"
    if meta_dir.is_dir():
        atomic_write_json(meta_dir / "inbox" / f"msg-proposal-{proposal_id}.json",
                          {"text": f"Proposal {proposal_id} was {body.decision}"
                                   + (f" — note: {body.note}" if body.note else ""),
                           "ts": now_iso(), "via": "proposals"})
    return {"ok": True}
