"""Canonical path helpers and atomic file IO.

Every cross-process file (status.json, inbox messages, answers, control.json) goes through
atomic_write so a reader never sees a partial file.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path


def expand(p: str | Path) -> Path:
    return Path(os.path.expandvars(str(p))).expanduser()


def config_file() -> Path:
    env = os.environ.get("RSCHED_CONFIG")
    if env:
        return expand(env)
    return expand("~/.config/routine-scheduler/config.yaml")


def atomic_write(path: str | Path, data: str | bytes) -> Path:
    """Write via tmp file + rename in the target directory (same filesystem)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode, encoding = ("wb", None) if isinstance(data, bytes) else ("w", "utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, mode, encoding=encoding) as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        Path(tmp).replace(path)
    except BaseException:
        try:
            Path(tmp).unlink()
        except OSError:
            pass
        raise
    return path


def atomic_write_json(path: str | Path, obj: object) -> Path:
    return atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def read_json(path: str | Path, default: object = None) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def within(root: Path, candidate: Path) -> bool:
    """True if candidate (resolved) lies inside root (resolved)."""
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_rel(base: Path, rel: str, extra_roots: Sequence[Path] = ()) -> Path:
    """Resolve a path from an action: relative → under base; absolute → must fall inside
    base or one of extra_roots. Raises PermissionError otherwise.
    """
    p = expand(rel)
    candidate = p if p.is_absolute() else (base / p)
    roots = [base, *extra_roots]
    for root in roots:
        if within(root, candidate):
            return candidate.resolve()
    allowed = ", ".join(str(r) for r in roots)
    raise PermissionError(f"path {rel!r} is outside the allowed roots ({allowed})")
