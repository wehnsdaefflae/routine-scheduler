"""Run/question/slug identifier generation and parsing.

run_id format: "<slug>:<YYYYMMDD-HHMMSS>" — the timestamp part is also the run directory name.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
RUN_TS_RE = re.compile(r"^\d{8}-\d{6}$")


def is_slug(s: str) -> bool:
    return bool(SLUG_RE.match(s))


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s or "routine"


def run_ts(now: datetime | None = None) -> str:
    """A run's timestamp id, ALWAYS in UTC. Run-ts is a bare `YYYYMMDD-HHMMSS` string with
    no offset, so generating AND reading it both in UTC is what keeps run-dir names, ordering
    and the UI's local-time rendering consistent regardless of the SERVER's timezone — a UTC
    host running Europe/Berlin routines otherwise skews every run-ts-derived time by the
    offset. The web UI converts the UTC stamp to the viewer's local time for display."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc)
    return now.strftime("%Y%m%d-%H%M%S")


def run_id(slug: str, ts: str) -> str:
    return f"{slug}:{ts}"


def parse_run_id(rid: str) -> tuple[str, str]:
    """Return (slug, ts). Raises ValueError on malformed ids."""
    slug, sep, ts = rid.partition(":")
    if not sep or not is_slug(slug) or not RUN_TS_RE.match(ts):
        raise ValueError(f"malformed run id: {rid!r}")
    return slug, ts


def question_id(ts: str, n: int) -> str:
    return f"q-{ts}-{n}"


def background_task_id(owner_slug: str) -> str:
    """A unique slug for a detached background task, tagged with its owner conversation for
    readability. Uniqueness (a random suffix) is load-bearing: the DetachedManager keys task
    dirs by this id, so a collision would silently drop a task."""
    import uuid

    base = owner_slug if is_slug(owner_slug) else slugify(owner_slug)
    return f"bg-{base}-{uuid.uuid4().hex[:8]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
