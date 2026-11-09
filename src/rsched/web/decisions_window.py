"""The WIRE shape of `GET /api/questions` — what one read of the decisions list carries (F810).

`decisions_read.open_decisions` is the whole catalog: every open ask, every answer still in
flight, and the durable SETTLED archive (F525) of every routine and conversation, up to
`decisions_read.SETTLED_CAP` per dir. Served as is, that was ~1.1 MB on this instance on
2026-11-05 — 389 settled rows came to 1.08 MB, the 12 open asks and the open audit decisions to
the rest — and `static/app.js` refetches it every 30 s in every open tab, for a header badge that
counts only the OPEN ones. The archive is history, and history was being shipped to a counter.

So the route bounds what it sends, three ways, and memoizes nothing of its own (the catalog it
cuts is already memoized and single-flighted, `readmodels/memo`):

- OPEN rows, and answers still in flight (`answered` but not `settled` — the run has not taken
  them yet, and each carries its `pickup`: who reads it, when), are sent IN FULL, always.
  Everything that counts what waits reads exactly those.
- SETTLED rows (a run has consumed the answer) are a WINDOW: the `settled` newest across the
  whole instance, newest first by when a run took them, and NONE unless a caller asks.
  `settled_total` says how many exist, so the page that shows them says "newest k of N" and
  offers more instead of silently truncating — the Decisions page is the one console reader
  that asks (`static/questions-store.js` `wantSettled`).
- A settled row is PROJECTED to the fields its receipt card reads (`SETTLED_FIELDS`): options,
  defaults, batch ids and the access-request entity list are an ask's machinery and mean
  nothing once it is answered.

`routine` narrows every row to one routine's or conversation's own, for an API caller that
wants its own history (self-audit reads its answered questions at orient) rather than the
fleet's newest.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .. import patchreceipt
from ..readmodels import stamps
from .decision_pickup import annotate
from .decisions_read import open_decisions

#: The most settled rows one read may ask for. The archive is capped per dir already
#: (`decisions_read.SETTLED_CAP`); this bounds one request against a fleet that keeps growing.
SETTLED_MAX = 1000

#: What a settled receipt card reads (static/views/questions.js's answered branch, the
#: declined chip, the unapplied-patch bar, the shared store's answered-here healing) — and
#: nothing else. A field a new settled surface needs is added here, deliberately.
SETTLED_FIELDS = frozenset({
    "qid", "routine", "conversation", "background", "owner", "meta", "mode", "type",
    "report", "report_target", "question", "asked", "run_id", "run_state",
    "answered", "answer", "answer_source", "settled", "ran_now", "consumed",
    "first_consumed", "revised", "decision", "reason", "settled_by",
    "config_patch", "config_target",
    patchreceipt.APPLIED, patchreceipt.TARGET, patchreceipt.NOTE,
})

_NEVER = datetime.min.replace(tzinfo=UTC)


def _taken(row: dict) -> datetime:
    """When a run took the answer — the archive's `consumed`; an audit decision carries no
    such stamp, so its report's `asked` (the report that still lists it) stands in.
    """
    return stamps.instant(row.get("consumed")) or stamps.instant(row.get("asked")) or _NEVER


def window(items: list[dict], *, settled: int = 0, routine: str = "") -> dict:
    """`{"items": [...], "settled_total": N}` — the open rows whole, the newest `settled` of
    the settled ones projected, in that order. When the window leaves rows out, a
    `settled_note` says how to ask for them: a ROUTINE reads this API with no console in
    front of it (self-audit, config-optimizer), and a cut it is not told about reads exactly
    like "nothing was ever answered".
    """
    if routine:
        items = [q for q in items if q.get("routine") == routine]
    history = [q for q in items if q.get("settled")]
    history.sort(key=_taken, reverse=True)
    shown = [{k: v for k, v in q.items() if k in SETTLED_FIELDS}
             for q in history[:max(0, min(settled, SETTLED_MAX))]]
    page: dict = {"items": [q for q in items if not q.get("settled")] + shown,
                  "settled_total": len(history)}
    if len(shown) < len(history):
        page["settled_note"] = (
            f"{len(history) - len(shown)} of {len(history)} settled decisions are not in "
            f"items — ask with ?settled=<n> (newest first, at most {SETTLED_MAX}) and "
            "?routine=<slug> for one routine's own")
    return page


def open_window(state, *, settled: int = 0, routine: str = "") -> dict:
    """The route's whole read: the catalog, every in-flight answer's READER stamped on it
    (`decision_pickup` — an answer nothing will read is the operator's again), the window.
    """
    return window(annotate(state, open_decisions(state.server)), settled=settled, routine=routine)
