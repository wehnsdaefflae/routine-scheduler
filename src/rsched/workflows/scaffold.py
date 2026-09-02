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
    write_tuning,
)
from ..health_events import log_health_event
from ..ids import is_slug, now_iso
from ..paths import atomic_write_yaml

# mnt/ = transient remote-machine share mounts; .util_outputs/ = spilled util output
# (engine-owned and pruned, and it can carry whatever a util printed — never committed)
GITIGNORE = "runs/\ninbox/\nquestions/\nmnt/\n.util_outputs/\n"



# The parameter list IS routine creation's config surface (creation flow + API both fill it);
# bundling it into an object would only relocate the same list.
def scaffold(server: ServerConfig, *, slug: str, name: str, instruction: str,  # noqa: PLR0913
             workflow_slug: str, cron: str = "", tz: str = "Europe/Berlin",
             description: str = "", models: dict[str, str] | None = None,
             budgets: dict | None = None,
             fs_read_roots: list[str] | None = None,
             fs_write_roots: list[str] | None = None,
             stages: dict[str, str] | None = None, enabled: bool = True,
             tags: list[str] | None = None, deliberation: str = "",
             template: str | None = None, stopping: list[str] | None = None) -> Path:
    """Create ~/routines/<slug>. The workflow is REFERENCED (edited only in the library);
    `stopping` seeds `state/stopping.json` — what DONE means for one run, in the USER's words;
    the routine holds general-rule SLUGS in routine.yaml (`rules:`, indexed by main.md's
    Standing practices tail — the prose stays in the library) + stages/ modules. The clarified
    `instruction` is the compile SEED: it is decomposed into the stages and NOT persisted (the
    stages are the routine's sole source of truth from here on). `permissions` (engine-enforced,
    user-changeable) go into routine.yaml. A one-line `description` (for the UI) is always
    written, falling back to the name; `models` maps a role to a catalog model NAME (else the
    role falls back to the server system_model).
    """
    from .. import library_docs
    from .. import rules as rules_mod
    from ..config import DEFAULT_RULES
    from ..rules import with_practices_tail
    from . import library

    if not is_slug(slug):
        raise ValueError(f"slug {slug!r} is not kebab-case")
    routine_dir = server.routines_home / slug
    if routine_dir.exists():
        raise ValueError(f"routine dir {routine_dir} already exists")

    # rules default to the workflow's `includes` (its suggested set), else the standard
    # set; validate against the library. Permissions validate against theirs.
    try:
        meta, _ = library.read_workflow(server.libraries_home, workflow_slug)
    except FileNotFoundError as exc:
        raise ValueError(f"workflow {workflow_slug!r} not found in the library") from exc
    # The rules a new routine holds come from the PATTERN it was built on, and the permissions
    # from the defaults — never from a judgement made at creation. Judging a routine's setup is
    # `recommend_setup`'s job, which runs on the routine PAGE, after the routine exists and has
    # a recipe to judge, and puts advice beside every toggle rather than flipping one (D108).
    available_rules = set(library_docs.slugs(server.rules_home))
    active_rules = [r for r in (meta.get("includes") or DEFAULT_RULES) if r in available_rules]
    available_perms = set(library_docs.slugs(server.permissions_home))
    active_perms = [p for p in DEFAULT_PERMISSIONS if p in available_perms]
    # the activation cascade: the capabilities the chosen conduct docs require, switched
    # on from the start (the user tunes both layers on the routine page afterwards)
    from ..grants import capabilities_for, floor_capabilities, read_library_requires

    # the SAME raise-then-floor discipline the save path applies (api_routines) — creation
    # used to raise only, so a floor violation surfaced on first edit instead of at birth
    lib = read_library_requires(server.permissions_home)
    capabilities = floor_capabilities(active_perms, lib, capabilities_for(active_perms, lib))
    # A settings template is a PRESELECTION, not a layer (operator decision 2026-08-30,
    # reversing 0.262.0): the template's values are copied into this file once, here, and the
    # routine owns them from that moment. `template=""` opts out explicitly; None means "fit
    # one", a deterministic best fit over what creation already decided — an LLM guess here
    # would write a wrong DEFAULT into a config file, which is worse than a slightly-narrow one
    # the user widens on the routine page.
    #
    # Copying rather than layering is what makes routine.yaml say what the routine IS. Under the
    # layer, a routine's own file recorded only its DIFFERENCES from a template, so reading it
    # told you almost nothing and the page had to explain a second inheritance chain on top of
    # the group's. The cost is the leverage: editing a template no longer reaches its adopters.
    from ..templates import config_for as _template_config
    from ..templates import suggest as _suggest_template

    chosen = _suggest_template(server.libraries_home, active_perms,
                               active_rules) if template is None else template
    tpl_conf = _template_config(server.libraries_home, chosen) if chosen else {}
    own_perms = list(dict.fromkeys([*(tpl_conf.get("permissions") or []), *active_perms]))
    own_rules = list(dict.fromkeys([*(tpl_conf.get("rules") or []), *active_rules]))
    tpl_caps = tpl_conf.get("capabilities") or {}
    own_caps: dict = {}
    for key in ("actions", "utils", "util_tags"):
        merged = list(dict.fromkeys([*(tpl_caps.get(key) or []), *(capabilities.get(key) or [])]))
        if merged:
            own_caps[key] = merged
    for key in ("confirm", "rule_confirm", "runs", "workflows"):
        # the routine's own dial wins over the template's; either is written in full
        if capabilities.get(key) or tpl_caps.get(key):
            own_caps[key] = capabilities.get(key) or tpl_caps[key]
    # The remaining shared keys a template may carry, merged into what the caller passed:
    # lists union (template first, so the caller's additions read after), maps fill only what
    # the caller left unset. `grants` is deliberately absent — a grant is a settled DECISION a
    # person made about one routine, and a template pre-answering one would be a template
    # granting a secret.
    tags = list(dict.fromkeys([*(tpl_conf.get("tags") or []), *(tags or [])])) or None
    machines = list(dict.fromkeys(tpl_conf.get("machines") or [])) or None
    fs_read_roots = list(dict.fromkeys([*(tpl_conf.get("fs_read_roots") or []),
                                        *(fs_read_roots or [])])) or None
    fs_write_roots = list(dict.fromkeys([*(tpl_conf.get("fs_write_roots") or []),
                                         *(fs_write_roots or [])])) or None
    models = {**(tpl_conf.get("models") or {}), **(models or {})} or None
    connections = dict(tpl_conf.get("connections") or {}) or None
    budgets = {**(tpl_conf.get("budgets") or {}), **(budgets or {})} or None
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
    result = decompose(server, workflow_slug, instruction, rules=active_rules)
    for sub in ("state", "stages", "inbox"):
        (routine_dir / sub).mkdir(parents=True)
    main_meta = {
        "name": name, "slug": slug,
        "materialized_from": {"slug": workflow_slug, "commit": commit,
                              "version": meta.get("version", 0)},
        # the workflow's `tools:` allowlist rides along — the engine enforces it per turn
        **({"tools": list(meta["tools"])} if meta.get("tools") is not None else {}),
    }
    rule_summaries = rules_mod.summaries(server.rules_home, active_rules)
    for stage_name, stage_body in result["stages"].items():
        (routine_dir / "stages" / f"{stage_name}.md").write_text(stage_body.rstrip() + "\n",
                                                                 encoding="utf-8")
    # extra purpose-specific stage modules from the creation flow also land in stages/
    for fname, fcontent in (stages or {}).items():
        safe = fname if fname.endswith(".md") else f"{fname}.md"
        (routine_dir / "stages" / Path(safe).name).write_text(fcontent, encoding="utf-8")
    # main.md last, over the now-complete stages/ — the stages are the sole source of truth
    main_body = with_practices_tail(result["main"], rule_summaries)
    (routine_dir / "main.md").write_text(dump_markdown(main_meta, main_body), encoding="utf-8")
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
        "enabled": enabled,
        **({"tags": list(tags)} if tags else {}),
        "schedule": {"cron": cron, "tz": tz, "catchup": "skip"},
        "workflow": {"library_slug": workflow_slug, "library_commit": commit},
        **({"models": models} if models else {}),
        **({"connections": connections} if connections else {}),
        **({"machines": machines} if machines else {}),
        "permissions": own_perms,
        "rules": own_rules,
        **({"capabilities": own_caps} if own_caps else {}),
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
    atomic_write_yaml(routine_dir / "routine.yaml", cfg)
    # STOPPING CONDITIONS (F334/D98) — what DONE means for one run, seeded from the answer the
    # creation flow already collected. The flow has always asked ("what DONE looks like for one
    # run, in the user's own words"; F383) and the answer went nowhere but the recipe prose, so
    # every routine ever created started with an empty goal document and was bounded only by its
    # budgets — the exact state D98 was taken to end. Written here rather than by a run, because
    # `state/stopping.json` is the USER's list; this IS their words, collected at the one moment
    # they were in the loop. Absent or empty seeds nothing: an invented condition is worse than
    # none, since every later run has to account for it.
    if stopping:
        from ..engine import stopping as stopping_mod
        stopping_mod.save(routine_dir,
                          {"mode": "all",
                           "groups": [{"id": "g1", "name": "", "mode": "all"}],
                           "conditions": [{"text": t, "status": "open", "group": "g1"}
                                          for t in stopping if str(t).strip()]},
                          now=now_iso())
    # tuning.yaml (recipe-classed, improver-editable): the deliberation level, creation-
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


def init_repo(repo_dir: Path, message: str) -> None:
    """Git init a managed repo with the neutral identity + push hook + first commit —
    ONE implementation for every managed repo (libgit.init_repo, F285).
    """
    libgit.init_repo(repo_dir, first_commit=message)

