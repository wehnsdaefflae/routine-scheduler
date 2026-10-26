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


def at_or_after(stamp: object, cutoff: object) -> bool:
    """Is `stamp` the same moment as `cutoff` or a later one?

    The reason this is a function and not `str(stamp) >= str(cutoff)` at the call site: text
    ordering of ISO stamps REVERSES across a DST change. Europe/Berlin fell back 03:00 CEST ->
    02:00 CET on 2026-10-25, and six audit decisions answered at `02:07..02:17+01:00` — 27 to
    38 minutes AFTER a report generated at `02:40+02:00` — every one sorted BELOW it as text.
    The surfaces reading that compare concluded "not answered yet" and re-asked all six; the
    operator answered them a second time, two of them differently (F771).

    A stamp that names no moment makes NO claim — False, never a text compare of the raw
    values. Only positive knowledge may flip a gate like this one, because the two failure
    modes are not symmetric: a corrupt marker read as "answered" hides a decision from the
    operator for good, while read as "not answered" it costs him one more click and he can see
    it. `'zzz' >= '2026-…'` is True as text, which is exactly that silent hiding.
    """
    a, b = instant(stamp), instant(cutoff)
    if a is None or b is None:
        return False
    return a >= b
