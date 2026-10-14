"""The model ids an endpoint ITSELF says it serves — what the console offers instead of a free
text box for a catalog entry's model id and the endpoint card's test model.

Reuses the readers `limits` already trusts (`catalogs`): OpenRouter's and Nano-GPT's own
listings, and the OpenAI-shaped `/models` every other gateway serves — a subscription proxy
answers `{"data": [{"id": …}]}` on `/v1/models`, and lists NO model for a credential it cannot
use, so an empty answer is "nothing offered", never "does not exist". The question is asked
with the endpoint's own key, resolved here on the server; what leaves is a sorted id list or a
fixed one-line reason — never a key, a URL with credentials or an upstream body (the same
redaction rule as `cliproxy_quota`). Every outcome is a dict, never an exception: the page
falls back to a plain text box and shows the reason.
"""

from __future__ import annotations

import httpx

from . import catalogs
from .base import CONNECT_TIMEOUT, EndpointError, resolve_api_key

_TIMEOUT = 15
NOT_CONFIGURED = "Set the endpoint's base URL first — there is nothing to ask."
UNREACHABLE = "Could not reach the endpoint to list its models."
NOT_A_LISTING = "The endpoint answered, but not with a model listing."


def _headers(ep) -> dict:
    """Both spellings of the credential an OpenAI- or Messages-shaped gateway accepts; empty
    for a keyless local backend (Ollama, vLLM).
    """
    try:
        key = resolve_api_key(name=ep.name, api_key=ep.api_key, key_var=ep.key_var,
                              key_env_file=ep.key_env_file, required=False)
    except EndpointError:
        key = ""
    if not key:
        return {}
    headers = {"Authorization": f"Bearer {key}"}
    if ep.kind == "anthropic":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    return headers


def _from_gateway(ep) -> dict:
    """`GET <models route>` on a gateway: OpenAI-shaped `{"data": [{"id": …}]}`."""
    try:
        resp = httpx.get(catalogs.models_url(ep), headers=_headers(ep), follow_redirects=False,
                         timeout=httpx.Timeout(_TIMEOUT, connect=CONNECT_TIMEOUT))
    except httpx.HTTPError:
        return {"ok": False, "error": UNREACHABLE}
    if resp.status_code != 200:
        return {"ok": False, "error": f"The endpoint's model listing answered HTTP "
                                      f"{resp.status_code}; check its key."}
    try:
        body = resp.json()
    except ValueError:
        return {"ok": False, "error": NOT_A_LISTING}
    ids = catalogs.listed_ids(body) if isinstance(body, dict) else None
    return _answer(ids)


def _answer(ids: set[str] | None) -> dict:
    if ids is None:
        return {"ok": False, "error": NOT_A_LISTING}
    if not ids:
        return {"ok": True, "ids": []}
    return {"ok": True, "ids": sorted(ids)}


def list_ids(ep) -> dict:
    """`{"ok": True, "ids": [sorted…]}` or `{"ok": False, "error": <one line>}`."""
    if not (ep.base_url or "").strip():
        return {"ok": False, "error": NOT_CONFIGURED}
    provider = catalogs.provider_of(ep)
    if provider in ("openrouter", "nanogpt"):
        _limits, ids = catalogs.read(provider, ep, [])
        return _answer(ids) if ids is not None else {"ok": False, "error": UNREACHABLE}
    return _from_gateway(ep)
