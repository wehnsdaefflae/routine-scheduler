"""Endpoint registry: config name → adapter instance, plus role resolution."""

from __future__ import annotations

from ..config import EndpointConfig, RoleRef, ServerConfig
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

    def for_role(self, role: str, overrides: dict[str, RoleRef]) -> tuple[ChatEndpoint, RoleRef]:
        """Resolve a role (orchestrator/subcall/cheap) through routine overrides + server
        defaults. Unknown roles fall back to subcall, then orchestrator."""
        merged = dict(self.server.default_roles)
        merged.update(overrides)
        ref = merged.get(role) or merged.get("subcall") or merged.get("orchestrator")
        if ref is None:
            raise EndpointError(f"no endpoint configured for role {role!r} (and no fallback)")
        return self.get(ref.endpoint), ref
