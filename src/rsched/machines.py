"""Remote machines — the instance-wide catalog of SSH-reachable hosts a routine may act on
(GPU boxes, build servers). A RESOURCE binding like OAuth connections: the catalog lives in
config.yaml (operator-only, `ServerConfig.machines`), a routine's `machines:` list names the
ones it may reach, and the run is a pure READER — the engine resolves the binding into the env
vars the reserved `remote` util receives. Key MATERIAL never sits in config: each catalog entry
names a Secrets-store key (`key_var`) holding the private key; only that is a credential.

Two env vars carry the binding to the util, both under the declared-only injection gate
(utils_lib._child_env): `RSCHED_MACHINES` — non-secret connection metadata (host/user/port/
host_key/workdir/description/tags) — and `RSCHED_MACHINE_KEYS` — {name: private-key PEM}, a
credential (its name ends in KEYS, so the util-authoring gate forces its declaration). See
docs/remote-machines.md.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .paths import atomic_write, config_file

if TYPE_CHECKING:
    from .config import MachineConfig, RoutineConfig, ServerConfig

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


# ------------------------------------------------------------------- sshfs share mounts ------
# A bound machine whose catalog entry sets `share` gets that remote dir MOUNTED at
# <routine>/mnt/<name>/ for the run's lifetime — the routine dir is already a sandbox write
# root, so local filesystem utils read/write the mounted remote files with no extra grant
# (verified: a Landlock jail rule on the routine dir covers the sshfs sub-mount). The engine
# (unsandboxed) does the mount, like OAuth consent — the private key never enters a util.
MOUNT_SUBDIR = "mnt"


@dataclass
class MountedShare:
    name: str
    mountpoint: Path
    keydir: Path        # util-invisible temp dir with the key + known_hosts (removed on unmount)


def routine_mount_dir(routine_dir: Path) -> Path:
    return routine_dir / MOUNT_SUBDIR


def known_hosts_lines(host: str, port: int, host_key_text: str) -> list[str]:
    """Catalog `host_key` → known_hosts lines for THIS host:port (engine-side twin of the
    `remote` util's helper; a non-default port is keyed [host]:port, as ssh expects). Pure.

    Accepts every shape an operator pastes: `ssh-keyscan` output (`host type key`), a
    `.pub` file (`type key comment`), or a bare `type key`. The key TYPE token anchors
    the parse — taking the last two tokens would silently pin `key comment` for a .pub
    paste and every connection would then refuse.
    """
    entry_host = host if int(port) == 22 else f"[{host}]:{port}"
    out = []
    for line in host_key_text.splitlines():
        parts = line.split()
        idx = next((i for i, tok in enumerate(parts)
                    if tok.startswith(("ssh-", "ecdsa-", "sk-"))), None)
        if idx is not None and len(parts) > idx + 1:
            out.append(f"{entry_host} {parts[idx]} {parts[idx + 1]}")
    return out


def sshfs_argv(mac: MachineConfig, mountpoint: Path, key_path: Path,
               known_hosts_path: Path) -> list[str]:
    """The sshfs command for a machine's share — host-key PINNED (StrictHostKeyChecking=yes
    against the pinned known_hosts), key-only auth, and reconnect so a brief drop self-heals.
    Pure (builds argv only); the caller runs it.
    """
    port = int(mac.port or 22)
    return [
        "sshfs", f"{mac.user}@{mac.host}:{mac.share}", str(mountpoint),
        "-p", str(port),
        "-o", f"IdentityFile={key_path}",
        "-o", f"UserKnownHostsFile={known_hosts_path}",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "reconnect",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-o", "BatchMode=yes",           # never prompt (headless)
    ]


def _unmount_path(mountpoint: Path) -> None:
    """Best-effort unmount, trying the FUSE helpers then umount (whichever the host has)."""
    for argv in (["fusermount3", "-u", str(mountpoint)], ["fusermount", "-u", str(mountpoint)],
                 ["umount", str(mountpoint)]):
        if shutil.which(argv[0]):
            r = subprocess.run(argv, capture_output=True, text=True, check=False)
            if r.returncode == 0:
                return


def _mount_base() -> Path:
    """A daemon-private dir for transient key/known_hosts files — under the config dir, which
    the util sandbox keeps INVISIBLE (so a mount's private key never sits where a util can read
    it, unlike /tmp or ~/.cache).
    """
    base = config_file().parent / ".mounts"
    base.mkdir(parents=True, exist_ok=True)
    base.chmod(0o700)
    return base


def _ensure_mnt_gitignored(routine_dir: Path) -> None:
    """Keep the mount dir out of the engine autocommit — else `git add -A` descends into the
    sshfs mount and commits the REMOTE filesystem into the routine's repo.
    """
    gi = routine_dir / ".gitignore"
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.is_file() else []
    if any(ln.strip().rstrip("/") == MOUNT_SUBDIR for ln in lines):
        return
    atomic_write(gi, "\n".join([*lines, "# remote-machine share mounts (transient)",
                                f"{MOUNT_SUBDIR}/", ""]))


def mount_routine_shares(routine: RoutineConfig, server: ServerConfig, *,
                         secrets: dict[str, str] | None = None) -> list[MountedShare]:
    """Mount every bound machine's `share` at <routine>/mnt/<name>/ for this run. Best-effort
    and NON-FATAL: a machine with no share, no key, no pinned host key, or an unreachable host
    is skipped with a warning — the run proceeds without that mount. Returns the shares that
    mounted (pass them to unmount_routine_shares in a finally).
    """
    bound = getattr(routine, "machines", None) or []
    catalog = getattr(server, "machines", {}) or {}
    wanted = [(n, catalog[n]) for n in dict.fromkeys(bound)
              if n in catalog and catalog[n].share]
    if not wanted:
        return []
    # Gitignore mnt/ BEFORE any mount attempt: a crashed prior run's STALE mount must
    # never be swept into the autocommit just because this run's own mounts failed early
    # (no sshfs / no key) and the write further down was skipped.
    _ensure_mnt_gitignored(routine.dir)
    if not shutil.which("sshfs"):
        log.warning("machines: sshfs not installed — cannot mount share(s) %s",
                    [n for n, _ in wanted])
        return []
    if secrets is None:
        from .secrets import load_secrets
        secrets = load_secrets()
    base = _mount_base()
    mounted: list[MountedShare] = []
    for name, mac in wanted:
        pem = (secrets.get(mac.key_var) or "").strip() if mac.key_var else ""
        if not pem or not mac.host_key.strip():
            log.warning("machines: cannot mount %r — %s", name,
                        "no private key" if not pem else "no pinned host key")
            continue
        mp = routine_mount_dir(routine.dir) / name
        keydir: Path | None = None
        try:
            _unmount_path(mp)                    # clear a stale mount from a crashed prior run
            mp.mkdir(parents=True, exist_ok=True)
            keydir = Path(tempfile.mkdtemp(dir=base))
            keyp = keydir / "key"
            keyp.write_text(pem + "\n" if not pem.endswith("\n") else pem, encoding="utf-8")
            keyp.chmod(0o600)
            khp = keydir / "known_hosts"
            khp.write_text("\n".join(known_hosts_lines(mac.host, mac.port, mac.host_key)) + "\n",
                           encoding="utf-8")
            r = subprocess.run(sshfs_argv(mac, mp, keyp, khp),
                               capture_output=True, text=True, timeout=45, check=False)
            if r.returncode != 0:
                log.warning("machines: sshfs mount of %r failed: %s", name,
                            r.stderr.strip() or f"exit {r.returncode}")
                shutil.rmtree(keydir, ignore_errors=True)
                continue
            mounted.append(MountedShare(name=name, mountpoint=mp, keydir=keydir))
            log.info("machines: mounted %r share at %s", name, mp)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("machines: mounting %r failed: %s", name, exc)
            if keydir is not None:   # the PEM must not outlive the failed attempt
                shutil.rmtree(keydir, ignore_errors=True)
    return mounted


# A keydir belongs to at most one live run; anything older than this at daemon boot is
# residue from a crash (detached runs survive daemon restarts, so a fresh sweep must
# not touch dirs a still-running engine could be using).
MOUNT_KEYDIR_MAX_AGE_S = 48 * 3600


def sweep_stale_mount_keys() -> int:
    """Boot reconcile for `.mounts/`: remove key/known_hosts dirs abandoned by crashed
    runs (unmount_routine_shares removes them on every clean exit — a SIGKILL does not).
    Returns how many were removed.
    """
    base = config_file().parent / ".mounts"
    if not base.is_dir():
        return 0
    cutoff = time.time() - MOUNT_KEYDIR_MAX_AGE_S
    removed = 0
    try:
        entries = list(base.iterdir())
    except OSError as exc:
        # This runs on run_forever's BOOT path — before the tick loop's per-tick guard
        # exists. An unreadable .mounts/ (permission blip, sandboxed test env) must be a
        # loudly-skipped sweep, never an exception that kills scheduling for good.
        log.warning("machines: cannot sweep .mounts/ (%s) — skipped", exc)
        return 0
    for d in entries:
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        log.warning("machines: swept %d stale mount key dir(s) from .mounts/", removed)
    return removed


def unmount_routine_shares(mounted: list[MountedShare]) -> None:
    """Unmount the shares mount_routine_shares set up + remove their transient key dirs.
    Best-effort — a SIGKILLed engine leaves a stale mount that the next run's pre-mount
    _unmount_path clears.
    """
    for m in mounted:
        _unmount_path(m.mountpoint)
        shutil.rmtree(m.keydir, ignore_errors=True)
