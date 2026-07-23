"""Transport-side config: endpoints, the model catalog (+ the resolved ModelRef), and
remote-machine catalog entries.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field, model_validator

from .base import (
    DEFAULT_MODEL_MAX_TOKENS,
    KEY_VAR_DEFAULTS,
    BlankableStr,
    EndpointKind,
    SchemaMode,
    _Config,
)


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
    max_tokens: int | None = None   # None → DEFAULT_MODEL_MAX_TOKENS at resolve time
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
    # Max OUTPUT tokens per completion — the model's real output limit. None = inherit the
    # endpoint's max_tokens, else DEFAULT_MODEL_MAX_TOKENS. Settings flags unset/implausible
    # values so "set correctly" is auditable.
    max_tokens: int | None = None
    # Ordered failover chain: catalog model NAMES tried in order when this model fails hard
    # (transport retries exhausted / non-retryable error). NOT transitive — only this list is
    # tried, each entry with its own endpoint and attributes. See endpoints/failover.py.
    fallbacks: list[str] = Field(default_factory=list)


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
    max_tokens: int = DEFAULT_MODEL_MAX_TOKENS
    name: str = ""


class MachineConfig(_Config):
    """One catalog machine: an SSH-reachable host a routine may act on (a GPU box, a build
    server). Instance-wide config (config.yaml `machines:`), operator-only — a routine binds a
    machine by NAME (RoutineConfig.machines), never creates one. Key MATERIAL never lives here:
    `key_var` names a Secrets-store key holding the private key (the one credential); the pinned
    `host_key` is the server's PUBLIC host key, verified strictly at connect. See
    docs/remote-machines.md.
    """

    name: str = ""          # filled from the `machines:` mapping key
    host: str               # hostname or IP the run connects to
    user: str               # ssh login user
    port: int = 22
    key_var: BlankableStr = ""      # Secrets-store key NAME holding the private key (PEM)
    # The server's pinned host key line ("ssh-ed25519 AAAA…"), verified strictly at connect
    # (no TOFU in a headless run). Empty → the `remote` util refuses to connect; scan it in
    # Settings → Machines. Multiple lines (one per algo) may be newline-joined.
    host_key: BlankableStr = ""
    # A remote directory to MOUNT (sshfs) into the routine when this machine is bound, at
    # <routine>/mnt/<name>/ — so local filesystem utils read/write remote files seamlessly
    # (compute stays on `remote exec`; only the filesystem is shared). Empty = no mount.
    share: BlankableStr = ""
    workdir: BlankableStr = ""       # default remote working dir for exec/jobs (else the login dir)
    description: BlankableStr = ""   # one-line human summary, surfaced to the model in CAPABILITIES
    tags: list[str] = Field(default_factory=list)
