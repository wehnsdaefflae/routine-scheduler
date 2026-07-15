"""Live endpoint smoke tests — real network calls, so gated behind RSCHED_LIVE_TESTS=1.

Covers the commonly-configured endpoints, all DIRECT model access: OpenRouter (GLM-5.2,
the default orchestrator), local Ollama (gemma), and the Anthropic Messages API (claude).
Each asks for a schema-constrained answer and validates it end-to-end.
"""

import os

import pytest

from rsched.config import load_server_config
from rsched.endpoints import EndpointRegistry
from rsched.paths import expand
from rsched.schema_guard import parse_reply

pytestmark = pytest.mark.skipif(os.environ.get("RSCHED_LIVE_TESTS") != "1",
                                reason="set RSCHED_LIVE_TESTS=1 for live endpoint smokes")

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer"],
          "properties": {"answer": {"type": "integer", "description": "the numeric result"}}}


@pytest.mark.parametrize(("endpoint", "model"), [
    ("openrouter", "z-ai/glm-5.2"),
    ("ollama-local", "gemma4:latest"),
    ("anthropic", "claude-sonnet-5"),
])
def test_live_schema_completion(endpoint, model):
    server, _ = load_server_config()
    cfg = server.endpoints.get(endpoint)
    if cfg is None:
        pytest.skip(f"endpoint {endpoint} not configured")
    if cfg.key_env_file and not expand(cfg.key_env_file).exists():
        pytest.skip(f"no credentials file {cfg.key_env_file}")
    ep = EndpointRegistry(server).get(endpoint)
    completion = ep.complete(
        [{"role": "system", "content": "Answer with one JSON object only."},
         {"role": "user", "content": "What is 2+3? Reply as JSON matching the schema."}],
        model=model, schema=SCHEMA, timeout=120,
    )
    obj = completion.parsed if completion.parsed is not None else parse_reply(completion.text, SCHEMA)
    assert obj["answer"] == 5
    assert completion.usage["out"] > 0
