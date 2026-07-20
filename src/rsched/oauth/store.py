"""Connection store — the daemon/web process is the SINGLE writer of the OAuth tokens a routine
run reads. One `connections.json` next to `config.yaml`, keyed `"<provider>:<account>"`, written
atomically at mode 0600. Values are write-only through the API (`list_connections` returns
metadata, never tokens), mirroring the Secrets store. A run is a pure READER: `tokens_for_routine`
turns a routine's `connections:` bindings into the env vars its utils receive.
"""
from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass, field

from ..paths import atomic_write_json, config_file, read_json

CONNECTIONS_FILE = "connections.json"
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")   # provider id + account label shape
# The daemon/web process is the single WRITER, but two coroutines/threads in it can mutate the
# store (the web connect callback and the refresh manager's worker thread). Serialize the
# read-modify-write so neither clobbers the other; cross-process readers rely on atomic_write.
_WRITE_LOCK = threading.Lock()


def connections_path():
    return config_file().parent / CONNECTIONS_FILE


@dataclass
class Connection:
    provider: str
    account: str
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0                    # unix seconds; 0 = non-expiring (e.g. Notion)
    scopes: list[str] = field(default_factory=list)
    obtained_at: float = 0.0
    label: str = ""                            # provider-supplied display (e.g. Notion workspace)
    needs_reauth: bool = False

    def key(self) -> str:
        return f"{self.provider}:{self.account}"

    def public(self) -> dict:
        """Metadata safe to return over the API — NEVER the tokens."""
        return {
            "provider": self.provider,
            "account": self.account,
            "label": self.label,
            "scopes": list(self.scopes),
            "expires_at": self.expires_at,
            "obtained_at": self.obtained_at,
            "needs_reauth": self.needs_reauth,
            "has_refresh": bool(self.refresh_token),
        }


_FIELDS = set(Connection.__dataclass_fields__)


def _conn_key(provider: str, account: str) -> str:
    return f"{provider}:{account}"


def load_connections() -> dict[str, Connection]:
    """All connections keyed `<provider>:<account>`; missing/corrupt file → {}. Unknown record
    keys are dropped (forward-compatible), malformed records skipped.
    """
    raw = read_json(connections_path(), default={})
    out: dict[str, Connection] = {}
    if isinstance(raw, dict):
        for key, rec in raw.items():
            if not isinstance(rec, dict):
                continue
            try:
                out[key] = Connection(**{str(k): rec[k] for k in rec if k in _FIELDS})
            except TypeError:
                continue          # a record missing provider/account — drop it
    return out


def get_connection(provider: str, account: str) -> Connection | None:
    return load_connections().get(_conn_key(provider, account))


def list_connections() -> list[dict]:
    """Metadata only — never token values (what the UI is allowed to see)."""
    return [c.public() for c in sorted(load_connections().values(), key=lambda c: c.key())]


def set_connection(conn: Connection) -> None:
    if not (_KEY_RE.match(conn.provider) and _KEY_RE.match(conn.account)):
        raise ValueError(f"invalid provider/account: {conn.provider!r}/{conn.account!r}")
    with _WRITE_LOCK:
        conns = load_connections()
        conns[conn.key()] = conn
        _write(conns)


def delete_connection(provider: str, account: str) -> bool:
    with _WRITE_LOCK:
        conns = load_connections()
        if _conn_key(provider, account) not in conns:
            return False
        del conns[_conn_key(provider, account)]
        _write(conns)
    return True


def tokens_for_routine(connections: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Resolve a routine's `connections:` map ({provider: account}) into the env vars its utils
    receive — `{"<PROVIDER>_ACCESS_TOKEN": access_token}`. Returns (env, warnings); a binding
    whose connection is missing, empty, or flagged needs_reauth is skipped with a warning so the
    caller can surface it (the util then simply won't see that token).
    """
    env: dict[str, str] = {}
    warnings: list[str] = []
    store = load_connections()
    for provider, account in (connections or {}).items():
        conn = store.get(_conn_key(provider, str(account)))
        if conn is None:
            warnings.append(f"{provider}:{account} is not connected")
        elif conn.needs_reauth or not conn.access_token:
            warnings.append(f"{provider}:{account} needs re-authorization")
        else:
            env[f"{provider.upper()}_ACCESS_TOKEN"] = conn.access_token
    return env, warnings


def _write(conns: dict[str, Connection]) -> None:
    path = connections_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {k: asdict(v) for k, v in conns.items()})
    try:
        path.chmod(0o600)
    except OSError:
        pass
