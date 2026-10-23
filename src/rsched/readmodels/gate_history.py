"""The run gate's HISTORY: the verdict that decided each fire, read off `runs/<ts>/gate.json`.

The gate has recorded every decision since F457 (`daemon/run_gate.py`'s `finally`, :284) and no
read surface served a byte of it. A routine's payload carried `run_gate` — the CONFIG, what the
gate WOULD ask — and its run rows, what ran. Between the two sat the question nobody could
answer without reading JSON on the box (R2764, the operator's words): "which ones fired, which
ones didn't".

Both halves are on disk, including the one that matters most. `daemon/runner.py` claims the run
dir (`_claim_run_dir`, :230) BEFORE it asks `run_gate.admit` (:330), so a SKIPPED fire leaves a
`runs/<ts>/` holding a `status.json` with `outcome: skipped` and a `gate.json` exactly like an
admitted fire's — a skip is a run row with no turns, not a missing row. So this is a projection
of files already written, not a new store, and no gated fire goes unrecorded.

It lives here, beside the other read folds, rather than in `engine/turnwindow.py` (which owns
the resume WINDOW and nothing else) or in `web/api_routines.py` (at its size ceiling, with zero
slack: `tests/size_ratchet.json`). `gate_run_row` is the composition the web layer calls —
`turnwindow.run_row`'s row plus this verdict — so adding the field cost that file no lines.
"""

from __future__ import annotations

from pathlib import Path

from ..engine.turnwindow import run_row
from ..paths import read_json
from . import memo

#: A gate reason is bounded at 1000 chars by the gate protocol itself (`run_gate._execute`),
#: which is a paragraph in a table cell. Rows are clipped to a sentence, the way every other
#: read surface clips model prose (`registry` summaries at 200).
REASON_MAX = 300


def _project(raw: object) -> dict | None:
    """One gate.json as a verdict, or None when the file names no decision.

    A gate.json without a `decision` is not a verdict: `gate_prepare` and the tests write one
    carrying `fingerprints` alone, and a fire that was never judged (an unconfigured gate, a
    run predating F457) has no file at all. Both must read as "no gate judged this fire" —
    rendering them as a gate that found nothing would invent a decision nobody made.

    A reader gets four fields per check: which check it was, what kind of question it asks,
    whether it found work, and the sentence it said so in. Everything else the file holds is
    the gate's own bookkeeping — `fingerprints` is a hash per check, the baseline the NEXT fire
    compares against (`daemon/gate_prepare.py`:110), meaningless to a reader and unbounded in
    width — so it is projected away HERE rather than filtered in the console.
    """
    if not isinstance(raw, dict) or not raw.get("decision"):
        return None
    checks = raw.get("checks")
    rows = [
        {"id": str(c.get("id") or ""), "kind": str(c.get("kind") or ""),
         "work": bool(c.get("work")), "reason": str(c.get("reason") or "")[:REASON_MAX]}
        for c in (checks if isinstance(checks, list) else []) if isinstance(c, dict)
    ]
    return {"decision": str(raw["decision"]),
            # why the fire happened at all: schedule | catchup | lane | report | trigger | …
            "fire_reason": str(raw.get("fire_reason") or ""),
            "reason": str(raw.get("reason") or "")[:REASON_MAX],
            "checks": rows}


def verdict(run_dir: Path) -> dict | None:
    """The gate's verdict on the fire that made `run_dir` — None when no gate judged it.

    Memoized on gate.json's own stat: a terminal run's verdict never changes, and a routine
    page reads 50 of them per request. `memo.memoized` hands back a copy, so no caller can
    reach into the cache.
    """
    path = Path(run_dir) / "gate.json"
    return memo.memoized(f"gate-verdict:{path}", [path], lambda: _project(read_json(path, {})))


def gate_run_row(info) -> dict:
    """One run as the routine page takes it: `turnwindow.run_row`'s row plus `gate`.

    The verdict is ADDED, never a replacement — the window, turn count and summary stay
    exactly where their readers expect them. `gate` is None for every fire no gate judged,
    which is most of the fleet, so an ungated routine's rows read as they always did.
    """
    return {**run_row(info), "gate": verdict(info.dir)}
