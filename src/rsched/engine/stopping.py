"""Semantic stopping conditions (F334/D98, v1) — user prose that bounds a run's job.

The USER owns the list (`state/stopping.json`, written by the web endpoint); the ENGINE
makes it impossible to silently ignore: the composer inlines every `open` condition into
the state digest, and the finish gate rejects a depth-0 finish whose summary accounts for
none of them (loop.py, the R108 deferral shape — one extra turn, the model addresses the
conditions, finishes again). The engine judges NOTHING semantic — a condition like
"stop once the PDF is verified" is only assessable by the model, so the contract is an
ACCOUNTING: the finish summary must carry `[s<n>] met — …` / `[s<n>] unmet — …` per open
condition, and readers (the sidebar badge, the user) take the model's word from there.
v2 (a verifier subcall that blocks with evidence) is a separate decision, only if v1
demonstrably leaks.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..paths import atomic_write_json, read_json

STATUSES = ("open", "met", "dropped")
_ID_RE = re.compile(r"^s\d+$")


def _store(routine_dir: Path) -> Path:
    return routine_dir / "state" / "stopping.json"


def load(routine_dir: Path) -> list[dict]:
    """Every condition, normalized shape; a missing/corrupt store is an empty list."""
    raw = read_json(_store(routine_dir))
    if not isinstance(raw, dict):
        return []
    return [{"id": str(c.get("id") or ""), "text": str(c["text"]).strip(),
             "status": c.get("status") if c.get("status") in STATUSES else "open",
             "ts": str(c.get("ts") or "")}
            for c in raw.get("conditions") or []
            if isinstance(c, dict) and str(c.get("text") or "").strip()]


def open_conditions(routine_dir: Path) -> list[dict]:
    return [c for c in load(routine_dir) if c["status"] == "open"]


def save(routine_dir: Path, conditions: list[dict], *, now: str) -> list[dict]:
    """Normalize + persist the USER's list: ids are assigned stably (s1, s2, … — an
    incoming row keeps a well-formed id, a new row gets the next free number), unknown
    statuses fall back to open, blank texts are dropped. Returns what was written.
    """
    used = {c["id"] for c in conditions
            if isinstance(c, dict) and _ID_RE.match(str(c.get("id") or ""))}
    n = 1
    rows = []
    for c in conditions:
        if not isinstance(c, dict) or not str(c.get("text") or "").strip():
            continue
        cid = str(c.get("id") or "")
        if not _ID_RE.match(cid):
            while f"s{n}" in used:
                n += 1
            cid = f"s{n}"
            used.add(cid)
        rows.append({"id": cid, "text": str(c["text"]).strip(),
                     "status": c.get("status") if c.get("status") in STATUSES else "open",
                     "ts": str(c.get("ts") or now)})
    atomic_write_json(_store(routine_dir), {"conditions": rows})
    return rows


def digest_section(routine_dir: Path) -> str:
    """The always-visible prompt block (state_digest inlines it beside the plan)."""
    conds = open_conditions(routine_dir)
    if not conds:
        return ""
    lines = "\n".join(f"- [{c['id']}] {c['text']}" for c in conds)
    return ("STOPPING CONDITIONS (state/stopping.json — the USER's meaning-level bounds on "
            "this job; the engine cannot judge them, you must):\n" + lines + "\n"
            "Your finish summary MUST account for each one: a line `[s<n>] met — <evidence>` "
            'or `[s<n>] unmet — <why>` per condition. A met LIMIT condition ("only '
            'diagnose", "stop once X is verified") means finish NOW rather than continue '
            "past it. A finish that ignores them is rejected and costs a turn.")


def unaccounted(summary: str, routine_dir: Path) -> list[str]:
    """The open condition ids a finish summary fails to mention as `[s<n>]` — the
    deterministic accounting check the finish gate enforces (semantics stay the model's).
    """
    return [c["id"] for c in open_conditions(routine_dir)
            if f"[{c['id']}]" not in (summary or "")]
