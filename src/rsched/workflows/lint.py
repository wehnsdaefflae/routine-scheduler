"""Workflow/fragment conformance — the gu-lint equivalent for the library.

Library workflows: frontmatter completeness, slug↔filename, resolvable includes, the three
required sections, declared params. Materialized copies: provenance + no unresolved
placeholders. Fragments: titled and non-trivial.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import frontmatter
from ..ids import is_slug
from .library import fragments_dir, list_fragments, list_modules, workflows_dir

REQUIRED_META = ("name", "slug", "description", "when_to_use", "version", "status")
REQUIRED_SECTIONS = ("## Run flow", "## Completion criteria")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")


def lint_workflow_text(raw: str, *, filename: str, fragment_slugs: list[str],
                       module_slugs: list[str] = ()) -> list[str]:
    """Lint a recipe's main.md. `filename` is '<slug>/main.md'; `module_slugs` are the step
    files present under the recipe's steps/ dir."""
    problems: list[str] = []
    meta, body = frontmatter.parse(raw)
    if not meta:
        return [f"{filename}: missing or unparseable YAML frontmatter"]
    for key in REQUIRED_META:
        if key not in meta or meta[key] in (None, ""):
            problems.append(f"{filename}: frontmatter missing {key!r}")
    slug = str(meta.get("slug", ""))
    recipe = filename.split("/")[0]                      # the recipe directory name
    if slug and not is_slug(slug):
        problems.append(f"{filename}: slug {slug!r} is not kebab-case")
    if slug and slug != recipe:
        problems.append(f"{filename}: frontmatter slug {slug!r} does not match recipe dir {recipe!r}")
    if meta.get("status") not in ("stable", "draft"):
        problems.append(f"{filename}: status must be stable|draft")
    params = meta.get("params")
    if params is not None and not isinstance(params, list):
        problems.append(f"{filename}: params must be a list")
    tags = meta.get("tags")
    if tags is not None and not isinstance(tags, list):
        problems.append(f"{filename}: tags must be a list")
    modules = meta.get("modules")
    if modules is not None and not isinstance(modules, list):
        problems.append(f"{filename}: modules must be a list")
    for mod in (modules or []):
        if mod not in module_slugs:
            problems.append(f"{filename}: module {mod!r} does not resolve to steps/{mod}.md")
    for frag in meta.get("includes") or []:
        if frag not in fragment_slugs:
            problems.append(f"{filename}: include {frag!r} does not resolve to fragments/{frag}.md")
    for section in REQUIRED_SECTIONS:
        if section not in body:
            problems.append(f"{filename}: missing section {section!r}")
    declared = set(params or [])
    used = set(PLACEHOLDER_RE.findall(body))
    for undeclared in sorted(used - declared):
        problems.append(f"{filename}: placeholder {{{{{undeclared}}}}} not declared in params")
    return problems


def lint_fragment_text(raw: str, *, filename: str) -> list[str]:
    problems = []
    meta, body = frontmatter.parse(raw)
    if not body.strip().startswith("# fragment:"):
        problems.append(f"{filename}: body must start with '# fragment: <slug> — <summary>' (after any frontmatter)")
    if "tags" in meta and not isinstance(meta.get("tags"), list):
        problems.append(f"{filename}: tags must be a list")
    if len(raw.strip().splitlines()) < 4:
        problems.append(f"{filename}: suspiciously short for a standard practice")
    return problems


def lint_materialized_text(raw: str, *, filename: str = "workflow.md") -> list[str]:
    problems = []
    meta, body = frontmatter.parse(raw)
    prov = meta.get("materialized_from")
    if not isinstance(prov, dict) or "slug" not in prov:
        problems.append(f"{filename}: frontmatter missing materialized_from.slug provenance")
    leftovers = PLACEHOLDER_RE.findall(body)
    if leftovers:
        problems.append(f"{filename}: unresolved placeholders: {sorted(set(leftovers))}")
    for section in ("## Run flow", "## Completion criteria"):
        if section not in body:
            problems.append(f"{filename}: missing section {section!r}")
    return problems


def lint_all(home: Path, fragments_home: Path | None = None) -> dict[str, list[str]]:
    """path-relative-name → problems. Empty lists mean clean. Fragments now live in their own
    library (fragments_home); fall back to the workflow library's fragments/ for legacy setups."""
    from .. import fragments_lib

    results: dict[str, list[str]] = {}
    if fragments_home and fragments_home.is_dir():
        frags = fragments_lib.slugs(fragments_home)
        frag_files = [(f"fragments/{p.name}", p) for p in sorted(fragments_home.glob("*.md"))]
    else:
        frags = list_fragments(home)
        fdir = fragments_dir(home)
        frag_files = [(f"fragments/{p.name}", p) for p in sorted(fdir.glob("*.md"))] if fdir.is_dir() else []
    wdir = workflows_dir(home)
    if wdir.is_dir():
        for sub in sorted(p for p in wdir.iterdir() if p.is_dir()):
            key = f"workflows/{sub.name}/main.md"
            main = sub / "main.md"
            if not main.is_file():
                results[key] = [f"{sub.name}: recipe directory has no main.md"]
                continue
            results[key] = lint_workflow_text(
                main.read_text(encoding="utf-8"), filename=f"{sub.name}/main.md",
                fragment_slugs=frags, module_slugs=list_modules(home, sub.name))
    for name, path in frag_files:
        results[name] = lint_fragment_text(path.read_text(encoding="utf-8"), filename=path.name)
    return results
