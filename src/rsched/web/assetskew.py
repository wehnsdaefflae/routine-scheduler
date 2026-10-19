"""ASSET/PROCESS SKEW — is the checkout serving `/static` still the build this process booted
with? (F696)

`app.mount("/static", StaticFiles(directory=STATIC_DIR))` serves the console's javascript and CSS
LIVE FROM DISK, while every Python object in this process stays at the build it imported at boot.
A release that lands mid-day therefore puts NEW assets in front of an OLD process, with nothing
versioning the one against the other — and FastAPI/pydantic drops an unknown multipart field
SILENTLY (no 422), so the browser's new form fields were accepted-and-discarded.

That is the operator's R2546: 'i set up everything on the conversation creation page and yet once
the conversation was started, most of the settings were gone and i had to do it again.' Every
layer was right at HEAD — the composer sent all 17 fields and `ComposerForm` declared all 17 —
but the daemon serving that console had booted at 07:34:53, both halves landed together at 08:47
in commit 392379ec, and the conversation was created at 13:42:08, inside the window.

WHY THE COMPARISON LIVES HERE, ON THE SERVER. Neither half of the browser's side can see it.
Nothing in an asset says which build it came from, so the console cannot name the build it is
running; and the process never restarted during the F696 window, so `/api/status`'s `build` never
changed either — a console comparing successive readings of it would have seen nothing all day.
The server is the only party holding BOTH facts: the stamp it read at boot, and the stamp the
checkout is at now. So it compares them and publishes the verdict, and the console renders it
(`static/components/notices.js`).

Stamped asset URLs were the other candidate and were rejected for this tree: the console is
plain ES modules importing each other by absolute path (`import { api } from "/static/api.js"`,
146 files), so a query string on the entry point cannot reach the imports below it. Pinning
assets to the build would mean rewriting every import specifier at serve time or introducing a
bundler — a build step this repo does not have — to tell the operator something one boolean on a
status payload he already polls can tell him.

Sibling: `rsched.engine.buildskew` (F677) is the same release landing mid-RUN, against held
PYTHON objects reached through lazy imports. This module is the ASSET half, which was not
versioned against the process at all.
"""

from __future__ import annotations

import time
from pathlib import Path

#: How long one live stamp read is reused. `/api/status` is polled by every open tab every 30 s
#: and by Settings' restart watch every 2 s, so an uncached `git log` per request would add a
#: subprocess to the console's quietest loop. Ten seconds is far inside the horizon that matters
#: (a human noticing a banner) and makes the stamp at most one read per status burst.
STAMP_TTL_S = 10.0

#: (read at, stamp) of the last live read — the TTL cache `disk_stamp` keeps. A one-slot LIST
#: rather than a rebound module global: the value is mutated in place, so no function needs a
#: `global` statement (ruff PLW0603) and there is exactly one cache for the process.
_cached: list[tuple[float, str]] = []


def read_stamp(repo: Path | None) -> str:
    """Short commit + date of the checkout at `repo` ('46e48e3 2026-07-13'), '' if unknown.

    Uncached and best-effort: a deployment that is not a git checkout, or one whose git is
    unusable, has no stamp — and no stamp must never read as a skew (see `stamps_skewed`).
    """
    if not repo:
        return ""
    try:
        from ..libgit import git

        out = git(repo, "log", "-1", "--format=%h %cs")
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def disk_stamp(repo: Path | None) -> str:
    """`read_stamp` for the LIVE checkout, at most once per `STAMP_TTL_S`."""
    now = time.monotonic()
    if _cached and now - _cached[0][0] < STAMP_TTL_S:
        return _cached[0][1]
    stamp = read_stamp(repo)
    _cached[:] = [(now, stamp)]
    return stamp


def forget_disk_stamp() -> None:
    """Drop the cached live stamp — the next read goes to disk.

    For tests, and for any caller that wants the current truth rather than a reading up to
    `STAMP_TTL_S` old.
    """
    _cached.clear()


def stamps_skewed(booted: str, disk: str) -> bool:
    """Has the checkout moved under the process? Only ever true when BOTH stamps are known.

    An unknown stamp is not evidence of anything, and a banner nobody can clear is worse than no
    banner: a deployment without git would otherwise accuse itself of skew forever.
    """
    return bool(booted) and bool(disk) and booted != disk


def status_fields(repo: Path | None, booted: str) -> dict:
    """The two keys `/api/status` publishes for the console: the live stamp, and the verdict."""
    disk = disk_stamp(repo)
    return {"disk_build": disk, "build_skew": stamps_skewed(booted, disk)}
