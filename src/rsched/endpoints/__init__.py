"""Endpoint registry: config name → adapter instance, plus role resolution.

The scheduler's engine IS the harness. Endpoints are model TRANSPORTS only: raw
chat-completion APIs (OpenAI-compatible, Anthropic Messages), plus the Claude Code CLI in
fully stripped print mode (`--tools ""`, our system prompt replacing its own, no settings/
MCP/session) — a subscription-billed completion function, never an agent loop.
"""

from __future__ import annotations

from ..config import EndpointConfig, ModelRef, ServerConfig
from .anthropic_api import AnthropicEndpoint
from .base import ChatEndpoint, Completion, EndpointError
from .claude_cli import ClaudeCliEndpoint
from .openai_compat import OpenAICompatEndpoint

__all__ = ["ChatEndpoint", "Completion", "EndpointError", "EndpointRegistry", "make_endpoint"]

_KINDS = {
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
    """Lazily instantiates and caches adapters by config name, and resolves which
    (endpoint, model) serves a given role — a routine's main/subroutine/tool_call or the
    server's system model."""

    def __init__(self, server: ServerConfig):
        self.server = server
        self._cache: dict[str, ChatEndpoint] = {}

    def get(self, name: str) -> ChatEndpoint:
        if name not in self._cache:
            cfg = self.server.endpoints.get(name)
            if cfg is None:
                raise EndpointError(f"endpoint {name!r} is not configured")
            self._cache[name] = make_endpoint(cfg)
        return self._cache[name]

    def for_model(self, kind: str, models: dict[str, ModelRef]) -> tuple[ChatEndpoint, ModelRef]:
        """Resolve one of a routine's models (main/subroutine/tool_call). A model the routine
        didn't set falls back to the server's single system_model."""
        ref = models.get(kind) or self.server.system_model
        if ref is None:
            raise EndpointError(f"no model configured for {kind!r} (and no system_model fallback)")
        return self.get(ref.endpoint), ref

    def for_system(self) -> tuple[ChatEndpoint, ModelRef]:
        """The one model for pre-routine machine work (workflow generation/suggestion, the
        clarify wizard)."""
        ref = self.server.system_model
        if ref is None:
            raise EndpointError("no system_model configured")
        return self.get(ref.endpoint), ref
