"""Create a routine directory: workflow REFERENCE (edited in the library), adapted trait
copies, steps/ modules, instruction; its own git repo with the auto-push hook."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from ..config import DEFAULT_BUDGETS, DEFAULT_PERMISSIONS, ServerConfig
from ..ids import is_slug

GITIGNORE = "runs/\ninbox/\nquestions/\n"

PRACTICES_HEADING = "## Standing practices"


def _with_practices_tail(main_body: str, trait_summaries: dict[str, str]) -> str:
    """Guarantee main.md ends with a Standing practices section referencing every trait file —
    the generator is asked to write one, but the reference must survive a forgetful LLM (and
    the no-LLM fallback). One line per trait: file + when to read it."""
    if not trait_summaries:
        return main_body
    if PRACTICES_HEADING.lower() in main_body.lower():
        return main_body
    lines = [f"- `traits/{slug}.md` — {summary or slug.replace('-', ' ')}"
             for slug, summary in trait_summaries.items()]
    tail = [PRACTICES_HEADING, "",
            "These practice modules are this routine's own adapted standards — read each with "
            "read_file before the situation it governs (the routine-improver meta routine "
            "refines them over time):", *lines]
    return main_body.rstrip() + "\n\n" + "\n".join(tail) + "\n"

POST_COMMIT_HOOK = """#!/usr/bin/env bash
# rsched auto-backup — push every commit to origin (best-effort, never blocks the commit).
branch="$(git symbolic-ref --short HEAD 2>/dev/null)" || exit 0
git remote get-url origin >/dev/null 2>&1 || exit 0
out="$(timeout 20 git push --quiet origin "$branch" 2>&1)"; rc=$?
if [ "$rc" -ne 0 ]; then
  printf '[rsched backup] push to origin failed (exit %d)…\\n%s\\n' "$rc" "$out" >&2
fi
exit 0
"""


def scaffold(server: ServerConfig, *, slug: str, name: str, instruction: str,
             workflow_slug: str, cron: str = "", tz: str = "Europe/Berlin",
             description: str = "", models: dict[str, str] | None = None,
             params: dict | None = None, budgets: dict | None = None,
             traits: list[str] | None = None,
             permissions: list[str] | None = None,
             fs_read_roots: list[str] | None = None,
             fs_write_roots: list[str] | None = None,
             steps: dict[str, str] | None = None, enabled: bool = True,
             tags: list[str] | None = None) -> Path:
    """Create ~/routines/<slug>. The workflow is REFERENCED (edited only in the library);
    the routine gets ADAPTED trait copies under traits/ (referenced from main.md's Standing
    practices tail — the routine's own files from then on) + steps/ modules + instruction.
    `permissions` (engine-enforced, user-changeable) go into routine.yaml. A one-line
    `description` (for the UI) is always written, falling back to the name; `models` maps a role
    to a catalog model NAME (else the role falls back to the server system_model)."""
    from .. import library_docs
    from ..config import DEFAULT_TRAITS
    from . import library

    if not is_slug(slug):
        raise ValueError(f"slug {slug!r} is not kebab-case")
    routine_dir = server.routines_home / slug
    if routine_dir.exists():
        raise ValueError(f"routine dir {routine_dir} already exists")

    # traits default to the workflow's `includes` (its suggested practice set), else the
    # standard set; validate against the library. Permissions validate against theirs.
    try:
        meta, _, _ = library.read_workflow(server.library_home, workflow_slug)
    except FileNotFoundError as exc:
        raise ValueError(f"workflow {workflow_slug!r} not found in the library") from exc
    available_traits = set(library_docs.slugs(server.traits_home))
    active_traits = traits if traits is not None else (meta.get("includes") or DEFAULT_TRAITS)
    active_traits = [t for t in active_traits if t in available_traits]
    available_perms = set(library_docs.slugs(server.permissions_home))
    active_perms = permissions if permissions is not None else list(DEFAULT_PERMISSIONS)
    active_perms = [p for p in active_perms if p in available_perms]
    # the activation cascade: the capabilities the chosen conduct docs require, switched
    # on from the start (the user tunes both layers on the routine page afterwards)
    from ..grants import capabilities_for, read_library_requires

    capabilities = capabilities_for(active_perms, read_library_requires(server.permissions_home))
    commit = library.head_commit(server.library_home)

    from . import provenance
    from .adapt import decompose, dump_markdown

    for sub in ("state", "steps", "inbox", "traits"):
        (routine_dir / sub).mkdir(parents=True)
    # DECOMPOSE the single-file workflow (applied to the instruction) into the routine's OWN main.md
    # (entry state machine) + one markdown module per step/state, adapting the selected traits along
    # the way. Self-contained: the library is never read at run time. Degrades to the whole workflow
    # as main.md + verbatim trait copies if no endpoint is available.
    result = decompose(server, workflow_slug, instruction, params=params, traits=active_traits)
    main_meta = {
        "name": name, "slug": slug,
        "materialized_from": {"slug": workflow_slug, "commit": commit, "version": meta.get("version", 0)},
        "modules": sorted(result["modules"]),
        # the workflow's `tools:` allowlist rides along — the engine enforces it per turn
        **({"tools": list(meta["tools"])} if meta.get("tools") is not None else {}),
        **({"tags": list(tags)} if tags else {}),
    }
    # trait copies: the generator's adapted version, else the library text verbatim — either
    # way the routine's OWN files from here on (self-refined, never toggled).
    trait_summaries: dict[str, str] = {}
    for slug_t in active_traits:
        body = (result.get("traits") or {}).get(slug_t)
        if not body:
            raw = library_docs.read_doc(server.traits_home, slug_t)
            body = library_docs.doc_body(raw).strip() if raw else ""
        if not body:
            continue
        (routine_dir / "traits" / f"{slug_t}.md").write_text(body.rstrip() + "\n", encoding="utf-8")
        m = library_docs.DOC_RE.search(body)
        trait_summaries[slug_t] = m.group("summary").strip() if m else ""
    for mod_name, mod_body in result["modules"].items():
        (routine_dir / "steps" / f"{mod_name}.md").write_text(mod_body.rstrip() + "\n", encoding="utf-8")
    # extra purpose-specific step modules from the wizard also land in steps/
    for fname, fcontent in (steps or {}).items():
        safe = fname if fname.endswith(".md") else f"{fname}.md"
        (routine_dir / "steps" / Path(safe).name).write_text(fcontent, encoding="utf-8")
    (routine_dir / "instruction.md").write_text(instruction.rstrip() + "\n", encoding="utf-8")
    # main.md last: stamp the seed ↔ steps provenance baseline over the now-complete steps/
    main_body = _with_practices_tail(result["main"], trait_summaries)
    (routine_dir / "main.md").write_text(
        dump_markdown(provenance.stamp(main_meta, routine_dir=routine_dir,
                                       main_body=main_body, instruction=instruction), main_body),
        encoding="utf-8")
    (routine_dir / "LEDGER.md").write_text(
        f"# LEDGER — {name}\n\n### seed — scaffolded from workflow '{workflow_slug}' @ {commit}\n",
        encoding="utf-8")
    (routine_dir / ".gitignore").write_text(GITIGNORE, encoding="utf-8")

    cfg = {
        "name": name,
        "slug": slug,
        "description": (description or "").strip() or name,
        "enabled": enabled,
        **({"tags": list(tags)} if tags else {}),
        "schedule": {"cron": cron, "tz": tz, "catchup": "skip"},
        "workflow": {"library_slug": workflow_slug, "library_commit": commit},
        **({"models": models} if models else {}),
        "permissions": active_perms,
        "capabilities": capabilities,
        "budgets": {**DEFAULT_BUDGETS, **(budgets or {})},
        "retention": {"keep_runs": 30},
    }
    if fs_read_roots:
        cfg["fs_read_roots"] = [_tilde(p) for p in fs_read_roots]
    if fs_write_roots:
        cfg["fs_write_roots"] = [_tilde(p) for p in fs_write_roots]
    (routine_dir / "routine.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")

    _git_init(routine_dir, f"scaffold {slug} from workflow {workflow_slug}")
    return routine_dir


def _tilde(path: str) -> str:
    """Collapse $HOME → ~ so an absolute path never embeds the account/home-dir name."""
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home) else path


# Neutral identity for managed repos — the user's real name never authors a commit.
GIT_IDENTITY = (("user.name", "routine-scheduler"),
                ("user.email", "noreply@routine-scheduler.local"))


def init_repo(repo_dir: Path, message: str) -> None:
    """git init a managed repo with the neutral identity + best-effort push hook, then
    make the first commit. Shared by routine and util-library scaffolding."""
    try:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir,
                       capture_output=True, timeout=30)
        for key, val in GIT_IDENTITY:
            subprocess.run(["git", "config", key, val], cwd=repo_dir, capture_output=True, timeout=15)
        hook = repo_dir / ".git" / "hooks" / "post-commit"
        hook.write_text(POST_COMMIT_HOOK, encoding="utf-8")
        os.chmod(hook, 0o755)
        subprocess.run(["git", "add", "-A"], cwd=repo_dir, capture_output=True, timeout=30)
        subprocess.run(["git", "commit", "-qm", message], cwd=repo_dir,
                       capture_output=True, timeout=30)
    except OSError:
        pass  # a routine without git still runs; the workflow can init later


def _git_init(routine_dir: Path, message: str) -> None:
    init_repo(routine_dir, message)
