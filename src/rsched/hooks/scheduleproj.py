"""A one-shot AS a hook — the `schedule` trigger's first and only user (D152 gap 5).

`hooks/__init__.TRIGGERS` has named four things that make a hook fire since step 1, and three
of them had users: `send` (a report, an injection, a hand-back), `match` (a reminder over what
a run produced), `moment` (a special turn). The fourth, `schedule` — "a time" — carried this
note instead of a user:

    #: - `schedule` — a time. Nothing uses this yet; the field costs nothing and naming it
    #:                keeps the next mechanism from being a fourth store.

That next mechanism already exists and it IS a fourth store: the one-shot request spool
(`rsched/schedule_once.py`, `.control/schedule-once/<slug>/req-*.json`), armed from the routine
page or by a routine's own `schedule_run` action, consumed by `daemon/schedule_once.py` on the
tick. So the question the whole campaign exists to make answerable — *what hooks exist in this
instance* — still could not be answered for anything time-fired: three mechanisms were on the
stream and the fourth was not, which is the shape step 1's docstring set out to prevent.

This module closes that, in the same shape `reportproj.py` uses and for the same reasons:

* **one derivation, not a second writer.** `hook_of_request` is a pure function of the stored
  request record, and `request_of_hook` is its exact inverse. The spool's bytes are untouched
  and `daemon/schedule_once.py` is untouched: consumption is still what guarantees a one-shot
  never repeats, and nothing about firing now depends on the stream.
* **the inverse is what makes the claim testable** rather than asserted. `reportinv` had to be
  written later, against 5,016 live rows; here it is written in the same change, because a
  projection whose inverse nobody wrote is a mirror whatever it is called.
* **the lifecycle is on the stream too.** A one-shot is armed, then fired, cancelled or
  skipped. `EVENTS` spells those exactly as the existing surfaces do — `oneshot_skipped` is
  already a health event with that name, and the daemon's own ledger counts `fires` — so a
  reader folding the stream and a reader folding the spool reach the same four facts.

**The reader side is exercised by tests, not yet by a page — stated so nobody assumes
otherwise.** `fires_at` and `due_before` are what make the stream answer *what will fire later*
for the INSTANCE, which is the thing no store could do across mechanisms. No console surface
calls them yet, and that is a deliberate non-decision rather than an oversight: `/api/schedule`'s
week strip already enumerates one-shots PER ROUTINE from the spool, so serving a second view of
the same facts would duplicate rather than add, and choosing where an instance-wide "what is
about to happen" belongs is a design question no decision of the operator's covers. The campaign's
own staging is "wrap first, replace last"; this is the wrap, and the reader is written and pinned
so the replace step has something to build on.

**What this does NOT do, deliberately.** It does not make the daemon fire from the stream. The
spool is consumed on fire and that consumption is the non-repeating guarantee (no routine.yaml
rewrite, no self-disabling cron); a stream is append-only, so "has this fired?" would become a
fold over later rows — strictly worse than a file that is gone. Wrap first, replace last, and
for this mechanism the replacement step is not wanted at all: what was missing was the ANSWER
to "what will fire later", not a new firing path.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..ids import now_iso
from . import Hook, addressee, split_addressee

#: The lifecycle events a one-shot row can carry, spelled as the existing surfaces spell them:
#: `fired` is what the daemon's own ledger counts (`state.json`'s `fires`), `skipped` is the
#: `oneshot_skipped` health event's own word (D237: a missed one-shot is consumed unfired and
#: never made up), `cancelled` is `schedule_once.cancel`'s. The arming itself is the hook, so
#: it is not an event — exactly as a report's filing is the hook and `delivered` is an event.
EVENTS = ("fired", "cancelled", "skipped")

#: Which of them END it. All three do, and that is the mechanism's substance: a one-shot is
#: consumed whichever way it ends, so no row about it is ever owed a later fact.
TERMINAL_EVENTS = EVENTS


def hook_of_request(record: dict, slug: str) -> Hook:
    """The hook a one-shot request IS: trigger `schedule`, delivery `fire`, disposition `open`.

    The trigger carries the INSTANT (`at`), which is what makes this a `schedule` hook rather
    than a `send` one: a `send` fires now and a `schedule` fires then, and the whole value of
    the field is that "then" is readable without opening a fourth store.

    `requested_by` is the ORIGIN, not the addressee: a one-shot armed by another routine (the
    reviewer's ask, which `schedule_run` honours) fires the TARGET, and the addressee is the
    routine that will run. Reading it the other way round would make a cross-armed one-shot
    look like a message back to the arming routine.
    """
    return Hook(id=str(record.get("id") or ""), source="schedule_once",
                trigger={"kind": "schedule", "at": str(record.get("fire_at") or "")},
                addressee=addressee("routine", slug),
                payload={"reason": str(record.get("reason") or ""),
                         "requested_by": str(record.get("requested_by") or "")},
                delivery="fire", disposition="open",
                origin={"routine": str(record.get("requested_by") or ""), "run_id": ""},
                ts=str(record.get("created") or "") or now_iso())


def request_of_hook(hook: Hook) -> tuple[str, dict]:
    """The EXACT inverse: the slug it fires, and the request record as the spool stores it.

    Total over what `hook_of_request` emits, which is what the campaign means by a projection
    rather than a mirror — `reportinv`'s lesson, applied in the same change this time instead
    of three steps later. A row this cannot invert is a row the stream cannot be trusted for.
    """
    # `split_addressee` is the ONE parser of an addressee, by that module's own rule: a second
    # place that splits it is where an addressing scheme grows a special case.
    _kind, slug = split_addressee(hook.addressee)
    payload = hook.payload or {}
    return slug, {"id": hook.id,
                  "fire_at": str((hook.trigger or {}).get("at") or ""),
                  "reason": str(payload.get("reason") or ""),
                  "requested_by": str(payload.get("requested_by") or ""),
                  "created": hook.ts}


def record_armed(routines_home, record: dict, slug: str) -> str:
    """Put an armed one-shot on the hook stream. Returns its hook id ("" when nothing was
    written), and never raises into the arming path.

    Best-effort BY DESIGN, and the opposite of `reportproj`'s choice — which refuses loudly
    when it cannot write, because there the hook IS the source and a lost row is lost work.
    Here the SPOOL is the source and stays so: the request file is what fires, the daemon reads
    only that, and this row exists to answer "what will fire later". A stream write that failed
    must not stop a one-shot being armed, or a diagnostic would have become a dependency.
    """
    from . import store as hookstore

    try:
        return hookstore.append(routines_home, hook_of_request(record, slug))
    except Exception:
        # Broad on purpose: a diagnostic may never break the arming it describes.
        return ""


def record_event(routines_home, *, hook_id: str, event: str, detail: str = "") -> str:
    """One later fact about an armed one-shot — `fired`, `cancelled` or `skipped`.

    An append-only stream says "this ended" with another row about the same id, which is what
    `reportproj.event` does for a report's lifecycle. Same best-effort contract as above, and
    an event outside `EVENTS` is refused rather than written: a vocabulary nothing checks is
    how `trigger_capped` went unread for weeks.
    """
    from . import store as hookstore

    if event not in EVENTS:
        return ""
    try:
        return hookstore.append(routines_home, Hook(
            id=str(hook_id), source="schedule_once",
            trigger={"kind": "schedule", "event": event},
            addressee=addressee("triage"), payload={"detail": str(detail or "")},
            delivery="fire", disposition="settled", origin={}, ts=now_iso()))
    except Exception:
        # Broad on purpose: a diagnostic may never break the fire it describes.
        return ""


def fires_at(hook: Hook) -> datetime | None:
    """The instant this hook fires, or None when the row carries no readable one.

    None rather than a raise, and never "now": a store must not be able to break a reader, and
    a row whose instant cannot be read must not sort as imminent. Compared as an INSTANT, the
    way every stamp in this codebase is — `ids.now_iso` writes local time with its offset, so a
    string compare against a UTC cutoff moves the edge by the host's offset.
    """
    raw = str((hook.trigger or {}).get("at") or "")
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


def due_before(hooks: list[Hook], cutoff: datetime) -> list[Hook]:
    """Those firing at or before `cutoff`, soonest first — "what is about to happen".

    The question no store could answer before this module: the spool answers it per routine
    (`slugs_with_requests` then a read each), and the stream now answers it for the instance in
    one fold. A hook with no readable instant is left OUT rather than treated as due.
    """
    dated: list[tuple[datetime, Hook]] = []
    for hook in hooks:
        when = fires_at(hook)
        if when is not None and when <= cutoff:
            dated.append((when, hook))
    return [hook for _when, hook in sorted(dated, key=lambda pair: pair[0])]
