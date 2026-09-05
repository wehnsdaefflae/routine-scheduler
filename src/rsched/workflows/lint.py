"""Workflow/rule/permission conformance — the gu-lint equivalent for the library.

Library workflows (Python patterns): META completeness, slug↔filename, resolvable includes,
a main() entry, PHASES, and an action-import line that agrees with the `tools:` allowlist.
Materialized copies: provenance + no unresolved placeholders. Rules: titled principle prose,
no capabilities. Permissions: titled, with a well-formed `requires:` key (the capabilities
their instructions presume — see grants.py). Templates: a settings PRESELECTION whose config
block is complete and adoptable. Global reminders: a well-formed `(regex -> consequence)`.

Everything the library HOLDS is linted, because `lint_all` is what `rsched lint` reports and a
directory it skips is a directory nobody checks. Two arrived after the first four and were not
added here — the templates a routine is created from, and the shared reminder store — so a
malformed one of either was found by whatever read it next.
"""

from __future__ import annotations

from pathlib import Path

import frontmatter
import yaml

from ..ids import is_slug
from .library import permissions_dir, rules_dir, workflows_dir


def lint_workflow_py(source: str, *, filename: str, rule_slugs: list[str]) -> list[str]:
    """Validate a Python-workflow file: parseable, META completeness, slug↔filename, resolvable
    includes, a main() entry, PHASES, and an action-import line consistent with `tools:`.
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
    problems += _tools_problems(meta, filename)
    return problems


def _tools_problems(meta: dict, filename: str) -> list[str]:
    """The `tools:` allowlist against the action kinds the pattern's import line names.

    `kindsurface.effective_kinds` NARROWS the schema to the allowlist, so a kind the pattern
    imports but `tools:` excludes is prose describing a channel the model cannot emit —
    improvement-proposer imported write_util/spawn/wait while declaring itself record-only.
    The import line is the pattern's own claim about what it uses, so the two must agree.
    """
    from ..engine.actions import ALWAYS_KINDS, KINDS

    problems: list[str] = []
    imported = [n for n in meta.get("action_imports") or [] if n in KINDS]
    tools = meta.get("tools")
    if tools is None:
        return problems
    if not isinstance(tools, list):
        return [f"{filename}: tools must be a list of action kinds"]
    unknown = [t for t in tools if t not in KINDS]
    if unknown:
        problems.append(f"{filename}: tools names unknown action kind(s) "
                        f"{unknown} — the vocabulary is engine/actionschema.KINDS")
    stray = [n for n in imported if n not in tools and n not in ALWAYS_KINDS]
    if stray:
        problems.append(f"{filename}: imports action kind(s) {stray} that tools: excludes — "
                        "the schema is narrowed to tools, so the pattern would describe "
                        "channels the run cannot emit. Trim the import or widen tools")
    return problems


def lint_rule_text(raw: str, *, filename: str) -> list[str]:
    """A rule is pure principle prose: titled, tagged, non-trivial — and NEVER carries
    capabilities (requires belongs to permissions; a rule carrying one would silently do
    nothing, which is worse than an error).

    It MAY carry `expects:` — the soft edge. A rule cannot switch a capability on, but it has
    to be able to say what it presumes: `status-page` tells a run to publish, which is useless
    without a write root to publish into. That edge grants nothing and blocks nothing; it is
    read by the setup resolver so the gap is visible before a run hits it.
    """
    from ..assists import normalize_assists
    from ..grants import normalize_expects
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
                        "capability to a permission doc under permissions/ (a rule may "
                        "declare `expects:`, which presumes without granting)")
    problems += [f"{filename}: {p}" for p in normalize_expects(meta.get("expects"))[1]]
    tags = meta.get("tags")
    tag_list = tags if isinstance(tags, list) else []
    if "tags" in meta and not isinstance(tags, list):
        problems.append(f"{filename}: tags must be a list")
    elif len([t for t in tag_list if isinstance(t, str) and t.strip()]) < 3:
        problems.append(f"{filename}: needs at least 3 tags")
    if len(raw.strip().splitlines()) < 4:
        problems.append(f"{filename}: suspiciously short for a general rule")
    problems += [f"{filename}: {p}" for p in normalize_assists(meta.get("assists"))[1]]
    problems += _effect_problems(meta, filename, "the run reads it and applies it")
    return problems


def _effect_problems(meta: dict, filename: str, subject: str) -> list[str]:
    """Every conduct doc must state three things about the toggle that holds it: what the
    routine does WITH it (`on`), what it does WITHOUT it (`off`), and when to hold it (`when`).

    None of them can be recovered from what a doc already has. The title names a topic ("ask
    policy — when and how to involve the user"), which tells a reader nothing they can act on;
    the body is written to the RUN in the imperative ("read the error before you try again"),
    which is an instruction for the agent and not a description for the person choosing. A
    toggle is a COMPARISON, so both sides have to be on the page, and the decision it actually
    asks — is this one for THIS routine? — is what `when` answers (operator, 2026-08-30).

    A machine can only check presence and a length floor; whether a sentence is really a
    behaviour is the author's job. `off` is checked against `on` because the failure mode is
    writing the same sentence twice with a negation and calling it a contrast.
    """
    from ..library_docs import EFFECT_FIELDS

    eff = meta.get("effect")
    if not isinstance(eff, dict):
        return [f"{filename}: needs an effect: block with {list(EFFECT_FIELDS)} — what the "
                f"routine does with this ({subject}), what it does without it, and when to "
                "hold it. The title and the body cannot stand in: one names a topic, the "
                "other instructs the run"]
    problems = []
    for key in EFFECT_FIELDS:
        text = str(eff.get(key) or "").strip()
        if not text:
            problems.append(f"{filename}: effect.{key} is missing")
        elif len(text) < 20:
            problems.append(f"{filename}: effect.{key} is too short to say what changes "
                            f"({text!r})")
    a, b = str(eff.get("with") or "").strip(), str(eff.get("without") or "").strip()
    if a and a == b:
        problems.append(f"{filename}: effect.with and effect.without are the same sentence — "
                        "the row exists to show the DIFFERENCE between holding it and not")
    return problems


def lint_template_text(raw: str, *, filename: str) -> list[str]:
    """A settings template: titled, tagged, and carrying a `config:` block restricted to the
    keys a DOMAIN may share — one vocabulary for both layers, so "where do I set this?" has one
    answer. The named permissions and rules must exist; a template pointing at a doc the
    library lost would silently give its adopters nothing.
    """
    from ..domains import CONFIG_KEYS
    from ..grants import normalize_capabilities

    problems: list[str] = []
    try:
        meta, body = frontmatter.parse(raw)
    except yaml.YAMLError as exc:
        return [f"{filename}: invalid YAML frontmatter: {exc}"]
    if not body.strip().startswith("# template:"):
        problems.append(f"{filename}: body must start with '# template: <name> — <summary>'")
    tags = meta.get("tags")
    if len([t for t in (tags if isinstance(tags, list) else []) if str(t).strip()]) < 3:
        problems.append(f"{filename}: needs at least 3 tags")
    config = meta.get("config")
    if not isinstance(config, dict) or not config:
        problems.append(f"{filename}: needs a non-empty config: block — a template that "
                        "carries nothing is a name with no meaning")
        return problems
    problems += [f"{filename}: config.{k}: not a shareable key "
                 f"(expected one of {', '.join(CONFIG_KEYS)})"
                 for k in config if k not in CONFIG_KEYS]
    if "capabilities" in config:
        problems += [f"{filename}: {p}" for p in
                     normalize_capabilities(config["capabilities"],
                                            label="config.capabilities")[1]]
    if "template" in config:
        problems.append(f"{filename}: a template cannot name another template")
    return problems


def lint_permission_text(raw: str, *, filename: str) -> list[str]:
    """A permission is a conduct doc: titled, with a well-formed `requires:` key naming
    the capabilities its instructions presume, and a SHORT body (it doubles as the
    prompt's capability note when held).

    `expects:` is the optional counterpart: entities the conduct presumes but nothing
    enforces — a bound machine for remote-machines, a session store for a messenger. It is
    validated here so a typo is caught at authoring time, never at 3am in a run.
    """
    from ..grants import normalize_capabilities, normalize_expects

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
    problems += [f"{filename}: {p}" for p in normalize_expects(meta.get("expects"))[1]]
    problems += _effect_problems(meta, filename, "the routine can do the thing")
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


def lint_global_reminder(raw: str, *, filename: str) -> list[str]:
    """A curated reminder in the shared store: parseable JSON with an id matching its
    filename, a compilable non-empty-matching regex, and a consequence to state.

    The same validators the `remind` action runs, applied to what is already on disk — a
    reminder is written through an approval, and an approval is not a syntax check.
    """
    import json

    from ..reminders import ID_RE, description_problem, regex_problem

    try:
        rec = json.loads(raw)
    except ValueError as exc:
        return [f"{filename}: invalid JSON: {exc}"]
    if not isinstance(rec, dict):
        return [f"{filename}: must be a JSON object"]
    problems: list[str] = []
    rid = str(rec.get("id") or "")
    if not ID_RE.match(rid):
        problems.append(f"{filename}: id {rid!r} is not a reminder id (rem-<slug>)")
    elif f"{rid}.json" != filename:
        problems.append(f"{filename}: id {rid!r} does not match the filename")
    if problem := regex_problem(rec.get("regex")):
        problems.append(f"{filename}: {problem}")
    if problem := description_problem(rec.get("description")):
        problems.append(f"{filename}: {problem}")
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
    from .. import templates
    tdir = templates.templates_home(home)
    if tdir.is_dir():
        for path in sorted(tdir.glob("*.md")):
            results[f"templates/{path.name}"] = lint_template_text(
                path.read_text(encoding="utf-8"), filename=path.name)
    from .. import reminders
    remdir = reminders.reminders_home(home)
    if remdir.is_dir():
        for path in sorted(remdir.glob("*.json")):
            results[f"reminders/{path.name}"] = lint_global_reminder(
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
