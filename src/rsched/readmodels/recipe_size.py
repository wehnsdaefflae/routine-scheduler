"""Per-routine recipe length for the Stats tab (F371 — user order 2026-08-21: the
recipe's size as its own differently-colored bar chart with a trend, beside token usage).

The recipe set is recipes.RECIPE_PATHSPECS (main.md + stages/ + tuning.yaml — the same
files the write gates protect and recipes.py versions). Current length is a live worktree
read in bytes (markdown prose: bytes ≈ chars); the trend baseline is the recipe blob
sizes at the last commit at least TREND_DAYS old, read with `git ls-tree -r -l` so the
whole baseline costs TWO git calls per routine. Routines home only — conversations are
unversioned and recipe-less. Best-effort like every readmodel: a dir without git history
(or a younger-than-baseline repo) reports `chars_baseline: null` and the view hides the
trend chip; git failures never break the stats call.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .. import libgit
from ..config import ServerConfig
from ..recipes import RECIPE_PATHSPECS

#: The trend lookback: current length vs the recipe as committed this many days ago.
TREND_DAYS = 30


def _current_chars(routine_dir: Path) -> int:
    total = 0
    for spec in RECIPE_PATHSPECS:
        p = routine_dir / spec
        if p.is_file():
            total += p.stat().st_size
        elif p.is_dir():
            total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return total


def _baseline_chars(routine_dir: Path) -> int | None:
    """Recipe bytes at the newest commit ≥ TREND_DAYS old, or None (no git / repo
    younger than the lookback / git failure).
    """
    if not (routine_dir / ".git").is_dir():
        return None
    try:
        r = libgit.git(routine_dir, "rev-list", "-1",
                       f"--before={TREND_DAYS} days ago", "HEAD")
        commit = r.stdout.strip()
        if r.returncode != 0 or not commit:
            return None
        r = libgit.git(routine_dir, "ls-tree", "-r", "-l", commit,
                       "--", *(spec for spec in RECIPE_PATHSPECS))
        if r.returncode != 0:
            return None
    except (OSError, subprocess.TimeoutExpired):
        return None
    total = 0
    for line in r.stdout.splitlines():
        # "<mode> blob <hash> <size>\t<path>" — -l appends the blob size column
        meta = line.split("\t", 1)[0].split()
        if len(meta) == 4 and meta[1] == "blob" and meta[3].isdigit():
            total += int(meta[3])
    return total


def recipe_sizes(server: ServerConfig) -> dict:
    """{"trend_days": N, "by_routine": {slug: {"chars", "chars_baseline"}}} over every
    routine dir with a routine.yaml, including zero-length recipes (an empty bar is a
    finding the eye should get to make).
    """
    out: dict[str, dict] = {}
    home = server.routines_home
    if home.is_dir():
        for d in sorted(home.iterdir()):
            if d.name.startswith(".") or not (d / "routine.yaml").is_file():
                continue
            out[d.name] = {"chars": _current_chars(d),
                           "chars_baseline": _baseline_chars(d)}
    return {"trend_days": TREND_DAYS, "by_routine": out}
