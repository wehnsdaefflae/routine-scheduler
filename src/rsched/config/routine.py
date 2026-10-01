"""Per-routine config: RoutineConfig, its loader, and the tuning.yaml reader/writer."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import (
    AliasPath,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ..ids import is_slug
from ..paths import atomic_write_yaml, read_yaml
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
    _problem_lines,
    _validate_lenient,
    default_tz,
)


class RunGateConfig(BaseModel):
    """Explicit, strict opt-in to pre-engine automatic admission: whether a scheduled fire
    first asks its CHECKS if there is work (docs/run-gates.md). `checks` is the vocabulary in
    `rsched/gatekit/`; the `script` kind is the routine's own `scripts/admit.py`.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    timeout_s: int = Field(default=30, ge=1, le=300)
    checks: list[dict] = Field(default_factory=list)

    @field_validator("checks")
    @classmethod
    def _known_checks(cls, v: list[dict]) -> list[dict]:
        from ..gatekit import validate

        if problems := validate(v):
            raise ValueError("; ".join(problems))
        return v

    @model_validator(mode="after")
    def _enabled_needs_a_check(self) -> RunGateConfig:
        # An enabled gate with nothing to ask would have to invent an answer; either one is a
        # lie — "skip" silently loses every fire, "run" is no gate at all.
        if self.enabled and not self.checks:
            raise ValueError("an enabled gate needs at least one check")
        return self


class RunGatePatch(BaseModel):
    """A PARTIAL run gate for the PATCH edge: each field optional, the merge validated whole
    by `RunGateConfig` (web/api_routine_patch._apply_run_gate).
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool | None = None
    timeout_s: int | None = Field(default=None, ge=1, le=300)
    checks: list[dict] | None = None


class RoutineConfig(_Config):
    """One routine's `routine.yaml`: schedule, models (main/tool_call/uncensored),
    budgets, held permissions, held general rules, filesystem roots, and retention. The
    routine's recipe lives next to it as `main.md` + `stages/`; the rules it practises are
    library slugs in `rules:`. (The instruction is a transient compile seed — decomposed
    into the stages at creation, never persisted.)
    """

    slug: str
    dir: Path
    name: BlankableStr = ""
    # THE off switch, and its one spelling: `enabled: false` starts no run at all — no cron,
    # trigger, lane or one-shot fire and no manual start (runner.fire refuses). Every reader
    # asks this field (the fire table, catch-up, lanes, the runner, the console's `enabled`);
    # the console's "Disabled" cadence and the dashboard's pause both write it.
    enabled: bool = True
    run_gate: RunGateConfig = Field(default_factory=RunGateConfig)
    tags: list[str] = Field(default_factory=list)  # freeform, for filtering (e.g. "meta")
    cron: BlankableStr = Field("", validation_alias=AliasPath("schedule", "cron"))
    # No zone named = the server's (default_tz): the zone the console shows and saves in.
    tz: str = Field(default_factory=default_tz, validation_alias=AliasPath("schedule", "tz"))
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
    # The heading this routine's card sits under on the Steward hub (docs/status-pages.md):
    # identity like `name`, named to the run in its harness contract. Empty = none named.
    hub_tab: BlankableStr = ""
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
    # The library SETTINGS PATTERN this routine follows (docs/patterns.md) — a reference the page
    # reads this file's values against, never a layer: nothing here is resolved from it at load,
    # so a deleted pattern leaves every value in this file exactly as it was.
    pattern: str = ""
    # The library's SHARED reminders (`<library>/reminders/`) this routine reads, by id. Its own
    # local reminders need no list; reading a shared one does — a caution another routine learned
    # is taken on by choice, the pattern proposing the ones that fit its kind of work.
    shared_reminders: list[str] = Field(default_factory=list)
    capabilities: dict = Field(default_factory=lambda: {
        k: list(v) if isinstance(v, list) else v for k, v in DEFAULT_CAPABILITIES.items()})
    fs_read_roots: list[HomePath] = Field(default_factory=list)
    # A write root that is a directory directly under `.control/group-stores/` is a SHARED
    # STORE this routine shares with every other routine naming it (rsched/sharedstores.py).
    fs_write_roots: list[HomePath] = Field(default_factory=list)
    # Event triggers — fire the routine on an external event, alongside cron. One
    # canonical list of {id, type, …} entries (webhook implemented; imap/watch_path
    # reserved in the same shape); validated in triggers.py, fired by the daemon's
    # TriggerManager (docs/triggers.md). User config like everything in this file:
    # created/deleted on the routine page, never by a run.
    triggers: list[dict] = Field(default_factory=list)
    # Retention deletes every run past this many after each run: 0 would delete them all.
    keep_runs: int = Field(30, ge=1, validation_alias=AliasPath("retention", "keep_runs"))
    # Whether the routine-improver meta routine visits this routine (default: yes; the
    # toggle on the routine page opts out with `improve: false`).
    improve: bool = True
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

    @field_validator("description", "hub_tab")
    @classmethod
    def _stripped(cls, v: str) -> str:
        return v.strip()

    @field_validator("tags", mode="before")
    @classmethod
    def _clean_tags(cls, v: object) -> object:
        if v is None:
            return []
        return [str(t).strip() for t in v if str(t).strip()] if isinstance(v, list) else v

    @classmethod
    def _default_of(cls, field: str | None) -> object:
        """A field's own default — what an absent or unusable value reads as."""
        factory = cast("Callable[[], object]", cls.model_fields[str(field)].default_factory)
        return factory()

    @field_validator("fs_read_roots", "fs_write_roots", "models", "connections", "triggers",
                     "machines", mode="before")
    @classmethod
    def _none_as_absent(cls, v: object, info: ValidationInfo) -> object:
        # a bare `key:` (YAML null) reads as the FIELD'S OWN empty default ([] or {})
        return cls._default_of(info.field_name) if v is None else v

    @field_validator("budgets", mode="before")
    @classmethod
    def _merged_over_defaults(cls, v: object) -> object:
        return {**DEFAULT_BUDGETS, **v} if isinstance(v, dict) else v

    @field_validator("permissions", "rules", mode="before")
    @classmethod
    def _default_unless_list(cls, v: object, info: ValidationInfo) -> object:
        # an explicit list wins ([] = holds none); absent/garbage → the field's defaults
        return [str(f) for f in v] if isinstance(v, list) else cls._default_of(info.field_name)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _default_unless_mapping(cls, v: object) -> object:
        # an explicit mapping wins ({} = everything gated off); anything else → defaults
        return v if isinstance(v, dict) else cls._default_of("capabilities")


TUNING_FILE = "tuning.yaml"
#: What routine.yaml's `schedule:` mapping may hold — the keys `cron`, `tz` and `catchup` load
#: from (their AliasPaths above).
SCHEDULE_KEYS = frozenset({"cron", "tz", "catchup"})


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
        raw = read_yaml(path, {})
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
        raw = read_yaml(path, {})
    except (OSError, yaml.YAMLError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    raw.update(updates)
    atomic_write_yaml(path, raw)


def record_grants(routine_dir: Path, updates: dict[str, bool]) -> None:
    """Persist grant-decision rows (entity id → bool) into routine.yaml's `grants:`
    mapping. Called ONLY by the web layer recording an explicit user decision (a
    Decisions-page allow/deny click, the routine page's editors) — the ENGINE writes no
    routine.yaml at all: a run's one-time grants live in memory on its RunContext.
    """
    path = routine_dir / "routine.yaml"
    raw = read_yaml(path, {})
    if not isinstance(raw, dict):
        raise TypeError(f"{path}: expected a mapping at top level")
    grants = raw.get("grants")
    grants = dict(grants) if isinstance(grants, dict) else {}
    grants.update({str(k): bool(v) for k, v in updates.items()})
    raw["grants"] = grants
    atomic_write_yaml(path, raw)


def load_routine(routine_dir: Path) -> tuple[RoutineConfig | None, list[str]]:
    """Parse <dir>/routine.yaml — the whole of the routine's config: nothing is layered under
    it, so what the file says is what the routine is. Returns (config, problems); invalid
    admission policy or exhausted recovery with an enabled gate also returns None. Other
    problems use best-effort recovery.
    """
    path = routine_dir / "routine.yaml"
    problems: list[str] = []
    try:
        raw = read_yaml(path, {})
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
    # The same check one level down: `schedule:` holds exactly the three keys its aliased
    # fields read. A stray one there is read by nothing — `schedule.disabled`, the off switch's
    # retired second spelling, most of all: it once had the final word over `enabled`.
    schedule_raw = raw.get("schedule")
    if isinstance(schedule_raw, dict):
        problems.extend(
            f"schedule.{key}: unknown routine.yaml key — "
            + ("a routine is switched off by the top-level `enabled: false`" if key == "disabled"
               else "check the spelling") + " (ignored)"
            for key in sorted(set(schedule_raw) - SCHEDULE_KEYS))
    try:
        gate = RunGateConfig.model_validate(raw.get("run_gate", {}))
    except ValidationError as exc:
        return None, [*problems, *_problem_lines(exc, ("run_gate",))]
    cfg = _validate_lenient(RoutineConfig, {**raw, "slug": slug, "dir": routine_dir}, problems)
    if cfg is None:
        if gate.enabled:
            return None, [*problems, "run_gate: enabled gate forbids whole-config fallback"]
        cfg = RoutineConfig(slug=slug, dir=routine_dir)
    cfg.run_gate = gate
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
    from ..entities import GUARDED_ROOT_REASON, guarded_roots, normalize_grants

    cfg.grants, grant_problems = normalize_grants(cfg.grants)
    problems += grant_problems
    # A credential store already listed as a folder grant. REPORTED, never dropped: this is
    # the one problem where degrading quietly is worse than the problem — two live routines
    # audit and export the server's own configuration as their actual job, and a root that
    # vanishes from under their next run fails them with nothing naming the cause. The PATCH
    # edges refuse a NEW one (web/config_fields.validate_roots); this is how an existing one stops
    # being silent, on the routine page and in `rsched validate`.
    for key in ("fs_read_roots", "fs_write_roots"):
        for root in guarded_roots(getattr(cfg, key)):
            problems.append(f"{key}: {root} {GUARDED_ROOT_REASON}. Every run of this routine "
                            "has it mounted — narrow the grant to what the recipe needs.")
    from ..triggers import validate_triggers

    cfg.triggers, trigger_problems = validate_triggers(cfg.triggers)
    problems += trigger_problems

    # A routine is self-contained: its recipe is materialized into main.md at generation, and the
    # workflow.library_slug is kept only as "generated-from" provenance.
    if not (routine_dir / "main.md").exists():
        problems.append("no main.md — the routine's recipe was not materialized in")
    return cfg, problems
