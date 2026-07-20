"""Central secrets store: one KEY=VALUE store injected into utils + endpoints + claude-cli.
Set any credential here (a util's token, a username, an API key, the Claude subscription token as
CLAUDE_CODE_OAUTH_TOKEN). The engine injects it into every util + endpoint at run time. Values are
write-only: the API returns key NAMES, never the values.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ... import secrets as secret_store
from .common import server_of

router = APIRouter()


@router.get("/settings/secrets")
def list_secrets(request: Request) -> dict:
    from ... import utils_lib
    from ...oauth.providers import connection_token_vars
    store_vals = secret_store.load_secrets()
    have = set(store_vals)
    # A secret whose value is a JSON object is a MULTI-ENTRY secret (e.g. FTP_SOURCES) — expose its
    # entry NAMES (never the values) so the UI can add/replace/delete one entry at a time without
    # re-typing the write-only blob.
    maps: dict[str, list[str]] = {}
    for k, raw in store_vals.items():
        parsed = _parse_map(raw)
        if parsed is not None:
            maps[k] = sorted(parsed)
    # A util declares an OAuth connection's access token (e.g. NOTION_ACCESS_TOKEN) only so the
    # sandbox lets the ENGINE-injected token through — the user never SETS it (it comes from binding
    # a connection), so it must not appear as a needed store secret.
    injected = connection_token_vars()
    # which env vars do the installed utils declare they need, and are they set yet?
    utils = utils_lib.list_utils(server_of(request).utils_home)
    by_name = {u["name"]: u for u in utils}
    declared: dict[str, list[str]] = {}
    for u in utils:
        for var in u.get("secrets", []):
            if var.upper() in injected:
                continue
            declared.setdefault(var, []).append(u["name"])
    # Carry the declaring util's usage + docstring so the UI can show the expected FORMAT of a
    # structured secret (e.g. FTP_SOURCES is a JSON map) right where the user sets it.
    needed = []
    for k, us in sorted(declared.items()):
        primary = by_name.get(us[0], {})
        needed.append({"key": k, "utils": us, "set": k in have,
                       "usage": primary.get("usage", ""), "doc": primary.get("doc", "")})
    return {"keys": sorted(have), "needed": needed, "maps": maps,
            "path": str(secret_store.secrets_path())}


def _parse_map(raw: str) -> dict | None:
    """The secret value as a JSON object, or None if it isn't one (a scalar secret)."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


class SecretBody(BaseModel):
    key: str
    value: str


class SecretEntry(BaseModel):
    name: str
    value: dict


@router.put("/settings/secrets/{key}/entry")
def put_secret_entry(_request: Request, key: str, body: SecretEntry) -> dict:
    """Add/replace ONE named entry in a JSON-map secret (e.g. an FTP source), merging server-side so
    the other entries' values are never returned to the client. Creates the secret if unset.
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "an entry name is required")
    raw = secret_store.load_secrets().get(key, "")
    data = _parse_map(raw) if raw else {}
    if data is None:
        raise HTTPException(400, f"{key} holds a non-JSON-object value — clear it to use entries")
    data[name] = body.value
    try:
        secret_store.set_secret(key.strip(), json.dumps(data, separators=(",", ":")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "entries": sorted(data)}


@router.delete("/settings/secrets/{key}/entry/{name}")
def delete_secret_entry(_request: Request, key: str, name: str) -> dict:
    """Remove one entry from a JSON-map secret; dropping the last entry deletes the secret."""
    data = _parse_map(secret_store.load_secrets().get(key, "") or "{}")
    if not data or name not in data:
        raise HTTPException(404, f"no entry {name!r} in {key}")
    del data[name]
    if data:
        secret_store.set_secret(key, json.dumps(data, separators=(",", ":")))
    else:
        secret_store.delete_secret(key)
    return {"ok": True, "entries": sorted(data)}


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
