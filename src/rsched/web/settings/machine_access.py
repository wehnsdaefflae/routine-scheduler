"""Settings → Machines setup wizard: give a catalog machine a login key and put it on the box.

Three steps after the machine is saved (host, user, pinned host key), each its own route:
- `key` — the daemon generates the login key and stores the private half in the Secrets store
  under the machine's `key_var` (named for the machine when it has none). An existing readable
  key is REUSED, never rotated: another machine may share that secret.
- `install-command` — the one line that installs the public half, for the operator to paste.
- `install` — the same line run on the box over a one-time password login
  (`machine_bootstrap`); the password is used for that one login and kept nowhere.
The wizard's last step is the ordinary `test` route, the exact path a run connects by.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ... import machine_access as access
from ... import machine_bootstrap
from ... import secrets as secret_store
from ...config import MachineConfig
from .common import rewrite_block, server_of

router = APIRouter()


def _machine(request: Request, name: str) -> MachineConfig:
    mac = server_of(request).machines.get(name)
    if mac is None:
        raise HTTPException(404, f"no machine {name!r}")
    return mac


def _public(mac: MachineConfig, name: str) -> str:
    """The public half of the machine's stored key; 409 when there is none to read yet."""
    pem = secret_store.load_secrets().get(mac.key_var or "", "")
    if not pem.strip():
        raise HTTPException(409, f"machine {name!r} has no login key yet — create it first")
    try:
        return access.public_line(pem, name)
    except ValueError as exc:
        raise HTTPException(409, f"the secret {mac.key_var} holds a key the scheduler cannot "
                                 f"read ({exc}) — it must be an unencrypted private key") from exc


@router.post("/settings/machines/{name}/key")
def ensure_key(request: Request, name: str) -> dict:
    """The machine's login key, created on first call. Answers the PUBLIC half only."""
    mac = _machine(request, name)
    key_var = mac.key_var or access.key_var_for(name)
    generated = False
    if not secret_store.load_secrets().get(key_var, "").strip():
        pem, _pub = access.generate_key(name)
        secret_store.set_secret(key_var, pem)
        generated = True
    if mac.key_var != key_var:
        def mutate(machines: dict) -> None:
            machines.setdefault(name, {})["key_var"] = key_var
        rewrite_block(request, "machines", mutate, "machines")
        mac = _machine(request, name)
    pub = _public(mac, name)
    return {"ok": True, "key_var": key_var, "generated": generated, "public_key": pub,
            "fingerprint": access.fingerprint(pub)}


def _mode(mode: str) -> str:
    if mode not in access.MODES:
        raise HTTPException(400, f"mode must be one of {access.MODES}")
    return mode


@router.get("/settings/machines/{name}/install-command")
def install_command(request: Request, name: str, mode: str = "account",
                    coowner: str = "") -> dict:
    """The line to paste on the box, and who must run it there."""
    mac = _machine(request, name)
    pub = _public(mac, name)
    try:
        command = access.install_command(_mode(mode), pub, mac.user, coowner.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    run_as = (f"as {mac.user}" if mode == "account"
              else "as an admin account that can sudo")
    return {"ok": True, "command": command, "run_as": run_as, "host": mac.host,
            "fingerprint": access.fingerprint(pub)}


class InstallBody(BaseModel):
    mode: str = "account"
    password: str
    login_user: str = ""      # agent mode: the admin account that logs in and runs sudo
    coowner: str = ""


@router.post("/settings/machines/{name}/install")
async def install(request: Request, name: str, body: InstallBody) -> dict:
    """Install the key over a one-time password login as the account the mode needs."""
    mac = _machine(request, name)
    mode = _mode(body.mode)
    if not body.password:
        raise HTTPException(400, "a password is required")
    if not mac.host_key:
        raise HTTPException(409, "the machine has no pinned host key — scan it first")
    pub = _public(mac, name)
    login = mac.user if mode == "account" else body.login_user.strip()
    try:
        access.check_user(login, "login user")
        command = access.install_command(mode, pub, mac.user, body.coowner.strip(),
                                         sudo_stdin=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await asyncio.to_thread(
        machine_bootstrap.run_with_password, mac.host, mac.port, mac.host_key, login,
        body.password, command, sudo=mode == "agent")
