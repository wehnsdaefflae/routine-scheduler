"""The instance config: ServerConfig (+ the library-sync job schedule) and its loader."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, field_validator

from ..paths import config_file, expand
from .base import BlankableStr, HomePath, _Config, _known_tz, _validate_lenient
from .modelconf import EndpointConfig, MachineConfig, ModelConfig


class LibrarySyncConfig(_Config):
    """The daemon-scheduled library sync (`library_sync:` in config.yaml): mirror the
    instance into the ONE library repo and git-sync it. Deliberately NOT a routine —
    the exact same commands every time, no LLM in the path (see library_sync.py).
    """

    enabled: bool = False
    cron: BlankableStr = "0 6 * * *"   # friendly-representable (daily 06:00) for the UI editor
    tz: str = "Europe/Berlin"

    _tz_known = field_validator("tz")(_known_tz)

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
    # External base URL a browser reaches this instance at (e.g. a Tailscale Serve https URL),
    # used to build OAuth redirect URIs: f"{public_url}/oauth/callback". NOT derivable from
    # bind/port (those are the listen address). Empty until set in Settings; the connect flow
    # refuses to start an auth-code flow without it. See docs/oauth-connections.md.
    public_url: BlankableStr = ""
    max_concurrent_runs: int = 2
    registry_rescan_s: int = 30
    # Util-subprocess sandbox mode (docs/sandboxing.md): every util runs inside a Landlock
    # filesystem/network jail derived from the run's permissions. "permissive" (default)
    # engages the jail whenever the kernel supports it and warns + runs unsandboxed when
    # not; "strict" refuses to run utils unsandboxed; "off" never wraps. Secrets scoping
    # (declared-only env injection) applies in EVERY mode — it needs no kernel support.
    sandbox: Literal["strict", "permissive", "off"] = "permissive"
    endpoints: dict[str, EndpointConfig] = Field(default_factory=dict)
    # The model CATALOG: name → a provider model bound to an endpoint, carrying its own
    # multimodality / context window / effort / temperature. Routines, conversations, and
    # the system model all reference an entry by NAME.
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    # The MACHINE catalog: name → an SSH-reachable host a routine may act on (GPU boxes,
    # build servers). A resource like the model catalog — operator-only; a routine binds one
    # by NAME (RoutineConfig.machines) and the reserved `remote` util acts on it. Key material
    # lives in the Secrets store (per-entry key_var), never here. See docs/remote-machines.md.
    machines: dict[str, MachineConfig] = Field(default_factory=dict)
    # The ONE fallback model for machine work that isn't a routine yet: workflow
    # generation/suggestion and the new-routine clarify wizard. A catalog model NAME;
    # routines set their own (also by name), falling back to this when a role is unset.
    system_model: str = ""
    # The scheduled instance→library sync job (Settings → Library sync).
    library_sync: LibrarySyncConfig = Field(default_factory=LibrarySyncConfig)
    source: Path | None = None

    @property
    def traits_home(self) -> Path:
        """The library repo's traits/ subdir (reusable practice prose)."""
        return self.libraries_home / "traits"

    @property
    def permissions_home(self) -> Path:
        """The library repo's permissions/ subdir (engine-enforced capabilities)."""
        return self.libraries_home / "permissions"

    @property
    def playbooks_home(self) -> Path:
        """The library repo's playbooks/ subdir (reusable conversation briefs)."""
        return self.libraries_home / "playbooks"


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

    # extra="ignore" would drop a mistyped key silently — the real field then reverts to
    # its default with ZERO trace (a misspelled `endpints:` = every endpoint gone).
    # Surface unknown top-level keys AND unknown per-entry keys as problem lines.
    problems.extend(f"{key}: unknown config.yaml key — check the spelling (ignored)"
                    for key in sorted(set(raw) - set(ServerConfig.model_fields))
                    if isinstance(raw, dict))
    for section, cls in (("endpoints", EndpointConfig), ("models", ModelConfig),
                         ("machines", MachineConfig)):
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
        for fb in mc.fallbacks:
            if fb == name:
                problems.append(f"models.{name}: fallbacks must not name the model itself")
            elif fb not in cfg.models:
                problems.append(f"models.{name}: fallback {fb!r} is not a catalog model")
    if cfg.system_model and cfg.system_model not in cfg.models:
        problems.append(f"system_model: {cfg.system_model!r} is not a catalog model")
    for name, mac in cfg.machines.items():
        mac.name = name
    return cfg, problems
