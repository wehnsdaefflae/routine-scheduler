"""A proxy's failure, explained: the endpoint test says what the error MEANS, not only its HTTP
status (the live case: Antigravity answering NOT FOUND for a model its own list shows as
`gemini-4-argon-disabled`)."""

from __future__ import annotations

import pytest

from conftest import authed_client, make_test_server
from rsched.endpoints import failure_hints
from rsched.endpoints.base import EndpointError

LIVE_404 = ('antigravity-proxy: HTTP 404: {"type":"error","error":{"type":"not_found_error",'
            '"message":"Requested entity was not found."}}')
LIVE_503 = ('antigravity-proxy: HTTP 503: {"type":"error","error":{"type":"api_error","message":'
            '"auth_unavailable: no auth available (providers=antigravity, model=gemini-4-argon-high; '
            'last upstream error: {\\"error\\": {\\"code\\": 404, \\"message\\": '
            '\\"Requested entity was not found.\\"}})"}}')


def test_the_live_antigravity_404_says_the_model_is_not_enabled_yet():
    hint = failure_hints.explain(LIVE_404)
    assert "not enabled for this account" in hint and "Nothing here is misconfigured" in hint


def test_the_503_that_follows_it_carries_the_same_cause():
    """The proxy cools the model after the 404 and answers 503 with the upstream error inside —
    the cause, not the cooldown, is what the operator needs to read."""
    assert "not enabled for this account" in failure_hints.explain(LIVE_503)


@pytest.mark.parametrize(("error", "needle"), [
    ('HTTP 400: {"message":"unknown provider for model gemini-3.1-pro-low"}', "No signed-in account"),
    ('HTTP 503: {"message":"auth_unavailable: no auth available (providers=codex)"}',
     "cooling down"),
])
def test_the_other_proxy_refusals_are_named(error, needle):
    assert needle in failure_hints.explain(error)


def test_an_error_nobody_has_explained_gets_no_invented_hint():
    assert failure_hints.explain("HTTP 500: upstream exploded") == ""
    assert failure_hints.explain("") == ""


def test_the_endpoint_test_route_carries_the_hint(tmp_path, monkeypatch):
    from rsched.endpoints.instrument import InstrumentedEndpoint

    def refuse(self, *a, **k):
        raise EndpointError(LIVE_404)

    monkeypatch.setattr(InstrumentedEndpoint, "complete", refuse)
    with authed_client(make_test_server(tmp_path)) as c:
        out = c.post("/api/settings/endpoints/dummy/test", json={"model": "m"}).json()
    assert out["ok"] is False and "not enabled for this account" in out["hint"]
    assert out["error"] == LIVE_404                       # the raw detail is still there beneath it
