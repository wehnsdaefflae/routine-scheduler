"""Remote machines — the instance-wide catalog of SSH-reachable hosts a routine may act on
(GPU boxes, build servers). A RESOURCE binding like OAuth connections: the catalog lives in
config.yaml (operator-only, `ServerConfig.machines`), a routine's `machines:` list names the
ones it may reach, and the run is a pure READER — the engine resolves the binding into the env
vars the reserved `remote` util receives. Key MATERIAL never sits in config: each catalog entry
names a Secrets-store key (`key_var`) holding the private key; only that is a credential.

Two env vars carry the binding to the util, both under the declared-only injection gate
(utils_run._child_env): `RSCHED_MACHINES` — non-secret connection metadata (host/user/port/
host_key/workdir/description/tags) — and `RSCHED_MACHINE_KEYS` — {name: private-key PEM}, a
credential (its name ends in KEYS, so the util-authoring gate forces its declaration). See
docs/remote-machines.md.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import MachineConfig

log = logging.getLogger("rsched.machines")

# The two env vars the engine injects for a routine's bound machines. Kept out of the Settings
# "needed secrets" prompt (like OAuth access-token vars) — the user never SETS these; they are
# assembled from the catalog + the per-machine key_var secret.
MACHINES_VAR = "RSCHED_MACHINES"          # env var NAME (non-secret metadata)
MACHINE_KEYS_VAR = "RSCHED_MACHINE_KEYS"  # env var NAME (carries the per-machine PEMs)


def machine_env_vars() -> set[str]:
    """The engine-injected machine env vars — these come from binding a machine, not from the
    user, so the Settings 'needed secrets' list must not prompt for them as store secrets.
    """
    return {MACHINES_VAR, MACHINE_KEYS_VAR}


def machine_public(mac: MachineConfig, *, key_set: bool, name: str | None = None) -> dict:
    """Non-secret connection metadata for one machine — what reaches the `remote` util (and the
    Settings/routine cards). Never the private key; `key_set` only reports whether the key_var
    secret is populated. `name` defaults to `mac.name` (filled by load_server_config) but can be
    overridden with the catalog key, so resolution never depends on that post-load step.
    """
    return {"name": name if name is not None else mac.name,
            "host": mac.host, "user": mac.user, "port": mac.port,
            "host_key": mac.host_key, "workdir": mac.workdir, "share": mac.share,
            "description": mac.description, "tags": list(mac.tags),
            "key_var": mac.key_var, "has_key": key_set, "has_host_key": bool(mac.host_key)}


def resolve_machines(names: list[str], catalog: dict[str, MachineConfig],
                     secrets: dict[str, str]) -> tuple[list[dict], dict[str, str], list[str]]:
    """Resolve a routine's bound machine NAMES against the catalog + the Secrets store. Returns
    (metadata list, {name: private-key PEM}, warnings). A name absent from the catalog, or one
    whose `key_var` is unset, is surfaced as a warning; its metadata is still returned (so the
    util can `--list` it and report the gap), but no key is provided for it.
    """
    meta: list[dict] = []
    keys: dict[str, str] = {}
    warnings: list[str] = []
    for name in dict.fromkeys(names or []):     # de-dupe, order-preserving
        mac = catalog.get(name)
        if mac is None:
            warnings.append(f"machine {name!r} is not in the catalog")
            continue
        pem = ""
        if not mac.key_var:
            warnings.append(f"machine {name!r} has no key_var configured (Settings → Machines)")
        else:
            pem = (secrets.get(mac.key_var) or "").strip()
            if not pem:
                warnings.append(
                    f"machine {name!r}: key_var {mac.key_var!r} is not set in Secrets")
        if pem:
            keys[name] = pem
        meta.append(machine_public(mac, key_set=bool(pem), name=name))
    return meta, keys, warnings


def machines_for_routine(names: list[str], catalog: dict[str, MachineConfig], *,
                         secrets: dict[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    """The engine injection: a routine's `machines:` bindings → the env vars its utils receive.
    Returns (env, warnings). No bindings → ({}, []). Otherwise `RSCHED_MACHINES` (JSON metadata
    list) and `RSCHED_MACHINE_KEYS` (JSON {name: PEM}) are always returned so the util sees the
    binding even when some entries could not be fully resolved.
    """
    if not names:
        return {}, []
    if secrets is None:
        from .secrets import load_secrets
        secrets = load_secrets()
    meta, keys, warnings = resolve_machines(names, catalog, secrets)
    env = {MACHINES_VAR: json.dumps(meta, separators=(",", ":")),
           MACHINE_KEYS_VAR: json.dumps(keys, separators=(",", ":"))}
    return env, warnings


