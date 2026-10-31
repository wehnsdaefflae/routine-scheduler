"""The `openai` adapter's one retry without `response_format`, read from a refusal's WORDING.

The retry fires on a 400 whose text names the structured-output field. Through an Otari gateway
the provider's refusal arrives reworded — "Model X does not support the requested response
format." — the field's name with a space and no underscore, so a hint list holding only the
identifier never fired and every turn on such a model failed instead of degrading.
"""

from __future__ import annotations

import json

import pytest

import rsched.endpoints.openai_compat as oai_mod
from rsched.config import EndpointConfig
from rsched.endpoints.base import EndpointError
from rsched.endpoints.openai_compat import OpenAICompatEndpoint

MESSAGES = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
OTARI_REFUSAL = json.dumps({"error": {
    "message": "Model glm-5.3-uncensored does not support the requested response format.",
    "type": "invalid_request_error"}})


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload)
        self.headers: dict = {}

    def json(self):
        return self._payload


def _endpoint() -> OpenAICompatEndpoint:
    return OpenAICompatEndpoint(EndpointConfig(
        name="otari-openai", kind="openai", base_url="http://172.17.0.1:4000/api/v1",
        api_key="k", context_tokens=9000))


def _serve(monkeypatch, refusal: str) -> list[dict]:
    bodies: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        bodies.append(json)
        if "response_format" in json:
            return _Response(400, text=refusal)
        return _Response(200, {"choices": [{"message": {"content": '{"ok":1}'}}],
                               "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    monkeypatch.setattr(oai_mod.httpx, "post", fake_post)
    return bodies


def test_a_gateway_reworded_refusal_retries_without_response_format(monkeypatch):
    bodies = _serve(monkeypatch, OTARI_REFUSAL)
    completion = _endpoint().complete(MESSAGES, model="glm-5.3-uncensored",
                                      schema={"type": "object"})
    assert len(bodies) == 2, bodies
    assert "response_format" in bodies[0] and "response_format" not in bodies[1]
    assert completion.text == '{"ok":1}'


def test_a_400_about_something_else_is_not_retried_without_the_schema(monkeypatch):
    """The hint stays precise: a refusal that names neither the field nor its wording is the
    provider's real answer, and dropping the schema would only hide it."""
    bodies = _serve(monkeypatch, json.dumps({"error": {"message": "max_tokens is too large"}}))
    with pytest.raises(EndpointError):
        _endpoint().complete(MESSAGES, model="m", schema={"type": "object"})
    assert all("response_format" in b for b in bodies), bodies
