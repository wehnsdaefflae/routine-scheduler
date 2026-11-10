"""Settings → Connections: read and save a scoped provider's consent scopes as a LIST.

`PUT /settings/oauth/{provider}/scopes` takes the whole list the editor shows, normalizes each
entry (`oauth/scopes.normalize`), refuses what the provider may not carry or what is over its
consent ceiling (`oauth/scopes.judge`) and writes `<PROVIDER>_OAUTH_SCOPES` — the same Secrets
entry `authorize-start` reads, so the next connect or reconnect asks for exactly this. Saving
changes no token: a connection's granted scopes change only when it is reconnected, which is
why the status carries `requested` beside each connection's own `scopes`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ... import secrets as secret_store
from ...oauth import providers, scopes

router = APIRouter()


def status(provider_id: str) -> dict:
    """The editor's read of one scoped provider — merged into `GET /settings/oauth`."""
    prov = providers.get_provider(provider_id)
    requested = scopes.current(provider_id)
    errors, warnings = scopes.judge(provider_id, requested) if requested else ([], [])
    return {"requested": requested,
            "groups": {s: scopes.api_of(s) for s in requested},
            "max_scopes": prov.max_scopes if prov else 0,
            "scope_prefixes": list(prov.scope_prefixes) if prov else [],
            "scope_base": prov.scope_base if prov else "",
            "scope_docs": scopes.DOCS_URL if prov and prov.scope_base else "",
            "creds_from": prov.creds_from if prov else "",
            "scope_errors": errors, "scope_warnings": warnings}


class ScopeList(BaseModel):
    scopes: list[str] = Field(max_length=200)


@router.put("/settings/oauth/{provider}/scopes")
def put_scopes(provider: str, body: ScopeList) -> dict:
    if providers.get_provider(provider) is None:
        raise HTTPException(404, f"unknown provider {provider!r}")
    wanted = list(dict.fromkeys(s for s in (scopes.normalize(provider, x) for x in body.scopes)
                                if s))
    errors, warnings = scopes.judge(provider, wanted)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    try:
        secret_store.set_secret(providers.scopes_secret_key(provider), " ".join(wanted))
    except (ValueError, OSError) as exc:
        raise HTTPException(500, f"cannot write the secrets store: {exc}") from exc
    return {"ok": True, "requested": wanted, "warnings": warnings}
