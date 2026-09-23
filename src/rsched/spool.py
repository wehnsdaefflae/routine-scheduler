"""The ONE durable request-spool mechanic (F286): a `.control/<family>/<slug>/` directory
of small JSON files — producers write atomically, the daemon consumes and unlinks. Three
spools ride it: trigger events (`evt-`), one-shot requests (`req-`), pending recipe/config
edits (`pe-`).

A rider's own module keeps only its VOCABULARY — its family name, its prefix and what its
records mean. Everything mechanical about the spool is here: writing an entry, listing one
slug's entries, listing the slugs that have any, reading and writing the daemon's fire ledger,
and dropping entries it can never act on. The two riders that have a ledger had `read_state` /
`write_state` byte-identical and `slugs_with_*` identical but for the prefix, so a fix to how a
spool is listed landed in one of them.

Consumption is the daemon's: it fires the entry and unlinks it, or — when it can never act on
one, because the routine is gone, off or retired and the trigger was deleted — DROPS it with a
line saying so (`drop`). An entry that vanishes silently reads exactly like one that was handled.

Queue-ordered names carry the F298 contract: a second-resolution stamp + a zero-padded
nanosecond sample + random hex. The nanosecond sample makes a same-second burst sort
strictly in queue order; the hex only de-collides parallel writers. Id-addressed spools
(schedule-once's `req-<id>`) pass their own `name` instead — their consumption order is
semantic (`fire_at`), not positional.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from .ids import run_ts
from .paths import atomic_write_json, read_json

log = logging.getLogger("rsched.spool")


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


def slugs_with(routines_home: Path, family: str, prefix: str) -> list[str]:
    """Every slug with at least one unconsumed entry in this family, in name order — what a
    daemon manager iterates on its tick.
    """
    root = routines_home / ".control" / family
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and any(d.glob(f"{prefix}-*.json")))


def read_state(routines_home: Path, family: str, slug: str) -> dict:
    """The daemon-maintained fire ledger beside one slug's entries ({} when there is none).

    Derived state, never config: the daemon writes it, the routine page renders it, and a
    hand-broken or missing file reads as "nothing has fired yet" rather than failing a tick.
    """
    st = read_json(spool_dir(routines_home, family, slug) / "state.json")
    return st if isinstance(st, dict) else {}


def write_state(routines_home: Path, family: str, slug: str, state: dict) -> None:
    atomic_write_json(spool_dir(routines_home, family, slug) / "state.json", state)


def drop(paths: list[Path], *, what: str, slug: str, reason: str) -> None:
    """Consume entries UNFIRED, and say so in the daemon log.

    `what` names the family in the operator's own words ("trigger events", "schedule-once
    requests") — the one thing that differed between the two copies this replaces.
    """
    for p in paths:
        p.unlink(missing_ok=True)
    log.warning("%s dropped routine=%s count=%d (%s)", what, slug, len(paths), reason)
