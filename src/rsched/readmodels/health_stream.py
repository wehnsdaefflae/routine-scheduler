"""The ONE parser of the health stream (`.control/health-events.jsonl`), and the two folds
the console reads it through.

The stream had fourteen writers and no reader inside the product: the nightly audit recipe
opens the file over ssh; nothing a person clicks in the console showed a line of it.
`BLOCKED_EVENTS` are the ones that say the same thing — something DUE did not happen — and
they are precisely the ones nothing else surfaces: a refused fire leaves no run, a stopped
chain leaves no member run, a capped trigger leaves a dark routine, a commit that did not land
leaves files no history holds. A failure has a run page; a fire that never happened has
nothing at all, which is how F316's week of missed lane fires passed with zero signal.

The unit of due work is not always a FIRE. `wrong_turn_detected` (R2652) is a TURN that came
back well-formed and wrong — paid for at full prompt price, its work re-driven or lost — and it
belongs here for the reason `finish_gate_failed` does: the run reads as ordinary on every other
surface, because a rejected turn recovers and the run finishes `ok`, so what did not happen is
invisible to any run page. `model_chain_exhausted` (F716) is the turn that could not be taken at
all — every model in the role's chain failed hard or was cooling. That one DOES leave a failed
run page, so it looks like the exception; it is not, because when the cause is the host's shared
proxy the failure is never one routine's, and a per-routine run page is exactly the surface that
cannot say "the fleet has no model". Eight such events in one day (2026-10-19, all claude-proxy
503 `auth_unavailable`) reached no surface a person clicks.

`blocked_fleet` is that fold: one row per (event, subject) with a count and the newest
detail, so "what is the fleet not doing" is answered by a fetch instead of by ssh.
`budget_endings` is the per-routine one — which of a routine's partial finishes a BUDGET
forced, the distinction the usage stream cannot carry because both land there as `partial`.

A fold answers for a WINDOW, and a window is a question about the clock as much as about the
file. So the half that depends on the file alone — the parse, and each folded event's stamp
read as an instant — is memoized on the stream's stat fingerprint with single-flight misses:
this rides a bus-event path (the dashboard refetches on `run_*`), and an un-memoized 280 KB
parse per request is what starved the daemon behind `/api/items` and `/api/questions`. The
window itself is cut at every call, over that shared list. A fold memoized on the file alone
kept answering for the moment it was first computed until the next append, so on a quiet
instance a week-old refused fire still read as this week's.

Stamps are compared as INSTANTS (`stamps.instant`), never as strings: `ids.now_iso` writes the
host's local time with its offset, so a string compare against a UTC cutoff moved the window's
edge by the host's offset, and two stamps either side of a DST change compared in the wrong
order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from .. import runcorrection
from ..config import ServerConfig
from ..health_events import HEALTH_EVENTS_FILE
from ..paths import read_jsonl
from . import memo
from .stamps import instant

#: The correction event both folds resolve against (`rsched/runcorrection.py`, F637/F703). It
#: is NOT blocked work and NOT an ending — it is what says an earlier ending of the same run
#: was superseded by a resumed leg that finished ok.
CORRECTION = runcorrection.EVENT

#: The events that mean WORK THAT WAS DUE DID NOT HAPPEN, each with the one line the console
#: labels the row with. The vocabulary is `health_events.py`'s header enum — a name added
#: there that belongs in this set must be added here too, or the console stays blind to it
#: exactly the way it was blind to all six.
BLOCKED_EVENTS: dict[str, str] = {
    "fire_refused": "a fire produced no run — the routine was still active, the daemon was "
                    "draining, or its `main` model role names a model the catalog no longer serves",
    "lane_fire_refused": "a due lane fire armed nothing — the previous chain is still in "
                         "flight",
    "fire_paused": "a due fire was skipped because scheduling was paused — it is owed, and "
                   "made up at the resume only while it is still early in this routine's "
                   "own cadence",
    "routine_catchup_declined": "a pause-skipped routine fire was NOT made up — it came due "
                                "too long ago for its own cadence, so the routine waits for "
                                "its next cron fire",
    "lane_fire_paused": "a due lane fire was skipped because scheduling was paused — the "
                        "fire is gone, not deferred, and the next one is a full cron "
                        "interval away",
    "lane_catchup_declined": "a pause-skipped lane fire was NOT made up — it came due too "
                             "long ago for its own cadence, so its members wait for the "
                             "next cron fire",
    "lane_chain_stopped": "a lane chain ended early, so its remaining members never ran",
    "lane_chain_member_skipped": "a chain named a member that is not a routine in any home",
    "scheduler_tick_error": "a scheduler tick raised — whatever that tick owed is late",
    # F678: the run finished on its own authored summary, but the CHECKS that should have
    # judged that ending did not run — so nothing verified what the run claimed.
    "finish_gate_failed": "a run's finish GATE raised instead of judging: the run finished on "
                          "its own authored status and summary, unchecked",
    "trigger_capped": "a trigger hit its daily cap: the routine is dark until the cap resets",
    "commit_failed": "a commit into a versioned repo did not land — the files are on disk and "
                     "out of its history until a later commit takes them",
    "backup_stale": "the nightly state snapshot is not current — the last attempt did not "
                    "complete, or none has for days — so the instance is running unbacked",
    "migration_expired": "a MIGRATION marker is past its expiry, so the release gate is red on "
                         "files nobody touched: no release can ship until the converged "
                         "migration code is deleted",
    "migration_due_soon": "a MIGRATION marker expires within the warning window — the deletion "
                          "is owed before that date, or it lands on whoever ships that day",
    "obligation_overdue": "something a person asked of this routine, or approved, has stayed "
                          "unfinished for over a week — every run answers for it and none has "
                          "met it (engine/obligations.py)",
    # R2652. The unit of due work here is the TURN: a turn that came back well-formed and
    # WRONG was paid for at full prompt price and produced nothing, and the engine re-drove
    # it. `attempts_exhausted` is the extreme — the turn produced nothing at all and the run
    # ended. It belongs on this fold for the reason `finish_gate_failed` does: the run reads as
    # ordinary on every other surface (a rejection recovers, so the run finishes `ok`), and
    # what did not happen is invisible to any run page. It is also the ONLY in-product reader
    # of the stream, so an event left off it is read by the nightly ssh recipe or by nobody.
    "wrong_turn_detected": "a turn came back well-formed and WRONG — rejected, repeated, or "
                           "out of schema attempts — so the turn was paid for and the work it "
                           "owed was re-driven or lost (engine/wrongturn.py)",
    # F716. The unit here is a TURN that could not be taken at all: every member of the role's
    # chain failed hard or was cooling, so there was no model to ask. A failed run has a run
    # page — but when the cause is the host's shared proxy the failure is never one routine's,
    # and no surface said so: 2026-10-19 carried five of these plus three run_failed, every one
    # claude-proxy 503 auth_unavailable, across scheduler-builder and bahnbonus-seat-position.
    # `subject` is the routine, so a provider outage reads as one row per affected routine with
    # the chain HEAD in the detail; a single row is a hiccup, several on one day is the fleet
    # off its models.
    "model_chain_exhausted": "a role's WHOLE fallback chain was unusable — every model failed "
                             "hard or was cooling, so the turn could not be taken: the detail "
                             "names the chain head and the last model tried "
                             "(engine/degrade.py)",
}

#: Partial finishes, split by whether a budget forced them (health_events.py's own
#: distinction). Read per routine, never fleet-wide: the fleet number says nothing a routine
#: page does not say better.
ENDING_EVENTS = ("budget_exhausted", "run_partial")

#: How far back a fold looks by default. A week covers every routine's own cadence (the
#: slowest live lane is weekly), which is the point: a row is evidence only against the
#: schedule that should have fired it.
DEFAULT_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 90

#: How many RECORDS `recent_events` hands back by default, and at most. The stream is read by
#: the record only when something is being chased, so the default is a screenful and the cap is
#: what one JSON answer can carry without becoming the 280 KB file it is a view of.
DEFAULT_LIMIT = 100
MAX_LIMIT = 2000


def stream_path(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / HEALTH_EVENTS_FILE


def health_records(routines_home: Path) -> list[dict]:
    """Every event, oldest first. Missing file → []. Unparseable lines are skipped.

    The returned list is SHARED — treat it as immutable, exactly like `usage_stream`'s.
    """
    path = stream_path(routines_home)

    return memo.memoized_shared(f"health-stream:{path}", [path], lambda: read_jsonl(path))


def _stamped(routines_home: Path) -> list[tuple[datetime, dict]]:
    """Every event either fold reads, paired with its stamp as an instant, oldest first — the
    half of a fold that depends on the file alone, so the half memoized on its fingerprint.
    An event whose stamp names no instant cannot sit inside any window and is left out here.

    SHARED like `health_records`: treat the list and its records as immutable.
    """
    path = stream_path(routines_home)

    def pair() -> list[tuple[datetime, dict]]:
        out: list[tuple[datetime, dict]] = []
        for rec in health_records(routines_home):
            event = rec.get("event")
            if event in BLOCKED_EVENTS or event in ENDING_EVENTS or event == CORRECTION:
                when = instant(rec.get("ts"))
                if when is not None:
                    out.append((when, rec))
        return out

    return memo.memoized_shared(f"health-stamped:{path}", [path], pair)


def _corrections(routines_home: Path) -> dict[str, datetime]:
    """Each corrected run_id → the instant of its NEWEST correction (F637/F703).

    Read off the same stamped list every fold uses, so resolving a correction costs no second
    parse. A run resumed twice keeps the latest, because a leg that failed AGAIN after an
    earlier correction is not excused by it: the comparison is always against a stamp.
    """
    path = stream_path(routines_home)

    def latest() -> dict[str, datetime]:
        out: dict[str, datetime] = {}
        for when, rec in _stamped(routines_home):
            if rec.get("event") != CORRECTION:
                continue
            run_id = str(rec.get("run_id") or "")
            if run_id:
                out[run_id] = max(when, out.get(run_id, when))
        return out

    return memo.memoized_shared(f"health-corrections:{path}", [path], latest)


def _corrected_after(corrections: dict[str, datetime], run_id: str,
                     when: datetime) -> bool:
    """Whether `run_id` was corrected at or after `when` — the row's own stamp, never a flat
    "this run was corrected once" flag.
    """
    corrected = corrections.get(run_id)
    return corrected is not None and corrected >= when


def _resolve(rec: dict, when: datetime,
             corrections: dict[str, datetime]) -> dict | None:
    """One blocked-work row as a reader should see it once corrections are applied: None when
    a later `ok` finish of the same run superseded it, an ANNOTATED COPY when the row stands
    but its not-ok verdict does not, else the record untouched.

    The lane-chain close is the annotated case, and deliberately so (F703): its remaining
    members really did not run, so the row stays on this fold — what it must stop claiming is
    that the member failed, once that member's run was resumed and finished ok. Records are
    shared and immutable, so an annotation copies.
    """
    if runcorrection.is_correctable(rec) and _corrected_after(
            corrections, str(rec.get("run_id") or ""), when):
        return None
    stopped_by = str(rec.get("stopped_by") or "")
    if stopped_by and _corrected_after(corrections, stopped_by, when):
        out = dict(rec)
        out["detail"] = str(rec.get("detail") or "") + runcorrection.lane_note(stopped_by)
        out["corrected"] = True
        return out
    return rec


def _since(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _window(routines_home: Path, events, since: datetime) -> list[tuple[datetime, dict]]:
    """The stamped events named in `events` at or after `since` — cut at every call, because
    the window moves while the file stands still.
    """
    return [(when, rec) for when, rec in _stamped(routines_home)
            if rec.get("event") in events and when >= since]


def blocked_fleet(server: ServerConfig, *, days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """What the fleet did not do in the last `days`: one row per (event, subject), newest
    first, each carrying its count, its first and last stamp and the newest `detail`.

    `subject` is the event's `routine` field as written — a routine slug for a refused fire
    or a capped trigger, a LANE ID for the three lane events (opaque by contract: resolve it
    against the lane store, never by reading a prefix), and empty for a scheduler tick.
    """
    days = max(1, min(int(days), MAX_WINDOW_DAYS))
    since = _since(days)
    rows: dict[tuple[str, str], dict] = {}
    span: dict[tuple[str, str], tuple[datetime, datetime]] = {}   # (first, last) instants
    corrections = _corrections(server.routines_home)
    for when, raw in _window(server.routines_home, BLOCKED_EVENTS, since):
        resolved = _resolve(raw, when, corrections)
        if resolved is None:
            continue        # a resumed leg finished ok: this ending is superseded (F637/F703)
        rec = resolved
        key = (str(rec.get("event")), str(rec.get("routine") or ""))
        ts, detail = str(rec.get("ts") or ""), str(rec.get("detail") or "")
        row = rows.get(key)
        if row is None:
            rows[key] = {"event": key[0], "means": BLOCKED_EVENTS[key[0]], "subject": key[1],
                         "count": 1, "first_ts": ts, "last_ts": ts, "detail": detail,
                         "corrected": bool(rec.get("corrected"))}
            span[key] = (when, when)
            continue
        row["corrected"] = row["corrected"] or bool(rec.get("corrected"))
        row["count"] += 1
        first, last = span[key]
        if when < first:
            first, row["first_ts"] = when, ts
        if when >= last:
            last, row["last_ts"], row["detail"] = when, ts, detail
        span[key] = (first, last)
    ordered = [rows[k] for k in sorted(rows, key=lambda k: (span[k][1], k[0]), reverse=True)]
    return {"window_days": days, "since": since.isoformat(),
            "total": sum(r["count"] for r in ordered), "rows": ordered,
            "vocabulary": dict(BLOCKED_EVENTS)}


def recent_events(server: ServerConfig, *, limit: int = DEFAULT_LIMIT,
                  routine: str = "", event: str = "") -> dict:
    """The newest `limit` events of the stream itself, newest first — the records, not a fold.

    R2764: `.control/` is not listable for uid 1000 on this instance (the file inside it is
    0644), so `gu rsched logs` raises `PermissionError` and the documented way to inspect gate
    and fire events is unusable from inside EVERY routine run. The web process already parses
    this exact file for `blocked_fleet`, so serving the records costs no new reader and no new
    store — it makes the stream reachable the way everything else about a run is.

    Neither existing fold answers for it. `blocked_fleet` keeps only `BLOCKED_EVENTS` — a gate
    decision, a run start, a commit that landed are all dropped — and collapses what survives
    into one row per (event, subject), which is the opposite of what a reader asking "what
    happened at 03:00" needs. This filters instead of folding: `routine` and `event` narrow,
    and the answer is capped because the live stream is ~280 KB and grows forever.

    `total` counts everything that MATCHED, so a bounded answer says what it left out. There is
    no window: an event is asked for by name here, not by the clock, and a reader chasing a
    quiet failure wants the last one of its kind however old it is.
    """
    limit = max(1, min(int(limit), MAX_LIMIT))
    rows = [rec for rec in health_records(server.routines_home)
            if (not routine or str(rec.get("routine") or "") == routine)
            and (not event or str(rec.get("event") or "") == event)]
    # `health_records` hands back a SHARED list, oldest first, that callers must not mutate —
    # so the newest slice is taken by reversed slicing, never by reversing in place.
    return {"events": list(reversed(rows[-limit:])), "total": len(rows), "limit": limit}


def budget_endings(server: ServerConfig, slug: str, *,
                   days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """How this routine's partial finishes ended in the last `days`: `budget_exhausted` is
    the count a budget forced, `run_partial` the count the model chose with budget to spare.

    The usage stream files both as `partial`, so a routine that outgrew its ceiling and one
    that keeps finding a source down read identically there. `last_detail` names which budget
    (the event carries `resource`/`limit`), which is the sentence a reader needs.

    `corrected` is the third count (F637/F703): a leg that ended here, whose run was then
    resumed and finished `ok`. It leaves the other two rather than vanishing — the budget
    really did end that leg — but it is no longer read as how the RUN ended, which is the
    question this split answers.
    """
    days = max(1, min(int(days), MAX_WINDOW_DAYS))
    counts = dict.fromkeys(ENDING_EVENTS, 0)
    corrections = _corrections(server.routines_home)
    corrected = 0
    last_detail, last_ts, newest = "", "", None
    for when, rec in _window(server.routines_home, ENDING_EVENTS, _since(days)):
        if str(rec.get("routine") or "") != slug:
            continue
        if runcorrection.is_correctable(rec) and _corrected_after(
                corrections, str(rec.get("run_id") or ""), when):
            corrected += 1
            continue
        counts[str(rec["event"])] += 1
        if newest is None or when >= newest:
            newest, last_ts = when, str(rec.get("ts") or "")
            last_detail = str(rec.get("detail") or "")
    return {"window_days": days, **counts, "corrected": corrected,
            "last_ts": last_ts, "last_detail": last_detail}
