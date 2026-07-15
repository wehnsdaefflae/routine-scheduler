"""Server config (~/.config/routine-scheduler/config.yaml) and routine.yaml loading.

Both loaders validate through pydantic, leniently: every invalid key is reported into a
problems list (so callers — registry, `rsched validate` — can show all of them at once)
and falls back to its default instead of failing the whole load.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, get_args

import yaml
from pydantic import (AliasPath, BaseModel, BeforeValidator, ConfigDict, Field,
                      ValidationError, field_validator)

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
# Kinds that are multimodal by construction, so an endpoint of this kind defaults to native
# image/PDF input unless the user says otherwise (the `anthropic` Messages API and the
# subscription CLI both take image blocks). `openai` varies per model (GLM 5.2 is text-only,
# most vision models aren't) so it defaults OFF — the user flips it on per endpoint.
NATIVE_MM_KINDS = {"anthropic", "claude-cli"}

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
    key_var: str = "ANTHROPIC_API_KEY"
    credentials_env: str = "~/.credentials/claude-code-oauth.env"  # claude-cli kind
    schema_mode: SchemaMode = "json_schema"  # openai kind only
    context_chars: int = 100_000
    temperature: float | None = None
    # Native image/PDF input: None = default by kind (see NATIVE_MM_KINDS — on for
    # anthropic/claude-cli, off for openai); a bool overrides. When off, images/PDFs the
    # orchestrator views route to the `vision` util instead of the endpoint itself.
    multimodal: bool | None = None
    # openai kind only: merged verbatim into every request body. This is where aggregator
    # routing lives — e.g. OpenRouter {"provider": {"ignore": [...]}} to exclude serving
    # providers whose constrained decoding measurably corrupts output (drops declared
    # fields, leaks foreign keys through "strict" mode).
    extra_body: dict = Field(default_factory=dict)

    def native_multimodal(self) -> bool:
        """Effective multimodal capability: the explicit flag, else the kind default."""
        return self.multimodal if self.multimodal is not None else (self.kind in NATIVE_MM_KINDS)


@dataclass
class ModelRef:
    """A model assignment: which endpoint serves it, the provider's model id, and an
    optional reasoning-effort hint the adapters map to their provider's knob."""

    endpoint: str
    model: str
    effort: str | None = None


class LibrarySyncConfig(_Config):
    """The daemon-scheduled library sync (`library_sync:` in config.yaml): mirror the
    instance into the ONE library repo and git-sync it. Deliberately NOT a routine —
    the exact same commands every time, no LLM in the path (see library_sync.py)."""

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
    system model for pre-routine machine work."""

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
    # ONE git repo holding workflows/, traits/, permissions/, playbooks/, utils/ (+ gu, README) — the library.
    libraries_home: HomePath = Field(
        default_factory=lambda: expand("~/.local/share/routine-scheduler-libraries"))
    libraries_remote: BlankableStr = ""  # clone-from / sync-to for the library repo
    source_repo: HomePath = Field(default_factory=lambda: Path(__file__).resolve().parents[2])
    source_remote: BlankableStr = ""     # optional: push target for self-audit's autonomous code commits
    github_client_id: BlankableStr = ""  # OAuth app client_id for the in-UI device flow (default: gh CLI's)
    max_concurrent_runs: int = 2
    registry_rescan_s: int = 30
    endpoints: dict[str, EndpointConfig] = Field(default_factory=dict)
    # The ONE fallback model for machine work that isn't a routine yet: workflow
    # generation/suggestion and the new-routine clarify wizard. Routines set their own models.
    system_model: ModelRef | None = None
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
        """The library repo's playbooks/ subdir (reusable conversation briefs — see playbooks.py)."""
        return self.libraries_home / "playbooks"


def _pop(data: dict, loc: tuple) -> None:
    """Remove the value at a (possibly nested) error location from the raw input."""
    for key in loc[:-1]:
        data = data.get(key) if isinstance(data, dict) else None
    if isinstance(data, dict) and loc:
        data.pop(loc[-1], None)


def _validate_lenient(model: type[_Config], data: dict, problems: list[str]):
    """model_validate that degrades per key: report every invalid key, drop it (or its
    parent, when a required subfield is missing) and retry so the rest still loads."""
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

    cfg = _validate_lenient(ServerConfig, {**raw, "source": path}, problems) \
        or ServerConfig(source=path)
    for name, ep in cfg.endpoints.items():
        ep.name = name
    if cfg.system_model and cfg.system_model.endpoint not in cfg.endpoints:
        problems.append(f"system_model: endpoint {cfg.system_model.endpoint!r} is not configured")
    return cfg, problems


class RoutineConfig(_Config):
    """One routine's `routine.yaml`: schedule, models (main/subroutine/tool_call/uncensored),
    budgets, held permissions, filesystem roots, and retention. The instruction and
    workflow live next to it as `instruction.md` / `main.md`; its adapted practice
    prose under `traits/`."""

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
    models: dict[str, ModelRef] = Field(default_factory=dict)  # main/subroutine/tool_call/uncensored
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
    def _none_as_absent(cls, v: object) -> object:
        return cls.model_fields["models"].default_factory() if v is None else v

    @field_validator("budgets", mode="before")
    @classmethod
    def _merged_over_defaults(cls, v: object) -> object:
        if not isinstance(v, dict):
            return v
        # legacy key: routine.yaml written before the timeout moved to minutes — convert,
        # never drop (an existing 8h stays 8h, just expressed as 480m)
        if "ask_timeout_h" in v:
            v = dict(v)
            legacy = v.pop("ask_timeout_h")
            v.setdefault("ask_timeout_min", int(legacy) * 60)
        return {**DEFAULT_BUDGETS, **v}

    @field_validator("permissions", mode="before")
    @classmethod
    def _default_unless_list(cls, v: object) -> object:
        return [str(f) for f in v] if isinstance(v, list) else list(DEFAULT_PERMISSIONS)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _default_unless_mapping(cls, v: object) -> object:
        # an explicit mapping wins ({} = everything gated off); anything else → defaults
        return v if isinstance(v, dict) else cls.model_fields["capabilities"].default_factory()


def load_routine(routine_dir: Path) -> tuple[RoutineConfig | None, list[str]]:
    """Parse <dir>/routine.yaml. Returns (config, problems); config is None only when the
    file is missing/unreadable — otherwise problems may be non-empty but best-effort applies."""
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
        problems.append("description is empty — every routine needs a one-line description (shown in the UI)")
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
    if not (routine_dir / "instruction.md").exists():
        problems.append("instruction.md missing")
    return cfg, problems
