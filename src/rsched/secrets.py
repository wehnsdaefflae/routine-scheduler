"""Secrets stores — KEY=VALUE files next to config.yaml that the engine injects into every
util subprocess, the claude-cli transport, and endpoint key lookup at RUN time (utils read
env-first by convention). One place in the UI to set ANY credential — including ones a
generated util needs — with no per-secret wiring and no restart. Values are written from the
UI, never echoed back.

TWO SCOPES (D103, operator decision 2026-08-26 — R497):

  - the **central store** `secrets.env`, instance-wide. A name here is shared vocabulary, so
    exposing one to a routine is a DECISION: the four-state `secret:<NAME>` grant.
  - a **routine-scoped store** `secrets.d/<slug>.env`, one file per routine. `SFTP_USER` means
    something different to every routine that has one, and a flat namespace forced them to
    collide or to be spelled `EYESTAB_SFTP_USER` by convention. A scoped secret belongs to its
    routine: no grant class, no ask — it is implicitly exposed to its owner's runs and to
    nobody else, and it SHADOWS a central value of the same name for that routine.

The declared-only invariant is unchanged and covers both: a util receives a var only if its
own `secrets:` header (or a transitive `calls:` sibling's) declares it.

Scoped values live under the CONFIG dir, never in the routine's own dir: a routine repo is
`git add -A` autocommitted and auto-pushed, so a secret written there would leave the host.

Format: one `KEY=VALUE` line per secret. A value CONTAINING newlines (an SSH private key —
the remote-machines `key_var` case) is stored as one line with the value JSON-quoted, so a
pasted PEM round-trips through the UI instead of silently corrupting into stray
pseudo-keys. Single-line values are written raw, byte-identical to the historical format.
"""
from __future__ import annotations

import json
import re

from .ids import is_slug
from .paths import atomic_write, config_file

SECRETS_FILE = "secrets.env"
SCOPED_DIR = "secrets.d"                             # one <slug>.env per routine (D103)
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")     # a valid environment variable name


def secrets_path():
    return config_file().parent / SECRETS_FILE


def scoped_path(slug: str):
    """`secrets.d/<slug>.env` — one routine's own store, beside the central one. Derived
    from `secrets_path()` rather than `config_file()` so the two scopes can never drift
    apart (and so one patch point relocates BOTH — the hermetic test fixture's). The slug is
    validated, so a caller can never walk out of the directory with a crafted name.
    """
    if not is_slug(slug):
        raise ValueError(f"{slug!r} is not a valid routine slug")
    return secrets_path().parent / SCOPED_DIR / f"{slug}.env"


def _decode_value(raw: str) -> str:
    """A double-quoted value is JSON-decoded (the multi-line escape); anything else keeps
    the historical treatment (strip whitespace and simple wrapping quotes).
    """
    s = raw.strip()
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        try:
            decoded = json.loads(s)
            if isinstance(decoded, str):
                return decoded
        except ValueError:
            pass
    return s.strip('"').strip("'")


def _read(path) -> dict[str, str]:
    """Parse one store file → {KEY: VALUE}; missing file → {}. Tolerant of comments and
    blank lines.
    """
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                k = k.strip()
                if KEY_RE.match(k):
                    out[k] = _decode_value(v)
    return out


def load_secrets() -> dict[str, str]:
    """The CENTRAL store → {KEY: VALUE}. Never merged with a routine's own store here:
    the merge is the engine's, at injection time, so every caller that reasons about
    EXPOSURE (the grant gate, the request validator) keeps seeing the shared names alone.
    """
    return _read(secrets_path())


def secret_keys() -> list[str]:
    """Names only — never values (what the UI is allowed to see)."""
    return sorted(load_secrets().keys())


def load_routine_secrets(slug: str) -> dict[str, str]:
    """One routine's OWN secrets → {KEY: VALUE}; no store → {}. Implicitly exposed to that
    routine's runs and to nothing else (D103).
    """
    return _read(scoped_path(slug))


def routine_secret_keys(slug: str) -> list[str]:
    """Names only, for the routine page's Secrets section."""
    return sorted(load_routine_secrets(slug).keys())


def set_routine_secret(slug: str, key: str, value: str) -> None:
    if not KEY_RE.match(key):
        raise ValueError(f"{key!r} is not a valid environment variable name")
    d = load_routine_secrets(slug)
    d[key] = value
    _write(d, scoped_path(slug))


def delete_routine_secret(slug: str, key: str) -> bool:
    d = load_routine_secrets(slug)
    if key not in d:
        return False
    del d[key]
    _write(d, scoped_path(slug))
    return True


def drop_routine_secrets(slug: str) -> bool:
    """Delete a routine's whole store — called when the routine itself is deleted, so a
    credential never outlives the only thing entitled to it.
    """
    path = scoped_path(slug)
    if not path.exists():
        return False
    path.unlink()
    return True


def set_secret(key: str, value: str) -> None:
    if not KEY_RE.match(key):
        raise ValueError(f"{key!r} is not a valid environment variable name")
    d = load_secrets()
    d[key] = value
    _write(d, secrets_path())


def delete_secret(key: str) -> bool:
    d = load_secrets()
    if key not in d:
        return False
    del d[key]
    _write(d, secrets_path())
    return True


def _encode_value(v: str) -> str:
    """Values with newlines (PEM keys) are JSON-quoted onto one line; plain values are
    written raw so a store of ordinary keys stays byte-identical to the historical file.
    """
    return json.dumps(v) if "\n" in v or "\r" in v else v


def _write(d: dict[str, str], path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, "".join(f"{k}={_encode_value(v)}\n" for k, v in d.items()))
    try:
        path.chmod(0o600)
    except OSError:
        pass
