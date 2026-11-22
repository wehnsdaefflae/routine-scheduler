"""A machine's LOGIN KEY and the commands that install it — the pure half of the Settings
setup wizard (docs/remote-machines.md § Setting one up).

The daemon generates the key pair itself and stores the private half straight into the Secrets
store under the machine's `key_var`; only the PUBLIC half ever leaves this module, as an
`authorized_keys` line and as a shell command the operator can paste on the box (or the wizard
runs there over a one-time password login, `machine_bootstrap`).

Two install shapes, one per kind of login account:
- ACCOUNT: an existing account (`user` itself) gets the line in its own
  `~/.ssh/authorized_keys`. Run AS that account; idempotent.
- AGENT: a restricted user is created by `deploy/setup-remote-agent-user.sh`, shipped inline
  (base64) so the command needs nothing on the box but bash and sudo. Run by an admin.
"""
from __future__ import annotations

import base64
import hashlib
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

from .paths import repo_root

#: An account name the install commands may splice into a shell line unquoted.
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

#: Prepended to every installed key: a run needs exec and sftp, never forwarding.
KEY_OPTIONS = "no-agent-forwarding,no-X11-forwarding,no-port-forwarding"

SETUP_SCRIPT = "deploy/setup-remote-agent-user.sh"

MODES = ("account", "agent")


def key_var_for(name: str) -> str:
    """The Secrets-store key a machine's generated login key is stored under."""
    return re.sub(r"[^A-Z0-9_]", "_", name.upper()) + "_SSH_KEY"


def generate_key(name: str) -> tuple[str, str]:
    """A fresh unencrypted ed25519 pair: (OpenSSH private PEM, "type base64 comment")."""
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                            serialization.NoEncryption()).decode()
    return pem, public_line(pem, name)


def public_line(pem: str, name: str) -> str:
    """The public half of a stored private key, commented `rsched-<name>`. Raises ValueError
    for a key that cannot be read (encrypted, truncated, an unsupported type).
    """
    buf = (pem if pem.endswith("\n") else pem + "\n").encode()
    key: PrivateKeyTypes
    try:
        key = serialization.load_ssh_private_key(buf, password=None)
    except (ValueError, TypeError, AssertionError):
        key = serialization.load_pem_private_key(buf, password=None)
    pub = key.public_key().public_bytes(serialization.Encoding.OpenSSH,
                                        serialization.PublicFormat.OpenSSH).decode()
    return f"{pub} rsched-{name}"


def fingerprint(line: str) -> str:
    """OpenSSH's SHA256 fingerprint of a public key line — what `ssh-keygen -lf` prints."""
    blob = base64.b64decode(line.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def authorized_line(pub: str) -> str:
    """The exact line the box gets: the key with forwarding switched off."""
    return f"{KEY_OPTIONS} {pub}"


def check_user(user: str, what: str = "user") -> str:
    """`user` if it is a plain account name; ValueError otherwise (it goes into a shell line)."""
    if not USER_RE.match(user):
        raise ValueError(f"{what} {user!r} is not a plain account name ([a-z_][a-z0-9_-]*)")
    return user


def account_command(pub: str) -> str:
    """Install the key for the account that RUNS this line, in its own authorized_keys. Adds
    the line only when that key is not there yet, so pasting it twice changes nothing.
    """
    blob = pub.split()[1]
    line = authorized_line(pub)
    return ("umask 077 && mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && "
            f"{{ grep -qF '{blob}' ~/.ssh/authorized_keys || "
            f"echo '{line}' >> ~/.ssh/authorized_keys; }} && "
            "echo 'rsched: key installed for '\"$(id -un)\"")


def agent_command(pub: str, user: str, coowner: str = "", *, sudo_stdin: bool = False) -> str:
    """Create (or update) the restricted user `user` and install the key for it, by running the
    shipped setup script as root. `sudo_stdin` reads sudo's password from stdin — the wizard's
    password path; a pasted line lets sudo ask at the terminal.
    """
    script = (repo_root() / SETUP_SCRIPT).read_bytes()
    env = [f"AGENT_USER={check_user(user)}", f"AGENT_PUBKEY='{authorized_line(pub)}'"]
    if coowner:
        env.append(f"AGENT_COOWNER={check_user(coowner, 'co-owner')}")
    sudo = "sudo -S -p ''" if sudo_stdin else "sudo"
    b64 = base64.b64encode(script).decode()
    return f'{sudo} env {' '.join(env)} bash -c "$(echo {b64} | base64 -d)"'


def install_command(mode: str, pub: str, user: str, coowner: str = "", *,
                    sudo_stdin: bool = False) -> str:
    """The one line that installs `pub` for `user` on the box, in `mode`."""
    if mode == "account":
        return account_command(pub)
    if mode == "agent":
        return agent_command(pub, user, coowner, sudo_stdin=sudo_stdin)
    raise ValueError(f"mode must be one of {MODES}")
