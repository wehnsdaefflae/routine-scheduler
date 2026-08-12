"""The ONE durable request-spool mechanic (F286): a `.control/<family>/<slug>/` directory
of small JSON files — producers write atomically, the daemon consumes and unlinks. Three
spools ride it: trigger events (`evt-`), one-shot requests (`req-`), pending recipe/config
edits (`pe-`).

Queue-ordered names carry the F298 contract: a second-resolution stamp + a zero-padded
nanosecond sample + random hex. The nanosecond sample makes a same-second burst sort
strictly in queue order; the hex only de-collides parallel writers. Id-addressed spools
(schedule-once's `req-<id>`) pass their own `name` instead — their consumption order is
semantic (`fire_at`), not positional.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from .ids import run_ts
from .paths import atomic_write_json


def spool_dir(routines_home: Path, family: str, slug: str) -> Path:
    return routines_home / ".control" / family / slug


def chrono_name(prefix: str) -> str:
    """A filename that sorts in QUEUE order (F298)."""
    return f"{prefix}-{run_ts()}-{time.time_ns():020d}-{uuid.uuid4().hex[:6]}.json"


def write(routines_home: Path, family: str, slug: str, record: dict,
          *, prefix: str = "", name: str = "") -> Path:
    """Write one spool entry atomically. `prefix` gets a chrono name; `name` is taken
    verbatim (id-addressed spools). Exactly one of the two must be given.
    """
    if bool(prefix) == bool(name):
        raise ValueError("pass exactly one of prefix/name")
    return atomic_write_json(
        spool_dir(routines_home, family, slug) / (name or chrono_name(prefix)), record)


def pending(routines_home: Path, family: str, slug: str, prefix: str) -> list[Path]:
    """Unconsumed entries in filename order — queue order for chrono-named spools."""
    d = spool_dir(routines_home, family, slug)
    return sorted(d.glob(f"{prefix}-*.json")) if d.is_dir() else []
