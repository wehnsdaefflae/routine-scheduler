"""Where a gateway sends a model NAME — the ROUTE whose catalog a model's limits are read from.

An Otari gateway serves normalised names (`claude-opus-5`, `gpt-5.6-luna`, `glm-5.3`) and its
`aliases:` decide which provider answers each one: a CLIProxyAPI subscription login, OpenRouter,
Nano-GPT, Anthropic's own API. The window and output maximum belong to that ROUTE, not to the
model: the Codex Plus login serves `gpt-5.6-luna` at 372,000 tokens where the API serves
1,050,000. Reading the model's public figure for a gateway name (what the reference catalog did
until 0.564.0) sized a run for a window its route does not have.

So a name resolves in two steps, and either can come back UNKNOWN — which stays unknown:

1. the gateway's routing file names `<provider>:<upstream id>` for it (`resolve`). An id that is
   already a selector (`openrouter:z-ai/glm-5.3`) is its own route; a routing POLICY name (one
   purpose, possibly several models in turn) resolves to no single route at all.
2. the provider names the CATALOG that publishes the route's figures (`catalog_of`): a provider
   the operator gave CLIProxyAPI plan groups (`GatewayConfig.plans`) is a subscription login read
   from CLIProxyAPI's catalog; OpenRouter, Nano-GPT and Anthropic's own API are recognised by the
   type or host Otari itself is configured with. Anything else publishes nothing we can read.

Read-only and offline: the file is read at a refresh (`limits.refresh`), never per turn.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import yaml

from ..paths import expand, read_yaml

log = logging.getLogger("rsched.limits")

#: CLIProxyAPI pins a reasoning level in the id it is SENT (`gpt-6.2-sol(max)`); its catalog
#: lists the model once, without the pin.
_LEVEL_PIN = re.compile(r"\([a-z]+\)$")


@dataclass(frozen=True)
class Route:
    """One resolved route: the gateway provider, the id that provider is sent, the catalog that
    publishes its figures (`cliproxy` | `openrouter` | `nanogpt` | `anthropic`, or "" for none
    readable) and, for a CLIProxyAPI login, the plan groups it signs in with.
    """

    provider: str
    upstream: str
    catalog: str
    plans: tuple[str, ...] = ()

    def label(self) -> str:
        plans = f" [{', '.join(self.plans)}]" if self.plans else ""
        return f"{self.provider}:{self.upstream}{plans}"

    def catalog_id(self) -> str:
        """The id the route's catalog lists it under (a CLIProxyAPI level pin removed)."""
        return _LEVEL_PIN.sub("", self.upstream)


def load(gateway) -> dict | None:
    """The gateway's routing document (`aliases:` + `providers:`), or None when it cannot be
    read — unanswered, so the refresh keeps what it knew rather than forgetting every route.
    """
    if not gateway.routes_file:
        return None
    try:
        doc = read_yaml(expand(gateway.routes_file), {})
    except (OSError, yaml.YAMLError) as exc:
        log.warning("limits: gateway %s routes unreadable (%s)", gateway.name, exc)
        return None
    return doc if isinstance(doc, dict) else None


def resolve(gateway, doc: dict, model_id: str) -> Route | None:
    """The route `model_id` takes through `gateway`, or None when no single route is named."""
    providers = doc.get("providers") or {}
    aliases = doc.get("aliases") or {}
    target = aliases.get(model_id) if isinstance(aliases, dict) else None
    if not isinstance(target, str):
        target = model_id                     # a `<provider>:<id>` selector routes itself
    provider, sep, upstream = target.partition(":")
    if not sep or not isinstance(providers, dict) or provider not in providers:
        return None
    entry = providers.get(provider)
    plans = tuple((gateway.plans or {}).get(provider) or ())
    return Route(provider=provider, upstream=upstream,
                 catalog=catalog_of(provider, entry if isinstance(entry, dict) else {}, plans),
                 plans=plans)


def catalog_of(provider: str, entry: dict, plans: tuple[str, ...]) -> str:
    """Which published catalog carries this gateway provider's figures. A provider with plan
    groups is a CLIProxyAPI login — only the operator knows that, and which plan. The rest are
    recognised as Otari recognises them: its own type name (`provider_type`, else the provider's
    name, which is the type for Otari's built-ins) and, for an OpenAI-shaped one, its host.
    """
    if plans:
        return "cliproxy"
    kind = str(entry.get("provider_type") or provider).lower()
    base = str(entry.get("api_base") or "").lower()
    if "openrouter" in base or (kind == "openrouter" and not base):
        return "openrouter"
    if "nano-gpt.com" in base:
        return "nanogpt"
    if kind == "anthropic" and (not base or "api.anthropic.com" in base):
        return "anthropic"
    return ""
