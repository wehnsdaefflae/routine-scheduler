"""One proxy, one key, several endpoints: the management binding resolves per proxy origin
and each endpoint's accounts follow the family of the models bound to it."""

from types import SimpleNamespace

import pytest

from rsched.config import EndpointConfig, ModelConfig
from rsched.endpoints import cliproxy_mgmt
from rsched.endpoints.base import EndpointError


@pytest.mark.parametrize(("ids", "expected"), [
    (["claude-opus-5", "claude-haiku-4-5-20251001"], {"claude"}),
    (["gpt-6-astra"], {"codex"}),
    (["openai/gpt-4o", "o4-mini", "codex-mini"], {"codex"}),
    (["claude-opus-5", "gpt-6-astra"], {"claude", "codex"}),
    (["gemini-2.5-pro"], set()),          # no known family: UNKNOWN, shows everything
    ([], set()),
])
def test_providers_follow_the_model_family(ids, expected):
    assert cliproxy_mgmt.providers_of(ids) == expected


def _server(**endpoints):
    return SimpleNamespace(endpoints=endpoints, models={})


def test_binding_is_the_endpoints_own_else_a_siblings_on_the_same_proxy():
    bound = EndpointConfig(kind="anthropic", base_url="http://cliproxy:8317",
                           quota_source="cliproxy")
    codex = EndpointConfig(kind="anthropic", base_url="HTTP://cliproxy:8317/v1")   # same origin
    other = EndpointConfig(kind="anthropic", base_url="http://elsewhere:8317")
    bare = EndpointConfig(kind="anthropic")
    server = _server(claude=bound, codex=codex, other=other, bare=bare)
    assert cliproxy_mgmt.binding(server, "claude") is bound
    assert cliproxy_mgmt.binding(server, "codex") is bound        # the sibling's key
    assert cliproxy_mgmt.binding(server, "other") is None         # a different proxy
    assert cliproxy_mgmt.binding(server, "bare") is None          # no base_url at all
    assert cliproxy_mgmt.binding(server, "nope") is None


def test_endpoint_providers_read_the_catalog():
    server = _server(claude=EndpointConfig(kind="anthropic", base_url="http://p:1",
                                           quota_source="cliproxy"),
                     codex=EndpointConfig(kind="anthropic", base_url="http://p:1"))
    server.models = {
        "Opus": ModelConfig(name="Opus", endpoint="claude", model="claude-opus-5"),
        "Astra": ModelConfig(name="Astra", endpoint="codex", model="gpt-6-astra"),
        "Astra high": ModelConfig(name="Astra high", endpoint="codex", model="gpt-6-astra"),
    }
    assert cliproxy_mgmt.bound_model_ids(server, "codex") == ["gpt-6-astra", "gpt-6-astra"]
    assert cliproxy_mgmt.endpoint_providers(server, "claude") == {"claude"}
    assert cliproxy_mgmt.endpoint_providers(server, "codex") == {"codex"}
    assert cliproxy_mgmt.endpoint_providers(server, "nothing-bound") == set()


def test_client_derives_the_management_url_and_refuses_a_non_http_base(monkeypatch):
    monkeypatch.setattr(cliproxy_mgmt, "resolve_api_key", lambda **kw: "k")
    client, url = cliproxy_mgmt.client(
        EndpointConfig(kind="anthropic", base_url="http://cliproxy:8317/v1/"), 5)
    with client:
        assert url == "http://cliproxy:8317/v0/management"
        assert client.headers["Authorization"] == "Bearer k"
    with pytest.raises(EndpointError, match="base URL"):
        cliproxy_mgmt.client(EndpointConfig(kind="anthropic", base_url="ftp://x"), 5)
    with pytest.raises(EndpointError, match="base URL"):
        cliproxy_mgmt.client(EndpointConfig(kind="anthropic", base_url="http://u@x:1"), 5)
