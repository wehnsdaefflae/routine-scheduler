"""Workflow/rule/permission conformance — the gu-lint equivalent for the library.

Library workflows (Python patterns): META completeness, slug↔filename, resolvable includes,
a main() entry, PHASES/COMPLETION. Materialized copies: provenance + no unresolved
placeholders. Rules: titled principle prose, no capabilities. Permissions: titled, with a
well-formed `requires:` key (the capabilities their instructions presume — see grants.py).
"""

from __future__ import annotations

from pathlib import Path

import frontmatter
import yaml

from ..ids import is_slug
from .library import permissions_dir, rules_dir, workflows_dir


def lint_workflow_py(source: str, *, filename: str, rule_slugs: list[str]) -> list[str]:
    """Validate a Python-workflow file: parseable, META completeness, slug↔filename, resolvable
    includes, a main() entry, and PHASES/COMPLETION (the Python equivalents of the required
    sections).
    """
    from .pyworkflow import REQUIRED_META, parse_py

    try:
        meta = parse_py(source)
    except SyntaxError as exc:
        return [f"{filename}: invalid Python ({exc.msg} at line {exc.lineno})"]
    except ValueError as exc:
        return [f"{filename}: {exc}"]
    problems: list[str] = [f"{filename}: META missing {key!r}"
                           for key in REQUIRED_META
                           if key not in meta or meta[key] in (None, "")]
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
    problems.extend(f"{filename}: include {rule!r} does not resolve to rules/{rule}.md"
                    for rule in meta.get("includes") or [] if rule not in rule_slugs)
    if not meta.get("has_main"):
        problems.append(f"{filename}: no top-level main() function (the per-run control flow)")
    if not meta.get("phases"):
        problems.append(f"{filename}: missing PHASES (the cross-run progression)")
    if not str(meta.get("completion") or "").strip():
        problems.append(f"{filename}: missing COMPLETION (done-for-run / done-overall)")
    tools = meta.get("tools")
    if tools is not None:
        from ..engine.actions import KINDS

        if not isinstance(tools, list):
            problems.append(f"{filename}: tools must be a list of action kinds")
        else:
            unknown = [t for t in tools if t not in KINDS]
            if unknown:
                problems.append(f"{filename}: tools names unknown action kind(s) "
                                f"{unknown} — the vocabulary is engine/actionschema.KINDS")
    return problems


def lint_rule_text(raw: str, *, filename: str) -> list[str]:
    """A rule is pure principle prose: titled, tagged, non-trivial — and NEVER carries
    capabilities (requires belongs to permissions; a rule carrying one would silently do
    nothing, which is worse than an error).
    """
    problems: list[str] = []
    try:
        meta, body = frontmatter.parse(raw)
    except yaml.YAMLError as exc:
        return [f"{filename}: invalid YAML frontmatter: {exc}"]
    if not body.strip().startswith("# rule:"):
        problems.append(f"{filename}: body must start with '# rule: <name> — <summary>' "
                        "(after any frontmatter)")
    if "grants" in meta or "requires" in meta:
        problems.append(f"{filename}: rules must not carry grants/requires — move the "
                        "capability to a permission doc under permissions/")
    tags = meta.get("tags")
    tag_list = tags if isinstance(tags, list) else []
    if "tags" in meta and not isinstance(tags, list):
        problems.append(f"{filename}: tags must be a list")
    elif len([t for t in tag_list if isinstance(t, str) and t.strip()]) < 3:
        problems.append(f"{filename}: needs at least 3 tags")
    if len(raw.strip().splitlines()) < 4:
        problems.append(f"{filename}: suspiciously short for a general rule")
    return problems


def lint_permission_text(raw: str, *, filename: str) -> list[str]:
    """A permission is a conduct doc: titled, with a well-formed `requires:` key naming
    the capabilities its instructions presume, and a SHORT body (it doubles as the
    prompt's capability note when held).
    """
    from ..grants import normalize_capabilities

    problems = []
    try:
        meta, body = frontmatter.parse(raw)
    except yaml.YAMLError as exc:
        return [f"{filename}: invalid YAML frontmatter: {exc}"]
    if not body.strip().startswith("# permission:"):
        problems.append(f"{filename}: body must start with '# permission: <name> — <summary>' "
                        "(after any frontmatter)")
    if "grants" in meta:
        problems.append(f"{filename}: grants: was renamed — permissions declare requires: "
                        "(the capabilities their instructions presume); the capabilities "
                        "themselves are per-routine config now")
    # The key must be PRESENT — declaring "this presumes nothing" is a decision, forgetting
    # it is a bug. An explicitly EMPTY `requires: {}` is legitimate: a conduct doc may teach
    # an UNGATED mechanism (global-utils covers the `util` base kind). Principle prose that
    # names no mechanism at all belongs in a rule, not here.
    if "requires" not in meta:
        problems.append(f"{filename}: a permission must carry a requires: key naming the "
                        "capabilities its instructions presume (use `requires: {}` when it "
                        "presumes none; pure principle prose belongs in a rule)")
    else:
        _req, req_problems = normalize_capabilities(meta["requires"], label="requires",
                                                    requires=True)
        problems += [f"{filename}: {p}" for p in req_problems]
    return problems


def lint_playbook_text(raw: str, *, filename: str = "MAIN.md") -> list[str]:
    """A playbook's MAIN.md: front matter (slug/title/one-line when/tags/axis) + an imperative
    '## Instructions' body. A reusable conversation brief, not a control-flow pattern.
    """
    problems: list[str] = []
    try:
        meta, body = frontmatter.parse(raw)
    except yaml.YAMLError as exc:
        return [f"{filename}: invalid YAML frontmatter: {exc}"]
    problems.extend(f"{filename}: front matter missing {key!r}"
                    for key in ("slug", "title", "when", "axis")
                    if not str(meta.get(key) or "").strip())
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


def lint_all(home: Path) -> dict[str, list[str]]:
    """path-relative-name → problems. Empty lists mean clean. `home` is the library repo root
    (workflows/, rules/ and permissions/ subdirs).
    """
    from .. import library_docs

    results: dict[str, list[str]] = {}
    rdir, pdir = rules_dir(home), permissions_dir(home)
    rules = library_docs.slugs(rdir)
    wdir = workflows_dir(home)
    if wdir.is_dir():
        for path in sorted(wdir.glob("*.py")):
            results[f"workflows/{path.name}"] = lint_workflow_py(
                path.read_text(encoding="utf-8"), filename=path.name, rule_slugs=rules)
    if rdir.is_dir():
        for path in sorted(rdir.glob("*.md")):
            results[f"rules/{path.name}"] = lint_rule_text(
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
