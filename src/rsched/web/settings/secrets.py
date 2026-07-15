"""Central secrets store: one KEY=VALUE store injected into utils + endpoints + claude-cli.
Set any credential here (a util's token, a username, an API key, the Claude subscription token as
CLAUDE_CODE_OAUTH_TOKEN). The engine injects it into every util + endpoint at run time. Values are
write-only: the API returns key NAMES, never the values.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ... import secrets as secret_store
from .common import server_of

router = APIRouter()


@router.get("/settings/secrets")
def list_secrets(request: Request) -> dict:
    from ... import utils_lib
    have = set(secret_store.secret_keys())
    # which env vars do the installed utils declare they need, and are they set yet?
    declared: dict[str, list[str]] = {}
    for u in utils_lib.list_utils(server_of(request).utils_home):
        for var in u.get("secrets", []):
            declared.setdefault(var, []).append(u["name"])
    needed = [{"key": k, "utils": us, "set": k in have} for k, us in sorted(declared.items())]
    return {"keys": sorted(have), "needed": needed, "path": str(secret_store.secrets_path())}


class SecretBody(BaseModel):
    key: str
    value: str


@router.put("/settings/secrets")
def put_secret(_request: Request, body: SecretBody) -> dict:
    try:
        secret_store.set_secret(body.key.strip(), body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"cannot write the secrets store: {exc}") from exc
    return {"ok": True, "keys": secret_store.secret_keys()}


@router.delete("/settings/secrets/{key}")
def remove_secret(_request: Request, key: str) -> dict:
    if not secret_store.delete_secret(key):
        raise HTTPException(404, f"no secret {key!r}")
    return {"ok": True, "keys": secret_store.secret_keys()}
