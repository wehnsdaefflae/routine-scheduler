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
    DELIBERATION_LEVELS,
    MODEL_KINDS,
    BlankableStr,
    HomePath,
    _Config,
    _known_tz,
    _validate_lenient,
)


class RoutineConfig(_Config):
    """One routine's `routine.yaml`: schedule, models (main/subroutine/tool_call/uncensored),
    budgets, held permissions, filesystem roots, and retention. The routine's recipe lives
    next to it as `main.md` + `stages/`; its adapted practice prose under `traits/`. (The
    instruction is a transient compile seed — decomposed into the stages at creation,
    never persisted.)
    """

    slug: str
    dir: Path
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
    # Role → catalog model NAME (main/subroutine/tool_call/uncensored). A role left unset
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
    budgets: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_BUDGETS))
    # The two permission layers (user-changeable only; explicit values win, otherwise a
    # new routine holds the defaults). `permissions` names the held CONDUCT docs (library
    # prose in the prompt); `capabilities` is the engine-enforced surface grants.py
    # loads the run policy from — {actions, utils, confirm, runs}. Traits (practice
    # prose) leave no yaml trace — they live as the routine's own files under traits/.
    permissions: list[str] = Field(default_factory=lambda: list(DEFAULT_PERMISSIONS))
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


def load_routine(routine_dir: Path) -> tuple[RoutineConfig | None, list[str]]:
    """Parse <dir>/routine.yaml. Returns (config, problems); config is None only when the
    file is missing/unreadable — otherwise problems may be non-empty but best-effort applies.
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
    # playbook.slug); `kind: conversation` is a deliberate marker pydantic drops. Any other
    # top-level key is a typo whose real field silently reverted to defaults (a misspelled
    # `permisions:` = a permission reset with zero problems reported).
    aliased = {"cron", "tz", "catchup", "workflow_slug", "workflow_commit",
               "playbook_slug", "keep_runs"}
    known = (set(RoutineConfig.model_fields) - aliased) | {"schedule", "workflow",
                                                           "playbook", "retention", "kind"}
    problems.extend(f"{key}: unknown routine.yaml key — check the spelling (ignored)"
                    for key in sorted(set(raw) - known))
    cfg = _validate_lenient(RoutineConfig, {**raw, "slug": slug, "dir": routine_dir}, problems) \
        or RoutineConfig(slug=slug, dir=routine_dir)
    cfg.name = cfg.name or slug
    if not cfg.description:
        problems.append("description is empty — every routine needs a one-line "
                        "description (shown in the UI)")
    for kind in [k for k in cfg.models if k not in MODEL_KINDS]:
        problems.append(f"models.{kind}: unknown model kind (expected one of {MODEL_KINDS})")
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
    from ..triggers import validate_triggers

    cfg.triggers, trigger_problems = validate_triggers(cfg.triggers)
    problems += trigger_problems

    # A routine is self-contained: its recipe is materialized into main.md at generation, and the
    # workflow.library_slug is kept only as "generated-from" provenance.
    if not (routine_dir / "main.md").exists():
        problems.append("no main.md — the routine's recipe was not materialized in")
    return cfg, problems
