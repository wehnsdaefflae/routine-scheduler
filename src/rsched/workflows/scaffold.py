"""Create a routine directory: workflow REFERENCE (edited in the library), the held
general-rule slugs, stages/ modules; its own git repo with the auto-push hook.
"""

from __future__ import annotations

from pathlib import Path

from .. import libgit
from ..config import (
    DEFAULT_BUDGETS,
    DEFAULT_DELIBERATION,
    DEFAULT_PERMISSIONS,
    DELIBERATION_LEVELS,
    ServerConfig,
    default_tz,
    write_tuning,
)
from ..health_events import log_health_event
from ..ids import is_slug, now_iso
from ..paths import atomic_write_yaml

# mnt/ = transient remote-machine share mounts; .util_outputs/ = spilled util output
# (engine-owned and pruned, and it can carry whatever a util printed — never committed)
GITIGNORE = "runs/\ninbox/\nquestions/\nmnt/\n.util_outputs/\n"


# The parameter list IS routine creation's config surface (the creation conversation, an
# accepted proposal and the CLI fill it); bundling it into an object would only relocate it.
def scaffold(server: ServerConfig, *, slug: str, name: str, instruction: str,  # noqa: PLR0913
             workflow_slug: str, cron: str = "", tz: str = "", description: str = "",
             fs_read_roots: list[str] | None = None,
             fs_write_roots: list[str] | None = None, tags: list[str] | None = None,
             pattern: str = "", done_when: list[str] | None = None,
             finish_line: list[str] | None = None,
             setup: list[str] | None = None, never: list[str] | None = None) -> Path:
    """Create ~/routines/<slug>, FOLLOWING a settings pattern (docs/patterns.md).

    The routine is saved with its pattern's values — `pattern` names it, or the recommender
    picks the one built on `workflow_slug` — and everything specific to THIS routine becomes
    PENDING changes on its page, under "check the changes i recommend.": what the person
    settled while designing it (`setup`, their answers to the pattern's questions), the
    `finish_line` they described (`run: …`, `you: …`, `YYYY-MM-DD: …`, `until YYYY-MM-DD`), and
    whatever else the recommender finds. The caller's own values (a cron, roots, tags) are
    applied — they are decisions made in code, not proposals.

    The workflow is REFERENCED (edited only in the library). The clarified `instruction` is
    the compile SEED: it is decomposed into main.md + stages/ and NOT persisted.
    `done_when` — what the person said one finished run delivers — becomes the recipe's
    `## Done when`; `never` — what they said a run must never do — its `## Never` (and
    context for the settings recommended to stop it before the action). A one-line
    `description` (for the UI) is always written, falling back to the name. `tz` is the zone
    the schedule is in — empty means the server's (`config.default_tz`), the zone the console
    shows and saves schedules in.
    """
    from .. import library_docs
    from ..config import DEFAULT_RULES
    from ..patterns import apply as pattern_apply
    from ..patterns import fields, recommend, store
    from . import library

    if not is_slug(slug):
        raise ValueError(f"slug {slug!r} is not kebab-case")
    tz = tz or default_tz()
    routine_dir = server.routines_home / slug
    if routine_dir.exists():
        raise ValueError(f"routine dir {routine_dir} already exists")

    try:
        meta, _ = library.read_workflow(server.libraries_home, workflow_slug)
    except FileNotFoundError as exc:
        raise ValueError(f"workflow {workflow_slug!r} not found in the library") from exc
    # The values a routine holds before its pattern's are laid over them: the default rules
    # plus the workflow's own kind rules, the default permissions — what a routine whose
    # library has no pattern at all holds. Validated against the library.
    available_rules = set(library_docs.slugs(server.rules_home))
    active_rules = [r for r in dict.fromkeys([*DEFAULT_RULES, *(meta.get("includes") or [])])
                    if r in available_rules]
    available_perms = set(library_docs.slugs(server.permissions_home))
    active_perms = [p for p in DEFAULT_PERMISSIONS if p in available_perms]
    # the activation cascade: the capabilities the chosen conduct docs require, switched
    # on from the start (the user tunes both layers on the routine page afterwards)
    from ..grants import capabilities_for, floor_capabilities, read_library_requires

    # the SAME raise-then-floor discipline the save path applies (api_routines) — creation
    # used to raise only, so a floor violation surfaced on first edit instead of at birth
    lib = read_library_requires(server.permissions_home)
    capabilities = floor_capabilities(active_perms, lib, capabilities_for(active_perms, lib))
    # Written in full minus the empty lists: every setting a new routine holds is in its own
    # file from the first save, so routine.yaml says what the routine IS.
    own_caps = {k: v for k, v in capabilities.items() if v}
    commit = library.head_commit(server.libraries_home)

    from .adapt import decompose, dump_markdown

    # DECOMPOSE the single-file workflow (applied to the instruction) into the routine's OWN
    # main.md (entry state machine) + one markdown stage per step/state. The instruction is
    # consumed here (not persisted); rules are NOT part of the decomposition — they are
    # general by construction and stay in the library. Degrades to the whole workflow as
    # main.md if no endpoint is available.
    # Decompose FIRST: it is the slow step (an LLM call that can run for minutes), and the
    # routine dir must not exist until every file's content is in hand — a half-made skeleton
    # sitting in the routines home for minutes reads as a broken build (R478: the user watched
    # empty dirs, deleted them mid-flight, and the writes that followed crashed the run).
    result = decompose(server, workflow_slug, instruction, done_when=list(done_when or []),
                       never=list(never or []))
    pattern = pattern or recommend.choose(server, workflow=workflow_slug, name=name,
                                          task=instruction)
    settings = (store.read(server.libraries_home, pattern) or {}).get("settings") or {}
    for sub in ("state", "stages", "inbox"):
        (routine_dir / sub).mkdir(parents=True)
    main_meta = {
        "name": name, "slug": slug,
        "materialized_from": {"slug": workflow_slug, "commit": commit,
                              "version": meta.get("version", 0)},
        # the workflow's `tools:` allowlist rides along — the engine enforces it per turn
        **({"tools": list(meta["tools"])} if meta.get("tools") is not None else {}),
    }
    for stage_name, stage_body in result["stages"].items():
        (routine_dir / "stages" / f"{stage_name}.md").write_text(stage_body.rstrip() + "\n",
                                                                 encoding="utf-8")
    # main.md last, over the now-complete stages/ — the stages are the sole source of truth
    (routine_dir / "main.md").write_text(dump_markdown(main_meta, result["main"]),
                                         encoding="utf-8")
    ledger = (f"# LEDGER — {name}\n\n"
              f"### seed — scaffolded from workflow '{workflow_slug}' @ {commit}\n")
    if result.get("degraded"):
        # never silent (F183/D41): the user must see the routine was born without its stages —
        # and WHY (F197: a cause-less warning sent the 2026-07-24 outage hunt through the
        # daemon journal, which sandboxed audit routines cannot read)
        why = str(result.get("reason") or "unknown failure")
        ledger += ("\n### ⚠ scaffolded without generated stages\nThe stage-generator was "
                   "unreachable at creation (usually a transient model outage — quota/rate "
                   "limit), so main.md is the verbatim workflow pattern and stages/ is empty. "
                   "The routine is fully functional and runs on the pattern as-is; for tailored "
                   "stages, re-create it when models are available (or ask a run to draft the "
                   f"stage modules).\nCause: {why}\n")
        log_health_event(server.routines_home, "wizard_build_degraded",
                         routine=slug, run_id="", detail=why)
    (routine_dir / "LEDGER.md").write_text(ledger, encoding="utf-8")
    (routine_dir / ".gitignore").write_text(GITIGNORE, encoding="utf-8")

    cfg = {
        "name": name,
        "slug": slug,
        "description": (description or "").strip() or name,
        "enabled": True,
        **({"tags": list(tags)} if tags else {}),
        "schedule": {"cron": cron, "tz": tz, "catchup": "skip"},
        "workflow": {"library_slug": workflow_slug, "library_commit": commit},
        "permissions": active_perms,
        "rules": active_rules,
        **({"capabilities": own_caps} if own_caps else {}),
        "budgets": dict(DEFAULT_BUDGETS),
        "retention": {"keep_runs": 30},
        # the pattern's values over the defaults — the routine is saved following it
        **({"pattern": pattern} if settings else {}),
        **pattern_apply.routine_yaml(settings, tz=tz),
    }
    # values the CALLER decided stand over the pattern's (a cron the CLI was given, roots the
    # API was sent); an unset one leaves the pattern's
    if cron:
        cfg["schedule"] = {"cron": cron, "tz": tz, "catchup": "skip"}
    if tags:
        cfg["tags"] = list(tags)
    if fs_read_roots:
        cfg["fs_read_roots"] = [fields.tilde(p) for p in fs_read_roots]
    if fs_write_roots:
        cfg["fs_write_roots"] = [fields.tilde(p) for p in fs_write_roots]
    atomic_write_yaml(routine_dir / "routine.yaml", cfg)
    # The pattern's own finish line, if it carries one; the finish line the PERSON described
    # is specific to this routine, so it is proposed below rather than written here.
    pattern_apply.write_finish_line(routine_dir, settings, now=now_iso())
    # tuning.yaml (recipe-classed, improver-editable): the pattern's deliberation level.
    # Always written, so the file exists for later tuning edits.
    level = pattern_apply.deliberation(settings)
    write_tuning(routine_dir, {"deliberation": level if level in DELIBERATION_LEVELS
                               else DEFAULT_DELIBERATION})

    # ONE implementation for every managed repo: the neutral identity, the push hook and the
    # first commit (F285)
    libgit.init_repo(routine_dir, first_commit=f"scaffold {slug} from workflow {workflow_slug}")
    # What is specific to THIS routine waits for the person's accept, highlighted on its page.
    from ..engine import finishline

    context = "\n".join([instruction, *(f"Settled while designing it: {line}"
                                        for line in setup or []),
                         *(f"A finished run delivers: {line}" for line in done_when or []),
                         *(f"A run must never: {line}" for line in never or [])])
    recommend.at_creation(server, slug=slug, pattern_slug=pattern if settings else "",
                          context=context,
                          finish_line=finishline.from_words(list(finish_line or [])))
    return routine_dir

