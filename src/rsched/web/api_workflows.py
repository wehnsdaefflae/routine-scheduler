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
    server = request.app.state.server
    lint = lint_all(home, server.fragments_home)
    return {
        "workflows": [{**w, "problems": lint.get(f"workflows/{w['file']}", [])}
                      for w in library.list_workflows(home)],
        "head": library.head_commit(home),
    }


@router.get("/library")
def library_overview(request: Request) -> dict:
    """Everything under the Library tab: workflows, fragments, and global utils."""
    from .. import fragments_lib, utils_lib

    home = _home(request)
    server = request.app.state.server
    lint = lint_all(home, server.fragments_home)
    return {
        "workflows": [{**w, "problems": lint.get(f"workflows/{w['file']}", [])}
                      for w in library.list_workflows(home)],
        "fragments": [{**f, "problems": lint.get(f"fragments/{f['slug']}.md", [])}
                      for f in fragments_lib.list_fragments(server.fragments_home)],
        "utils": utils_lib.list_utils(server.utils_home),
        "heads": {"workflows": library.head_commit(home)},
    }


@router.get("/library/fragments/{slug}")
def fragment_detail(request: Request, slug: str) -> dict:
    from .. import fragments_lib

    server = request.app.state.server
    content = fragments_lib.read_fragment(server.fragments_home, slug)
    if content is None:
        raise HTTPException(404, f"no fragment {slug!r}")
    return {"slug": slug, "content": content,
            "log": fragments_lib.git_log(server.fragments_home, f"{slug}.md")}


class FragmentBody(BaseModel):
    content: str


@router.put("/library/fragments/{slug}")
def put_fragment(request: Request, slug: str, body: FragmentBody) -> dict:
    from .. import fragments_lib
    from ..workflows.lint import lint_fragment_text

    server = request.app.state.server
    problems = lint_fragment_text(body.content, filename=f"{slug}.md")
    if problems:
        raise HTTPException(422, "; ".join(problems))
    fragments_lib.write_fragment(server.fragments_home, slug, body.content.rstrip() + "\n")
    fragments_lib.git_commit(server.fragments_home, f"edit fragment {slug} via web")
    return {"ok": True}


@router.get("/library/utils/{name}")
def util_detail(request: Request, name: str) -> dict:
    from .. import utils_lib

    server = request.app.state.server
    content = utils_lib.read_util(server.utils_home, name)
    if content is None:
        raise HTTPException(404, f"no util {name!r}")
    return {"name": name, "content": content}


class UtilBody(BaseModel):
    content: str


@router.put("/library/utils/{name}")
def put_util(request: Request, name: str, body: UtilBody) -> dict:
    """Edit a global util (selftest-gated, committed) — mirrors the write_util engine action."""
    from .. import utils_lib

    server = request.app.state.server
    utils_lib.ensure_library(server.utils_home, remote=server.utils_remote)
    utils_lib.write_util_file(server.utils_home, name, body.content)
    ok, output = utils_lib.selftest(server.utils_home, name)
    if not ok:
        raise HTTPException(422, f"selftest failed (not committed):\n{output[:800]}")
    utils_lib.git_commit(server.utils_home, f"revise {name} via web")
    return {"ok": True}


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
    from .. import fragments_lib

    home = _home(request)
    server = request.app.state.server
    problems = lint_workflow_text(body.content, filename=f"{slug}.md",
                                  fragment_slugs=fragments_lib.slugs(server.fragments_home))
    if problems:
        raise HTTPException(422, "; ".join(problems))
    (home / f"workflows/{slug}.md").write_text(body.content.rstrip() + "\n", encoding="utf-8")
    library.git_commit(home, f"edit workflows/{slug}.md via web")
    return {"ok": True, "head": library.head_commit(home)}


@router.post("/workflows/lint")
def lint(request: Request) -> dict:
    server = request.app.state.server
    return {"results": lint_all(_home(request), server.fragments_home)}


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
