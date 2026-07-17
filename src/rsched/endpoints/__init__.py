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
from . import failover
from .anthropic_api import AnthropicEndpoint
from .base import ChatEndpoint, Completion, EndpointError
from .claude_cli import ClaudeCliEndpoint
from .instrument import InstrumentedEndpoint
from .openai_compat import OpenAICompatEndpoint

__all__ = ["ChatEndpoint", "Completion", "EndpointError", "EndpointRegistry",
           "InstrumentedEndpoint", "make_endpoint"]

_KINDS: dict[str, Callable[[EndpointConfig], ChatEndpoint]] = {
    "openai": OpenAICompatEndpoint,
    "anthropic": AnthropicEndpoint,
    "claude-cli": ClaudeCliEndpoint,
}


def make_endpoint(cfg: EndpointConfig) -> ChatEndpoint:
    try:
        return _KINDS[cfg.kind](cfg)
    except KeyError:
        raise EndpointError(f"unknown endpoint kind {cfg.kind!r} for {cfg.name!r}") from None


class EndpointRegistry:
    """Lazily instantiates and caches adapters by config name, and resolves a catalog model
    NAME (a routine's main/subroutine/tool_call/uncensored role or the server's system model)
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
        ref = ModelRef(endpoint=mc.endpoint, model=mc.model, effort=mc.effort,
                       multimodal=multimodal,
                       context_chars=mc.context_chars or ep_cfg.context_chars,
                       temperature=temperature,
                       max_tokens=mc.max_tokens or ep_cfg.max_tokens
                       or DEFAULT_MODEL_MAX_TOKENS,
                       name=name)
        return self.get(mc.endpoint), ref

    def resolve_chain(self, name: str) -> list[tuple[InstrumentedEndpoint, ModelRef]]:
        """The model plus its declared `fallbacks:`, each resolved in order — the failover
        chain. Self-references, duplicates, and unresolvable fallback names are skipped (the
        config loader flags them as problems) — a bad chain entry must never break the
        primary's resolution.
        """
        chain = [self.resolve(name)]
        mc = self.server.models.get(name)
        seen = {name}
        for fb in (mc.fallbacks if mc else []):
            if fb in seen:
                continue
            seen.add(fb)
            try:
                chain.append(self.resolve(fb))
            except EndpointError:
                continue
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
        """Resolve one of a routine's model roles (main/subroutine/tool_call) by catalog name.
        A role the routine left unset falls back to the server's system_model (also a name).
        Cooldown-aware: a model whose provider just failed hard is skipped for its first
        not-cooling fallback (failover.pick), so every resolution site avoids a known-bad
        provider without changing.
        """
        return failover.pick(self.for_model_chain(kind, models))

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
        clarify wizard) — the server system_model catalog name. Cooldown-aware like for_model.
        """
        if not self.server.system_model:
            raise EndpointError("no system_model configured")
        return failover.pick(self.resolve_chain(self.server.system_model))
