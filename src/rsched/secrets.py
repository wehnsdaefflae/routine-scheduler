"""Secrets stores — KEY=VALUE files next to config.yaml that the engine injects into every
util subprocess and endpoint key lookup at RUN time (utils read
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

Every write is a read-modify-write of a whole store file, so each one runs under that file's
lock (`_update`): the settings handlers run on worker threads, and two concurrent edits — two
entries saved into one JSON-map secret, a key set while another is deleted — each read the
same file and the second write silently undid the first.

Format: one `KEY=VALUE` line per secret. A value CONTAINING newlines (an SSH private key —
the remote-machines `key_var` case) is stored as one line with the value JSON-quoted, so a
pasted PEM round-trips through the UI instead of silently corrupting into stray
pseudo-keys. Single-line values are written raw, byte-identical to the historical format.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from .ids import is_slug
from .paths import atomic_write, config_file, file_lock

T = TypeVar("T")

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
    _check_key(key)
    _update(scoped_path(slug), lambda d: d.__setitem__(key, value))


def delete_routine_secret(slug: str, key: str) -> bool:
    return _update(scoped_path(slug), lambda d: d.pop(key, None) is not None)


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
    update_secret(key, lambda _old: value)


def delete_secret(key: str) -> bool:
    return _update(secrets_path(), lambda d: d.pop(key, None) is not None)


def update_secret(key: str, edit: Callable[[str | None], str | None]) -> str | None:
    """Read-modify-write ONE central secret under the store's lock: `edit` gets the current
    value (None when unset) and returns the new one, or None to delete it; returns what it
    returned. The JSON-map entry routes (web/settings/secrets.py) edit one entry of a value
    through this, so two entries saved at once both land. An exception from `edit` leaves the
    store untouched.
    """
    _check_key(key)

    def apply(d: dict[str, str]) -> str | None:
        new = edit(d.get(key))
        if new is None:
            d.pop(key, None)
        else:
            d[key] = new
        return new

    return _update(secrets_path(), apply)


def _check_key(key: str) -> None:
    if not KEY_RE.match(key):
        raise ValueError(f"{key!r} is not a valid environment variable name")


def _update(path: Path, edit: Callable[[dict[str, str]], T]) -> T:  # noqa: UP047 — pdoc can't parse PEP 695 generics
    """Apply `edit` to one store's {KEY: VALUE} map under that file's lock, writing the file
    back only when the map changed — the one read-modify-write every setter goes through.
    """
    with file_lock(path.with_name(f".{path.name}.lock")):
        d = _read(path)
        before = dict(d)
        result = edit(d)
        if d != before:
            _write(d, path)
        return result


def _encode_value(v: str) -> str:
    """Values with newlines (PEM keys) are JSON-quoted onto one line; plain values are
    written raw so a store of ordinary keys stays byte-identical to the historical file.
    """
    return json.dumps(v) if "\n" in v or "\r" in v else v


def _write(d: dict[str, str], path: Path) -> None:
    # 0600 on the temp file BEFORE the rename: chmod-after-write left the new file
    # briefly carrying whatever the old one had
    atomic_write(path, "".join(f"{k}={_encode_value(v)}\n" for k, v in d.items()),
                 mode=0o600)
