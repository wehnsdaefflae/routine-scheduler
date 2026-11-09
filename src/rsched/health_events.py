"""Health-events file: append-only JSONL log of key daemon/engine events for audit consumption.

Writes to <routines_home>/.control/health-events.jsonl. Each line is a JSON object:
{"ts": <iso>, "event": <a name in health_vocab.EVENTS>, "routine": <slug>,
 "run_id": <id>, "detail": <str>}

THE VOCABULARY IS `rsched.health_vocab.EVENTS` — data, with what each event means beside it —
and it is machine-checked: every event name emitted anywhere in `src/rsched` must appear in it
(tests/test_health_events.py walks the call sites). It lived in this docstring until 2026-11-09,
when this file reached the size bar and the next event could not be documented anywhere. A reader
keyed on the documented list — the audit recipe, the `health-events` util's filters — sees
nothing of an event added without a line there: `trigger_capped` went unread for weeks that way.

Two of these events report a STANDING CONDITION rather than an incident — `cost_trend_degraded`
and `cache_read_degraded`, both read at RUN END — and a run end is not a run: a resumed run
reaches it once per resume, so ONE run filed 26 cost-trend rows in an afternoon. Those are
filed at most once per (event, routine) per `health_cooldown.REPEAT_COOLDOWN_HOURS` hours,
applied inside `log_health_event` so no call site can forget it. `health_cooldown`'s docstring
holds the measurements, the window's justification and the payload-dedupe dead end. Every
other event here is an incident and is filed every time: two of them are two facts.

budget_exhausted vs run_partial: BOTH are a `partial` finish, and they used to be one event.
budget_exhausted is the finish a BUDGET VIOLATION forced (the reserved finish turn was spent, or
the engine ended the run itself); run_partial is a partial the model chose with budget to spare.
The distinction is the point: a fleet that "exhausts its budget" nightly reads as starved, when
most of those were authored finishes. budget_exhausted carries `resource` (turns
· total_turns · wall_clock · tokens · cost), `limit` and `status` as structured fields — WHICH
budget ended the run was recorded nowhere, and reconstructing it from status.json worked for 22
of 90 cases. It is emitted whenever the RESERVED FINISH TURN was spent, whatever the finish
status: a run that spends it and still finishes `ok` was ended by a budget just as much (11 such
runs since 09-01 left no event at all). An abort's row: `loopend.abort_fields` (F751, F597).
"""

from __future__ import annotations

from pathlib import Path

from . import health_cooldown
from .ids import now_iso
from .paths import append_jsonl

HEALTH_EVENTS_FILE = "health-events.jsonl"

#: Re-exported for every reader that asks "is this event repeat-governed?" — the table and the
#: reasoning live in `health_cooldown`, whose whole subject that is.
REPEAT_COOLDOWN_HOURS = health_cooldown.REPEAT_COOLDOWN_HOURS


def log_health_event(routines_home: Path, event: str, *, routine: str,
                     run_id: str, detail: str = "", **fields: object) -> None:
    """Append a health event to the JSONL log under routines_home/.control/.

    `fields` adds event-specific STRUCTURED keys beside the five common ones — `rc` and
    `vm_hwm_kb` on a close-out, `resource`/`limit` on budget_exhausted, `repo` on a commit event,
    …; each event's own are listed in the module docstring. A None value is dropped rather than
    written, so a caller can pass an unknown reading without minting a null the readers must
    handle. An event named in `REPEAT_COOLDOWN_HOURS` is DROPPED when the same (event, routine)
    was filed inside its window — the standing-condition rule, applied here so no call site can
    forget it.

    Best-effort: silently ignores I/O errors so logging never blocks the daemon or engine.
    """
    if health_cooldown.blocks(routines_home, event, routine):
        return
    try:
        append_jsonl(Path(routines_home) / ".control" / HEALTH_EVENTS_FILE, {
            "ts": now_iso(),
            "event": event,
            "routine": routine,
            "run_id": run_id,
            "detail": detail[:500],
            **{k: v for k, v in fields.items() if v is not None},
        })
    except OSError:
        pass
