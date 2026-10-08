"""Which ROUTINE a run belonged to — a slug names one only for a while.

Archiving moves a routine's dir to `.archive/<slug>-<run_ts>` (UTC, `ids.run_ts`, the moment it
was archived) and frees the slug, which a new routine may take: on this instance `do-my-taxes`
and `steward-hub-maintainer` were each archived and created again. The usage stream keys runs by
slug alone, so two different routines' runs sit in one series under one name, and every reading
that compares a routine's runs with each other — a change found at the boundary between them, a
release judged on its runs before and after — would compare two routines.

Every slug therefore has INCARNATIONS: its archived dirs, oldest first, then the live dir. A run
belongs to the first one archived AFTER it started, else to the live one. An archive whose name
carries no stamp (archived before archives were stamped) was archived at an unknown time, so it
claims a slug's runs only where no live routine does.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ARCHIVE = ".archive"
_STAMPED = re.compile(r"^(?P<slug>[a-z0-9][a-z0-9-]*)-(?P<ts>\d{8}-\d{6})$")


@dataclass(frozen=True)
class Incarnation:
    name: str              # the slug for the live routine, the archive's dir name otherwise
    dir: Path
    archived: str | None   # the archive's run_ts; None for the live routine

    @property
    def live(self) -> bool:
        return self.archived is None


def _is_routine(d: Path) -> bool:
    return (d / "routine.yaml").is_file()


def archived_dirs(routines_home: Path) -> list[Path]:
    archive = routines_home / ARCHIVE
    return sorted(archive.iterdir()) if archive.is_dir() else []


def incarnations(routines_home: Path, slug: str,
                 archived: list[Path] | None = None) -> list[Incarnation]:
    """`slug`'s incarnations: its archived routines oldest first, then the live one.
    `archived` is `archived_dirs(routines_home)`, passed in by a caller asking for many slugs.
    """
    stamped: list[Incarnation] = []
    unstamped: list[Incarnation] = []
    for d in archived_dirs(routines_home) if archived is None else archived:
        m = _STAMPED.match(d.name)
        if m and m["slug"] == slug and _is_routine(d):
            stamped.append(Incarnation(d.name, d, m["ts"]))
        elif d.name == slug and _is_routine(d):
            unstamped.append(Incarnation(d.name, d, None))
    live = routines_home / slug
    tail = [Incarnation(slug, live, None)] if _is_routine(live) else unstamped[:1]
    return sorted(stamped, key=lambda i: str(i.archived)) + tail


def incarnation_of(incs: list[Incarnation], run_ts: str) -> Incarnation | None:
    """The incarnation a run started at `run_ts` belonged to (run_ts strings sort by time)."""
    return next((i for i in incs if i.archived is None or run_ts < i.archived), None)


def resolver(routines_home: Path) -> Callable[[dict], str]:
    """`name_of(record)`: the name of the routine a usage record's run belonged to — the live
    slug or an archived dir's name — or the record's own `routine` where no routine dir claims
    it (a conversation, a background task, a routine deleted outright). Every reader that
    compares or sums ONE routine's runs keys them by this, never by the bare slug: a slug that
    was archived and created again would otherwise hand the new routine the old one's history.
    """
    archived = archived_dirs(routines_home)
    cache: dict[str, list[Incarnation]] = {}

    def name_of(rec: dict) -> str:
        slug = str(rec.get("routine") or "")
        if slug not in cache:
            cache[slug] = incarnations(routines_home, slug, archived) if slug else []
        inc = incarnation_of(cache[slug], str(rec.get("run_id") or "").rsplit(":", 1)[-1])
        return inc.name if inc is not None else slug

    return name_of


def live_runs(routines_home: Path, records: list[dict], slug: str) -> list[dict]:
    """`records` of the routine `slug` names TODAY — none of an archived routine's before it."""
    name_of = resolver(routines_home)
    return [r for r in records if r.get("routine") == slug and name_of(r) == slug]
