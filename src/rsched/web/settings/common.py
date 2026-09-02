"""Shared plumbing for the settings modules: the live ServerConfig, config.yaml
read-modify-write persistence, and git-remote helpers.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import HTTPException, Request
from pydantic import BaseModel

from ... import libgit
from ...config import load_server_config


class RemoteBody(BaseModel):
    remote: str


def server_of(request: Request):
    return request.app.state.server


def config_path(request: Request) -> Path:
    p = server_of(request).source
    if p is None or not p.exists():
        raise HTTPException(500, "server config file not found")
    return p


def update_config(request: Request, mutate) -> Path:
    """Read-modify-write config.yaml; returns the path so callers can reload derived state.
    The daemon-side ServerConfig is live-reloaded by callers; engine subprocesses read it fresh.
    """
    from ...paths import atomic_write_yaml, read_yaml

    path = config_path(request)
    raw = read_yaml(path, {})
    mutate(raw)
    # atomic: engine subprocesses load config fresh mid-run — a torn read is a broken run
    atomic_write_yaml(path, raw)
    return path


def reload_into(request: Request, path: Path, *fields: str) -> list[str]:
    """Re-read config.yaml from `path`, mirror the named fields onto the LIVE ServerConfig,
    return the loader's problem list. The daemon holds one long-lived ServerConfig that every
    request and every scheduler tick reads, so a write that is not mirrored back is invisible
    until a restart. Only the NAMED fields are copied and the object is never rebound — that
    is what keeps the runtime knobs a save may change apart from the install-time ones (homes,
    bind, port, auth token) that must never move under a running process.
    """
    fresh, problems = load_server_config(path)
    server = server_of(request)
    for field in fields:
        setattr(server, field, getattr(fresh, field))
    return problems


def rewrite_block(request: Request, key: str, mutate: Callable[[dict], None],
                  *mirror: str) -> dict:
    """Read-modify-write ONE top-level config.yaml mapping (`endpoints`, `models`, `machines`)
    and mirror the result onto the live ServerConfig — the whole body of a settings CRUD
    handler, so each handler says only what it changes inside the block. `mutate` is handed
    that block (empty when the key is absent) and edits it in place; `mirror` names the fields
    this block's save refreshes, a per-block decision (an endpoints save also re-mirrors
    `system_model`) that belongs on the one named wrapper, not at each CRUD handler.
    """
    def apply(raw: dict) -> None:
        block = raw.get(key) or {}
        mutate(block)
        raw[key] = block

    path = update_config(request, apply)
    return {"ok": True, "problems": reload_into(request, path, *mirror)}


def remote_of(home: Path) -> str:
    r = libgit.git(home, "remote", "get-url", "origin")
    return r.stdout.strip() if r.returncode == 0 else ""
