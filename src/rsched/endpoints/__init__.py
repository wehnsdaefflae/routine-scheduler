"""Endpoint registry: config name → adapter instance, plus role resolution.

The scheduler's engine IS the harness. Endpoints are model TRANSPORTS only: raw
chat-completion APIs (OpenAI-compatible, Anthropic Messages), plus the Claude Code CLI in
fully stripped print mode (`--tools ""`, our system prompt replacing its own, no settings/
MCP/session) — a subscription-billed completion function, never an agent loop.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import (
    DEFAULT_MODEL_MAX_TOKENS,
    NATIVE_MM_KINDS,
    EndpointConfig,
    ModelRef,
    ServerConfig,
)
from . import failover, limits
from .anthropic_api import AnthropicEndpoint
from .base import ChatEndpoint, Completion, EndpointError, cache_read_share
from .instrument import InstrumentedEndpoint
from .openai_compat import OpenAICompatEndpoint

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
        return InstrumentedEndpoint(self._cache[name])

    def resolve(self, name: str) -> tuple[InstrumentedEndpoint, ModelRef]:
        """A catalog model NAME → its serving endpoint adapter + a fully-resolved ModelRef:
        the provider id and effort verbatim, and the per-model multimodal/context/temperature
        with the serving endpoint's kind-default / own values filled in for any left unset.
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
        # ONE precedence chain, and the ORDER is the whole design (endpoints/limits.py):
        #
        #   per-MODEL config  →  what the PROVIDER reports  →  the endpoint default  →  the floor
        #
        # A per-model value is the operator speaking about THIS model — sizing down on purpose for
        # a cost budget or a slow provider — and `engine/window.py` already promises to honour it,
        # so discovery must not overrule it. An ENDPOINT value sits BELOW discovery because that
        # is what it has always been documented as: "DEFAULTS a catalog model inherits when it
        # leaves the field unset" (config/modelconf.py). One guess made once for a whole endpoint
        # is exactly the thing the provider's own answer should replace — and keeping it as the
        # fallback means nothing has to be deleted from a config.yaml that is not versioned.
        # Read-only from the cache: `resolve` is on the per-turn path and never makes a network
        # call, so a miss is simply the next tier down.
        found = limits.lookup(self.server.routines_home, mc.endpoint, mc.model) or {}
        ref = ModelRef(endpoint=mc.endpoint, model=mc.model, effort=mc.effort,
                       multimodal=multimodal,
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

    def resolve_chain(self, name: str) -> list[tuple[InstrumentedEndpoint, ModelRef]]:
        """The model plus its fallbacks TRANSITIVELY — breadth-first, in declaration order.
        Self-references, duplicates, and unresolvable fallback names are skipped (the config
        loader flags them as problems) — a bad chain entry must never break the primary's
        resolution.

        Transitive because a flat chain silently contradicts the config that declares it
        (R1504/R1492). `Astra high`'s fallbacks are `[Opus high]` and `Opus high` declares
        none, so the chain was two models long and its second member sat on the same
        cliproxy endpoint as the first — one failure domain, no way out. On 2026-09-14 that
        killed eight routines at turn 0 on a single credential expiry, and it is also what
        ends a run on a classifier refusal: `engine/degrade` walks this chain for both, and
        an empty walk is a dead run. Following each entry's OWN fallbacks makes the reachable
        set what a reader of config.yaml would already expect it to be — `Astra max` reaches
        `GLM 5.3` through `Opus 5 max` — with no config edit and nothing to keep in sync.

        Breadth-first, not depth-first, so the ORDER still expresses intent: every fallback
        the primary itself declared is tried before anything only a fallback declared. A
        model's chain is therefore "my own alternatives first, then theirs", which is the
        reading its author had in mind when they wrote the list.
        """
        chain = [self.resolve(name)]
        seen = {name}
        mc = self.server.models.get(name)
        queue = list(mc.fallbacks if mc else [])
        while queue:
            fb = queue.pop(0)
            if fb in seen:
                continue
            seen.add(fb)
            try:
                chain.append(self.resolve(fb))
            except EndpointError:
                # Unresolvable (missing endpoint, bad model) — skip it, but still follow what
                # it declared: a broken rung must not truncate the ladder below it.
                pass
            fb_mc = self.server.models.get(fb)
            queue.extend(f for f in (fb_mc.fallbacks if fb_mc else []) if f not in seen)
        return chain

    def for_model_chain(self, kind: str,
                        models: dict[str, str]) -> list[tuple[InstrumentedEndpoint, ModelRef]]:
        """A routine role's full failover chain (primary first). The engine's turn
        completion walks it when the picked model fails hard mid-turn.
        """
        name = models.get(kind) or self.server.system_model
        if not name:
            raise EndpointError(f"no model configured for {kind!r} (and no system_model fallback)")
        return self.resolve_chain(name)

    def for_model(self, kind: str, models: dict[str, str]) -> tuple[InstrumentedEndpoint, ModelRef]:
        """Resolve one of a routine's model roles (main/tool_call) by catalog name.
        A role the routine left unset falls back to the server's system_model (also a name).
        Cooldown-aware: a model whose provider just failed hard is skipped for its first
        not-cooling fallback (failover.pick), so every resolution site avoids a known-bad
        provider without changing.
        """
        return failover.pick(self.for_model_chain(kind, models))

    def for_name(self, name: str) -> tuple[InstrumentedEndpoint, ModelRef]:
        """Resolve a CATALOG model by its NAME — the per-call `model` override path (an
        action naming a concrete catalog model instead of a role). Cooldown-aware like
        for_model (the model's own fallbacks: chain applies); raises EndpointError for a
        name the catalog does not carry — callers turn that into a teaching rejection
        naming the available models (the `list_models` action shows the same catalog).
        """
        return failover.pick(self.resolve_chain(name))

    def for_uncensored(
            self, models: dict[str, str]) -> tuple[InstrumentedEndpoint, ModelRef] | None:
        """The routine's OPTIONAL uncensored model — the target a refused `llm` tool-call is
        re-referred to. Unlike for_model, this has NO system_model fallback: an unset role
        returns None, which means "referral off" (referring a refusal to the same censored
        default model would be pointless). Only routines that explicitly name a
        `models.uncensored` catalog entry opt into referral.
        """
        name = models.get("uncensored")
        if not name:
            return None
        return failover.pick(self.resolve_chain(name))

    def for_system(self) -> tuple[InstrumentedEndpoint, ModelRef]:
        """The one model for pre-routine machine work (workflow generation/suggestion, the
        clarify flow) — the server system_model catalog name. Cooldown-aware like for_model.
        """
        if not self.server.system_model:
            raise EndpointError("no system_model configured")
        return failover.pick(self.resolve_chain(self.server.system_model))
