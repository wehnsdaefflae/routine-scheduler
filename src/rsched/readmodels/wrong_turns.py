"""Wrong turns, folded by CAUSE — the reading `wrong_turn_detected` was filed for (2026-11-19).

`wrong_turn_detected` (R2652, `engine/wrongturn.py`) is a turn that came back well-formed and
WRONG: rejected by the schema, repeated, or out of attempts. It was folded onto the blocked-work
surface beside refused fires and a stale backup, and on 2026-11-19 it was ~230 of that page's
285 rows' worth of incidents. The operator's reading: those are not blocked work. A rejected
turn is re-driven, its run goes on and normally finishes `ok`; nothing that was DUE failed to
happen, which is the one thing that page says. Filed there, the rate drowned the rows that do
say it (the stale backup that was the page's one operator block sat under them) and still did
not answer the question the measurement exists for: WHICH mistake recurs, so that the engine,
the schema or the prompt can remove it at its source.

So this fold groups by cause rather than by routine: the detail with its turn number, numbers
and quoted values masked (`cause`), so `start_line: -40 is less than the minimum of 1` from one
routine and `start_line: -5 …` from another are ONE row — one fix — with the routines, models
and count beside it. The run-ending reason (`attempts_exhausted`) stays a wrong turn here, and a
resumed run that finished ok supersedes it exactly as the blocked fold does
(`runcorrection`). The cost of a wrong turn is already counted where a routine's own health is
judged: it is FRICTION in `engine/overhead.py`'s process share.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from ..config import ServerConfig
from .health_stream import (
    DEFAULT_WINDOW_DAYS,
    MAX_WINDOW_DAYS,
    _corrections,
    _resolve,
    _since,
    _window,
)

EVENT = "wrong_turn_detected"
#: How many rows the fold hands back: the head of the distribution is the actionable part.
MAX_ROWS = 40

_TURN = re.compile(r"^\s*turn\s+\d+:\s*")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_LISTISH = re.compile(r"\[\{.*?\}\]|\{.*?\}")


def cause(detail: str) -> str:
    """The detail with what varies between occurrences of ONE mistake masked: the turn number,
    quoted values, numbers and inline objects — so the same error from two runs is one row.
    """
    text = _TURN.sub("", detail)
    text = _LISTISH.sub("{…}", text)
    text = _QUOTED.sub("'…'", text)
    text = _NUMBER.sub("N", text)
    return " ".join(text.split())[:200]


def fold(server: ServerConfig, *, days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """The last `days` of wrong turns, one row per cause, most frequent first: `count`, the
    `reason` class, `routines` and `models` as `{name: count}`, the newest raw `detail` and its
    stamp. `total` counts every wrong turn in the window, `causes` the distinct rows before
    the `MAX_ROWS` cut, so a bounded answer says what it left out.
    """
    days = max(1, min(int(days), MAX_WINDOW_DAYS))
    since = _since(days)
    corrections = _corrections(server.routines_home)
    rows: dict[str, dict] = {}
    newest: dict[str, datetime] = {}
    total = 0
    for when, raw in _window(server.routines_home, {EVENT}, since):
        rec = _resolve(raw, when, corrections)
        if rec is None:
            continue
        total += 1
        detail = str(rec.get("detail") or "")
        key = cause(detail)
        row = rows.setdefault(key, {"cause": key, "count": 0, "reason": "",
                                    "routines": Counter(), "models": Counter(),
                                    "detail": "", "last_ts": ""})
        row["count"] += 1
        row["routines"][str(rec.get("routine") or "")] += 1
        if rec.get("model"):
            row["models"][str(rec["model"])] += 1
        if key not in newest or when >= newest[key]:
            newest[key] = when
            row.update(reason=str(rec.get("reason") or ""), detail=detail,
                       last_ts=str(rec.get("ts") or ""))
    ordered = sorted(rows.values(), key=lambda r: (-r["count"], r["cause"]))
    out = [{**r, "routines": dict(r["routines"].most_common()),
            "models": dict(r["models"].most_common())} for r in ordered[:MAX_ROWS]]
    return {"window_days": days, "since": since.isoformat(), "total": total,
            "causes": len(ordered), "rows": out}
