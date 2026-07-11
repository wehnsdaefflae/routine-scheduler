"""Workflow/fragment conformance — the gu-lint equivalent for the library.

Library workflows (Python patterns): META completeness, slug↔filename, resolvable includes,
a main() entry, PHASES/COMPLETION. Materialized copies: provenance + no unresolved
placeholders. Fragments: titled, non-trivial, and a well-formed `grants:` key (the
machine-enforced capability side — see grants.py).
"""

from __future__ import annotations

import re
from pathlib import Path

import frontmatter
import yaml

from ..ids import is_slug
from .library import fragments_dir, workflows_dir

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")


def lint_workflow_py(source: str, *, filename: str, fragment_slugs: list[str]) -> list[str]:
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
    if meta.get("status") not in ("stable", "draft"):
        problems.append(f"{filename}: status must be stable|draft")
    tags = meta.get("tags")
    if tags is not None and not isinstance(tags, list):
        problems.append(f"{filename}: tags must be a list")
    elif len([t for t in (tags or []) if isinstance(t, str) and t.strip()]) < 3:
        problems.append(f"{filename}: needs at least 3 tags")
    for frag in meta.get("includes") or []:
        if frag not in fragment_slugs:
            problems.append(f"{filename}: include {frag!r} does not resolve to fragments/{frag}.md")
    if not meta.get("has_main"):
        problems.append(f"{filename}: no top-level main() function (the per-run control flow)")
    if not meta.get("phases"):
        problems.append(f"{filename}: missing PHASES (the cross-run progression)")
    if not str(meta.get("completion") or "").strip():
        problems.append(f"{filename}: missing COMPLETION (done-for-run / done-overall)")
    return problems


def lint_fragment_text(raw: str, *, filename: str) -> list[str]:
    from ..grants import normalize_grants

    problems = []
    try:
        meta, body = frontmatter.parse(raw)
    except yaml.YAMLError as exc:
        return [f"{filename}: invalid YAML frontmatter: {exc}"]
    if not body.strip().startswith("# fragment:"):
        problems.append(f"{filename}: body must start with '# fragment: <slug> — <summary>' (after any frontmatter)")
    if "grants" in meta:
        problems += [f"{filename}: {p}" for p in normalize_grants(meta["grants"])[1]]
    tags = meta.get("tags")
    if "tags" in meta and not isinstance(tags, list):
        problems.append(f"{filename}: tags must be a list")
    elif len([t for t in (tags or []) if isinstance(t, str) and t.strip()]) < 3:
        problems.append(f"{filename}: needs at least 3 tags")
    if len(raw.strip().splitlines()) < 4:
        problems.append(f"{filename}: suspiciously short for a standard practice")
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
    (workflows/ and fragments/ subdirs)."""
    from .. import fragments_lib

    results: dict[str, list[str]] = {}
    fdir = fragments_dir(home)
    frags = fragments_lib.slugs(fdir)
    wdir = workflows_dir(home)
    if wdir.is_dir():
        for path in sorted(wdir.glob("*.py")):
            results[f"workflows/{path.name}"] = lint_workflow_py(
                path.read_text(encoding="utf-8"), filename=path.name, fragment_slugs=frags)
    if fdir.is_dir():
        for path in sorted(fdir.glob("*.md")):
            results[f"fragments/{path.name}"] = lint_fragment_text(
                path.read_text(encoding="utf-8"), filename=path.name)
    return results
