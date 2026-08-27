"""Endpoint credit/balance support: which providers, and the Nano-GPT URL shape.

The /settings/endpoints/{name}/credits route shows the account balance for providers
that expose one — OpenRouter (GET {base}/credits, Bearer auth) and Nano-GPT
(POST /api/check-balance on the ORIGIN, x-api-key auth; verified live 2026-07-16:
returns string usd_balance/nano_balance). These pin the provider sniff, the manage
links, and the balance-URL derivation from the configured /api/v1 base.
"""
from types import SimpleNamespace

from rsched.web.settings.endpoint_probe import credits_provider, nanogpt_balance_url
from rsched.web.settings.endpoints import CREDIT_MANAGE_URLS


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


def test_endpoint_probe_routes_are_mounted():
    """Regression (F395): the F393 split extracted endpoint_probe.py out of endpoints.py
    but its router must be wired into the settings package aggregate — otherwise
    /settings/endpoints/{name}/credits and /test 404 while the frontend still calls them
    (settings-endpoints.js). The handler-only tests above pass either way, so this asserts
    the wiring in settings/__init__.py directly."""
    from rsched.web.settings import router as settings_router

    # settings_router lazily includes sub-routers as _IncludedRouter wrappers
    # (fastapi.routing), each exposing .original_router — collect the mounted paths
    # through them rather than the flattened app graph.
    mounted = set()
    for inc in settings_router.routes:
        sub = getattr(inc, "original_router", None)
        if sub is None:
            continue
        mounted |= {rt.path for rt in sub.routes if hasattr(rt, "path")}
    assert "/settings/endpoints/{name}/credits" in mounted
    assert "/settings/endpoints/{name}/test" in mounted
