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
    "max_wall_clock_min": 45,
    # The orchestrator re-sends the whole conversation every turn, so cumulative input
    # tokens dominate: ~60 turns × ~25k prompt ≈ 1.5M. 500k proved too tight in practice.
    "max_total_tokens": 1_500_000,
    "max_subruns": 8,
    "max_subrun_depth": 2,
    "ask_timeout_h": 8,
}
# The standards a new routine gets when its routine.yaml names no explicit `fragments:` list:
# the always-useful base (ask policy, tool use, memory, fact-checking) plus the five after-run
# improvement passes. Fragments also carry the routine's GRANTS (see grants.py):
# `util-authoring` is in the default so a new routine can write utils with user approval —
# the behavior routines always had. `communication` (grants the discord util) stays opt-in.
DEFAULT_FRAGMENTS = ["ask-policy", "global-utils", "util-authoring", "ledger-discipline",
                     "web-research", "improve-bugfix", "improve-research", "improve-features",
                     "improve-ui", "improve-efficiency"]
# Each routine picks its own three models: the MAIN orchestrator loop, the model spawned
# SUBROUTINEs run their main loop on, and the model TOOL_CALLs (the `llm` action) use.
MODEL_KINDS = ("main", "subroutine", "tool_call")
# Endpoints are model TRANSPORTS, never a second harness. "claude-cli" is the Claude Code
# CLI in fully stripped print mode (tools off, our system prompt replaces its own) — a
# subscription-billed completion function; the engine remains the only agent loop.
EndpointKind = Literal["openai", "anthropic", "claude-cli"]
SchemaMode = Literal["json_schema", "json_object", "ollama_native", "none"]
ENDPOINT_KINDS = get_args(EndpointKind)
SCHEMA_MODES = get_args(SchemaMode)

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
    # openai kind only: merged verbatim into every request body. This is where aggregator
    # routing lives — e.g. OpenRouter {"provider": {"ignore": [...]}} to exclude serving
    # providers whose constrained decoding measurably corrupts output (drops declared
    # fields, leaks foreign keys through "strict" mode).
    extra_body: dict = Field(default_factory=dict)


@dataclass
class ModelRef:
    """A model assignment: which endpoint serves it, the provider's model id, and an
    optional reasoning-effort hint the adapters map to their provider's knob."""

    endpoint: str
    model: str
    effort: str | None = None


class ServerConfig(_Config):
    """The instance config (`~/.config/routine-scheduler/config.yaml`): bind/auth, the
    homes (routines, the one library repo, this source repo), endpoints, and the single
    system model for pre-routine machine work."""

    bind: str = "127.0.0.1"
    port: int = 8321
    token: BlankableStr = ""
    routines_home: HomePath = Field(default_factory=lambda: expand("~/routines"))
    # ONE git repo holding workflows/, fragments/, utils/ (+ gu, README) — the library.
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
    source: Path | None = None

    @property
    def library_home(self) -> Path:
        """The library repo root — workflows live in its workflows/ subdir."""
        return self.libraries_home

    @property
    def fragments_home(self) -> Path:
        """The library repo's fragments/ subdir."""
        return self.libraries_home / "fragments"

    @property
    def utils_home(self) -> Path:
        """The library repo root — utils live in its utils/ subdir (with `gu` at the root)."""
        return self.libraries_home


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
    """One routine's `routine.yaml`: schedule, models (main/subroutine/tool_call),
    budgets, active fragments, filesystem roots, and retention. The instruction and
    workflow live next to it as `instruction.md` / `main.md`."""

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
    description: BlankableStr = ""  # one-line human summary shown in the UI (always present)
    models: dict[str, ModelRef] = Field(default_factory=dict)  # main/subroutine/tool_call
    budgets: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_BUDGETS))
    # Fragments are the source of truth for a routine's standards AND its granted
    # capabilities (the grants are machine-read from the LIBRARY copies — see grants.py).
    # An explicit list wins; otherwise a new routine gets the default set.
    fragments: list[str] = Field(default_factory=lambda: list(DEFAULT_FRAGMENTS))
    fs_read_roots: list[HomePath] = Field(default_factory=list)
    fs_write_roots: list[HomePath] = Field(default_factory=list)
    keep_runs: int = Field(30, validation_alias=AliasPath("retention", "keep_runs"))

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
        return {**DEFAULT_BUDGETS, **v} if isinstance(v, dict) else v

    @field_validator("fragments", mode="before")
    @classmethod
    def _default_unless_list(cls, v: object) -> object:
        return [str(f) for f in v] if isinstance(v, list) else list(DEFAULT_FRAGMENTS)


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

    # A routine is self-contained: its recipe is materialized into main.md at generation, and the
    # workflow.library_slug is kept only as "generated-from" provenance.
    if not (routine_dir / "main.md").exists():
        problems.append("no main.md — the routine's recipe was not materialized in")
    if not (routine_dir / "instruction.md").exists():
        problems.append("instruction.md missing")
    return cfg, problems
