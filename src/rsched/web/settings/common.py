"""Shared plumbing for the settings modules: the live ServerConfig, config.yaml
read-modify-write persistence, and git-remote helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from fastapi import HTTPException, Request
from pydantic import BaseModel


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
    The daemon-side ServerConfig is live-reloaded by callers; engine subprocesses read it fresh."""
    path = config_path(request)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    mutate(raw)
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def remote_of(home: Path) -> str:
    r = subprocess.run(["git", "-C", str(home), "remote", "get-url", "origin"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""
