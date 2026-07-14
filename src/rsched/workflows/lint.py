"""Workflow/trait/permission conformance — the gu-lint equivalent for the library.

Library workflows (Python patterns): META completeness, slug↔filename, resolvable includes,
a main() entry, PHASES/COMPLETION. Materialized copies: provenance + no unresolved
placeholders. Traits: titled practice prose, no capabilities. Permissions: titled, with a
well-formed `requires:` key (the capabilities their instructions presume — see grants.py).
"""

from __future__ import annotations

import re
from pathlib import Path

import frontmatter
import yaml

from ..ids import is_slug
from .library import permissions_dir, traits_dir, workflows_dir

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")


def lint_workflow_py(source: str, *, filename: str, trait_slugs: list[str]) -> list[str]:
    """Validate a Python-workflow file: parseable, META completeness, slug↔filename, resolvable
    includes, a main() entry, and PHASES/COMPLETION (the Python equivalents of the required sections)."""
    from .pyworkflow import REQUIRED_META, parse_py

    try:
        meta = parse_py(source)
    except SyntaxError as exc:
        return [f"{filename}: invalid Python ({exc.msg} at line {exc.lineno})"]
    except ValueError as exc:
        return [f"{filename}: {exc}"]
    problems: list[str] = []
    for key in REQUIRED_META:
        if key not in meta or meta[key] in (None, ""):
            problems.append(f"{filename}: META missing {key!r}")
    slug = str(meta.get("slug", ""))
    if slug and not is_slug(slug):
        problems.append(f"{filename}: slug {slug!r} is not kebab-case")
    if slug and filename != f"{slug}.py":
        problems.append(f"{filename}: filename does not match slug {slug!r}")
    tags = meta.get("tags")
    if tags is not None and not isinstance(tags, list):
        problems.append(f"{filename}: tags must be a list")
    elif len([t for t in (tags or []) if isinstance(t, str) and t.strip()]) < 3:
        problems.append(f"{filename}: needs at least 3 tags")
    for trait in meta.get("includes") or []:
        if trait not in trait_slugs:
            problems.append(f"{filename}: include {trait!r} does not resolve to traits/{trait}.md")
    if not meta.get("has_main"):
        problems.append(f"{filename}: no top-level main() function (the per-run control flow)")
    if not meta.get("phases"):
        problems.append(f"{filename}: missing PHASES (the cross-run progression)")
    if not str(meta.get("completion") or "").strip():
        problems.append(f"{filename}: missing COMPLETION (done-for-run / done-overall)")
    return problems


def lint_trait_text(raw: str, *, filename: str) -> list[str]:
    """A trait is pure practice prose: titled, tagged, non-trivial — and NEVER carries
    capabilities (requires belongs to permissions; a trait carrying one would silently do
    nothing, which is worse than an error)."""
    problems = []
    try:
        meta, body = frontmatter.parse(raw)
    except yaml.YAMLError as exc:
        return [f"{filename}: invalid YAML frontmatter: {exc}"]
    if not body.strip().startswith("# trait:"):
        problems.append(f"{filename}: body must start with '# trait: <name> — <summary>' (after any frontmatter)")
    if "grants" in meta or "requires" in meta:
        problems.append(f"{filename}: traits must not carry grants/requires — move the "
                        "capability to a permission doc under permissions/")
    tags = meta.get("tags")
    if "tags" in meta and not isinstance(tags, list):
        problems.append(f"{filename}: tags must be a list")
    elif len([t for t in (tags or []) if isinstance(t, str) and t.strip()]) < 3:
        problems.append(f"{filename}: needs at least 3 tags")
    if len(raw.strip().splitlines()) < 4:
        problems.append(f"{filename}: suspiciously short for a practice module")
    return problems


def lint_permission_text(raw: str, *, filename: str) -> list[str]:
    """A permission is a conduct doc: titled, with a well-formed `requires:` key naming
    the capabilities its instructions presume, and a SHORT body (it doubles as the
    prompt's capability note when held)."""
    from ..grants import normalize_capabilities

    problems = []
    try:
        meta, body = frontmatter.parse(raw)
    except yaml.YAMLError as exc:
        return [f"{filename}: invalid YAML frontmatter: {exc}"]
    if not body.strip().startswith("# permission:"):
        problems.append(f"{filename}: body must start with '# permission: <name> — <summary>' (after any frontmatter)")
    if "grants" in meta:
        problems.append(f"{filename}: grants: was renamed — permissions declare requires: "
                        "(the capabilities their instructions presume); the capabilities "
                        "themselves are per-routine config now")
    if "requires" not in meta:
        problems.append(f"{filename}: a permission must carry a requires: key naming the "
                        "capabilities its instructions presume (pure prose belongs in a trait)")
    else:
        req, req_problems = normalize_capabilities(meta["requires"], label="requires",
                                                   requires=True)
        problems += [f"{filename}: {p}" for p in req_problems]
        if not req and not req_problems:
            problems.append(f"{filename}: requires: is empty")
    return problems


def lint_playbook_text(raw: str, *, filename: str = "MAIN.md") -> list[str]:
    """A playbook's MAIN.md: front matter (slug/title/one-line when/tags/axis) + an imperative
    '## Instructions' body. It is a reusable conversation brief, not a control-flow pattern."""
    problems = []
    try:
        meta, body = frontmatter.parse(raw)
    except yaml.YAMLError as exc:
        return [f"{filename}: invalid YAML frontmatter: {exc}"]
    for key in ("slug", "title", "when", "axis"):
        if not str(meta.get(key) or "").strip():
            problems.append(f"{filename}: front matter missing {key!r}")
    slug = str(meta.get("slug") or "")
    if slug and not is_slug(slug):
        problems.append(f"{filename}: slug {slug!r} is not kebab-case")
    if "\n" in str(meta.get("when") or "").strip():
        problems.append(f"{filename}: 'when' must be a single line (the catalog entry)")
    tags = meta.get("tags")
    if not isinstance(tags, list) or not [t for t in tags if str(t).strip()]:
        problems.append(f"{filename}: needs at least one tag")
    if "## Instructions" not in body:
        problems.append(f"{filename}: body must have an '## Instructions' section")
    return problems


def lint_materialized_text(raw: str, *, filename: str = "main.md") -> list[str]:
    problems = []
    try:
        meta, body = frontmatter.parse(raw)
    except yaml.YAMLError as exc:
        return [f"{filename}: invalid YAML frontmatter: {exc}"]
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


def lint_all(home: Path) -> dict[str, list[str]]:
    """path-relative-name → problems. Empty lists mean clean. `home` is the library repo root
    (workflows/, traits/ and permissions/ subdirs)."""
    from .. import library_docs

    results: dict[str, list[str]] = {}
    tdir, pdir = traits_dir(home), permissions_dir(home)
    traits = library_docs.slugs(tdir)
    wdir = workflows_dir(home)
    if wdir.is_dir():
        for path in sorted(wdir.glob("*.py")):
            results[f"workflows/{path.name}"] = lint_workflow_py(
                path.read_text(encoding="utf-8"), filename=path.name, trait_slugs=traits)
    if tdir.is_dir():
        for path in sorted(tdir.glob("*.md")):
            results[f"traits/{path.name}"] = lint_trait_text(
                path.read_text(encoding="utf-8"), filename=path.name)
    if pdir.is_dir():
        for path in sorted(pdir.glob("*.md")):
            results[f"permissions/{path.name}"] = lint_permission_text(
                path.read_text(encoding="utf-8"), filename=path.name)
    from .. import playbooks
    pbdir = playbooks.playbooks_dir(home)
    if pbdir.is_dir():
        for sub in sorted(p for p in pbdir.iterdir() if p.is_dir()):
            main = sub / playbooks.MAIN
            if main.is_file():
                results[f"playbooks/{sub.name}/MAIN.md"] = lint_playbook_text(
                    main.read_text(encoding="utf-8"), filename=f"{sub.name}/MAIN.md")
    return results
