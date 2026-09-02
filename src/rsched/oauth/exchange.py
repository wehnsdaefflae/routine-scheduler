"""The OAuth token-endpoint POST — the one place a client secret goes on the wire.

Two callers reach a provider's token_url with different grants: the connect flow's callback
(`web/settings/oauth.py`, authorization_code) and the daemon's refresh manager
(`daemon/oauth_refresh.py`, refresh_token). What they share is not the grant but the
PROVIDER's dialect — the client-authentication style and the body encoding declared by
`providers.Provider` — and that is exactly what must not exist twice: connect and refresh are
separated by a token lifetime, so a quirk added to the connect copy alone fails days later, at
refresh time, with the connect flow still testing green. This lives beside the registry rather
than inside it because `providers.py` declares itself metadata-only, with no network.
"""

from __future__ import annotations

import httpx

from .providers import ClientCreds, Provider


def post_token(prov: Provider, creds: ClientCreds, data: dict[str, str]) -> httpx.Response:
    """POST one grant to `prov`'s token endpoint and return the RAW response.

    `data` carries only the grant-specific fields; the client credentials go on however this
    provider takes them, and a public PKCE client's empty `client_secret` is simply not sent.
    The response comes back unread because the callers read failure differently: the connect
    callback turns anything into one generic user-facing page, while a refusal at refresh time
    flags the connection `needs_reauth` and a network error stays transient for the next tick.
    """
    body = dict(data)          # never mutate the caller's grant dict
    auth: tuple[str, str] | None = None
    if prov.exchange_auth == "basic":
        auth = (creds.client_id, creds.client_secret)
    else:
        body["client_id"] = creds.client_id
        if creds.client_secret:
            body["client_secret"] = creds.client_secret
    headers = {"Accept": "application/json"}
    if prov.exchange_encoding == "json":
        return httpx.post(prov.token_url, json=body, auth=auth, headers=headers, timeout=20)
    return httpx.post(prov.token_url, data=body, auth=auth, headers=headers, timeout=20)
