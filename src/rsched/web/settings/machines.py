"""Machine catalog settings: CRUD over the config.yaml `machines:` block, plus a host-key
SCAN and a live reachability TEST — both run the reserved `remote` util server-side (the very
code a run uses), so what Settings proves is exactly what a routine gets. The private key never
lives here; a machine's `key_var` names a Secrets-store key, set on the Secrets page.
"""
from __future__ import annotations

import asyncio
import json
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ... import machines as machines_mod
from ... import sandbox, utils_lib
from ...config import MachineConfig, load_server_config
from ...secrets import load_secrets
from .common import server_of, update_config

router = APIRouter()

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")   # catalog key = the name routines bind
REMOTE_UTIL = "remote"


def _machine_view(mac: MachineConfig, have_keys: set[str]) -> dict:
    return machines_mod.machine_public(
        mac, name=mac.name, key_set=bool(mac.key_var and mac.key_var in have_keys))


@router.get("/settings/machines")
def list_machines(request: Request) -> dict:
    """The machine catalog (non-secret) — for the Settings card and the routine binding picker.
    Each entry reports whether its key_var secret is set (never the value).
    """
    server = server_of(request)
    have = set(load_secrets())
    return {"machines": [_machine_view(m, have) for m in server.machines.values()]}


def _rewrite_machines(request: Request, mutate) -> dict:
    def apply(raw: dict) -> None:
        machines = raw.get("machines") or {}
        mutate(machines)
        raw["machines"] = machines

    path = update_config(request, apply)
    fresh, problems = load_server_config(path)
    server = server_of(request)
    server.machines = fresh.machines
    return {"ok": True, "problems": problems}


class MachineBody(BaseModel):
    name: str
    host: str = ""
    user: str = ""
    port: int = 22
    key_var: str = ""
    host_key: str = ""
    share: str = ""       # remote dir to mount at <routine>/mnt/<name>/ when bound (sshfs)
    workdir: str = ""
    description: str = ""
    tags: list[str] = []


@router.put("/settings/machines/{name}")
def upsert_machine(request: Request, body: MachineBody, name: str | None = None) -> dict:
    key = name or body.name
    if not _NAME_RE.match(key):
        raise HTTPException(400, "machine name must be lowercase [a-z0-9] with - or _")
    if not body.host.strip() or not body.user.strip():
        raise HTTPException(400, "host and user are required")

    def mutate(machines: dict) -> None:
        spec = {k: v for k, v in body.model_dump().items()
                if k != "name" and v not in ("", None) and v != []}
        machines[key] = spec

    return _rewrite_machines(request, mutate)


@router.delete("/settings/machines/{name}")
def delete_machine(request: Request, name: str) -> dict:
    """Remove a catalog machine. A routine still bound to it simply resolves to nothing at run
    time (the same 'bind ahead of connecting' tolerance connections have), so no cross-home scan.
    """
    if name not in server_of(request).machines:
        raise HTTPException(404, f"no machine {name!r}")

    def mutate(machines: dict) -> None:
        machines.pop(name, None)

    return _rewrite_machines(request, mutate)


def _run_remote(server, args: list[str], extra_secrets: dict[str, str]) -> tuple[int, str, str]:
    """Run the reserved `remote` util from the daemon/web process (base sandbox policy — no run
    filesystem roots; the util only needs the network its `net:` line declares).
    """
    return utils_lib.run_util(server.utils_home, REMOTE_UTIL, args, timeout=90,
                              policy=sandbox.base_policy(server), extra_secrets=extra_secrets)


def _parse_util_json(code: int, out: str, err: str) -> dict:
    if code != 0:
        return {"ok": False, "error": (err.strip() or out.strip()
                                       or f"remote util exited {code}")}
    try:
        return {"ok": True, **json.loads(out)}
    except (ValueError, TypeError):
        return {"ok": False, "error": f"could not parse remote util output: {out[:300]}"}


class ScanBody(BaseModel):
    host: str
    port: int = 22


@router.post("/settings/machines/scan")
async def scan_host(request: Request, body: ScanBody) -> dict:
    """Read a host's public key line for pinning — the operator reviews it and saves it into the
    machine's `host_key`. This is the ONE step that does not verify a pinned key (it bootstraps
    the pin), so it is Settings-only, never something a run does.
    """
    server = server_of(request)
    if not body.host.strip():
        raise HTTPException(400, "host is required")
    args = ["scan-host", body.host.strip(), "--port", str(body.port), "--json"]
    return await asyncio.to_thread(lambda: _parse_util_json(*_run_remote(server, args, {})))


@router.post("/settings/machines/{name}/test")
async def test_machine(request: Request, name: str) -> dict:
    """Connect to a catalog machine and run `true` — the exact host-key-pinned, keyed path a run
    uses. Surfaces resolution warnings (unset key_var, missing host_key) alongside the result.
    """
    server = server_of(request)
    if name not in server.machines:
        raise HTTPException(404, f"no machine {name!r}")
    env, warnings = machines_mod.machines_for_routine([name], server.machines)

    def call() -> dict:
        result = _parse_util_json(*_run_remote(server, ["test", name, "--json"], env))
        result["warnings"] = warnings
        return result

    return await asyncio.to_thread(call)
