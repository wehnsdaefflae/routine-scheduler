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
DEFAULT_SELF = {"audit": True, "improve": True, "ledger": True, "fresh_eyes": True, "hygiene": True}
DEFAULT_ALLOWLIST = ["gu *", "git *", "uv run --script *"]
# legacy self-toggle → fragment slug
SELF_FRAGMENT = {"audit": "self-audit", "improve": "improvement", "ledger": "ledger-discipline",
                 "fresh_eyes": "fresh-eyes", "hygiene": "hygiene"}
# always active regardless of the legacy toggles (util guidance + ask policy)
BASE_FRAGMENTS = ["ask-policy", "global-utils"]


def fragments_from_self(self_flags: dict) -> list[str]:
    frags = list(BASE_FRAGMENTS)
    for flag, slug in SELF_FRAGMENT.items():
        if self_flags.get(flag, True):
            frags.append(slug)
    return frags
ROLES = ("orchestrator", "subcall", "cheap")
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
class RoleRef:
    endpoint: str
    model: str
    effort: str | None = None


@dataclass
class ServerConfig:
    bind: str = "127.0.0.1"
    port: int = 8321
    token: str = ""
    routines_home: Path = field(default_factory=lambda: expand("~/routines"))
    library_home: Path = field(default_factory=lambda: expand("~/.local/share/workflow-library"))
    library_remote: str = ""            # optional: clone-from / sync-to for the workflow library
    fragments_home: Path = field(default_factory=lambda: expand("~/.local/share/routine-fragments"))
    fragments_remote: str = ""          # optional: clone-from / sync-to for the fragment library
    utils_home: Path = field(default_factory=lambda: expand("~/.local/share/global-utils"))
    utils_remote: str = ""              # optional: clone-from / sync-to for the util library
    confirm_util_changes: bool = True   # ask the user before a util is created/revised (req 7)
    max_concurrent_runs: int = 2
    registry_rescan_s: int = 30
    endpoints: dict[str, EndpointConfig] = field(default_factory=dict)
    default_roles: dict[str, RoleRef] = field(default_factory=dict)
    source: Path | None = None


def _role_ref(raw: object, problems: list[str], where: str) -> RoleRef | None:
    if not isinstance(raw, dict) or "endpoint" not in raw or "model" not in raw:
        problems.append(f"{where}: expected mapping with 'endpoint' and 'model'")
        return None
    return RoleRef(endpoint=str(raw["endpoint"]), model=str(raw["model"]),
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
    if "library_home" in raw:
        cfg.library_home = expand(raw["library_home"])
    if "utils_home" in raw:
        cfg.utils_home = expand(raw["utils_home"])
    if "fragments_home" in raw:
        cfg.fragments_home = expand(raw["fragments_home"])
    cfg.library_remote = str(raw.get("library_remote", "") or "")
    cfg.fragments_remote = str(raw.get("fragments_remote", "") or "")
    cfg.utils_remote = str(raw.get("utils_remote", "") or "")
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

    for role, spec in (raw.get("default_roles") or {}).items():
        if role not in ROLES:
            problems.append(f"default_roles.{role}: unknown role (expected one of {ROLES})")
            continue
        ref = _role_ref(spec, problems, f"default_roles.{role}")
        if ref:
            if ref.endpoint not in cfg.endpoints:
                problems.append(f"default_roles.{role}: endpoint {ref.endpoint!r} is not configured")
            cfg.default_roles[role] = ref

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
    roles: dict[str, RoleRef] = field(default_factory=dict)  # overrides; server defaults fill gaps
    budgets: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_BUDGETS))
    fragments: list[str] = field(default_factory=list)       # active fragment slugs (the source of truth)
    self_flags: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_SELF))  # legacy migration source
    shell_allowlist: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWLIST))  # legacy, unused (no shell)
    fs_read_roots: list[Path] = field(default_factory=list)
    fs_write_roots: list[Path] = field(default_factory=list)
    confirm_util_changes: bool | None = None  # None = inherit the server default
    notifications: str = "ui"
    keep_runs: int = 30

    def resolve_roles(self, server: ServerConfig) -> dict[str, RoleRef]:
        roles = dict(server.default_roles)
        roles.update(self.roles)
        return roles

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

    for role, spec in (raw.get("endpoints") or {}).items():
        if role not in ROLES:
            problems.append(f"endpoints.{role}: unknown role (expected one of {ROLES})")
            continue
        ref = _role_ref(spec, problems, f"endpoints.{role}")
        if ref:
            cfg.roles[role] = ref

    for key, val in (raw.get("budgets") or {}).items():
        if key not in DEFAULT_BUDGETS:
            problems.append(f"budgets.{key}: unknown budget")
            continue
        try:
            cfg.budgets[key] = int(val)
        except (TypeError, ValueError):
            problems.append(f"budgets.{key}: expected an integer")

    for key, val in (raw.get("self") or {}).items():
        if key not in DEFAULT_SELF:
            problems.append(f"self.{key}: unknown toggle")
            continue
        cfg.self_flags[key] = bool(val)

    # Fragments are the source of truth for a routine's standards. Explicit list wins;
    # otherwise migrate from the legacy `self` toggles (+ the always-on defaults).
    if isinstance(raw.get("fragments"), list):
        cfg.fragments = [str(f) for f in raw["fragments"]]
    else:
        cfg.fragments = fragments_from_self(cfg.self_flags)

    if "shell_allowlist" in raw:
        al = raw["shell_allowlist"]
        if isinstance(al, list) and all(isinstance(x, str) for x in al):
            cfg.shell_allowlist = al
        else:
            problems.append("shell_allowlist: expected a list of strings")
    for key, target in (("fs_read_roots", cfg.fs_read_roots), ("fs_write_roots", cfg.fs_write_roots)):
        for item in raw.get(key) or []:
            target.append(expand(item))

    if "confirm_util_changes" in raw:
        cfg.confirm_util_changes = bool(raw["confirm_util_changes"])
    cfg.notifications = str(raw.get("notifications", cfg.notifications))
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
