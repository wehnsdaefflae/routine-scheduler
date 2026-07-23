"""Create a routine directory: workflow REFERENCE (edited in the library), adapted trait
copies, stages/ modules; its own git repo with the auto-push hook.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from ..config import (
    DEFAULT_BUDGETS,
    DEFAULT_DELIBERATION,
    DEFAULT_PERMISSIONS,
    DELIBERATION_LEVELS,
    ServerConfig,
    write_tuning,
)
from ..ids import is_slug

GITIGNORE = "runs/\ninbox/\nquestions/\nmnt/\n"   # mnt/ = transient remote-machine share mounts

PRACTICES_HEADING = "## Standing practices"
TAIL_LEAD = ("These practice modules are this routine's own standards — read each with "
             "read_file before the situation it governs (the routine-improver meta routine "
             "refines them over time):")


def render_practices_tail(trait_lines: list[str]) -> str:
    """The Standing-practices section body — heading, lead, one line per trait. The ONE
    place the tail's shape lives: scaffold/conversation creation appends it, traits.py's
    post-creation resync rebuilds it (two drifted copies of the lead once disagreed).
    """
    return "\n".join([PRACTICES_HEADING, "", TAIL_LEAD, *trait_lines])


def trait_line(slug: str, summary: str) -> str:
    return f"- `traits/{slug}.md` — {summary or slug.replace('-', ' ')}"


def with_practices_tail(main_body: str, trait_summaries: dict[str, str]) -> str:
    """Guarantee main.md ends with a Standing practices section referencing every trait file —
    the generator is asked to write one, but the reference must survive a forgetful LLM (and
    the no-LLM fallback).
    """
    if not trait_summaries:
        return main_body
    if PRACTICES_HEADING.lower() in main_body.lower():
        return main_body
    lines = [trait_line(slug, summary) for slug, summary in trait_summaries.items()]
    return main_body.rstrip() + "\n\n" + render_practices_tail(lines) + "\n"


def copy_traits(traits_home, dest_dir, slugs: list[str],
                adapted: dict[str, str] | None = None) -> dict[str, str]:
    """Write each selected trait into dest_dir/traits/ — the decompose pass's ADAPTED body
    when one exists, else the library text verbatim — and return {slug: summary} for the
    practices tail. Either way the files are the routine's/conversation's OWN from here on
    (self-refined, never toggled); a slug the library doesn't carry is skipped silently.
    """
    from .. import library_docs

    summaries: dict[str, str] = {}
    for slug in slugs:
        body = (adapted or {}).get(slug)
        if not body:
            raw = library_docs.read_doc(traits_home, slug)
            body = library_docs.doc_body(raw).strip() if raw else ""
        if not body:
            continue
        (dest_dir / "traits" / f"{slug}.md").write_text(body.rstrip() + "\n", encoding="utf-8")
        m = library_docs.DOC_RE.search(body)
        summaries[slug] = m.group("summary").strip() if m else ""
    return summaries

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


# The parameter list IS routine creation's config surface (wizard + API both fill it);
# bundling it into an object would only relocate the same list.
def scaffold(server: ServerConfig, *, slug: str, name: str, instruction: str,  # noqa: PLR0913
             workflow_slug: str, cron: str = "", tz: str = "Europe/Berlin",
             description: str = "", models: dict[str, str] | None = None,
             params: dict | None = None, budgets: dict | None = None,
             traits: list[str] | None = None,
             permissions: list[str] | None = None,
             fs_read_roots: list[str] | None = None,
             fs_write_roots: list[str] | None = None,
             stages: dict[str, str] | None = None, enabled: bool = True,
             tags: list[str] | None = None, deliberation: str = "") -> Path:
    """Create ~/routines/<slug>. The workflow is REFERENCED (edited only in the library);
    the routine gets ADAPTED trait copies under traits/ (referenced from main.md's Standing
    practices tail — the routine's own files from then on) + stages/ modules. The clarified
    `instruction` is the compile SEED: it is decomposed into the stages and NOT persisted (the
    stages are the routine's sole source of truth from here on). `permissions` (engine-enforced,
    user-changeable) go into routine.yaml. A one-line `description` (for the UI) is always
    written, falling back to the name; `models` maps a role to a catalog model NAME (else the
    role falls back to the server system_model).
    """
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
        meta, _ = library.read_workflow(server.libraries_home, workflow_slug)
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
    from ..grants import capabilities_for, floor_capabilities, read_library_requires

    # the SAME raise-then-floor discipline the save path applies (api_routines) — creation
    # used to raise only, so a floor violation surfaced on first edit instead of at birth
    lib = read_library_requires(server.permissions_home)
    capabilities = floor_capabilities(active_perms, lib, capabilities_for(active_perms, lib))
    commit = library.head_commit(server.libraries_home)

    from .adapt import decompose, dump_markdown

    for sub in ("state", "stages", "inbox", "traits"):
        (routine_dir / sub).mkdir(parents=True)
    # DECOMPOSE the single-file workflow (applied to the instruction) into the routine's OWN
    # main.md (entry state machine) + one markdown stage per step/state, adapting the selected
    # traits along the way. Self-contained: the library is never read at run time, and the
    # instruction is consumed here (not persisted). Degrades to the whole workflow as main.md +
    # verbatim trait copies if no endpoint is available.
    result = decompose(server, workflow_slug, instruction, params=params, traits=active_traits)
    main_meta = {
        "name": name, "slug": slug,
        "materialized_from": {"slug": workflow_slug, "commit": commit,
                              "version": meta.get("version", 0)},
        "stages": sorted(result["stages"]),
        # the workflow's `tools:` allowlist rides along — the engine enforces it per turn
        **({"tools": list(meta["tools"])} if meta.get("tools") is not None else {}),
        **({"tags": list(tags)} if tags else {}),
    }
    trait_summaries = copy_traits(server.traits_home, routine_dir, active_traits,
                                  adapted=result.get("traits"))
    for stage_name, stage_body in result["stages"].items():
        (routine_dir / "stages" / f"{stage_name}.md").write_text(stage_body.rstrip() + "\n",
                                                                 encoding="utf-8")
    # extra purpose-specific stage modules from the wizard also land in stages/
    for fname, fcontent in (stages or {}).items():
        safe = fname if fname.endswith(".md") else f"{fname}.md"
        (routine_dir / "stages" / Path(safe).name).write_text(fcontent, encoding="utf-8")
    # main.md last, over the now-complete stages/ — the stages are the sole source of truth
    main_body = with_practices_tail(result["main"], trait_summaries)
    (routine_dir / "main.md").write_text(dump_markdown(main_meta, main_body), encoding="utf-8")
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
        # unknown keys are dropped, not persisted — a caller typo must not seed junk
        # config that the strict loader then flags on every read
        "budgets": {**DEFAULT_BUDGETS,
                    **{k: v for k, v in (budgets or {}).items() if k in DEFAULT_BUDGETS}},
        "retention": {"keep_runs": 30},
    }
    if fs_read_roots:
        cfg["fs_read_roots"] = [_tilde(p) for p in fs_read_roots]
    if fs_write_roots:
        cfg["fs_write_roots"] = [_tilde(p) for p in fs_write_roots]
    (routine_dir / "routine.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    # tuning.yaml (recipe-classed, improver-editable): the deliberation level, wizard-
    # suggested per task. Always written, so the file exists for later tuning edits.
    write_tuning(routine_dir, {"deliberation": deliberation
                               if deliberation in DELIBERATION_LEVELS
                               else DEFAULT_DELIBERATION})

    init_repo(routine_dir, f"scaffold {slug} from workflow {workflow_slug}")
    return routine_dir


def _tilde(path: str) -> str:
    """Collapse $HOME → ~ so an absolute path never embeds the account/home-dir name."""
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home) else path


from ..libgit import IDENTITY_PAIRS as GIT_IDENTITY  # noqa: E402 — one identity home


def init_repo(repo_dir: Path, message: str) -> None:
    """Git init a managed repo with the neutral identity + best-effort push hook, then
    make the first commit. Shared by routine and util-library scaffolding.
    """
    try:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir,
                       capture_output=True, timeout=30, check=False)
        for key, val in GIT_IDENTITY:
            subprocess.run(["git", "config", key, val], cwd=repo_dir,
                           capture_output=True, timeout=15, check=False)
        hook = repo_dir / ".git" / "hooks" / "post-commit"
        hook.write_text(POST_COMMIT_HOOK, encoding="utf-8")
        hook.chmod(0o755)  # git hooks must be executable
        subprocess.run(["git", "add", "-A"], cwd=repo_dir,
                       capture_output=True, timeout=30, check=False)
        subprocess.run(["git", "commit", "-qm", message], cwd=repo_dir,
                       capture_output=True, timeout=30, check=False)
    except OSError:
        pass  # a routine without git still runs; the workflow can init later

