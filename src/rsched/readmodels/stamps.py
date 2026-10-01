"""Stamps as instants — the one way a read model compares two moments.

`ids.now_iso` writes the HOST's local time with its offset (`…T08:00:00+02:00` on the Berlin
host), git's `%cI` writes the committer's offset, and a run-ts is UTC with no offset at all. No
two of those can be compared as text: a string compare against a UTC cutoff moved the health
window's edge by the host's offset, stamps either side of a DST change ordered backwards, and a
compact run-ts sorted above every ISO stamp. Every comparison here goes through `instant`.

A run-ts is NOT handed to it: `fromisoformat` accepts `20261001-080000` as a basic-format
NAIVE time, which `instant` would then read in the host's zone although every run-ts is UTC.
`registry.parse_run_ts` is that form's reader.
"""

from __future__ import annotations

from datetime import datetime


def instant(raw: object) -> datetime | None:
    """`raw` as an aware datetime, or None when it names no moment. A naive stamp is read in
    the host's zone — the zone `now_iso` writes in.
    """
    try:
        when = datetime.fromisoformat(str(raw or ""))
    except ValueError:
        return None
    return when if when.tzinfo else when.astimezone()
