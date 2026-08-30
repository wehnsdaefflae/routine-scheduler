"""Per-routine config: RoutineConfig, its loader, and the tuning.yaml reader/writer."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import AliasPath, Field, ValidationInfo, field_validator

from ..ids import is_slug
from ..paths import atomic_write
from .base import (
    DEFAULT_BUDGETS,
    DEFAULT_CAPABILITIES,
    DEFAULT_DELIBERATION,
    DEFAULT_PERMISSIONS,
    DEFAULT_RULES,
    DELIBERATION_LEVELS,
    MODEL_KINDS,
    BlankableStr,
    HomePath,
    _Config,
    _known_tz,
    _validate_lenient,
)
from .groupconfig import apply_group_config, group_config_for


class RoutineConfig(_Config):
    """One routine's `routine.yaml`: schedule, models (main/tool_call/uncensored),
    budgets, held permissions, held general rules, filesystem roots, and retention. The
    routine's recipe lives next to it as `main.md` + `stages/`; the rules it practises are
    library slugs in `rules:`. (The instruction is a transient compile seed — decomposed
    into the stages at creation, never persisted.)
    """

    slug: str
    dir: Path
    # The SETTINGS TEMPLATE this routine adopts (library `templates/<slug>.md`) — a named
    # starting point for permissions, capabilities, rules, grants and roots. It layers UNDER
    # the group's shared config, which layers under this file: every field stays editable
    # here and an override is an ordinary routine.yaml key (rsched/templates.py).
    template: BlankableStr = ""
    # What this routine DROPS from what its template (or group) supplies. The shared layers
    # merge as a UNION — the group model's rule, kept — so without this a routine could add to
    # a template but never subtract from one, and adopting a template would cost granularity.
    # Names a permission slug, a rule slug, a util or a gated action; anything else is ignored.
    template_except: list[str] = Field(default_factory=list)
    name: BlankableStr = ""
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)  # freeform, for filtering (e.g. "meta")
    cron: BlankableStr = Field("", validation_alias=AliasPath("schedule", "cron"))
    tz: str = Field("Europe/Berlin", validation_alias=AliasPath("schedule", "tz"))
    catchup: Literal["skip", "run_once"] = Field(
        "skip", validation_alias=AliasPath("schedule", "catchup"))
    workflow_slug: BlankableStr = Field("", validation_alias=AliasPath("workflow", "library_slug"))
    workflow_commit: BlankableStr = Field(
        "", validation_alias=AliasPath("workflow", "library_commit"))
    # Conversations only: the library playbook this conversation was seeded from (the
    # `playbook: {slug, commit}` binding). Empty = a fresh conversation. Drives the
    # Update-playbook button; a Save-as-playbook always creates a new one regardless.
    playbook_slug: BlankableStr = Field("", validation_alias=AliasPath("playbook", "slug"))
    # Detached background tasks only: the spawning conversation ({slug, dir}). The
    # DetachedManager reads this to deliver the finished result back. None for every
    # normal routine/conversation (a declared field, so it survives the extra="ignore" drop).
    owner: dict | None = None
    description: BlankableStr = ""  # one-line human summary shown in the UI (always present)
    # What this dir IS, when it is not an ordinary scheduled routine: "conversation" (an
    # interactive session under conversations_home). Empty for a normal routine.
    kind: BlankableStr = ""
    # Role → catalog model NAME (main/tool_call/uncensored). A role left unset
    # falls back to the server system_model. Resolved live via EndpointRegistry, so editing
    # a catalog model updates every routine that names it.
    models: dict[str, str] = Field(default_factory=dict)
    # OAuth connection bindings: provider id → account label (Settings → Connections). A run bound
    # here gets that provider's current access token injected into any util that declares it (as
    # <PROVIDER>_ACCESS_TOKEN). A RESOURCE binding like models/fs_roots — the binding is the grant;
    # connections are user config, never set by a run. See docs/oauth-connections.md.
    connections: dict[str, str] = Field(default_factory=dict)
    # Remote-machine bindings: catalog machine NAMES this routine may act on (Settings →
    # Machines). A RESOURCE binding like connections/models — the list IS the grant; the
    # reserved `remote` util receives the bound machines' connection details + private keys.
    # Never set by a run. See docs/remote-machines.md.
    machines: list[str] = Field(default_factory=list)
    # Grant-decision rows (entities.py ids): the deny-forever tombstones for ANY entity
    # (`util:discord: false` — asks are suppressed) plus secret exposure, the one class
    # with no native switch (`secret:FOO_KEY: true/false`; absent = undecided, asked on
    # first use — D39). Written ONLY by the web layer recording an explicit user decision
    # (the Decisions page's allow/deny buttons, the routine page's editors) — no run and
    # no engine code writes this file, ever.
    grants: dict[str, bool] = Field(default_factory=dict)
    budgets: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_BUDGETS))
    # The two permission layers (user-changeable only; explicit values win, otherwise a
    # new routine holds the defaults). `permissions` names the held CONDUCT docs (library
    # prose in the prompt); `capabilities` is the engine-enforced surface grants.py
    # loads the run policy from — {actions, utils, confirm, runs}.
    permissions: list[str] = Field(default_factory=lambda: list(DEFAULT_PERMISSIONS))
    # The GENERAL RULES this routine practises: library slugs, never copies. The rule text
    # lives once under <libraries_home>/rules/ and the run reads it on demand (`read_rule`),
    # so a library revision reaches every holder at once. User-only, like everything here.
    rules: list[str] = Field(default_factory=lambda: list(DEFAULT_RULES))
    capabilities: dict = Field(default_factory=lambda: {
        k: list(v) if isinstance(v, list) else v for k, v in DEFAULT_CAPABILITIES.items()})
    fs_read_roots: list[HomePath] = Field(default_factory=list)
    fs_write_roots: list[HomePath] = Field(default_factory=list)
    # Event triggers — fire the routine on an external event, alongside cron. One
    # canonical list of {id, type, …} entries (webhook implemented; imap/watch_path
    # reserved in the same shape); validated in triggers.py, fired by the daemon's
    # TriggerManager (docs/triggers.md). User config like everything in this file:
    # created/deleted on the routine page, never by a run.
    triggers: list[dict] = Field(default_factory=list)
    keep_runs: int = Field(30, validation_alias=AliasPath("retention", "keep_runs"))
    # Whether the routine-improver meta routine visits this routine (default: yes; the
    # toggle on the routine page opts out with `improve: false`).
    improve: bool = True
    # What this routine INHERITED from its group's shared config (D82): {field: "<n> from the
    # group"} plus the group's name. Runtime handles like `deliberation` — computed at load,
    # never written to routine.yaml (the file stays the routine's OWN authority, so removing
    # it from a group cleanly returns it to what its file says). The routine page reads these
    # to mark a value as coming from the group rather than from this routine.
    inherited: dict[str, str] = Field(default_factory=dict)
    inherited_from: str = ""
    # How much thinking lands on paper (see DELIBERATION_LEVELS). The runtime handle
    # only: load_routine fills it from TUNING (tuning.yaml) — routine.yaml never carries
    # it (config = authority, tuning = machine-tunable behavior).
    deliberation: str = DEFAULT_DELIBERATION

    @field_validator("cron")
    @classmethod
    def _croniter_accepts(cls, v: str) -> str:
        if v:
            from croniter import croniter

            try:
                croniter(v)
            except (ValueError, KeyError) as exc:
                raise ValueError(str(exc)) from exc
        return v

    _tz_known = field_validator("tz")(_known_tz)

    @field_validator("description")
    @classmethod
    def _stripped(cls, v: str) -> str:
        return v.strip()

    @field_validator("tags", mode="before")
    @classmethod
    def _clean_tags(cls, v: object) -> object:
        if v is None:
            return []
        return [str(t).strip() for t in v if str(t).strip()] if isinstance(v, list) else v

    @field_validator("fs_read_roots", "fs_write_roots", "models", "connections", "triggers",
                     "machines", mode="before")
    @classmethod
    def _none_as_absent(cls, v: object, info: ValidationInfo) -> object:
        # a bare `key:` (YAML null) reads as the FIELD'S OWN empty default ([] or {})
        if v is not None or info.field_name is None:
            return v
        factory = cast("Callable[[], object]",
                       cls.model_fields[info.field_name].default_factory)
        return factory()

    @field_validator("budgets", mode="before")
    @classmethod
    def _merged_over_defaults(cls, v: object) -> object:
        return {**DEFAULT_BUDGETS, **v} if isinstance(v, dict) else v

    @field_validator("permissions", mode="before")
    @classmethod
    def _default_unless_list(cls, v: object) -> object:
        return [str(f) for f in v] if isinstance(v, list) else list(DEFAULT_PERMISSIONS)

    @field_validator("rules", mode="before")
    @classmethod
    def _rules_default_unless_list(cls, v: object) -> object:
        # an explicit list wins ([] = practises nothing); absent/garbage → the defaults
        return [str(f) for f in v] if isinstance(v, list) else list(DEFAULT_RULES)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _default_unless_mapping(cls, v: object) -> object:
        # an explicit mapping wins ({} = everything gated off); anything else → defaults
        if isinstance(v, dict):
            return v
        factory = cast("Callable[[], object]",
                       cls.model_fields["capabilities"].default_factory)
        return factory()


TUNING_FILE = "tuning.yaml"


def load_tuning(routine_dir: Path) -> tuple[dict, list[str]]:
    """<dir>/tuning.yaml — the routine's machine-tunable BEHAVIOR parameters (today:
    `deliberation`), recipe-classed: the routine-improver may edit it under its
    fs_write_root, while routine.yaml stays the user's sealed authority config. Absent
    file = all defaults. Returns (values, problems); unknown keys/values are reported
    and dropped, never applied.
    """
    path = routine_dir / TUNING_FILE
    if not path.is_file():
        return {}, []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {}, [f"tuning.yaml: {exc}"]
    if not isinstance(raw, dict):
        return {}, ["tuning.yaml: expected a mapping at top level"]
    problems: list[str] = []
    out: dict = {}
    level = raw.pop("deliberation", None)
    if level is not None:
        if level in DELIBERATION_LEVELS:
            out["deliberation"] = level
        else:
            problems.append(f"tuning.yaml deliberation: unknown level {level!r} "
                            f"(expected one of {DELIBERATION_LEVELS})")
    problems += [f"tuning.yaml {key}: unknown tuning key" for key in raw]
    return out, problems


def write_tuning(routine_dir: Path, updates: dict) -> None:
    """Merge updates into tuning.yaml (atomic). Callers validate values; the web layer's
    slider and the creators (scaffold, conversations, clarify sessions) write through here.
    """
    path = routine_dir / TUNING_FILE
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, yaml.YAMLError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    raw.update(updates)
    atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))


def record_grants(routine_dir: Path, updates: dict[str, bool]) -> None:
    """Persist grant-decision rows (entity id → bool) into routine.yaml's `grants:`
    mapping. Called ONLY by the web layer recording an explicit user decision (a
    Decisions-page allow/deny click, the routine page's editors) — the ENGINE writes no
    routine.yaml at all: a run's one-time grants live in memory on its RunContext.
    """
    path = routine_dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError(f"{path}: expected a mapping at top level")
    grants = raw.get("grants")
    grants = dict(grants) if isinstance(grants, dict) else {}
    grants.update({str(k): bool(v) for k, v in updates.items()})
    raw["grants"] = grants
    atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))




def _libraries_home_for(_routine_dir: Path) -> Path | None:
    """Where the templates live, for resolving a routine's `template:`.

    Unlike the groups store — which sits beside the routine by filesystem convention — the
    library home is server config, and `load_routine` has no server to ask. It is taken from
    the config THIS PROCESS loaded (`config.server.active_libraries_home`), never from the
    ambient `config_file()`: an engine subprocess is started with an explicit `--config` and
    reading the default there would silently resolve a different instance's library.

    None means no config has been loaded in this process, and the template is simply not
    resolved — the routine runs on its own config, which is the safe direction. A caller that
    HAS a server should pass `libraries_home` explicitly rather than rely on this: one process
    may legitimately load several configs (the test suite does), and then "the last one loaded"
    is not the same as "this routine's".
    """
    from .server import active_libraries_home

    return active_libraries_home()


def load_routine(routine_dir: Path, *,
                 libraries_home: Path | None = None,
                 ) -> tuple[RoutineConfig | None, list[str]]:
    """Parse <dir>/routine.yaml, then layer the shared config of any group the routine belongs
    to underneath it (D82 — the group is a default, the routine's own keys win). Returns
    (config, problems); config is None only when the file is missing/unreadable — otherwise
    problems may be non-empty but best-effort applies.
    """
    path = routine_dir / "routine.yaml"
    problems: list[str] = []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        return None, [f"{path}: {exc}"]
    except yaml.YAMLError as exc:
        return None, [f"{path}: invalid YAML: {exc}"]
    if not isinstance(raw, dict):
        return None, [f"{path}: expected a mapping at top level"]

    slug = str(raw.get("slug") or routine_dir.name)
    if not is_slug(slug):
        problems.append(f"slug {slug!r} is not kebab-case")
    if slug != routine_dir.name:
        problems.append(f"slug {slug!r} does not match directory name {routine_dir.name!r}")
    if not isinstance(raw.get("schedule") or {}, dict):
        problems.append("schedule: expected a mapping")

    # aliased fields load from their CONTAINER key (schedule.cron, workflow.library_slug,
    # playbook.slug). Any other top-level key is a typo whose real field silently reverted to
    # defaults (a misspelled `permisions:` = a permission reset with zero problems reported).
    aliased = {"cron", "tz", "catchup", "workflow_slug", "workflow_commit",
               "playbook_slug", "keep_runs"}
    known = (set(RoutineConfig.model_fields) - aliased) | {"schedule", "workflow",
                                                           "playbook", "retention"}
    problems.extend(f"{key}: unknown routine.yaml key — check the spelling (ignored)"
                    for key in sorted(set(raw) - known))
    # The group's shared config goes UNDER the routine's own (D82) — after the unknown-key
    # check, which must judge the file the user actually wrote, not the merge result.
    group_config, group_name = group_config_for(routine_dir, slug)
    inherited: dict[str, str] = {}
    if group_config:
        raw, inherited = apply_group_config(raw, group_config)
    # …then the TEMPLATE, which is the broadest default. Order matters: each merge only fills
    # what is still unset, so applying the group first keeps own > group > template. The group
    # may also NAME the template, which is why it is resolved after that merge.
    template_slug = str(raw.get("template") or "")
    if template_slug:
        from ..templates import config_for as _template_config

        lib_home = libraries_home or _libraries_home_for(routine_dir)
        tpl = _template_config(lib_home, template_slug) if lib_home else {}
        if tpl:
            raw, from_template = apply_group_config(raw, tpl)
            for key in from_template:
                inherited.setdefault(key, f"{from_template[key]} from the template")
    drop = {str(x) for x in (raw.get("template_except") or []) if isinstance(x, str)}
    if drop:
        for key in ("permissions", "rules"):
            raw[key] = [v for v in (raw.get(key) or []) if v not in drop]
        caps = dict(raw.get("capabilities") or {})
        for key in ("actions", "utils", "util_tags"):
            if caps.get(key):
                caps[key] = [v for v in caps[key] if v not in drop]
        if caps:
            raw["capabilities"] = caps
    cfg = _validate_lenient(RoutineConfig, {**raw, "slug": slug, "dir": routine_dir}, problems) \
        or RoutineConfig(slug=slug, dir=routine_dir)
    cfg.inherited, cfg.inherited_from = inherited, (group_name if inherited else "")
    cfg.name = cfg.name or slug
    if not cfg.description:
        problems.append("description is empty — every routine needs a one-line "
                        "description (shown in the UI)")
    for kind in [k for k in cfg.models if k not in MODEL_KINDS]:
        hint = (" — the subroutine role is retired: children run the routine's MAIN model "
                "by default (a call overrides per child); remove this key"
                if kind == "subroutine" else "")
        problems.append(f"models.{kind}: unknown model kind "
                        f"(expected one of {MODEL_KINDS}){hint}")
        del cfg.models[kind]
    from ..oauth.providers import PROVIDERS  # function-level: oauth imports secrets, not config
    for prov in [p for p in cfg.connections if p not in PROVIDERS]:
        problems.append(
            f"connections.{prov}: unknown provider (expected one of {sorted(PROVIDERS)})")
        del cfg.connections[prov]
    for key in [k for k in cfg.budgets if k not in DEFAULT_BUDGETS]:
        problems.append(f"budgets.{key}: unknown budget")
        del cfg.budgets[key]
    # deliberation lives in TUNING, never in config — a routine.yaml key is stale data
    if "deliberation" in raw:
        problems.append("deliberation: belongs in tuning.yaml (machine-tunable behavior) "
                        "— the routine.yaml key is ignored")
    tuning, tuning_problems = load_tuning(routine_dir)
    problems += tuning_problems
    cfg.deliberation = tuning.get("deliberation", DEFAULT_DELIBERATION)
    from ..grants import normalize_capabilities  # function-level: grants imports engine.actions

    cfg.capabilities, cap_problems = normalize_capabilities(cfg.capabilities)
    problems += cap_problems
    from ..entities import normalize_grants  # function-level: entities imports grants

    cfg.grants, grant_problems = normalize_grants(cfg.grants)
    problems += grant_problems
    from ..triggers import validate_triggers

    cfg.triggers, trigger_problems = validate_triggers(cfg.triggers)
    problems += trigger_problems

    # A routine is self-contained: its recipe is materialized into main.md at generation, and the
    # workflow.library_slug is kept only as "generated-from" provenance.
    if not (routine_dir / "main.md").exists():
        problems.append("no main.md — the routine's recipe was not materialized in")
    return cfg, problems
