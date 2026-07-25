"""Workflow/trait/permission conformance — the gu-lint equivalent for the library.

Library workflows (Python patterns): META completeness, slug↔filename, resolvable includes,
a main() entry, PHASES/COMPLETION. Materialized copies: provenance + no unresolved
placeholders. Traits: titled practice prose, no capabilities. Permissions: titled, with a
well-formed `requires:` key (the capabilities their instructions presume — see grants.py).
Recipes, patterns and traits alike: no named utils (see `named_utils` below).
"""

from __future__ import annotations

import re
from pathlib import Path

import frontmatter
import yaml

from ..ids import is_slug
from .library import permissions_dir, traits_dir, workflows_dir

# Util names that also read as an ordinary word, a service or a protocol — a recipe may
# legitimately name the SERVICE it works with ("read the newsletters from Gmail", "the shell
# permission", "over FTP"). Those are flagged only when written as an INVOCATION. Every other
# util name is coined (`static-publish`, `codemap`, `jsonblob`) and has no innocent reading, so
# it is flagged anywhere. A new util defaults to coined — the strict side, because a false
# positive is loud and one edit away while a false negative silently permits the thing the rule
# exists to stop. Add a name here only with the reason it is ambiguous.
AMBIGUOUS_UTIL_NAMES = frozenset({
    "claude",    # the model/company, named constantly in prose
    "discord",   # the service
    "ftp",       # the protocol
    "gmail",     # the service
    "notion",    # the service
    "remote",    # ordinary word ("the remote machine")
    "shell",     # ordinary word, AND a permission name, AND a reserved util
    "sym",       # ordinary abbreviation ("sym link", "symbol")
    "ted",       # ordinary word / name
    "vision",    # ordinary word ("a vision model", "multimodal vision")
    "zulip",     # the service
})


def named_utils(text: str, util_names: list[str]) -> list[str]:
    """The utils `text` names, in the order found — empty when it names none.

    A recipe, pattern or trait describes the WORK, never the toolbox: it names the CAPABILITY a
    step needs and leaves the tool to the run, which is shown the live util catalog in its
    CAPABILITIES prompt section and records what worked in its own memory. A tool named in prose
    is stale the day it is renamed or removed, and it stops the run from discovering a better
    one. The `util` ACTION is part of the action schema and is not a tool choice — only a
    specific util's identity is.
    """
    found: list[str] = []
    for name in sorted(set(util_names)):
        esc = re.escape(name)
        # Invocation shapes, all on one line: the shell form `gu <name>`; the action form
        # `util name=<name>` / `{"kind": "util", "name": "<name>"}`; and the English form
        # "the <name> util" (backticks and quotes around the name are common, hence \W{0,2}).
        patterns = [rf"\bgu\s+{esc}\b",
                    rf"\butil\b[^\n]{{0,20}}\bname\b[^\n]{{0,8}}{esc}\b",
                    rf"\b(?:the|a|an)\s+\W{{0,2}}{esc}\W{{0,2}}\s+util\b"]
        if name not in AMBIGUOUS_UTIL_NAMES:
            # A bare mention — but NOT one inside a path. Some artefacts are named after the tool
            # that writes them (`<repo>/.codemap/`, `.control/health-events.jsonl`), and a path is
            # a TASK fact the recipe must be free to state exactly.
            patterns.append(rf"(?<![./]){esc}\b")
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            found.append(name)
    return found


def _no_named_utils(text: str, *, filename: str, util_names: list[str]) -> list[str]:
    """`named_utils` as a lint problem line (empty when clean)."""
    named = named_utils(text, util_names)
    if not named:
        return []
    return [f"{filename}: names util(s) {named} — a recipe says WHAT to do, never which tool. "
            "Name the capability the step needs; the run picks the tool from its CAPABILITIES "
            "catalog and records what worked in its own memory"]


def lint_workflow_py(source: str, *, filename: str, trait_slugs: list[str],
                     util_names: list[str] | None = None) -> list[str]:
    """Validate a Python-workflow file: parseable, META completeness, slug↔filename, resolvable
    includes, a main() entry, and PHASES/COMPLETION (the Python equivalents of the required
    sections), and that it names no util.
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
    problems.extend(f"{filename}: include {trait!r} does not resolve to traits/{trait}.md"
                    for trait in meta.get("includes") or [] if trait not in trait_slugs)
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
                                f"{unknown} — the vocabulary is engine/actions.KINDS")
    problems += _no_named_utils(source, filename=filename, util_names=util_names or [])
    return problems


def lint_trait_text(raw: str, *, filename: str, util_names: list[str] | None = None) -> list[str]:
    """A trait is pure practice prose: titled, tagged, non-trivial — and NEVER carries
    capabilities (requires belongs to permissions; a trait carrying one would silently do
    nothing, which is worse than an error) or a named util.
    """
    problems = _no_named_utils(raw, filename=filename, util_names=util_names or [])
    try:
        meta, body = frontmatter.parse(raw)
    except yaml.YAMLError as exc:
        return [f"{filename}: invalid YAML frontmatter: {exc}"]
    if not body.strip().startswith("# trait:"):
        problems.append(f"{filename}: body must start with '# trait: <name> — <summary>' "
                        "(after any frontmatter)")
    if "grants" in meta or "requires" in meta:
        problems.append(f"{filename}: traits must not carry grants/requires — move the "
                        "capability to a permission doc under permissions/")
    tags = meta.get("tags")
    tag_list = tags if isinstance(tags, list) else []
    if "tags" in meta and not isinstance(tags, list):
        problems.append(f"{filename}: tags must be a list")
    elif len([t for t in tag_list if isinstance(t, str) and t.strip()]) < 3:
        problems.append(f"{filename}: needs at least 3 tags")
    if len(raw.strip().splitlines()) < 4:
        problems.append(f"{filename}: suspiciously short for a practice module")
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


def lint_playbook_text(raw: str, *, filename: str = "MAIN.md",
                       util_names: list[str] | None = None) -> list[str]:
    """A playbook's MAIN.md: front matter (slug/title/one-line when/tags/axis) + an imperative
    '## Instructions' body. It is a reusable conversation brief, not a control-flow pattern —
    and, like a recipe, it names capabilities rather than utils.
    """
    problems: list[str] = _no_named_utils(raw, filename=filename, util_names=util_names or [])
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


def lint_recipe_text(raw: str, *, filename: str, util_names: list[str]) -> list[str]:
    """A materialized routine recipe — `main.md`, a `stages/` module, or the routine's own copy
    of a trait. The library linters above never see these: a recipe is generated per routine and
    lives in the routine's own repo, so this is the only place the no-named-utils rule reaches
    the documents an actual run reads.
    """
    return _no_named_utils(raw, filename=filename, util_names=util_names)


def lint_routine(routine_dir: Path, util_names: list[str]) -> dict[str, list[str]]:
    """path-relative-name → problems for one routine's recipe (main.md + stages/ + traits/).
    Empty lists mean clean. `state/` and `.memory/` are deliberately NOT linted: naming the tool
    that worked is exactly what a routine's memory is FOR.
    """
    results: dict[str, list[str]] = {}
    main = routine_dir / "main.md"
    if main.is_file():
        results["main.md"] = lint_recipe_text(main.read_text(encoding="utf-8"),
                                              filename="main.md", util_names=util_names)
    for sub in ("stages", "traits"):
        d = routine_dir / sub
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.md")):
            rel = f"{sub}/{path.name}"
            results[rel] = lint_recipe_text(path.read_text(encoding="utf-8"),
                                            filename=rel, util_names=util_names)
    return results


def lint_all(home: Path) -> dict[str, list[str]]:
    """path-relative-name → problems. Empty lists mean clean. `home` is the library repo root
    (workflows/, traits/ and permissions/ subdirs).
    """
    from .. import library_docs, utils_lib

    results: dict[str, list[str]] = {}
    tdir, pdir = traits_dir(home), permissions_dir(home)
    traits = library_docs.slugs(tdir)
    utils = [u["name"] for u in utils_lib.list_utils(home)]
    wdir = workflows_dir(home)
    if wdir.is_dir():
        for path in sorted(wdir.glob("*.py")):
            results[f"workflows/{path.name}"] = lint_workflow_py(
                path.read_text(encoding="utf-8"), filename=path.name, trait_slugs=traits,
                util_names=utils)
    if tdir.is_dir():
        for path in sorted(tdir.glob("*.md")):
            results[f"traits/{path.name}"] = lint_trait_text(
                path.read_text(encoding="utf-8"), filename=path.name, util_names=utils)
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
                    main.read_text(encoding="utf-8"), filename=f"{sub.name}/MAIN.md",
                    util_names=utils)
    return results
