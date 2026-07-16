"""Endpoint credit/balance support: which providers, and the Nano-GPT URL shape.

The /settings/endpoints/{name}/credits route shows the account balance for providers
that expose one — OpenRouter (GET {base}/credits, Bearer auth) and Nano-GPT
(POST /api/check-balance on the ORIGIN, x-api-key auth; verified live 2026-07-16:
returns string usd_balance/nano_balance). These pin the provider sniff, the manage
links, and the balance-URL derivation from the configured /api/v1 base.
"""
from types import SimpleNamespace

from rsched.web.settings.endpoints import CREDIT_MANAGE_URLS, credits_provider, nanogpt_balance_url


def ep(kind="openai", base_url=""):
    return SimpleNamespace(kind=kind, base_url=base_url)


def test_provider_sniff():
    assert credits_provider(ep(base_url="https://openrouter.ai/api/v1")) == "openrouter"
    assert credits_provider(ep(base_url="https://nano-gpt.com/api/v1")) == "nanogpt"
    assert credits_provider(ep(base_url="https://api.example.com/v1")) is None
    assert credits_provider(ep(kind="anthropic", base_url="https://nano-gpt.com/api/v1")) is None
    assert credits_provider(ep(base_url=None)) is None


def test_every_provider_has_a_manage_link():
    assert set(CREDIT_MANAGE_URLS) == {"openrouter", "nanogpt"}
    assert all(u.startswith("https://") for u in CREDIT_MANAGE_URLS.values())


def test_nanogpt_balance_url_lives_on_the_origin():
    # the configured base is the OpenAI-compatible /api/v1 — check-balance is NOT under it
    assert nanogpt_balance_url("https://nano-gpt.com/api/v1") \
        == "https://nano-gpt.com/api/check-balance"
    assert nanogpt_balance_url("https://nano-gpt.com/api/v1/") \
        == "https://nano-gpt.com/api/check-balance"
