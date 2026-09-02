"""Workflow library API: list with lint badges, content + git history, lint-gated edits,
delete. The user's levers over workflows are EDIT and DELETE — there is no accept/decline
gate; the routine-improver routine applies its changes directly (lint-gated, committed).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import templates, utils_header, utils_run
from ..paths import atomic_write
from ..workflows import library
from ..workflows.lint import (
    lint_all,
    lint_permission_text,
    lint_rule_text,
    lint_template_text,
    lint_workflow_py,
)

router = APIRouter(tags=["workflows"])


def _home(request: Request):
    home = request.app.state.server.libraries_home
    if not home.is_dir():
        raise HTTPException(503, f"workflow library not found at {home} — run deploy/install.sh")
    return home


def _workflow_file(home, slug: str):
    """The workflow file for a slug (`workflows/<slug>.py`), or None."""
    path = library.workflows_dir(home) / f"{slug}.py"
    return path if path.exists() else None


@router.get("/library")
def library_overview(request: Request) -> dict:
    """Everything under the Library tab: workflows, rules, permissions, playbooks, global utils."""
    from .. import library_docs, playbooks, utils_lib
    from ..config import DEFAULT_BUDGETS, DEFAULT_DELIBERATION, DEFAULT_PERMISSIONS, DEFAULT_RULES

    home = _home(request)
    server = request.app.state.server
    lint = lint_all(home)
    return {
        "workflows": [{**w, "problems": lint.get(f"workflows/{w['file']}", [])}
                      for w in library.list_workflows(home)],
        "rules": [{**r, "problems": lint.get(f"rules/{r['slug']}.md", [])}
                  for r in library_docs.list_docs(server.rules_home)],
        "permissions": [{**p, "problems": lint.get(f"permissions/{p['slug']}.md", [])}
                        for p in library_docs.list_docs(server.permissions_home)],
        # Settings TEMPLATES: the named starting points a routine or a group adopts. Carried
        # in the same payload as the docs they bundle, because the routine page's picker and
        # the Library tab's editor read the one call.
        "templates": templates.list_templates(server.libraries_home),
        "playbooks": [{**p, "problems": lint.get(f"playbooks/{p['slug']}/MAIN.md", [])}
                      for p in playbooks.list_playbooks(home)],
        "utils": utils_lib.list_utils(server.libraries_home),
        "default_rules": list(DEFAULT_RULES),
        "default_permissions": list(DEFAULT_PERMISSIONS),
        "default_budgets": dict(DEFAULT_BUDGETS),
        "default_deliberation": DEFAULT_DELIBERATION,
        "heads": {"workflows": library.head_commit(home)},
    }


def _docs_home(request: Request, kind: str):
    server = request.app.state.server
    if kind == "rules":
        return server.rules_home
    if kind == "permissions":
        return server.permissions_home
    if kind == "templates":
        return templates.templates_home(server.libraries_home)
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
    out: dict = {"slug": slug, "content": content,
           "log": library_docs.git_log(home, f"{slug}.md")}
    if kind == "permissions":
        # parsed requires: prefills the structured editor panel (see PUT below)
        import frontmatter as fm

        from ..grants import normalize_capabilities

        try:
            meta = fm.loads(content).metadata
        except Exception:
            meta = {}
        out["requires"] = normalize_capabilities(meta.get("requires"), label="requires",
                                                 requires=True)[0]
    return out


class DocBody(BaseModel):
    content: str
    # permissions only: the structured requires panel's value — merged into the doc's
    # frontmatter server-side, so the client never assembles YAML
    requires: dict | None = None
    # the token a `.../impact` preview returned, echoed back to confirm a breaking save
    impact_digest: str | None = None


# The Library tab is the SECOND writer of every library document (the engine's authoring
# actions are the first). Only the engine's path had an approval to hang a blast radius on, so
# a hand edit here reached every holder with nothing said. `impact_digest` is the confirm token
# that closes it: the client previews, the preview returns a digest, and the save carries it
# back. A library that MOVED in between yields a different digest and the save is refused —
# which is the point, since the whole hazard is a change nobody saw the consequences of.
_IMPACT_KIND = {"utils": "util", "rules": "rule", "permissions": "permission",
                "templates": "template"}


def _impact_for(request: Request, kind: str, slug: str, content: str | None) -> dict:
    from ..library_impact import impact

    server = request.app.state.server
    return impact(server, _IMPACT_KIND[kind], slug, content)


def _require_digest(request: Request, kind: str, slug: str, content: str | None,
                    supplied: str | None) -> None:
    """Refuse a save whose impact the caller has not seen (or saw before the library moved).

    Informational, not an approval: on the Library tab YOU are the authority, so a matching
    digest always passes and only a MISSING or STALE one stops. A save with no holders at all
    needs no token — there is nothing to have been shown.
    """
    result = _impact_for(request, kind, slug, content)
    if not result["breaks"]:
        return
    if supplied == result["digest"]:
        return
    raise HTTPException(409, "this change breaks routines that hold it: "
                        + "; ".join(f"{b['slug']} ({', '.join(b['gains'])})"
                                    for b in result["breaks"])
                        + f" — re-request the preview and save with impact_digest="
                          f"{result['digest']} to confirm")


class ImpactBody(BaseModel):
    """The preview's own body, because it asks a QUESTION rather than proposing a save: an
    absent `content` means DELETION, which `library_impact.impact` has always modelled and no
    caller could ask for — `DocBody.content` is required, so the most destructive case was the
    one the endpoint could not answer.
    """

    content: str | None = None


@router.post("/library/{kind}/{slug}/impact")
def preview_impact(request: Request, kind: str, slug: str, body: ImpactBody) -> dict:
    """Who holds this document; what would this content do to them (D-setup-coherence).

    Read-only: it writes nothing and decides nothing. The Library tab calls it before every
    save and before every delete, so the blast radius is seen rather than discovered.
    """
    if kind not in _IMPACT_KIND:
        raise HTTPException(404, f"unknown library kind {kind!r}")
    return _impact_for(request, kind, slug, body.content)


@router.put("/library/{kind}/{slug}")
def put_library_doc(request: Request, kind: str, slug: str, body: DocBody) -> dict:
    from .. import library_docs

    if kind == "utils":
        return put_util(request, slug, UtilBody(content=body.content,
                                                impact_digest=body.impact_digest))
    home = _docs_home(request, kind)
    content = body.content
    if kind == "permissions" and body.requires is not None:
        import frontmatter as fm

        from ..grants import normalize_capabilities

        req, problems = normalize_capabilities(body.requires, label="requires", requires=True)
        if problems:
            raise HTTPException(422, "; ".join(problems))
        try:
            post = fm.loads(content)
        except Exception as exc:
            raise HTTPException(422, f"invalid frontmatter: {exc}") from exc
        post.metadata["requires"] = req
        content = fm.dumps(post, sort_keys=False)
    linter = {"rules": lint_rule_text, "templates": lint_template_text}.get(
        kind, lint_permission_text)
    problems = linter(content, filename=f"{slug}.md")
    if problems:
        raise HTTPException(422, "; ".join(problems))
    _require_digest(request, kind, slug, content, body.impact_digest)
    library_docs.write_doc(home, slug, content.rstrip() + "\n")
    library_docs.git_commit(home, f"edit {kind[:-1]} {slug} via web", paths=[f"{slug}.md"])
    return {"ok": True}


@router.delete("/library/{kind}/{slug}")
def delete_library_doc(request: Request, kind: str, slug: str) -> dict:
    """Delete a rule (committed; a deleted SEED rule returns at the next daemon boot) or
    a util (`kind=utils` dispatches below). Permission docs are NOT deletable — they are
    the capability layer's conduct surface; edit them instead.
    """
    from .. import library_docs

    if kind == "utils":
        return delete_util(request, slug)
    if kind == "permissions":
        raise HTTPException(400, "permission docs cannot be deleted — they are the "
                                 "capability layer's conduct surface; edit the doc instead")
    home = _docs_home(request, kind)
    path = home / f"{slug}.md"
    if not path.is_file():
        raise HTTPException(404, f"no {kind[:-1]} {slug!r}")
    path.unlink()
    library_docs.git_commit(home, f"delete {kind[:-1]} {slug} via web", paths=[f"{slug}.md"])
    return {"ok": True}


def delete_util(request: Request, name: str) -> dict:
    """Delete a global util — its whole <name>/ dir, committed, so it is recoverable from
    git history. Routines discover utils live; the catalog shrinks at their next run.
    """
    from .. import utils_lib

    server = request.app.state.server
    if not utils_lib.exists(server.libraries_home, name):
        raise HTTPException(404, f"no util {name!r}")
    utils_lib.remove_util_file(server.libraries_home, name)   # atomic rename-aside + delete
    utils_lib.git_commit(server.libraries_home, f"delete util {name} via web",
                         paths=[f"utils/{name}"])
    return {"ok": True}


def util_detail(request: Request, name: str) -> dict:
    # reached via /library/{kind}/{slug} with kind="utils" (library_doc_detail dispatches
    # here) — a direct route registration would be shadowed by that pattern anyway
    from .. import utils_lib

    server = request.app.state.server
    content = utils_lib.read_util(server.libraries_home, name)
    if content is None:
        raise HTTPException(404, f"no util {name!r}")
    return {"name": name, "content": content}


class UtilBody(BaseModel):
    content: str
    impact_digest: str | None = None


def put_util(request: Request, name: str, body: UtilBody) -> dict:
    """Edit a global util (selftest-gated, committed) — mirrors the write_util engine action."""
    from .. import sandbox, utils_lib

    server = request.app.state.server
    problems = utils_header.header_problems(body.content)
    if problems:
        raise HTTPException(422, "header problems (not saved): " + "; ".join(problems))
    _require_digest(request, "utils", name, body.content, body.impact_digest)
    utils_lib.ensure_library(server.libraries_home, remote=server.libraries_remote)
    utils_lib.write_util_file(server.libraries_home, name, body.content)
    ok, output = utils_run.selftest(server.libraries_home, name,
                                    policy=sandbox.base_policy(server))
    if not ok:
        raise HTTPException(422, f"selftest failed (not committed):\n{output[:800]}")
    utils_lib.git_commit(server.libraries_home, f"revise {name} via web", paths=[f"utils/{name}"])
    return {"ok": True}


@router.get("/workflows/{slug}")
def workflow_detail(request: Request, slug: str) -> dict:
    home = _home(request)
    # `_workflow_file` returns the path only when it exists, and only ever builds
    # `workflows/<slug>.py` — a pattern is a Python file, so there is no second format to
    # report and no second existence check to make.
    path = _workflow_file(home, slug)
    if path is None:
        raise HTTPException(404, f"no workflow {slug!r}")
    rel = str(path.relative_to(home))
    return {"slug": slug, "content": path.read_text(encoding="utf-8"),
            "log": library.git_log(home, rel)}


class PutBody(BaseModel):
    content: str


@router.put("/workflows/{slug}")
def put_workflow(request: Request, slug: str, body: PutBody) -> dict:
    from .. import library_docs

    home = _home(request)
    server = request.app.state.server
    rules = library_docs.slugs(server.rules_home)
    problems = lint_workflow_py(body.content, filename=f"{slug}.py", rule_slugs=rules)
    if problems:
        raise HTTPException(422, "; ".join(problems))
    rel = f"workflows/{slug}.py"
    atomic_write(home / rel, body.content.rstrip() + "\n")
    library.git_commit(home, f"edit {rel} via web", paths=[rel])
    return {"ok": True, "head": library.head_commit(home)}


@router.delete("/workflows/{slug}")
def delete_workflow(request: Request, slug: str) -> dict:
    """Delete a workflow pattern (committed). Routines materialized from it are untouched —
    they own their recipes. A deleted SEED pattern reappears at the next daemon boot
    (sync_seed_library_docs restores missing seed docs). `clarify-instruction` is
    undeletable: routine creation runs it for every routine.
    """
    if slug == "clarify-instruction":
        raise HTTPException(400, "clarify-instruction cannot be deleted — the new-routine "
                                 "routine creation runs it for every routine")
    home = _home(request)
    path = _workflow_file(home, slug)
    if path is None:
        raise HTTPException(404, f"no workflow {slug!r}")
    path.unlink()
    library.git_commit(home, f"delete workflows/{slug}.py via web",
                       paths=[f"workflows/{slug}.py"])
    return {"ok": True, "head": library.head_commit(home)}

