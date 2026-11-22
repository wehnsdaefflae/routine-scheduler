"""Run ONE install command on a machine over a one-time PASSWORD login — the setup wizard's
"do it for me" path, so the operator never pastes anything onto the box.

It uses the system `ssh` client (the daemon carries no SSH library; the `remote` util is the
only key-based client), fed the password through an SSH_ASKPASS helper that reads it from this
process's environment: the password never touches disk, argv or a log. The host key is the
machine's PINNED one, checked strictly — a password is never offered to an unverified host.
When the command needs root, the same password answers `sudo -S` on stdin.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

_PW_VAR = "RSCHED_BOOTSTRAP_PW"
_ASKPASS = f'#!/bin/sh\nprintf "%s\\n" "${_PW_VAR}"\n'
TIMEOUT_S = 180

#: ssh's own words for a refused password, read so the operator is told what to do next.
_REFUSED = ("Permission denied", "Too many authentication failures")


def known_hosts_lines(host: str, port: int, host_key: str) -> list[str]:
    """A catalog `host_key` (keyscan "host type b64", a .pub line, or bare "type b64") as
    known_hosts lines for host:port — the same normalisation as the `remote` util's
    `hostkey_lines`, anchored on the key TYPE token.
    """
    entry = host if int(port) == 22 else f"[{host}]:{port}"
    out = []
    for line in host_key.splitlines():
        parts = line.split()
        idx = next((i for i, tok in enumerate(parts)
                    if tok.startswith(("ssh-", "ecdsa-", "sk-"))), None)
        if idx is not None and len(parts) > idx + 1:
            out.append(f"{entry} {parts[idx]} {parts[idx + 1]}")
    return out


def ssh_argv(host: str, port: int, user: str, known_hosts: Path, command: str) -> list[str]:
    """The ssh invocation: password auth only, the pinned host key only, no user config."""
    return ["ssh", "-F", "/dev/null", "-T", "-p", str(int(port)),
            "-o", "BatchMode=no", "-o", "PubkeyAuthentication=no",
            "-o", "PreferredAuthentications=keyboard-interactive,password",
            "-o", "NumberOfPasswordPrompts=1", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts}", "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=15", "-o", "LogLevel=ERROR",
            f"{user}@{host}", command]


def run_with_password(host: str, port: int, host_key: str, user: str, password: str,
                      command: str, *, sudo: bool = False) -> dict:
    """Log in as `user` with `password`, run `command`, report {ok, output} or {ok, error}.
    `sudo` feeds the password on stdin for the command's `sudo -S`.
    """
    lines = known_hosts_lines(host, port, host_key)
    if not lines:
        return {"ok": False, "error": "the machine has no pinned host key — scan it first"}
    with tempfile.TemporaryDirectory(prefix="rsched-bootstrap-") as tmp:
        kh, askpass = Path(tmp) / "known_hosts", Path(tmp) / "askpass"
        kh.write_text("\n".join(lines) + "\n", encoding="utf-8")
        askpass.write_text(_ASKPASS, encoding="utf-8")
        askpass.chmod(0o700)
        env = {**os.environ, _PW_VAR: password, "SSH_ASKPASS": str(askpass),
               "SSH_ASKPASS_REQUIRE": "force", "DISPLAY": os.environ.get("DISPLAY", ":0")}
        try:
            proc = subprocess.run(
                ssh_argv(host, port, user, kh, command), env=env, capture_output=True,
                input=(password + "\n") if sudo else "", text=True, timeout=TIMEOUT_S,
                check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"no answer from {host} within {TIMEOUT_S}s"}
        except FileNotFoundError:
            return {"ok": False, "error": "no ssh client on the scheduler host"}
    out = proc.stdout + proc.stderr
    if password:
        out = out.replace(password, "•••")
    if proc.returncode == 0:
        return {"ok": True, "output": out.strip()[-4000:]}
    if proc.returncode == 255 and any(s in out for s in _REFUSED):
        return {"ok": False, "refused": True,
                "error": f"{user}@{host} refused the password (or the box allows key login "
                         "only) — use the copy command instead"}
    if "Host key verification failed" in out:
        return {"ok": False, "error": f"{host} presented a host key other than the pinned one — "
                                      "no password was sent; re-scan the host if it changed"}
    return {"ok": False, "error": out.strip()[-4000:] or f"ssh exited {proc.returncode}"}
