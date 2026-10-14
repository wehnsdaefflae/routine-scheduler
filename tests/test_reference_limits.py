"""A model whose route publishes NO figures is sized from the public reference catalog.

A subscription proxy lists ids and nothing else, so a model it serves (Gemini 4 Argon through an
Antigravity sign-in) had no window: the endpoint's 25,000-token default applied and the engine
compacted a million-token model at 15,000 tokens. The reference catalog (OpenRouter's public
listing) carries the window under the name vendors share, and the proxy's id differs from it only
by a thinking level baked into the id (`gemini-4-argon-high` ↔ `google/gemini-4-argon`).
"""

from __future__ import annotations

import pytest

from helpers import tmp_server
from rsched.config import EndpointConfig, ModelConfig
from rsched.endpoints import catalogs, limits

REFERENCE = {"data": [
    {"id": "google/gemini-4-argon", "context_length": 1_048_576,
     "top_provider": {"max_completion_tokens": 65_536}},
    # another offer of the same model, with a different output limit: never the primary row
    {"id": "google/gemini-4-argon:batch", "context_length": 1_048_576,
     "top_provider": {"max_completion_tokens": 943_718}},
    {"id": "openai/gpt-oss-120b", "context_length": 131_072,
     "top_provider": {"max_completion_tokens": 117_964}},
    {"id": "vendor-a/shared-name", "context_length": 200_000},
    {"id": "vendor-b/shared-name", "context_length": 64_000},
    {"id": "no/figures"},
]}
PROXY_LISTING = {"data": [{"id": "gemini-4-argon-high", "object": "model"},
                          {"id": "gpt-oss-120b-medium"}, {"id": "never-heard-of-it"}]}


@pytest.mark.parametrize(("model_id", "name"), [
    ("gemini-4-argon-high", "gemini-4-argon"),
    ("google/gemini-4-argon", "gemini-4-argon"),
    ("google/gemini-4-argon:batch", "gemini-4-argon"),
    ("GPT-OSS-120B-Medium", "gpt-oss-120b"),
    ("claude-opus-4-6-thinking", "claude-opus-4-6"),
    ("gemini-3.8-flash", "gemini-3.8-flash"),            # no level: left alone
    ("glm-5.3-uncensored", "glm-5.3-uncensored"),        # a word that is not a level: left alone
])
def test_ids_reduce_to_the_name_vendors_share(model_id, name):
    assert catalogs.reference_name(model_id) == name


def test_the_reference_table_keeps_primary_rows_and_the_smaller_shared_window(monkeypatch):
    monkeypatch.setattr(catalogs, "_get", lambda *a, **k: REFERENCE)
    table = catalogs.reference_table()
    assert table["gemini-4-argon"] == (1_048_576, 65_536)       # not the :batch row's 943,718
    assert table["shared-name"] == (64_000, None)               # a wrong guess upward overfills
    assert "figures" not in table                               # a row with no window is no answer


def test_an_unreachable_reference_catalog_is_an_empty_answer_not_an_error(monkeypatch):
    monkeypatch.setattr(catalogs, "_get", lambda *a, **k: None)
    assert catalogs.reference_table() == {}


def _proxy(tmp_path, *models, kind="anthropic"):
    return tmp_server(
        tmp_path,
        endpoints={"proxy": EndpointConfig(name="proxy", kind=kind, base_url="http://cliproxy:8317",
                                           context_tokens=25_000)},
        models={m: ModelConfig(name=m, endpoint="proxy", model=m) for m in models})


def _answer(monkeypatch, reference=REFERENCE):
    seen = []

    def get(url, headers=None):
        seen.append(url)
        return reference if url == catalogs.REFERENCE_URL else PROXY_LISTING

    monkeypatch.setattr(catalogs, "_get", get)
    return seen


def test_a_proxy_model_is_sized_from_the_reference_catalog(tmp_path, monkeypatch):
    server = _proxy(tmp_path, "gemini-4-argon-high", "gpt-oss-120b-medium")
    _answer(monkeypatch)
    limits.refresh(server, force=True)
    argon = limits.lookup(server.routines_home, "proxy", "gemini-4-argon-high")
    assert argon["context_tokens"] == 1_048_576 and argon["source"] == "reference"
    # the output cap is the harness's own (ENGINE_OUTPUT_CEILING), never the vendor's maximum
    assert argon["provider_max_output_tokens"] == 65_536
    assert argon["max_output_tokens"] == limits.ENGINE_OUTPUT_CEILING
    oss = limits.lookup(server.routines_home, "proxy", "gpt-oss-120b-medium")
    assert oss["context_tokens"] == 131_072 and oss["source"] == "reference"


def test_the_endpoint_default_no_longer_cripples_such_a_model(tmp_path, monkeypatch):
    from rsched.endpoints import EndpointRegistry

    server = _proxy(tmp_path, "gemini-4-argon-high")
    _answer(monkeypatch)
    limits.refresh(server, force=True)
    assert EndpointRegistry(server).resolve("gemini-4-argon-high")[1].context_tokens == 1_048_576


def test_an_id_the_reference_does_not_know_stays_a_miss(tmp_path, monkeypatch):
    server = _proxy(tmp_path, "never-heard-of-it")
    _answer(monkeypatch)
    assert limits.refresh(server, force=True)["misses"] == ["proxy/never-heard-of-it"]
    assert limits.lookup(server.routines_home, "proxy", "never-heard-of-it") is None


def test_the_providers_own_figures_and_the_claude_table_come_first(tmp_path, monkeypatch):
    server = _proxy(tmp_path, "claude-sonnet-5", "gemini-4-argon-high")
    listing = {"data": [{"id": "claude-sonnet-5"},
                        {"id": "gemini-4-argon-high", "context_length": 500_000}]}
    monkeypatch.setattr(catalogs, "_get", lambda url, headers=None:
                        REFERENCE if url == catalogs.REFERENCE_URL else listing)
    limits.refresh(server, force=True)
    assert limits.lookup(server.routines_home, "proxy", "claude-sonnet-5")["source"] == "table"
    own = limits.lookup(server.routines_home, "proxy", "gemini-4-argon-high")
    assert own["context_tokens"] == 500_000 and own["source"] == "openai"   # what it published


def test_the_reference_is_read_once_and_only_when_a_model_needs_it(tmp_path, monkeypatch):
    server = _proxy(tmp_path, "gemini-4-argon-high", "gpt-oss-120b-medium")
    seen = _answer(monkeypatch)
    limits.refresh(server, force=True)
    assert seen.count(catalogs.REFERENCE_URL) == 1
    covered = _proxy(tmp_path / "b", "claude-sonnet-5")
    seen2 = _answer(monkeypatch)
    limits.refresh(covered, force=True)
    assert catalogs.REFERENCE_URL not in seen2          # the Claude table answered: no read at all


def test_openrouter_nanogpt_and_ollama_are_never_second_guessed(tmp_path, monkeypatch):
    """Their own listings are the authority; a model they do not carry stays a miss rather than
    taking another route's figure."""
    server = tmp_server(
        tmp_path,
        endpoints={"or": EndpointConfig(name="or", kind="openai",
                                        base_url="https://openrouter.ai/api/v1")},
        models={"m": ModelConfig(name="m", endpoint="or", model="google/gemini-4-argon-high")})
    seen = _answer(monkeypatch, {"data": []})
    limits.refresh(server, force=True)
    assert limits.lookup(server.routines_home, "or", "google/gemini-4-argon-high") is None
    assert seen.count(catalogs.REFERENCE_URL) == 0 or \
        limits.lookup(server.routines_home, "or", "google/gemini-4-argon-high") is None
