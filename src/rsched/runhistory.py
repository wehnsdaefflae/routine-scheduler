"""The instance's own HISTORY, read back from git to rebuild what a past run's record never
carried — MIGRATION(expires=2026-11-30): only the one-shot run-record backfill
(`migrate_runrecords.py`) reads it, and it goes with that migration.

Every reading answers one question: AS OF INSTANT T, what was …

- the ENGINE release — what `main` held at T, read from `main`'s REFLOG: when the branch MOVED,
  not when a commit was written (a release written on a branch and fast-forwarded hours later
  reached the instance at the merge). The daemon picks new code up only at its next restart, so
  a run that started between a merge and that restart is attributed to the newer release — the
  one error this cannot remove, because nothing records the restart. Without a reflog (a fresh
  clone) the version-bump commits' own dates stand in.
- a ROUTINE's `routine.yaml` and `tuning.yaml` — as committed in the routine's repo (a web edit
  commits at once, a run's own change at its end) — and its RECIPE version (`recipes`).
- a library RULE's text — as committed in the library repo, hashed the way a live run hashes it.

Each is a `Timeline`: (instant, value) points in time order; `at(t)` is the value of the last
point not after `t`, or None before the first — "not known", never a guess.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import libgit
from .recipes import RECIPE_PATHSPECS

_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)
_REFLOG_DATE_RE = re.compile(r"@\{([^}]+)\}")
VERSION_FILE = "src/rsched/__init__.py"


@dataclass
class Timeline:
    points: list[tuple[datetime, object]] = field(default_factory=list)

    def at(self, when: datetime) -> object | None:
        i = bisect.bisect_right([t for t, _v in self.points], when)
        return self.points[i - 1][1] if i else None


def _instant(text: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return stamp if stamp.tzinfo else None


def _show(repo: Path, commit: str, path: str) -> str | None:
    """A file's text at a commit, or None when the commit does not carry it."""
    r = libgit.git(repo, "show", f"{commit}:{path}")
    return r.stdout if r.returncode == 0 else None


def _log(repo: Path, *pathspecs: str) -> list[tuple[datetime, str]]:
    """`(commit time, commit)` of every commit touching `pathspecs`, oldest first."""
    r = libgit.git(repo, "log", "--format=%H%x09%cI", "--", *pathspecs)
    out = []
    for line in r.stdout.splitlines() if r.returncode == 0 else ():
        commit, _, stamp = line.partition("\t")
        if (when := _instant(stamp)) is not None:
            out.append((when, commit))
    return sorted(out)


def engine_timeline(repo: Path) -> Timeline:
    """The release `main` held over time (module docstring)."""
    r = libgit.git(repo, "reflog", "show", "--date=iso-strict", "--format=%H%x09%gd", "main")
    moves = []
    for line in r.stdout.splitlines() if r.returncode == 0 else ():
        commit, _, ref = line.partition("\t")
        m = _REFLOG_DATE_RE.search(ref)
        if m and (when := _instant(m[1])) is not None:
            moves.append((when, commit))
    moves = sorted(moves) or _log(repo, VERSION_FILE)
    versions: dict[str, str] = {}
    points: list[tuple[datetime, object]] = []
    for when, commit in moves:
        if commit not in versions:
            text = _show(repo, commit, VERSION_FILE) or ""
            versions[commit] = m[1] if (m := _VERSION_RE.search(text)) else ""
        if versions[commit]:
            points.append((when, versions[commit]))
    return Timeline(points)


@dataclass(frozen=True)
class RoutineState:
    """A routine's two config files as committed at some instant."""

    routine_yaml: str
    tuning_yaml: str | None


def routine_timeline(routine_dir: Path) -> Timeline:
    points: list[tuple[datetime, object]] = []
    for when, commit in _log(routine_dir, "routine.yaml", "tuning.yaml"):
        text = _show(routine_dir, commit, "routine.yaml")
        if text is not None:
            points.append((when, RoutineState(text, _show(routine_dir, commit, "tuning.yaml"))))
    return Timeline(points)


def recipe_timeline(routine_dir: Path) -> Timeline:
    """The routine's recipe version (the full commit hash a live run stamps) over time."""
    return Timeline([(when, commit) for when, commit in _log(routine_dir, *RECIPE_PATHSPECS)])


def rule_timeline(library: Path, slug: str, hasher) -> Timeline:
    """`hasher(text)` of the library rule `slug` over time; "" from a commit that deleted it,
    exactly as a live run hashes a rule file that is not there.
    """
    points: list[tuple[datetime, object]] = []
    for when, commit in _log(library, f"rules/{slug}.md"):
        text = _show(library, commit, f"rules/{slug}.md")
        points.append((when, hasher(text) if text is not None else ""))
    return Timeline(points)
