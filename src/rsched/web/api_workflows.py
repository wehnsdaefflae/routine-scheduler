"""Workflow library API: list with lint badges, content + git history, lint-gated edits,
delete. The user's levers over workflows are EDIT and DELETE — there is no accept/decline
gate; the workflow-curator routine applies its changes directly (lint-gated, committed)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..workflows import library
from ..workflows.lint import (lint_all, lint_permission_text, lint_trait_text,
                              lint_workflow_py)

router = APIRouter(tags=["workflows"])


def _home(request: Request):
    home = request.app.state.server.library_home
    if not home.is_dir():
        raise HTTPException(503, f"workflow library not found at {home} — run deploy/install.sh")
    return home


def _workflow_file(home, slug: str):
    """The workflow file for a slug (`workflows/<slug>.py`), or None."""
    path = library.workflows_dir(home) / f"{slug}.py"
    return path if path.exists() else None


@router.get("/workflows")
def list_workflows(request: Request) -> dict:
    home = _home(request)
    lint = lint_all(home)
    return {
        "workflows": [{**w, "problems": lint.get(f"workflows/{w['file']}", [])}
                      for w in library.list_workflows(home)],
        "head": library.head_commit(home),
    }


@router.get("/library")
def library_overview(request: Request) -> dict:
    """Everything under the Library tab: workflows, traits, permissions, and global utils."""
    from .. import library_docs, utils_lib
    from ..config import DEFAULT_BUDGETS, DEFAULT_PERMISSIONS, DEFAULT_TRAITS

    home = _home(request)
    server = request.app.state.server
    lint = lint_all(home)
    return {
        "workflows": [{**w, "problems": lint.get(f"workflows/{w['file']}", [])}
                      for w in library.list_workflows(home)],
        "traits": [{**t, "problems": lint.get(f"traits/{t['slug']}.md", [])}
                   for t in library_docs.list_docs(server.traits_home)],
        "permissions": [{**p, "problems": lint.get(f"permissions/{p['slug']}.md", [])}
                        for p in library_docs.list_docs(server.permissions_home)],
        "utils": utils_lib.list_utils(server.utils_home),
        "default_traits": list(DEFAULT_TRAITS),
        "default_permissions": list(DEFAULT_PERMISSIONS),
        "default_budgets": dict(DEFAULT_BUDGETS),
        "heads": {"workflows": library.head_commit(home)},
    }


def _docs_home(request: Request, kind: str):
    server = request.app.state.server
    if kind == "traits":
        return server.traits_home
    if kind == "permissions":
        return server.permissions_home
    raise HTTPException(404, f"unknown library doc kind {kind!r}")


@router.get("/library/{kind}/{slug}")
def library_doc_detail(request: Request, kind: str, slug: str) -> dict:
    from .. import library_docs

    if kind == "utils":
        return util_detail(request, slug)
    home = _docs_home(request, kind)
    content = library_docs.read_doc(home, slug)
    if content is None:
        raise HTTPException(404, f"no {kind[:-1]} {slug!r}")
    return {"slug": slug, "content": content,
            "log": library_docs.git_log(home, f"{slug}.md")}


class DocBody(BaseModel):
    content: str


@router.put("/library/{kind}/{slug}")
def put_library_doc(request: Request, kind: str, slug: str, body: DocBody) -> dict:
    from .. import library_docs

    if kind == "utils":
        return put_util(request, slug, UtilBody(content=body.content))
    home = _docs_home(request, kind)
    linter = lint_trait_text if kind == "traits" else lint_permission_text
    problems = linter(body.content, filename=f"{slug}.md")
    if problems:
        raise HTTPException(422, "; ".join(problems))
    library_docs.write_doc(home, slug, body.content.rstrip() + "\n")
    library_docs.git_commit(home, f"edit {kind[:-1]} {slug} via web")
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
    problems = utils_lib.header_problems(body.content)
    if problems:
        raise HTTPException(422, "header problems (not saved): " + "; ".join(problems))
    utils_lib.ensure_library(server.utils_home, remote=server.libraries_remote)
    utils_lib.write_util_file(server.utils_home, name, body.content)
    ok, output = utils_lib.selftest(server.utils_home, name)
    if not ok:
        raise HTTPException(422, f"selftest failed (not committed):\n{output[:800]}")
    utils_lib.git_commit(server.utils_home, f"revise {name} via web")
    return {"ok": True}


@router.get("/workflows/{slug}")
def workflow_detail(request: Request, slug: str) -> dict:
    home = _home(request)
    path = _workflow_file(home, slug)
    if not path or not path.exists():
        raise HTTPException(404, f"no workflow {slug!r}")
    rel = str(path.relative_to(home))
    return {"slug": slug, "content": path.read_text(encoding="utf-8"), "log": library.git_log(home, rel),
            "format": "py" if path.suffix == ".py" else "md"}


class PutBody(BaseModel):
    content: str


@router.put("/workflows/{slug}")
def put_workflow(request: Request, slug: str, body: PutBody) -> dict:
    from .. import library_docs

    home = _home(request)
    server = request.app.state.server
    traits = library_docs.slugs(server.traits_home)
    problems = lint_workflow_py(body.content, filename=f"{slug}.py", trait_slugs=traits)
    if problems:
        raise HTTPException(422, "; ".join(problems))
    rel = f"workflows/{slug}.py"
    (home / rel).write_text(body.content.rstrip() + "\n", encoding="utf-8")
    library.git_commit(home, f"edit {rel} via web")
    return {"ok": True, "head": library.head_commit(home)}


@router.delete("/workflows/{slug}")
def delete_workflow(request: Request, slug: str) -> dict:
    """Delete a workflow pattern (committed). Routines materialized from it are untouched —
    they own their recipes. A deleted SEED pattern reappears at the next daemon boot
    (sync_seed_library_docs restores missing seed docs)."""
    home = _home(request)
    path = _workflow_file(home, slug)
    if path is None:
        raise HTTPException(404, f"no workflow {slug!r}")
    path.unlink()
    library.git_commit(home, f"delete workflows/{slug}.py via web")
    return {"ok": True, "head": library.head_commit(home)}


@router.post("/workflows/lint")
def lint(request: Request) -> dict:
    return {"results": lint_all(_home(request))}

