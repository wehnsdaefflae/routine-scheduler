"""Server config (~/.config/routine-scheduler/config.yaml) and routine.yaml loading.

Both loaders validate through pydantic, leniently: every invalid key is reported into a
problems list (so callers — registry, `rsched validate` — can show all of them at once)
and falls back to its default instead of failing the whole load.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, cast, get_args

import yaml
from pydantic import (
    AliasPath,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .ids import is_slug
from .paths import config_file, expand

DEFAULT_BUDGETS = {
    "max_turns": 60,
    # -1 = unlimited (default): a cumulative turn ceiling across ALL of a run's resume
    # windows (a conversation's whole life is the sum of its replies). max_turns bounds ONE
    # window (one reply); max_total_turns bounds the whole conversation. Scheduled routines
    # run a single window, so this is inert for them unless explicitly set.
    "max_total_turns": -1,
    "max_wall_clock_min": 45,
    # -1 = unlimited: for max_total_tokens this is the default (every finite cap we tried,
    # 500k then 1.5M, eventually cut off legitimate work). max_wall_clock_min and max_cost
    # also honor -1 = unlimited, so an operator can lift the time, token, and dollar ceilings
    # independently and let max_turns be the sole backstop. Set a positive number to bound
    # spend on a specific routine. max_cost is a whole-dollar ceiling on real provider spend
    # (usage["cost"], reported today only by metered endpoints like OpenRouter); -1 = no cap.
    "max_total_tokens": -1,
    "max_cost": -1,
    "max_subruns": 8,
    "max_subrun_depth": 2,
    "ask_timeout_min": 5,
}
# The two-layer permission defaults a new routine gets when routine.yaml is silent.
# PERMISSIONS are conduct docs (library prose reaching the prompt when held);
# CAPABILITIES are the atomic machine-enforced surface (see grants.py) — gated action
# kinds, reserved utils, the write_util approval level, previous-run read depth. The two
# stay consistent via the web layer's cascades: activating a doc switches on what its
# `requires:` names; switching a capability off deactivates the docs requiring it.
# `communication` (discord), `run-history` depth and `shell` stay opt-in. There is NO
# self-modification permission: a run never edits its own recipe or routine.yaml — the
# routine-improver meta routine refines recipes centrally (its fs_write_roots covering
# the homes is the one engine-recognized unlock). Defaults added here AFTER routines
# exist reach them via bootstrap.ADOPT_PERMISSIONS (one-time, at boot).
DEFAULT_PERMISSIONS = ["util-authoring", "memory"]
DEFAULT_CAPABILITIES = {"actions": ["write_util", "memory_read", "memory_write"],
                        "utils": [], "confirm": "always", "runs": "none",
                        "workflows": "catalog"}
# TRAITS a new routine gets when creation picks none explicitly (the wizard normally
# preselects per task): reusable practice prose, adapted into the routine's own traits/
# at creation and referenced from the end of its main.md. Not toggleable afterwards —
# they are the routine's files from then on. Improvement passes are NOT part of a
# routine's own traits: the routine-improver meta routine runs them across all routines
# and conversations (honoring each one's `improve: false` opt-out).
DEFAULT_TRAITS = ["ask-policy", "global-utils", "ledger-discipline", "web-research"]
# Each routine picks its own models: the MAIN orchestrator loop, the model spawned
# SUBROUTINEs run their main loop on, the model TOOL_CALLs (the `llm` action) use, and an
# OPTIONAL UNCENSORED model a refused `llm` tool-call is re-referred to. The uncensored role
# is opt-in and has NO system_model fallback: a routine refers a refusal ONLY when it has
# explicitly configured this role (e.g. to a Nano-GPT abliterated model). Leaving it unset
# preserves the previous behaviour exactly.
MODEL_KINDS = ("main", "subroutine", "tool_call", "uncensored")
# Endpoints are model TRANSPORTS, never a second harness. "claude-cli" is the Claude Code
# CLI in fully stripped print mode (tools off, our system prompt replaces its own) — a
# subscription-billed completion function; the engine remains the only agent loop.
EndpointKind = Literal["openai", "anthropic", "claude-cli"]
SchemaMode = Literal["json_schema", "json_object", "ollama_native", "none"]
ENDPOINT_KINDS = get_args(EndpointKind)
SCHEMA_MODES = get_args(SchemaMode)
# Kinds whose models are multimodal by construction, so a catalog model on an endpoint of this
# kind defaults to native image/PDF input unless the model says otherwise (the `anthropic`
# Messages API and the subscription CLI only ever serve Claude, which takes image blocks).
# `openai` varies per model (GLM is text-only, GPT-4o/Gemini aren't) so it defaults OFF — the
# user flips `multimodal` on for the specific catalog model (see ModelConfig).
NATIVE_MM_KINDS = {"anthropic", "claude-cli"}
# The secrets-store / env-file variable an endpoint's key is looked up under when the
# config doesn't set `key_var`. Per KIND: an openai endpoint must never default to the
# Anthropic key. claude-cli has no entry — it authenticates via the subscription token
# (`credentials_env` / CLAUDE_CODE_OAUTH_TOKEN), never key_var.
KEY_VAR_DEFAULTS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}

# YAML-friendly coercions: a bare `key:` (null) reads as the empty string; path strings
# expand `~` and $VARS.
BlankableStr = Annotated[str, BeforeValidator(lambda v: "" if v is None else v)]
HomePath = Annotated[Path, BeforeValidator(lambda v: expand(v) if isinstance(v, str) else v)]


class _Config(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True,
                              coerce_numbers_to_str=True)


class EndpointConfig(_Config):
    """One configured model transport (see docs/endpoints.md for the setup guide)."""

    name: str = ""  # filled from the `endpoints:` mapping key
    kind: EndpointKind
    base_url: BlankableStr = ""
    api_key: BlankableStr = ""
    key_env_file: BlankableStr = ""
    key_var: BlankableStr = ""  # unset → the endpoint kind's KEY_VAR_DEFAULTS entry
    credentials_env: str = "~/.credentials/claude-code-oauth.env"  # claude-cli kind
    schema_mode: SchemaMode = "json_schema"  # openai kind only
    # DEFAULTS a catalog model inherits when it leaves the field unset. Per-model attributes
    # live on ModelConfig now — one endpoint serves many models with different windows,
    # vision support, and sampling. context_chars ≈ 4 × the token window.
    context_chars: int = 100_000
    temperature: float | None = None
    # openai kind only: merged verbatim into every request body. This is where aggregator
    # routing lives — e.g. OpenRouter {"provider": {"ignore": [...]}} to exclude serving
    # providers whose constrained decoding measurably corrupts output (drops declared
    # fields, leaks foreign keys through "strict" mode).
    extra_body: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _kind_default_key_var(self):
        if not self.key_var:
            self.key_var = KEY_VAR_DEFAULTS.get(self.kind, "")
        return self


class ModelConfig(_Config):
    """One catalog model: a provider model id BOUND to a configured endpoint, plus the
    per-model attributes that used to (wrongly) sit on the endpoint. One endpoint serves
    many models, so multimodality, context window, effort, and temperature belong here.
    A None attribute inherits the serving endpoint's default (multimodal → the endpoint
    kind's NATIVE_MM_KINDS default; context_chars/temperature → the endpoint's own).
    Routines/conversations reference a model by its catalog NAME (see RoutineConfig.models).
    """

    name: str = ""  # filled from the `models:` mapping key
    endpoint: str   # which configured endpoint transports this model
    model: str      # the provider's model id (e.g. "openai/gpt-4o")
    # None = inherit the endpoint kind default (anthropic/claude-cli on, openai off).
    multimodal: bool | None = None
    # None = inherit the endpoint's context_chars. ≈ 4 × the token window.
    context_chars: int | None = None
    effort: str | None = None          # reasoning-effort hint (low|medium|high|xhigh|max)
    temperature: float | None = None   # None = inherit the endpoint's temperature default


@dataclass
class ModelRef:
    """A RESOLVED model handle produced by EndpointRegistry from a catalog entry + its
    endpoint — no longer parsed from yaml. Carries the provider model id and every
    per-model attribute the run needs: reasoning effort, native multimodality, the
    context-window budget, sampling temperature, and the catalog name it came from.
    """

    endpoint: str
    model: str
    effort: str | None = None
    multimodal: bool = False
    context_chars: int = 100_000
    temperature: float | None = None
    name: str = ""


class LibrarySyncConfig(_Config):
    """The daemon-scheduled library sync (`library_sync:` in config.yaml): mirror the
    instance into the ONE library repo and git-sync it. Deliberately NOT a routine —
    the exact same commands every time, no LLM in the path (see library_sync.py).
    """

    enabled: bool = False
    cron: BlankableStr = "0 6 * * *"   # friendly-representable (daily 06:00) for the UI editor
    tz: str = "Europe/Berlin"

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


class ServerConfig(_Config):
    """The instance config (`~/.config/routine-scheduler/config.yaml`): bind/auth, the
    homes (routines, the one library repo, this source repo), endpoints, and the single
    system model for pre-routine machine work.
    """

    bind: str = "127.0.0.1"
    port: int = 8321
    token: BlankableStr = ""
    routines_home: HomePath = Field(default_factory=lambda: expand("~/routines"))
    # Conversations (interactive, Claude-Code-like sessions) are routine-shaped dirs under
    # their OWN home: schedule-less, un-versioned, one continuous run continued in place.
    conversations_home: HomePath = Field(default_factory=lambda: expand("~/conversations"))
    # Detached background tasks (long fire-and-forget jobs a conversation launches with the
    # `detach` action) are routine-shaped dirs under their OWN home too: daemon-managed,
    # each `routine.yaml` records its `owner` conversation, deleted after delivery.
    background_home: HomePath = Field(default_factory=lambda: expand("~/background"))
    # ONE git repo holding workflows/, traits/, permissions/, playbooks/, utils/ — the library.
    libraries_home: HomePath = Field(
        default_factory=lambda: expand("~/.local/share/routine-scheduler-libraries"))
    libraries_remote: BlankableStr = ""  # clone-from / sync-to for the library repo
    source_repo: HomePath = Field(default_factory=lambda: Path(__file__).resolve().parents[2])
    source_remote: BlankableStr = ""     # optional: self-audit's push target for code commits
    github_client_id: BlankableStr = ""  # OAuth client_id for the device flow (default: gh CLI's)
    max_concurrent_runs: int = 2
    registry_rescan_s: int = 30
    endpoints: dict[str, EndpointConfig] = Field(default_factory=dict)
    # The model CATALOG: name → a provider model bound to an endpoint, carrying its own
    # multimodality / context window / effort / temperature. Routines, conversations, and
    # the system model all reference an entry by NAME.
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    # The ONE fallback model for machine work that isn't a routine yet: workflow
    # generation/suggestion and the new-routine clarify wizard. A catalog model NAME;
    # routines set their own (also by name), falling back to this when a role is unset.
    system_model: str = ""
    # The scheduled instance→library sync job (Settings → Library sync).
    library_sync: LibrarySyncConfig = Field(default_factory=LibrarySyncConfig)
    source: Path | None = None

    @property
    def library_home(self) -> Path:
        """The library repo root — workflows live in its workflows/ subdir."""
        return self.libraries_home

    @property
    def traits_home(self) -> Path:
        """The library repo's traits/ subdir (reusable practice prose)."""
        return self.libraries_home / "traits"

    @property
    def permissions_home(self) -> Path:
        """The library repo's permissions/ subdir (engine-enforced capabilities)."""
        return self.libraries_home / "permissions"

    @property
    def utils_home(self) -> Path:
        """The library repo root — utils live in its utils/ subdir (with `gu` at the root)."""
        return self.libraries_home

    @property
    def playbooks_home(self) -> Path:
        """The library repo's playbooks/ subdir (reusable conversation briefs)."""
        return self.libraries_home / "playbooks"


def _pop(data: dict, loc: tuple) -> None:
    """Remove the value at a (possibly nested) error location from the raw input."""
    node: object = data
    for key in loc[:-1]:
        node = node.get(key) if isinstance(node, dict) else None
    if isinstance(node, dict) and loc:
        node.pop(loc[-1], None)


def _validate_lenient(model: type[_Config], data: dict, problems: list[str]):
    """model_validate that degrades per key: report every invalid key, drop it (or its
    parent, when a required subfield is missing) and retry so the rest still loads.
    """
    for round_no in range(4):
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            for err in exc.errors():
                if round_no == 0:  # later rounds only see errors derived from a drop
                    where = ".".join(str(p) for p in err["loc"]) or "(root)"
                    problems.append(f"{where}: {err['msg'].removeprefix('Value error, ')}")
                loc = err["loc"][:-1] if err["type"] == "missing" else err["loc"]
                if not loc:
                    return None
                _pop(data, loc)
    return None


def load_server_config(path: Path | None = None) -> tuple[ServerConfig, list[str]]:
    path = path or config_file()
    problems: list[str] = []
    raw: object = {}
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            return ServerConfig(source=path), [f"{path}: invalid YAML: {exc}"]
    else:
        problems.append(f"{path}: not found (using defaults; run deploy/install.sh)")
    if not isinstance(raw, dict):
        return ServerConfig(source=path), [f"{path}: expected a mapping at top level"]

    # extra="ignore" would drop a mistyped endpoint/model key silently — surface each as a
    # problem line (a warning; the entry still loads with the unknown key ignored).
    for section, cls in (("endpoints", EndpointConfig), ("models", ModelConfig)):
        entries = raw.get(section)
        if isinstance(entries, dict):
            for name, entry in entries.items():
                if isinstance(entry, dict):
                    problems.extend(f"{section}.{name}.{key}: unknown key (ignored)"
                                    for key in sorted(set(entry) - set(cls.model_fields)))

    cfg = _validate_lenient(ServerConfig, {**raw, "source": path}, problems) \
        or ServerConfig(source=path)
    for name, ep in cfg.endpoints.items():
        ep.name = name
    for name, mc in cfg.models.items():
        mc.name = name
        if mc.endpoint not in cfg.endpoints:
            problems.append(f"models.{name}: endpoint {mc.endpoint!r} is not configured")
    if cfg.system_model and cfg.system_model not in cfg.models:
        problems.append(f"system_model: {cfg.system_model!r} is not a catalog model")
    return cfg, problems


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
    keep_runs: int = Field(30, validation_alias=AliasPath("retention", "keep_runs"))
    # Whether the routine-improver meta routine visits this routine (default: yes; the
    # toggle on the routine page opts out with `improve: false`).
    improve: bool = True

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

    @field_validator("fs_read_roots", "fs_write_roots", "models", mode="before")
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

    cfg = _validate_lenient(RoutineConfig, {**raw, "slug": slug, "dir": routine_dir}, problems) \
        or RoutineConfig(slug=slug, dir=routine_dir)
    cfg.name = cfg.name or slug
    if not cfg.description:
        problems.append("description is empty — every routine needs a one-line "
                        "description (shown in the UI)")
    for kind in [k for k in cfg.models if k not in MODEL_KINDS]:
        problems.append(f"models.{kind}: unknown model kind (expected one of {MODEL_KINDS})")
        del cfg.models[kind]
    for key in [k for k in cfg.budgets if k not in DEFAULT_BUDGETS]:
        problems.append(f"budgets.{key}: unknown budget")
        del cfg.budgets[key]
    from .grants import normalize_capabilities  # function-level: grants imports engine.actions

    cfg.capabilities, cap_problems = normalize_capabilities(cfg.capabilities)
    problems += cap_problems

    # A routine is self-contained: its recipe is materialized into main.md at generation, and the
    # workflow.library_slug is kept only as "generated-from" provenance.
    if not (routine_dir / "main.md").exists():
        problems.append("no main.md — the routine's recipe was not materialized in")
    return cfg, problems
