"""OAuth provider registry — the per-provider metadata the connect flow and the refresh manager
key off. Only NON-secret endpoints + flags live here; the OAuth app credentials live in the
central Secrets store as `<PROVIDER>_OAUTH_CLIENT_ID` / `<PROVIDER>_OAUTH_CLIENT_SECRET`, so the
UI's one secrets surface manages them and they never sit in config or code.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..secrets import load_secrets


@dataclass(frozen=True)
class Provider:
    """One OAuth provider. `expiring` drives the refresh manager (False = long-lived token,
    no refresh — Notion); `uses_pkce` = auth-code with an S256 code challenge.
    """

    id: str
    name: str
    authorize_url: str
    token_url: str
    console_url: str = ""       # where the user creates the OAuth app (the provider's dev console)
    default_scopes: tuple[str, ...] = ()
    uses_pkce: bool = True
    expiring: bool = True
    # Provider-specific quirks of the authorize URL + token exchange:
    authorize_extra: tuple[tuple[str, str], ...] = ()  # extra authorize params (Notion: owner=user)
    exchange_auth: str = "body"      # "basic" = HTTP Basic (Notion); "body" = client creds in body
    exchange_encoding: str = "form"  # request body: "form" (standard) or "json" (Notion)


# Notion is fully implemented and verified; google/slack are scaffolds that prove the registry
# shape — their endpoints are correct, but a provider only goes live once its client creds are set
# in Settings and the flow is exercised. Add a provider by adding an entry here (+ creds).
PROVIDERS: dict[str, Provider] = {
    "notion": Provider(
        id="notion",
        name="Notion",
        authorize_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",  # noqa: S106 — token ENDPOINT URL, not a secret
        console_url="https://www.notion.so/my-integrations",
        default_scopes=(),          # Notion scopes are fixed on the integration, not per-request
        uses_pkce=True,
        expiring=False,             # Notion bearer tokens are long-lived; no refresh_token issued
        authorize_extra=(("owner", "user"),),   # Notion requires owner=user on the authorize URL
        exchange_auth="basic",      # Notion authenticates the token exchange with HTTP Basic
        exchange_encoding="json",   # …and takes a JSON body (not form-encoded)
    ),
    "google": Provider(
        id="google",
        name="Google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",  # noqa: S106 — token ENDPOINT URL, not a secret
        console_url="https://console.cloud.google.com/apis/credentials",
        default_scopes=("openid", "email"),
        uses_pkce=True,
        expiring=True,
        # access_type=offline + prompt=consent make Google return a (stable) refresh_token.
        authorize_extra=(("access_type", "offline"), ("prompt", "consent")),
    ),
    "slack": Provider(
        id="slack",
        name="Slack",
        authorize_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",  # noqa: S106 — token ENDPOINT URL, not a secret
        console_url="https://api.slack.com/apps",
        default_scopes=("users:read",),
        uses_pkce=True,
        expiring=True,
    ),
}


def get_provider(provider_id: str) -> Provider | None:
    return PROVIDERS.get(provider_id)


def provider_ids() -> list[str]:
    return sorted(PROVIDERS)


@dataclass(frozen=True)
class ClientCreds:
    client_id: str
    client_secret: str    # may be "" for a pure public/PKCE client


def client_creds(provider_id: str) -> ClientCreds | None:
    """The provider's OAuth app credentials from the Secrets store, or None if the client_id is
    unset. Keys: `<PROVIDER>_OAUTH_CLIENT_ID` / `<PROVIDER>_OAUTH_CLIENT_SECRET`.
    """
    store = load_secrets()
    prefix = provider_id.upper()
    client_id = store.get(f"{prefix}_OAUTH_CLIENT_ID", "").strip()
    if not client_id:
        return None
    return ClientCreds(client_id=client_id,
                       client_secret=store.get(f"{prefix}_OAUTH_CLIENT_SECRET", "").strip())


def creds_secret_keys(provider_id: str) -> tuple[str, str]:
    """The two Secrets-store key names a provider's OAuth app credentials use (for the UI)."""
    prefix = provider_id.upper()
    return f"{prefix}_OAUTH_CLIENT_ID", f"{prefix}_OAUTH_CLIENT_SECRET"


def access_token_var(provider_id: str) -> str:
    """The env var a bound connection's access token is injected into for a util."""
    return f"{provider_id.upper()}_ACCESS_TOKEN"


def connection_token_vars() -> set[str]:
    """Every provider's access-token env var — these are ENGINE-INJECTED from a connection, so the
    Settings 'needed secrets' list must not prompt for them as user-set store secrets.
    """
    return {access_token_var(pid) for pid in provider_ids()}
