"""Endpoint registry: config name → adapter instance, plus role resolution.

The scheduler's engine IS the harness. Endpoints are model TRANSPORTS only: two raw
chat-completion wires (OpenAI-compatible, Anthropic Messages) — a completion function, never
an agent loop. A Claude subscription is the Anthropic wire pointed at a proxy that owns the
login (docs/model-gateway.md).
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import (
    DEFAULT_MODEL_MAX_TOKENS,
    NATIVE_MM_KINDS,
    EndpointConfig,
    ModelRef,
    ServerConfig,
    role_default_model,
)
from . import failover, limits
from .anthropic_api import AnthropicEndpoint
from .base import ChatEndpoint, Completion, EndpointError
from .instrument import InstrumentedEndpoint
from .openai_compat import OpenAICompatEndpoint
from .usage import cache_read_share

__all__ = ["ChatEndpoint", "Completion", "EndpointError", "EndpointRegistry",
           "InstrumentedEndpoint", "cache_read_share", "make_endpoint"]

_KINDS: dict[str, Callable[[EndpointConfig], ChatEndpoint]] = {
    "openai": OpenAICompatEndpoint,
    "anthropic": AnthropicEndpoint,
}


def make_endpoint(cfg: EndpointConfig) -> ChatEndpoint:
    try:
        return _KINDS[cfg.kind](cfg)
    except KeyError:
        raise EndpointError(f"unknown endpoint kind {cfg.kind!r} for {cfg.name!r}") from None


class EndpointRegistry:
    """Lazily instantiates and caches adapters by config name, and resolves a catalog model
    NAME (a routine's main/tool_call/uncensored role, a per-call override, or the server's
    system model)
    to its serving endpoint adapter + a fully-resolved ModelRef.
    """

    def __init__(self, server: ServerConfig):
        self.server = server
        self._cache: dict[str, ChatEndpoint] = {}

    def get(self, name: str) -> InstrumentedEndpoint:
        """Resolve a configured endpoint. The raw adapter is cached, but every caller gets a
        fresh InstrumentedEndpoint wrapper — the single seam through which all LLM calls are
        observed (nothing reaches a transport except via a wrapped endpoint).
        """
        if name not in self._cache:
            cfg = self.server.endpoints.get(name)
            if cfg is None:
                raise EndpointError(f"endpoint {name!r} is not configured")
            self._cache[name] = make_endpoint(cfg)
        return InstrumentedEndpoint(self._cache[name], self.server.routines_home)

    def resolve(self, name: str,
                effort: str | None = None) -> tuple[InstrumentedEndpoint, ModelRef]:
        """A catalog model NAME → its serving endpoint adapter + a fully-resolved ModelRef:
        the provider id verbatim, the `effort` the CALLER chose (a role's setting — the catalog
        entry carries none), and the per-model multimodal/context/temperature with the serving
        endpoint's kind-default / own values filled in for any left unset. The `audio` and
        `video` modality bits come across verbatim — they inherit no endpoint default (D192).
        """
        mc = self.server.models.get(name)
        if mc is None:
            raise EndpointError(f"model {name!r} is not in the catalog")
        ep_cfg = self.server.endpoints.get(mc.endpoint)
        if ep_cfg is None:
            raise EndpointError(
                f"model {name!r} names endpoint {mc.endpoint!r}, which is not configured")
        multimodal = (mc.multimodal if mc.multimodal is not None
                      else ep_cfg.kind in NATIVE_MM_KINDS)
        temperature = mc.temperature if mc.temperature is not None else ep_cfg.temperature
        # ONE precedence chain (endpoints/limits.py):
        #   per-MODEL config  →  what the PROVIDER reports  →  the endpoint default  →  the floor
        # A per-model value is the operator sizing THIS model on purpose, so discovery never
        # overrules it; an endpoint value is a default the provider's answer should replace.
        # Read-only from the cache: `resolve` is on the per-turn path and never fetches.
        found = limits.lookup(self.server.routines_home, mc.endpoint, mc.model) or {}
        ref = ModelRef(endpoint=mc.endpoint, model=mc.model, effort=effort or None,
                       multimodal=multimodal, audio=mc.audio, video=mc.video,
                       context_tokens=(mc.context_tokens
                                      or limits.window_tokens(found)
                                      or ep_cfg.context_tokens),
                       temperature=temperature,
                       max_tokens=(mc.max_tokens
                                   or found.get("max_output_tokens")
                                   or ep_cfg.max_tokens
                                   or DEFAULT_MODEL_MAX_TOKENS),
                       name=name)
        return self.get(mc.endpoint), ref

    def for_endpoint(self, spec: str,
                     effort: str | None = None) -> tuple[InstrumentedEndpoint, ModelRef]:
        """`<endpoint name>/<provider model id>` → that endpoint's adapter + a ModelRef for an
        id the CATALOG does not carry — the arbitrary-endpoint path.

        The endpoint is named from config, never as a URL: the `llm` action runs in the engine,
        outside the util jail, so a URL would be a destination chosen by a model with nothing to
        review it. The spec reads as the run's prompt names its model (`otari/claude-opus-5`).

        Attributes come from `resolve`'s precedence minus the per-model tier. `name` stays EMPTY
        so an unregistered id is never indistinguishable from a catalog entry.
        """
        ep_name, _, model_id = spec.partition("/")
        ep_cfg = self.server.endpoints.get(ep_name)
        if ep_cfg is None:
            # A decision endpoint is a different wire (one typed answer): a wrong-action
            # mistake, answered as one rather than as a missing endpoint.
            if ep_name in (self.server.decision_endpoints or {}):
                raise EndpointError(
                    f"{ep_name!r} is a DECISION endpoint, which answers typed questions "
                    "rather than completions — put the question to it with the `decide` "
                    "action instead.")
            avail = ", ".join(sorted(self.server.endpoints)) or "none configured"
            raise EndpointError(f"endpoint {ep_name!r} is not configured. Configured "
                                f"endpoints: {avail}.")
        if not model_id:
            raise EndpointError(
                f"{spec!r} names endpoint {ep_name!r} with no model id — write it as "
                f"'{ep_name}/<model id the endpoint serves>'.")
        found = limits.lookup(self.server.routines_home, ep_name, model_id) or {}
        ref = ModelRef(endpoint=ep_name, model=model_id, effort=effort or None,
                       multimodal=ep_cfg.kind in NATIVE_MM_KINDS,
                       context_tokens=(limits.window_tokens(found) or ep_cfg.context_tokens),
                       temperature=ep_cfg.temperature,
                       max_tokens=(found.get("max_output_tokens") or ep_cfg.max_tokens
                                   or DEFAULT_MODEL_MAX_TOKENS))
        return self.get(ep_name), ref

    def resolve_chain(self, name: str,
                      effort: str | None = None) -> list[tuple[InstrumentedEndpoint, ModelRef]]:
        """The model plus its fallbacks TRANSITIVELY — breadth-first, in declaration order.
        Self-references, duplicates, and unresolvable fallback names are skipped (the config
        loader flags them as problems) — a bad chain entry must never break the primary's
        resolution.

        Transitive because a flat chain contradicts the config that declares it: a fallback's
        own fallbacks are reachable to a reader of config.yaml, and `engine/degrade` walks this
        chain on a hard failure or a classifier refusal, where an empty walk is a dead run.
        Breadth-first so a model's own alternatives are tried before its fallbacks' ones.

        Every member runs at the ONE `effort` the caller chose for the role: effort belongs to
        the use, not to the model, so a failover never changes how hard the work is thought about.
        """
        chain = [self.resolve(name, effort)]
        seen = {name}
        mc = self.server.models.get(name)
        queue = list(mc.fallbacks if mc else [])
        while queue:
            fb = queue.pop(0)
            if fb in seen:
                continue
            seen.add(fb)
            try:
                chain.append(self.resolve(fb, effort))
            except EndpointError:
                # Unresolvable (missing endpoint, bad model) — skip it, but still follow what
                # it declared: a broken rung must not truncate the ladder below it.
                pass
            fb_mc = self.server.models.get(fb)
            queue.extend(f for f in (fb_mc.fallbacks if fb_mc else []) if f not in seen)
        return chain

    def for_model_chain(self, kind: str, models: dict[str, str],
                        efforts: dict[str, str]) -> list[tuple[InstrumentedEndpoint, ModelRef]]:
        """A routine role's full failover chain (primary first), at the role's effort. The
        engine's turn completion walks it when the picked model fails hard mid-turn.

        A role the routine left unset runs the INSTANCE DEFAULT for that role
        (`config.base.role_default_model`): `tool_call` prefers `system_tool_call_model` and
        falls through to `system_model` when that is unset, every other role takes
        `system_model` directly. At the role's own effort when the routine names one, else at
        the effort belonging to whichever default answered.
        """
        if name := models.get(kind):
            return self.resolve_chain(name, efforts.get(kind))
        name, effort = role_default_model(self.server, kind)
        if not name:
            raise EndpointError(f"no model configured for {kind!r} (and no system_model fallback)")
        return self.resolve_chain(name, efforts.get(kind) or effort)

    def for_model(self, kind: str, models: dict[str, str],
                  efforts: dict[str, str]) -> tuple[InstrumentedEndpoint, ModelRef]:
        """Resolve one of a routine's model roles (main/tool_call) by catalog name.
        A role the routine left unset falls back to the server's system_model (also a name).
        Cooldown-aware: a model whose provider just failed hard is skipped for its first
        not-cooling fallback (failover.pick), so every resolution site avoids a known-bad
        provider without changing.
        """
        return failover.pick(self.for_model_chain(kind, models, efforts))

    def for_name(self, name: str,
                 effort: str | None = None) -> tuple[InstrumentedEndpoint, ModelRef]:
        """Resolve a CATALOG model by its NAME — the per-call `model` override path (an
        action naming a concrete catalog model instead of a role). Cooldown-aware like
        for_model (the model's own fallbacks: chain applies); raises EndpointError for a
        name the catalog does not carry — callers turn that into a teaching rejection
        naming the available models (the `list_models` action shows the same catalog).
        """
        return failover.pick(self.resolve_chain(name, effort))

    def for_uncensored(
            self, models: dict[str, str],
            efforts: dict[str, str]) -> tuple[InstrumentedEndpoint, ModelRef] | None:
        """The routine's OPTIONAL uncensored model — the target a refused `llm` tool-call is
        re-referred to. Unlike for_model, this has NO system_model fallback: an unset role
        returns None, which means "referral off" (referring a refusal to the same censored
        default model would be pointless). Only routines that explicitly name a
        `models.uncensored` catalog entry opt into referral.
        """
        name = models.get("uncensored")
        if not name:
            return None
        return failover.pick(self.resolve_chain(name, efforts.get("uncensored")))

    def for_system(self) -> tuple[InstrumentedEndpoint, ModelRef]:
        """The one model for pre-routine machine work (workflow generation/suggestion, the
        clarify flow) — the server system_model catalog name. Cooldown-aware like for_model.
        """
        if not self.server.system_model:
            raise EndpointError("no system_model configured")
        return failover.pick(self.resolve_chain(self.server.system_model,
                                                self.server.system_effort))
