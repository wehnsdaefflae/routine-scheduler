"""Transport-side config: endpoints, the model catalog (+ the resolved ModelRef), and
remote-machine catalog entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from .base import (
    DEFAULT_CONTEXT_TOKENS,
    DEFAULT_MODEL_MAX_TOKENS,
    KEY_VAR_DEFAULTS,
    BlankableStr,
    EndpointKind,
    SchemaMode,
    _Config,
)


class GatewayConfig(_Config):
    """A ROUTING gateway in front of one or more endpoints (an Otari stack): which provider each
    model name it serves is routed to, so discovery sizes a model by its ROUTE and never by the
    model's public figure (endpoints/routes.py). Two facts no listing tells the scheduler:

    - `routes_file` — the gateway's own routing config (Otari's `config.yml`, `aliases:` and
      `providers:`), read at each limits refresh. Otari's alias API needs its MASTER key, which
      no routine-side credential should hold; the file is the routing as the operator wrote it.
    - `plans` — for a provider that is a CLIProxyAPI subscription login, the plan GROUPS of
      CLIProxyAPI's model catalog it signs in with (`cliproxy: [claude, antigravity]`,
      `chatgpt: [codex-plus]`). A route's window depends on the plan (Codex Plus serves
      gpt-5.6-luna at 372,000 tokens where the API serves 1,050,000), and nothing on the wire says
      which plan a login holds — so it is said here, never guessed.
    """

    name: str = ""  # filled from the `gateways:` mapping key
    routes_file: BlankableStr = ""
    plans: dict[str, list[str]] = Field(default_factory=dict)


class EndpointConfig(_Config):
    """One configured model transport (see docs/endpoints.md for the setup guide)."""

    name: str = ""  # filled from the `endpoints:` mapping key
    kind: EndpointKind
    base_url: BlankableStr = ""
    api_key: BlankableStr = ""
    key_env_file: BlankableStr = ""
    key_var: BlankableStr = ""  # unset → the endpoint kind's KEY_VAR_DEFAULTS entry
    quota_source: Literal["", "cliproxy"] = ""
    quota_key_var: str = "CLIPROXY_MANAGEMENT_KEY"
    quota_auth_index: str = ""  # blank selects the sole enabled Claude account
    schema_mode: SchemaMode = "json_schema"  # openai kind only
    # anthropic kind only: how the action tool is offered. `auto` (the default) lets a configured
    # effort and the model's thinking reach it; `forced` is for models validated only on the
    # forced route (the Codex models the subscription proxy serves on this wire) — forcing
    # strips effort and thinking (endpoints/anthropic_api.py says why and what was measured).
    tool_choice: Literal["auto", "forced"] = "auto"
    # DEFAULTS a catalog model inherits when it leaves the field unset — and since 0.296.0 they
    # sit BELOW the figure the provider itself reports (endpoints/limits.py), because one guess
    # made once for a whole endpoint is exactly what the provider's own answer should replace.
    # A per-MODEL value still wins over both: that is an operator sizing this model down.
    # Per-model attributes live on ModelConfig — one endpoint serves many models with different
    # windows, vision support, and sampling. context_tokens is the full input + output token window.
    context_tokens: int = DEFAULT_CONTEXT_TOKENS
    temperature: float | None = None
    max_tokens: int | None = None   # None → DEFAULT_MODEL_MAX_TOKENS at resolve time
    # openai kind only: merged verbatim into every request body. This is where aggregator
    # routing lives — e.g. OpenRouter {"provider": {"ignore": [...]}} to exclude serving
    # providers whose constrained decoding measurably corrupts output (drops declared
    # fields, leaks foreign keys through "strict" mode).
    extra_body: dict = Field(default_factory=dict)
    # The `gateways:` entry this endpoint reaches models through, or blank. Set, the model ids
    # here are the gateway's NAMES and are sized by the route each one resolves to
    # (endpoints/routes.py); blank, by this endpoint's own listing.
    gateway: BlankableStr = ""

    @model_validator(mode="after")
    def _kind_default_key_var(self):
        if not self.key_var:
            self.key_var = KEY_VAR_DEFAULTS.get(self.kind, "")
        return self


class ModelConfig(_Config):
    """One catalog model: a provider model id BOUND to a configured endpoint, plus the
    per-model attributes that used to (wrongly) sit on the endpoint. One endpoint serves
    many models, so multimodality, context window and temperature belong here. Reasoning
    EFFORT does not: it is chosen where the model is used (RoutineConfig.efforts, the server's
    `system_effort`), so one catalog entry serves every level.
    A None attribute inherits the serving endpoint's default (multimodal → the endpoint
    kind's NATIVE_MM_KINDS default; context_tokens/temperature → the endpoint's own). The
    `audio`/`video` modality bits inherit nothing — they are plain per-model opt-ins (D192).
    Routines/conversations reference a model by its catalog NAME (see RoutineConfig.models).
    """

    name: str = ""  # filled from the `models:` mapping key
    endpoint: str   # which configured endpoint transports this model
    model: str      # the provider's model id (e.g. "openai/gpt-4o")
    # None = inherit the endpoint kind default (anthropic on, openai off).
    multimodal: bool | None = None
    # D192 — the two TIME-BASED modalities, each a plain opt-in with no kind default. Unlike
    # images there is no endpoint kind whose models all have ears: on one OpenAI-compatible
    # endpoint `gpt-4o-audio-preview` takes audio and `glm` beside it does not, so an inherited
    # default would be wrong for most of the catalog either way it pointed. Off until the
    # operator says this model takes it; the serving adapter still has to carry the wire
    # (endpoints/media_types.supports_media_type ANDs the two).
    audio: bool = False
    video: bool = False
    # None = discover the provider window, then inherit the endpoint default.
    context_tokens: int | None = None
    # None = inherit the endpoint's temperature default. On a current Claude model it is
    # REJECTED and dropped on a degraded retry, so setting one there costs a wasted round
    # trip per turn rather than failing — leave it blank and steer with the role's effort.
    temperature: float | None = None
    # Requested OUTPUT token cap per completion, reserved separately from input. None uses the
    # route's published output maximum (endpoints/limits.py), then the endpoint/default fallback.
    max_tokens: int | None = None
    # Ordered failover chain: catalog model NAMES tried in order when this model fails hard
    # (transport retries exhausted / non-retryable error). TRANSITIVE and breadth-first —
    # this list first, then each entry's own fallbacks, each with its own endpoint and
    # attributes. See endpoints/failover.py.
    fallbacks: list[str] = Field(default_factory=list)
    # D279(a) — WHEN TO USE THIS MODEL, in one line, for the run that is choosing one. A run
    # picking a `model` for spawn/subtask/llm, or a `role_swap` position, sees a catalog of
    # NAMES: nothing tells it that one is cheap and blunt, that another is the only one here
    # with vision, or that a third is rate-limited to uselessness before noon. The operator
    # knows all three and had nowhere to write them.
    #
    # It is ADVICE, never a routing rule: nothing in the engine reads it to decide anything,
    # and unset prints nothing at all (an empty line in a catalog listing would be worse than
    # no line, because it reads as "no opinion recorded" where there simply is no field). That
    # is also why it is not a structured set of flags — a flag the engine does not read is a
    # fact that drifts, while a sentence is read by the only thing that acts on it.
    note: BlankableStr = ""


@dataclass
class ModelRef:
    """A RESOLVED model handle produced by EndpointRegistry from a catalog entry + its
    endpoint — no longer parsed from yaml. Carries the provider model id and every
    per-model attribute the run needs: reasoning effort, native multimodality (and the
    audio/video modality bits), the context-window budget, sampling temperature, and the
    catalog name it came from.
    """

    endpoint: str
    model: str
    effort: str | None = None
    multimodal: bool = False
    # D192: the per-model audio/video bits, resolved from the catalog entry. `multimodal` is
    # still the master gate — these two say which of the time-based modalities THIS model takes
    # on top of it (endpoints/media_types.supports_media_type).
    audio: bool = False
    video: bool = False
    context_tokens: int = DEFAULT_CONTEXT_TOKENS
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
    port: int = Field(22, ge=1, le=65535)
    key_var: BlankableStr = ""      # Secrets-store key NAME holding the private key (PEM)
    # The server's pinned host key line ("ssh-ed25519 AAAA…"), verified strictly at connect
    # (no TOFU in a headless run). Empty → the `remote` util refuses to connect; scan it in
    # Settings → Machines. Multiple lines (one per algo) may be newline-joined.
    host_key: BlankableStr = ""
    # A remote directory to MOUNT (sshfs) into the routine when this machine is bound, at
    # <routine>/mnt/<name>/ — so local filesystem utils read/write remote files seamlessly
    # (compute stays on `remote exec`; only the filesystem is shared). Empty = no mount.
    share: BlankableStr = ""
    # The remote dir detached jobs (and their queue tickets) live under, as `.rsched-jobs/`;
    # empty = the login $HOME. `remote exec` does not use it — its cwd is `--cwd` or the login dir.
    workdir: BlankableStr = ""
    # ONE JOB AT A TIME. A GPU box is a single resource: two training jobs on one card do not run
    # half as fast, they run out of VRAM. Set this and `remote submit` stops launching immediately
    # — it takes a QUEUE TICKET and the box runs the jobs in fair-share order (round-robin across
    # routines, FIFO within one), each under a mandatory deadline. The submitting run is never
    # blocked: it gets a job id and a queue position back at once. See rsched/machine_queue.py and
    # docs/remote-machines.md. Cooperative, like every other machine guard: a human on the box, or
    # a `shell` action, still bypasses it.
    exclusive: bool = False
    description: BlankableStr = ""   # one-line human summary, surfaced to the model in CAPABILITIES
    tags: list[str] = Field(default_factory=list)
