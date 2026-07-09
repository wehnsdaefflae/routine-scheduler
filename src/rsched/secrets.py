"""Central secrets store — one KEY=VALUE file next to config.yaml that the engine injects into
every util subprocess, the claude-cli transport, and endpoint key lookup at RUN time (utils read
env-first by convention). One place in the UI to set ANY credential — including ones a generated
util needs — with no per-secret wiring and no restart. Values are written from the UI, never
echoed back.
"""
from __future__ import annotations

import re

from .paths import atomic_write, config_file

SECRETS_FILE = "secrets.env"
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")     # a valid environment variable name


def secrets_path():
    return config_file().parent / SECRETS_FILE


def load_secrets() -> dict[str, str]:
    """Parse the store → {KEY: VALUE}; missing file → {}. Tolerant of comments/blank lines."""
    path = secrets_path()
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                k = k.strip()
                if KEY_RE.match(k):
                    out[k] = v.strip().strip('"').strip("'")
    return out


def secret_keys() -> list[str]:
    """Names only — never values (what the UI is allowed to see)."""
    return sorted(load_secrets().keys())


def set_secret(key: str, value: str) -> None:
    if not KEY_RE.match(key):
        raise ValueError(f"{key!r} is not a valid environment variable name")
    d = load_secrets()
    d[key] = value
    _write(d)


def delete_secret(key: str) -> bool:
    d = load_secrets()
    if key not in d:
        return False
    del d[key]
    _write(d)
    return True


def _write(d: dict[str, str]) -> None:
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, "".join(f"{k}={v}\n" for k, v in d.items()))
    try:
        path.chmod(0o600)
    except OSError:
        pass
