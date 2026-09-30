"""What every gate check shares, jail side: the UnknownError signal, the baseline, secrets, JSON
paths. Standard library only — this file runs inside the gate's Landlock jail.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from pathlib import Path

NET_TIMEOUT_S = 10.0


class UnknownError(Exception):
    """The check could not establish its answer — which a gate must read as work."""


def now(ctx: dict) -> _dt.datetime:
    return _dt.datetime.fromisoformat(str(ctx["now"]))


def since(ctx: dict) -> _dt.datetime:
    """When the last run that finished ok started — what "new" and "changed" are measured from."""
    started = (ctx.get("last_ok") or {}).get("started")
    if not started:
        raise UnknownError("no earlier run finished ok, so there is nothing to compare against")
    try:
        return _dt.datetime.fromisoformat(str(started))
    except ValueError as exc:
        raise UnknownError(f"the last ok run's start time {started!r} does not parse") from exc


def baseline(ctx: dict, cid: str) -> str:
    fp = ((ctx.get("last_ok") or {}).get("fingerprints") or {}).get(cid)
    if not fp:
        raise UnknownError("the last ok run recorded no fingerprint for this check yet")
    return str(fp)


def secret(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise UnknownError(f"secret {name} was not provided to the gate")
    return value


def web_login(name: str, source: str) -> dict:
    """One entry of a web-auth sources secret: `{host, user, pass, port?}`."""
    try:
        sources = json.loads(secret(name))
    except ValueError as exc:
        raise UnknownError(f"{name} is not a JSON map of web logins") from exc
    entry = sources.get(source) if isinstance(sources, dict) else None
    if not isinstance(entry, dict) or not entry.get("user"):
        raise UnknownError(f"{name} has no usable {source!r} entry")
    return entry


def digest(value: object) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(
        value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def dig(value: object, path: str) -> object:
    """Walk a dotted path; `*` maps the rest of the path over a list."""
    parts = [p for p in path.split(".") if p]
    for i, part in enumerate(parts):
        if part == "*":
            if not isinstance(value, list):
                raise UnknownError(f"{'.'.join(parts[:i]) or 'the value'} is not a list")
            rest = ".".join(parts[i + 1:])
            return [dig(v, rest) if rest else v for v in value]
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise UnknownError(f"no {path!r} in it")
    return value


def read_json_file(ctx: dict, rel: str) -> object:
    path = Path(ctx["routine_dir"]) / rel
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as exc:
        raise UnknownError(f"could not read {rel}: {exc}") from exc
