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
    now = now or datetime.now(timezone.utc).astimezone()
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


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
