"""The one-shot spool and the hook stream, COMPARED — report-only, the spool still served.

`scheduleproj` put every armed one-shot on the stream as a `schedule` hook, and
`readmodels/hook_activity.next_fires` folds those rows into "what fires next" for the whole
instance. The week strip (`web/api_schedule.schedule_week`) still answers the same question from
the SPOOL, per routine, and the D152 campaign's remaining step is to switch that read to the
fold. The order attached to that step is what this module is: the switch is staged behind *a
week of the parity check running in report-only mode*, and the check did not exist.

**Report-only means exactly one thing: what is SERVED does not change.** `week_one_shots`
returns the spool's own instants, scanned the way the route scanned them itself, and the stream
is read only to be compared. Nothing here can make the strip show a fire the spool does not
hold, or hide one it does — which is the property that makes a week of measurement worth
anything: if the fold is wrong, the operator's dashboard is still right while we find out.

**The comparison is on the hook ID, never on the instants.** The id is the spool request's own
id (`scheduleproj.hook_of_request` carries it through, and `request_of_hook` inverts it), so two
readings disagreeing is a statement about a REQUEST — nameable, greppable in both stores. A
comparison of instant lists would say "the readings differ" and leave the repair to guesswork,
and it would call two one-shots armed for the same minute one row.

**Both directions are a shortfall, and they are different repairs.**

* `only_in_spool` — the stream LOST a request: `record_armed` is best-effort by design (a
  diagnostic may never stop a one-shot being armed), so a failed write leaves the spool holding
  a fire the fold cannot see. After the switch, that fire disappears from the strip.
* `only_in_stream` — the stream kept a request the spool no longer holds: the arming row is
  there with no terminal row after it, because a `fired` · `cancelled` · `skipped` write failed
  the same best-effort way, or the request file was removed by something that is not
  `schedule_once.cancel`. After the switch, the strip draws a fire that cannot happen.

Firing is untouched and stays untouched: the daemon consumes the request file, and that
consumption is the non-repeating guarantee.

The event is filed under the name the design order gives it, `hook_stream_diverged`, and it is
repeat-governed (`health_cooldown`) because this check runs on a POLLED surface — the dashboard
opens as often as the operator opens it, and a standing disagreement is one fact, not one fact
per page load.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from .. import schedule_once
from ..health_events import log_health_event
from ..readmodels.stamps import instant

log = logging.getLogger(__name__)

#: The event name the design order names. One name, both directions: the detail says which, and
#: a reader filtering the health stream wants every disagreement between these two stores.
DIVERGED_EVENT = "hook_stream_diverged"


def _in_window(raw: str, start: datetime, end: datetime) -> str:
    """`raw` as a UTC ISO instant when it falls inside [start, end), else "".

    Parsed through `readmodels.stamps.instant` — the one place two moments are compared — and
    normalised to UTC, because the spool stores whatever offset the arming side wrote.
    """
    when = instant(raw)
    if when is None:
        return ""
    when = when.astimezone(UTC)
    return when.isoformat() if start <= when < end else ""


def spool_fires(routines_home: Path, slug: str, start: datetime,
                end: datetime) -> dict[str, str]:
    """`{request id: ISO instant}` for `slug`'s armed one-shots inside [start, end).

    This is the reading the week strip has always served; it lives here now because this module
    owns the comparison, and a route that serves one of two readings should not also be the
    place either reading is implemented.
    """
    out: dict[str, str] = {}
    for path in schedule_once.pending_requests(routines_home, slug):
        record = schedule_once.read_request(path)
        if not record.get("active", True):
            continue
        when = _in_window(str(record.get("fire_at") or ""), start, end)
        if when:
            out[str(record.get("id") or "")] = when
    return out


def stream_fires(server, start: datetime, end: datetime) -> dict[str, dict[str, str]]:
    """`{slug: {hook id: ISO instant}}` — the stream's pending one-shots inside [start, end).

    Read through `readmodels.hook_activity.next_fires`, never through a second fold of the
    stream: the lifecycle has to be folded away BEFORE the window is applied, or every one-shot
    that ever fired reads as pending, and that fold already exists with its own tests. The
    horizon asked for is the window's own far edge, and `next_fires` hands back the overdue
    pending rows as well, so the filter here is the only thing that bounds the result.

    Imported where it is used rather than at module scope: `hook_activity` imports this package,
    and the comparison is a leaf nothing in `hooks` imports back.
    """
    from ..readmodels import hook_activity

    now = datetime.now(UTC)
    horizon_h = max(1, int((end - now).total_seconds() // 3600) + 1)
    out: dict[str, dict[str, str]] = {}
    for row in hook_activity.next_fires(server, now=now, horizon_h=horizon_h):
        when = _in_window(str(row.get("fires_at") or ""), start, end)
        if when:
            out.setdefault(str(row.get("routine") or ""), {})[str(row.get("hook") or "")] = when
    return out


def compare(spool: dict[str, dict[str, str]],
            stream: dict[str, dict[str, str]]) -> tuple[list[str], list[str]]:
    """`(only_in_spool, only_in_stream)` as `slug/id` strings, sorted — the whole verdict.

    Pure, so the property under measurement is testable without a spool, a stream or a clock.
    """
    def flat(rows: dict[str, dict[str, str]]) -> set[str]:
        return {f"{slug}/{hook_id}" for slug, ids in rows.items() for hook_id in ids}

    only_spool = flat(spool) - flat(stream)
    only_stream = flat(stream) - flat(spool)
    return sorted(only_spool), sorted(only_stream)


def _file_divergence(routines_home: Path, only_spool: list[str],
                     only_stream: list[str]) -> None:
    """File the one event, naming the ids. Best-effort: a diagnostic may never be the reason a
    page fails to render, so every failure here is swallowed down to a log line.
    """
    parts = []
    if only_spool:
        parts.append(f"{len(only_spool)} armed one-shot(s) the stream's fold cannot see "
                     f"({', '.join(only_spool[:12])}) — `scheduleproj.record_armed` is "
                     f"best-effort, so a failed write leaves the spool ahead")
    if only_stream:
        parts.append(f"{len(only_stream)} pending row(s) in the stream the spool no longer "
                     f"holds ({', '.join(only_stream[:12])}) — a `fired`/`cancelled`/`skipped` "
                     f"row never landed, or a request file was removed outside "
                     f"`schedule_once.cancel`")
    detail = ("the week strip's one-shots: the spool and the hook stream disagree. "
              + "; ".join(parts)
              + ". The SPOOL is what is served and what fires, so nothing is wrong on the "
                "dashboard; this is the parity check the D152 read switch is staged behind")
    try:
        log_health_event(routines_home, DIVERGED_EVENT, routine="", run_id="", detail=detail,
                         only_in_spool=len(only_spool), only_in_stream=len(only_stream))
    except Exception:
        log.warning("schedule-parity: the divergence event could not be filed")
    log.warning("schedule-parity: %s", detail)


def week_one_shots(server, slugs: list[str], start: datetime,
                   end: datetime) -> dict[str, list[str]]:
    """`{slug: [ISO instant…]}` from the SPOOL for every slug in `slugs` — what the strip serves
    — with the stream's fold compared against it and a disagreement filed.

    One call per week-strip read: the spool is scanned per slug exactly as before, and the
    stream is folded once for the whole instance (memoized on the file's fingerprint), so the
    comparison costs one parse per append rather than one per routine.

    The comparison can never change the answer. A failure inside it changes the answer even
    less: it is swallowed, because a diagnostic that takes the dashboard down with it is worse
    than no diagnostic.
    """
    home = server.routines_home
    spool = {slug: spool_fires(home, slug, start, end) for slug in slugs}
    try:
        only_spool, only_stream = compare(spool, stream_fires(server, start, end))
        if only_spool or only_stream:
            _file_divergence(home, only_spool, only_stream)
    except Exception:
        log.warning("schedule-parity: the comparison could not be made", exc_info=True)
    return {slug: sorted(ids.values()) for slug, ids in spool.items()}
