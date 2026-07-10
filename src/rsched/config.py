"""Server config (~/.config/routine-scheduler/config.yaml) and routine.yaml loading.

Both loaders validate strictly enough to catch typos early and return dataclasses with
defaults applied. Validation problems are collected into lists so callers (registry,
`rsched validate`) can report all of them at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

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
# improvement passes. `communication` is available but opt-in (it needs a Discord util).
DEFAULT_FRAGMENTS = ["ask-policy", "global-utils", "ledger-discipline", "web-research",
                     "improve-bugfix", "improve-research", "improve-features",
                     "improve-ui", "improve-efficiency"]
# Each routine picks its own three models: the MAIN orchestrator loop, the model spawned
# SUBROUTINEs run their main loop on, and the model TOOL_CALLs (the `llm` action) use.
MODEL_KINDS = ("main", "subroutine", "tool_call")
# Endpoints are model TRANSPORTS, never a second harness. "claude-cli" is the Claude Code
# CLI in fully stripped print mode (tools off, our system prompt replaces its own) — a
# subscription-billed completion function; the engine remains the only agent loop.
ENDPOINT_KINDS = ("openai", "anthropic", "claude-cli")
SCHEMA_MODES = ("json_schema", "json_object", "ollama_native", "none")


@dataclass
class EndpointConfig:
    name: str
    kind: str
    base_url: str = ""
    api_key: str = ""
    key_env_file: str = ""
    key_var: str = "ANTHROPIC_API_KEY"
    credentials_env: str = "~/.credentials/claude-code-oauth.env"  # claude-cli kind
    schema_mode: str = "json_schema"  # openai kind only
    context_chars: int = 100_000
    temperature: float | None = None


@dataclass
class ModelRef:
    endpoint: str
    model: str
    effort: str | None = None


@dataclass
class ServerConfig:
    bind: str = "127.0.0.1"
    port: int = 8321
    token: str = ""
    routines_home: Path = field(default_factory=lambda: expand("~/routines"))
    # ONE git repo holding workflows/, fragments/, utils/ (+ gu, README) — the library.
    libraries_home: Path = field(
        default_factory=lambda: expand("~/.local/share/routine-scheduler-libraries"))
    libraries_remote: str = ""          # clone-from / sync-to for the library repo
    source_repo: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2])
    source_remote: str = ""             # optional: push target for self-audit's autonomous code commits
    github_client_id: str = ""          # OAuth app client_id for the in-UI device flow (default: gh CLI's)
    confirm_util_changes: bool = True   # ask the user before a util is created/revised (req 7)
    max_concurrent_runs: int = 2
    registry_rescan_s: int = 30
    endpoints: dict[str, EndpointConfig] = field(default_factory=dict)
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


def _model_ref(raw: object, problems: list[str], where: str) -> ModelRef | None:
    if not isinstance(raw, dict) or "endpoint" not in raw or "model" not in raw:
        problems.append(f"{where}: expected mapping with 'endpoint' and 'model'")
        return None
    return ModelRef(endpoint=str(raw["endpoint"]), model=str(raw["model"]),
                    effort=raw.get("effort"))


def load_server_config(path: Path | None = None) -> tuple[ServerConfig, list[str]]:
    path = path or config_file()
    problems: list[str] = []
    raw: dict = {}
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            return ServerConfig(source=path), [f"{path}: invalid YAML: {exc}"]
    else:
        problems.append(f"{path}: not found (using defaults; run deploy/install.sh)")

    cfg = ServerConfig(source=path)
    cfg.bind = str(raw.get("bind", cfg.bind))
    cfg.port = int(raw.get("port", cfg.port))
    cfg.token = str(raw.get("token", "") or "")
    if "routines_home" in raw:
        cfg.routines_home = expand(raw["routines_home"])
    if "libraries_home" in raw:
        cfg.libraries_home = expand(raw["libraries_home"])
    cfg.libraries_remote = str(raw.get("libraries_remote", "") or "")
    if "source_repo" in raw:
        cfg.source_repo = expand(raw["source_repo"])
    cfg.source_remote = str(raw.get("source_remote", "") or "")
    cfg.github_client_id = str(raw.get("github_client_id", "") or "")
    cfg.confirm_util_changes = bool(raw.get("confirm_util_changes", cfg.confirm_util_changes))
    cfg.max_concurrent_runs = int(raw.get("max_concurrent_runs", cfg.max_concurrent_runs))
    cfg.registry_rescan_s = int(raw.get("registry_rescan_s", cfg.registry_rescan_s))

    for name, spec in (raw.get("endpoints") or {}).items():
        if not isinstance(spec, dict):
            problems.append(f"endpoints.{name}: expected a mapping")
            continue
        kind = spec.get("kind", "")
        if kind not in ENDPOINT_KINDS:
            problems.append(f"endpoints.{name}: kind must be one of {ENDPOINT_KINDS}, got {kind!r}")
            continue
        ep = EndpointConfig(name=name, kind=kind)
        for key in ("base_url", "api_key", "key_env_file", "key_var", "credentials_env",
                    "schema_mode"):
            if key in spec:
                setattr(ep, key, str(spec[key]))
        if ep.schema_mode not in SCHEMA_MODES:
            problems.append(f"endpoints.{name}: schema_mode must be one of {SCHEMA_MODES}")
            ep.schema_mode = "none"
        if "context_chars" in spec:
            ep.context_chars = int(spec["context_chars"])
        if "temperature" in spec:
            ep.temperature = float(spec["temperature"])
        cfg.endpoints[name] = ep

    if "system_model" in raw and raw["system_model"]:
        ref = _model_ref(raw["system_model"], problems, "system_model")
        if ref:
            if ref.endpoint not in cfg.endpoints:
                problems.append(f"system_model: endpoint {ref.endpoint!r} is not configured")
            cfg.system_model = ref

    return cfg, problems


@dataclass
class RoutineConfig:
    slug: str
    dir: Path
    name: str = ""
    enabled: bool = True
    tags: list[str] = field(default_factory=list)  # freeform, for filtering (e.g. "meta")
    cron: str = ""
    tz: str = "Europe/Berlin"
    catchup: str = "skip"  # skip | run_once
    workflow_slug: str = ""
    workflow_commit: str = ""
    description: str = ""  # one-line human summary shown in the UI (always present)
    models: dict[str, ModelRef] = field(default_factory=dict)  # main/subroutine/tool_call
    budgets: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_BUDGETS))
    fragments: list[str] = field(default_factory=list)       # active fragment slugs (the source of truth)
    fs_read_roots: list[Path] = field(default_factory=list)
    fs_write_roots: list[Path] = field(default_factory=list)
    confirm_util_changes: bool | None = None  # None = inherit the server default
    keep_runs: int = 30

    def confirm_utils(self, server: ServerConfig) -> bool:
        return server.confirm_util_changes if self.confirm_util_changes is None \
            else self.confirm_util_changes


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
    cfg = RoutineConfig(slug=slug, dir=routine_dir)
    if not is_slug(slug):
        problems.append(f"slug {slug!r} is not kebab-case")
    if slug != routine_dir.name:
        problems.append(f"slug {slug!r} does not match directory name {routine_dir.name!r}")
    cfg.name = str(raw.get("name") or slug)
    cfg.description = str(raw.get("description") or "").strip()
    if not cfg.description:
        problems.append("description is empty — every routine needs a one-line description (shown in the UI)")
    cfg.enabled = bool(raw.get("enabled", True))
    cfg.tags = [str(t).strip() for t in (raw.get("tags") or []) if str(t).strip()]

    sched = raw.get("schedule") or {}
    if isinstance(sched, dict):
        cfg.cron = str(sched.get("cron", "") or "")
        cfg.tz = str(sched.get("tz", cfg.tz))
        cfg.catchup = str(sched.get("catchup", cfg.catchup))
        if cfg.catchup not in ("skip", "run_once"):
            problems.append(f"schedule.catchup must be skip|run_once, got {cfg.catchup!r}")
            cfg.catchup = "skip"
    else:
        problems.append("schedule: expected a mapping")
    if cfg.cron:
        try:
            from croniter import croniter

            croniter(cfg.cron)
        except (ValueError, KeyError) as exc:
            problems.append(f"schedule.cron {cfg.cron!r}: {exc}")
            cfg.cron = ""

    wf = raw.get("workflow") or {}
    if isinstance(wf, dict):
        cfg.workflow_slug = str(wf.get("library_slug", "") or "")
        cfg.workflow_commit = str(wf.get("library_commit", "") or "")

    for kind, spec in (raw.get("models") or {}).items():
        if kind not in MODEL_KINDS:
            problems.append(f"models.{kind}: unknown model kind (expected one of {MODEL_KINDS})")
            continue
        ref = _model_ref(spec, problems, f"models.{kind}")
        if ref:
            cfg.models[kind] = ref

    for key, val in (raw.get("budgets") or {}).items():
        if key not in DEFAULT_BUDGETS:
            problems.append(f"budgets.{key}: unknown budget")
            continue
        try:
            cfg.budgets[key] = int(val)
        except (TypeError, ValueError):
            problems.append(f"budgets.{key}: expected an integer")

    # Fragments are the source of truth for a routine's standards. An explicit list wins;
    # otherwise a new routine gets the default set.
    if isinstance(raw.get("fragments"), list):
        cfg.fragments = [str(f) for f in raw["fragments"]]
    else:
        cfg.fragments = list(DEFAULT_FRAGMENTS)

    for key, target in (("fs_read_roots", cfg.fs_read_roots), ("fs_write_roots", cfg.fs_write_roots)):
        for item in raw.get(key) or []:
            target.append(expand(item))

    if "confirm_util_changes" in raw:
        cfg.confirm_util_changes = bool(raw["confirm_util_changes"])
    ret = raw.get("retention") or {}
    if isinstance(ret, dict) and "keep_runs" in ret:
        try:
            cfg.keep_runs = int(ret["keep_runs"])
        except (TypeError, ValueError):
            problems.append("retention.keep_runs: expected an integer")

    # A routine is self-contained: its recipe is materialized into main.md at generation, and the
    # workflow.library_slug is kept only as "generated-from" provenance.
    if not (routine_dir / "main.md").exists():
        problems.append("no main.md — the routine's recipe was not materialized in")
    if not (routine_dir / "instruction.md").exists():
        problems.append("instruction.md missing")
    return cfg, problems
