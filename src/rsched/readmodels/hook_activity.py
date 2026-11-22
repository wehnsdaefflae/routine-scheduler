"""HOOK ACTIVITY — the two questions the hook stream exists to answer for the INSTANCE.

`.control/hooks.jsonl` is written for every one-shot (`hooks/scheduleproj`), every mid-run
injection (`hooks/store.record_injection`) and every run-tree hook (`hooks/store.record_runtree`),
and no surface read any of those rows: `scheduleproj`'s own docstring said its reader was
"exercised by tests, not yet by a page". This module is that page's read model, and it answers
exactly two things:

* **What fires next?** `next_fires` — the schedule-once hooks still PENDING, soonest first.
* **Who spoke into which run?** `deliveries` — injection and run-tree rows, newest first, with
  the channel rendered in words and a failed run-tree write read as undelivered.

**The pending fold comes FIRST and the ordering second, never the other way round.** A one-shot
ENDS with a later row about the same id (`fired` · `cancelled` · `skipped`), not with a change to
the arming row, so `scheduleproj.due_before` over the raw stream would list every one-shot that
ever fired as overdue. `_pending` folds the lifecycle away and `due_before` orders what is left.
For the same reason a pending row whose instant is already PAST is kept and marked, never
dropped: it is a one-shot the daemon has not consumed yet, which is a thing to see.

The stream is read ONCE per fingerprint (`readmodels/memo`, like `health_stream`): both folds and
every request share that one parse, and the windows are cut at every call because they move while
the file stands still. The lists are memoized SHARED — treat them and their rows as immutable.

The spool stays the source of what actually FIRES (`docs/schedule-once.md`): this is a reading,
and `/api/schedule/week` still scans the spool per routine.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..config import ServerConfig
from ..engine.channels import user_authored
from ..hooks import Hook, split_addressee
from ..hooks.scheduleproj import TERMINAL_EVENTS, due_before, fires_at
from ..hooks.store import hooks_path, read_hooks
from . import memo
from .stamps import instant

#: The panel's own window, and the most either side may ask for. A one-shot may be armed up to a
#: year out (`schedule_once.MAX_HORIZON`), so a horizon is a READING choice rather than a bound
#: on the mechanism; two weeks is the widest the panel offers.
DEFAULT_HORIZON_H = 24
MAX_HORIZON_H = 24 * 14
DEFAULT_SINCE_H = 24
MAX_SINCE_H = 24 * 14

#: The sources whose rows are a DELIVERY — something was spoken into a run. Reports and
#: reminders are not here: a report has the Messages page and the ledger, a reminder has the
#: routine page, and neither was ever delivered mid-run.
DELIVERY_SOURCES = ("injection", "runtree")

#: An injection's channel IN WORDS (`engine/channels.VIAS` is the closed set it is drawn from).
#: The channel is the one thing a reader needs that the text does not say — who spoke — and a
#: raw `web-converse` says it only to someone who has read `channels.py`. A channel this table
#: does not know renders as its own name rather than as nothing: the set grows, and a row from a
#: newer build must still read.
VIA_WORDS = {
    "": "you, from the routine page",
    "conversation": "you, in the composer",
    "web": "you, from the run view",
    "web-converse": "you, from the run view's converse box",
    "web-audit": "you, from the audit surface",
    "report": "a report",
    "background": "a background hand-back",
    "branch": "a branch hand-back",
    "trigger": "a trigger",
    "schedule_once": "a one-shot's provenance",
    "pending": "the outcome of a queued proposal",
    "oversight": "an escalation rung",
    "hook": "another run of the run tree",
}

#: What `disposition: open` MEANS on a run-tree row, in the words the panel shows. `store
#: .record_runtree` writes it when `inbox.file_message` raised: the freight never arrived, and
#: this is the only record of it anywhere — a run-tree hook deliberately gets no `R<n>` row.
UNDELIVERED_NOTE = "not delivered — the write failed"


def _stream(routines_home: Path) -> list[Hook]:
    """Every row in the stream as `Hook`s, in order — the one parse both folds read off.

    SHARED: treat the list and its rows as immutable (a `Hook` is frozen anyway).
    """
    path = hooks_path(routines_home)

    return memo.memoized_shared(f"hook-activity:{path}", [path],
                                lambda: read_hooks(routines_home))


def _pending(routines_home: Path) -> list[Hook]:
    """The schedule-once hooks still armed: an arming row (`trigger.at` set) with no LATER row
    about the same id carrying a `TERMINAL_EVENTS` event.

    Order matters, which is why this is a pass over the stream and not two set comprehensions:
    the stream is append-only, so an ending is always a later row, and an id armed AGAIN after
    an ending is pending again. Memoized on the file alone — nothing here depends on the clock.
    """
    path = hooks_path(routines_home)

    def fold() -> list[Hook]:
        armed: dict[str, Hook] = {}
        for hook in _stream(routines_home):
            if hook.source != "schedule_once":
                continue
            trigger = hook.trigger or {}
            if str(trigger.get("event") or "") in TERMINAL_EVENTS:
                armed.pop(hook.id, None)
            elif trigger.get("at"):
                armed[hook.id] = hook
        return list(armed.values())

    return memo.memoized_shared(f"hook-pending:{path}", [path], fold)


def _fire_row(hook: Hook, now: datetime) -> dict:
    """One pending one-shot as the panel reads it: which routine, when, why, and who armed it."""
    _kind, slug = split_addressee(hook.addressee)
    when = fires_at(hook)
    payload = hook.payload or {}
    return {"hook": hook.id,
            "routine": slug,
            "fires_at": when.isoformat() if when else "",
            "reason": str(payload.get("reason") or ""),
            "requested_by": str(payload.get("requested_by") or ""),
            # a one-shot the daemon has not consumed yet: shown as such, never dropped
            "overdue": bool(when and when <= now)}


def next_fires(server: ServerConfig, *, now: datetime | None = None,
               horizon_h: int = DEFAULT_HORIZON_H) -> list[dict]:
    """The pending one-shots firing within `horizon_h`, soonest first — plus every pending one
    whose instant is already past, marked `overdue`.

    `now` is injectable because the window is the only part of this reading that moves on its
    own; it defaults to the actual instant.
    """
    now = now or datetime.now(UTC)
    horizon_h = max(1, min(int(horizon_h), MAX_HORIZON_H))
    pending = _pending(server.routines_home)
    return [_fire_row(hook, now)
            for hook in due_before(pending, now + timedelta(hours=horizon_h))]


def _who(hook: Hook) -> tuple[str, str, bool]:
    """Who spoke, as `(via, in words, was it a person)`.

    An INJECTION carries its channel on the row (`trigger.via`) and `channels.user_authored`
    decides person from machine — one answer for every consumer, never a second list here.
    A RUN-TREE row carries no channel: the run tree IS the channel, so the addressee says who
    spoke (`parent`, `child:<label>`) and a run is never a person.
    """
    if hook.source == "runtree":
        kind, name = split_addressee(hook.addressee)
        if kind == "child":
            return "hook", f"child {name}" if name else "a child run", False
        return "hook", "parent run", False
    via = str((hook.trigger or {}).get("via") or "")
    return via, VIA_WORDS.get(via, via or "an unnamed channel"), user_authored(via)


def _first_line(text: str) -> str:
    """The freight's first line — what a one-line row can carry of it."""
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _delivery_row(hook: Hook) -> dict:
    """One injection or run-tree row as the panel reads it.

    `run_id` is the run the row is ABOUT: for an injection the addressee (the run that was
    spoken INTO), for a run-tree hook the origin (the run that spoke, since the addressee is a
    relative address — `parent` names no run id at all).
    """
    origin = hook.origin or {}
    payload = hook.payload or {}
    via, who, person = _who(hook)
    if hook.source == "injection":
        _kind, run_id = split_addressee(hook.addressee)
        text = _first_line(str(payload.get("text") or ""))
    else:
        run_id = str(origin.get("run_id") or "")
        text = _first_line(str(payload.get("title") or ""))
    # `open` on a run-tree row is a FAILED write, not an open thread: the freight never arrived.
    undelivered = hook.source == "runtree" and hook.disposition == "open"
    return {"hook": hook.id, "source": hook.source, "run_id": run_id,
            "routine": str(origin.get("routine") or ""),
            "turn": int(origin.get("turn") or 0),
            # the channel VERBATIM as the row carries it, beside the words — a reader needs the
            # sentence, a filter needs the name, and deriving one from the other is not a reader's
            # business
            "via": via, "who": who, "person": person,
            "addressee": hook.addressee, "text": text, "ts": hook.ts,
            "undelivered": undelivered,
            "undelivered_note": UNDELIVERED_NOTE if undelivered else ""}


def deliveries(server: ServerConfig, *, since_h: int = DEFAULT_SINCE_H) -> list[dict]:
    """Everything spoken into a run in the last `since_h` hours, newest first.

    A row whose stamp names no instant cannot sit inside a window and is left out — the same
    rule `health_stream` applies, for the same reason: a stamp that says nothing must not sort
    as imminent (`readmodels/stamps`).

    The stream's own ORDER breaks a tie, which is load-bearing rather than tidy: `ids.now_iso`
    writes to the second, two hooks in one second are ordinary (a run-tree hand-back and the
    injection it caused), and a plain stable sort would then put the OLDEST of them first in a
    list whose whole claim is "newest first". The later row in an append-only stream IS the
    later fact.
    """
    since_h = max(1, min(int(since_h), MAX_SINCE_H))
    cutoff = datetime.now(UTC) - timedelta(hours=since_h)
    dated: list[tuple[datetime, int, Hook]] = []
    for position, hook in enumerate(_stream(server.routines_home)):
        if hook.source not in DELIVERY_SOURCES:
            continue
        when = instant(hook.ts)
        if when is not None and when >= cutoff:
            dated.append((when, position, hook))
    dated.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [_delivery_row(hook) for _when, _position, hook in dated]


def activity(server: ServerConfig, *, horizon_h: int = DEFAULT_HORIZON_H,
             since_h: int = DEFAULT_SINCE_H) -> dict:
    """Both lists, with the windows they were read for — the panel's whole payload."""
    return {"horizon_h": max(1, min(int(horizon_h), MAX_HORIZON_H)),
            "since_h": max(1, min(int(since_h), MAX_SINCE_H)),
            "next_fires": next_fires(server, horizon_h=horizon_h),
            "deliveries": deliveries(server, since_h=since_h)}
