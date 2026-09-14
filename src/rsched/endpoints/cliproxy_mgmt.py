"""The proxy's management binding, resolved per ENDPOINT — and which of its accounts serve
which endpoint.

CLIProxyAPI is one process holding several credentials (a Claude account, a Codex account),
reached through as many endpoints as the operator configured — one per provider here, both
on the same base_url. Two facts follow, and both used to be wrong on the cards:

- The management key belongs to the PROXY, not to an endpoint. An endpoint without its own
  `quota_source: cliproxy` binding resolves to a sibling's on the same origin (`binding`), so
  the Codex endpoint reaches the account list and the sign-in without a second copy of the
  key in its config.
- Which accounts matter to a card is decided by the model ids bound to that endpoint,
  mapped to the proxy's provider names by model FAMILY (`providers_of`). A table, not a
  management lookup: the proxy lists NO models for a credential it cannot use, which is the
  exact state a sign-in control exists for (2026-09-14: the cooling Codex credential served
  an empty list while the Codex card was where its sign-in belonged).
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from ..config import EndpointConfig
from .base import EndpointError, resolve_api_key

# auth-file provider name → the management login route's provider name, and the card label
LOGIN_PROVIDER = {"claude": "anthropic", "codex": "codex"}
LABEL = {"claude": "Claude", "codex": "Codex"}
# a model id's family → the auth-file provider that serves it (prefix match, first wins)
MODEL_FAMILY = (("claude-", "claude"), ("gpt-", "codex"), ("codex-", "codex"),
                ("o1", "codex"), ("o3", "codex"), ("o4", "codex"))


def providers_of(model_ids) -> set[str]:
    """The auth-file provider names the given model ids belong to. An id no family claims
    contributes nothing; an empty result means UNKNOWN, and every consumer shows everything
    for unknown rather than nothing.
    """
    out: set[str] = set()
    for mid in model_ids:
        low = str(mid).lower().rsplit("/", 1)[-1]        # "openai/gpt-4o" → "gpt-4o"
        for prefix, provider in MODEL_FAMILY:
            if low.startswith(prefix):
                out.add(provider)
                break
    return out


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def binding(server, name: str) -> EndpointConfig | None:
    """The endpoint carrying the management binding for the proxy `name` talks to: itself
    when it has one, else a sibling on the same origin (one proxy, one key), else None.
    """
    ep = server.endpoints.get(name)
    if ep is None:
        return None
    if ep.quota_source == "cliproxy":
        return ep
    if not ep.base_url:
        return None
    origin = _origin(ep.base_url)
    for other in server.endpoints.values():
        if (other.quota_source == "cliproxy" and other.base_url
                and _origin(other.base_url) == origin):
            return other
    return None


def bound_model_ids(server, name: str) -> list[str]:
    """The provider model ids the catalog binds to endpoint `name`."""
    return [m.model for m in server.models.values() if m.endpoint == name]


def endpoint_providers(server, name: str) -> set[str]:
    return providers_of(bound_model_ids(server, name))


def client(cfg: EndpointConfig, timeout: int) -> tuple[httpx.Client, str]:
    """A management-API client for a binding endpoint: the proxy root without `/v1`, the
    management key from `quota_key_var`. The key is resolved only here, on the server.
    """
    origin = urlsplit(cfg.base_url)
    if origin.scheme not in {"http", "https"} or not origin.netloc or origin.username:
        raise EndpointError("Set the proxy's HTTP base URL in Settings.")
    key = resolve_api_key(name="CLIProxyAPI management", api_key="",
                          key_var=cfg.quota_key_var, key_env_file="", required=True)
    url = cfg.base_url.rstrip("/").removesuffix("/v1") + "/v0/management"
    return httpx.Client(timeout=timeout, follow_redirects=False,
                        headers={"Authorization": f"Bearer {key}"}), url
