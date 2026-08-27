"""Semantic stopping conditions (F334/D98) — user prose that bounds a run's job.

The user's order (2026-08-14): a run should stop on a MEANING-level condition ("stop once the
PDF is published and verified", "only diagnose — do not start fixing"), **not only on budget
walls**. Budgets stay what CLAUDE.md says they are — a runaway BACKSTOP, never a pace — and the
thing that actually decides when a job is done lives here.

The USER owns the list (the web endpoints write it); the ENGINE makes it impossible to silently
ignore, and RECORDS what the run concluded. The engine judges NOTHING semantic: a condition like
"stop once the PDF is verified" is only assessable by the model, so the contract is an
ACCOUNTING — the finish summary must carry `[s<n>] met — …` / `[s<n>] unmet — …` per active
condition, the finish gate rejects a summary that skips one, and `record_accounting` stamps the
model's verdict back into the store so every reader (the sidebar panel, the next run, the user)
sees the same state.

## Logical structure

Conditions are not a flat AND-list. They sit in GROUPS, each group combines its members with
`all` (AND) or `any` (OR), and the document combines the groups the same way — two levels, which
covers "(A AND B) OR (C AND D)" and "(A OR B) AND (C OR D)". Deeper nesting is where a UI and a
weak model both stop being able to reason about it, so it is deliberately not offered.

Two further connectives, both about WHEN a condition is live rather than how it combines:

- **`requires`** — ids that must be `met` before this condition is active at all. That is the
  sequencing case ("s3 only matters once s1 is met"): a dormant condition is shown to the run so
  it can see the shape of the job, but it is NOT required in the accounting, because demanding a
  verdict on something that cannot have happened yet only teaches the model to write noise. A
  requirement that is `dropped` or missing counts as SATISFIED — a gate nobody is watching must
  never block forever.
- **`stage`** — a routine condition scoped to one stage module (the "per-stage routine
  conditions" half of the original order). Active only while the run is in that stage.

## Evaluation

`met` satisfies; `dropped` is excluded entirely; anything else is unsatisfied. An EMPTY group is
vacuously satisfied under either mode — an empty group expresses no requirement, and the strict
reading of an empty `any` would let an emptied group block a job forever. A document with no
conditions at all evaluates to `None` (no opinion), which is what lets the digest and the UI stay
silent rather than announcing a satisfied goal nobody set.

Satisfaction is REPORTED, never enforced: the digest tells the run when every condition is met so
it finishes rather than drifting on, and the panel shows it. The engine does not force a finish —
that is v2 (a verifier subcall that blocks with evidence), a separate decision, and forcing on an
accounting the model itself wrote would just be the model stopping itself with extra steps.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..paths import atomic_write_json, read_json

STATUSES = ("open", "met", "dropped")
MODES = ("all", "any")
_ID_RE = re.compile(r"^s\d+$")
_GID_RE = re.compile(r"^g\d+$")

#: The finish-summary accounting line the gate requires and the writer reads back.
#: The separators are SAME-LINE only (no `\s`): a class that eats the newline runs
#: one entry's note into the line after it.
_ACCOUNT_RE = re.compile(r"\[(s\d+)\][ \t]*(met|unmet)\b[ \t:—–-]*", re.IGNORECASE)

DEFAULT_GROUP = "g1"


def _store(routine_dir: Path) -> Path:
    return routine_dir / "state" / "stopping.json"


# ---------------------------------------------------------------------------- load / save ----

def load(routine_dir: Path) -> dict:
    """The whole document, normalized to `{mode, groups, conditions}`. A missing or corrupt
    store reads as the empty document — never raises, because this is read on every boot.
    """
    raw = read_json(_store(routine_dir))
    return normalize(raw if isinstance(raw, dict) else {})


def normalize(raw: dict) -> dict:
    """Coerce any stored/posted document into the canonical shape. Junk is dropped rather than
    raised on: one bad row must not cost a run its whole goal list.
    """
    groups, seen_g = [], set()
    for g in raw.get("groups") or []:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id") or "")
        if not _GID_RE.match(gid) or gid in seen_g:
            continue
        seen_g.add(gid)
        groups.append({"id": gid, "name": str(g.get("name") or "").strip(),
                       "mode": g["mode"] if g.get("mode") in MODES else "all"})
    conditions = []
    for c in raw.get("conditions") or []:
        if not isinstance(c, dict) or not str(c.get("text") or "").strip():
            continue
        cid = str(c.get("id") or "")
        gid = str(c.get("group") or "")
        conditions.append({
            "id": cid if _ID_RE.match(cid) else "",
            "text": str(c["text"]).strip(),
            "status": c["status"] if c.get("status") in STATUSES else "open",
            "group": gid if gid in seen_g else "",
            "requires": [str(r) for r in (c.get("requires") or []) if _ID_RE.match(str(r))],
            "stage": str(c.get("stage") or "").strip(),
            "ts": str(c.get("ts") or ""),
            "note": str(c.get("note") or ""),
            "resolved_ts": str(c.get("resolved_ts") or ""),
            "resolved_run": str(c.get("resolved_run") or ""),
            # v2: the verifier's standing objection to a verdict the run re-asserted. The
            # model keeps the last word; the disagreement is kept beside it.
            "disputed": str(c.get("disputed") or ""),
        })
    # Every condition belongs to a group so evaluation has one shape; a document that named
    # none gets the default group, which is also what a simple flat list looks like.
    if any(not c["group"] for c in conditions) and not any(g["id"] == DEFAULT_GROUP
                                                           for g in groups):
        groups.insert(0, {"id": DEFAULT_GROUP, "name": "", "mode": "all"})
    for c in conditions:
        c["group"] = c["group"] or DEFAULT_GROUP
    return {"mode": raw["mode"] if raw.get("mode") in MODES else "all",
            "groups": groups, "conditions": conditions}


def _assign_ids(doc: dict, *, now: str) -> dict:
    """Stable ids: a well-formed incoming id is kept, a new row gets the next free number."""
    used = {c["id"] for c in doc["conditions"] if c["id"]}
    n = 1
    for c in doc["conditions"]:
        if not c["id"]:
            while f"s{n}" in used:
                n += 1
            c["id"] = f"s{n}"
            used.add(c["id"])
        c["ts"] = c["ts"] or now
    # a `requires` pointing at an id that does not exist is dropped: a dangling gate would
    # read as "blocked" forever, and the reader could never tell why
    ids = {c["id"] for c in doc["conditions"]}
    for c in doc["conditions"]:
        c["requires"] = [r for r in c["requires"] if r in ids and r != c["id"]]
    return doc


#: Written by the ENGINE at a finish (record_accounting), never by the user's PUT. A
#: whole-document save carries them forward from the store instead of taking them from the
#: request — otherwise every edit of a condition's text would erase the run's own conclusion.
ENGINE_OWNED = ("note", "resolved_ts", "resolved_run", "disputed")


def save(routine_dir: Path, doc: dict, *, now: str) -> dict:
    """Normalize + persist the USER's document, preserving the engine-owned fields of every
    condition that already exists. Returns what was written.
    """
    prior = {c["id"]: c for c in load(routine_dir)["conditions"] if c["id"]}
    out = _assign_ids(normalize(doc), now=now)
    for c in out["conditions"]:
        if (was := prior.get(c["id"])) is not None:
            for key in ENGINE_OWNED:
                c[key] = was[key]
    _store(routine_dir).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_store(routine_dir), out)
    return out


# ------------------------------------------------------------------------------- activity ----

def blocked_reason(cond: dict, by_id: dict, *, phase: str = "") -> str:
    """Why this OPEN condition is not live yet — "" when it is active.

    Kept as prose rather than a bool because it is shown verbatim in both the prompt and the
    panel: "waiting on s1" is actionable, "dormant" is not.
    """
    waiting = [r for r in cond["requires"]
               if (dep := by_id.get(r)) is not None and dep["status"] == "open"]
    if waiting:
        return "waiting on " + ", ".join(waiting)
    if cond["stage"] and phase and cond["stage"] != phase:
        return f"only in stage {cond['stage']}"
    if cond["stage"] and not phase:
        return f"only in stage {cond['stage']} (no stage recorded yet)"
    return ""


def active(doc: dict, *, phase: str = "") -> list[dict]:
    """The open conditions the run must account for RIGHT NOW — dormant ones excluded."""
    by_id = {c["id"]: c for c in doc["conditions"]}
    return [c for c in doc["conditions"]
            if c["status"] == "open" and not blocked_reason(c, by_id, phase=phase)]


# ----------------------------------------------------------------------------- evaluation ----

def evaluate(doc: dict) -> dict:
    """`{satisfied, groups: [{id, name, mode, satisfied, met, total}]}`.

    `satisfied` is None when the document sets no conditions at all — "no opinion", distinct
    from False, so nothing announces a goal the user never set.
    """
    live = [c for c in doc["conditions"] if c["status"] != "dropped"]
    rows = []
    for g in doc["groups"]:
        members = [c for c in live if c["group"] == g["id"]]
        met = [c for c in members if c["status"] == "met"]
        # an empty group expresses no requirement, under EITHER mode — a strict empty `any`
        # would let an emptied group block the job forever
        ok = True if not members else (
            len(met) == len(members) if g["mode"] == "all" else bool(met))
        rows.append({"id": g["id"], "name": g["name"], "mode": g["mode"],
                     "satisfied": ok, "met": len(met), "total": len(members)})
    if not live:
        return {"satisfied": None, "groups": rows}
    scored = [r for r in rows if r["total"]]
    overall = (all(r["satisfied"] for r in scored) if doc["mode"] == "all"
               else any(r["satisfied"] for r in scored))
    return {"satisfied": overall, "groups": rows}


# --------------------------------------------------------------------------------- prompt ----

def _line(cond: dict, by_id: dict, phase: str) -> str:
    mark = {"met": "✓", "dropped": "–"}.get(cond["status"], "○")
    why = blocked_reason(cond, by_id, phase=phase) if cond["status"] == "open" else ""
    tail = f"  ({why})" if why else ""
    return f"  {mark} [{cond['id']}] {cond['text']}{tail}"


def digest_section(routine_dir: Path, *, phase: str = "") -> str:
    """The always-visible prompt block (state_digest inlines it beside the plan).

    Renders the STRUCTURE, not a flat list: a run that cannot see that two conditions are an
    OR will treat them as an AND and work past the point the user meant it to stop.
    """
    doc = load(routine_dir)
    live = [c for c in doc["conditions"] if c["status"] != "dropped"]
    if not live:
        return ""
    by_id = {c["id"]: c for c in doc["conditions"]}
    verdict = evaluate(doc)
    joiner = "ALL of these groups" if doc["mode"] == "all" else "ANY of these groups"
    out = ["STOPPING CONDITIONS (state/stopping.json — the USER's meaning-level bounds on this "
           "job; the engine cannot judge them, you must). The job is DONE when "
           f"{joiner} is satisfied:"]
    for g in doc["groups"]:
        members = [c for c in live if c["group"] == g["id"]]
        if not members:
            continue
        label = f'"{g["name"]}" ' if g["name"] else ""
        out.append(f"{label}— {g['mode'].upper()} of:")
        out.extend(_line(c, by_id, phase) for c in members)
    act = active(doc, phase=phase)
    if act:
        out.append(
            "Your finish summary MUST account for each ACTIVE condition ("
            + ", ".join(c["id"] for c in act)
            + "): a line `[s<n>] met — <evidence>` or `[s<n>] unmet — <why>` per condition. "
              "A finish that skips one is rejected and costs a turn. Conditions marked "
              "waiting are NOT yours to account for yet — they become active when what they "
              "wait on is met.")
    if verdict["satisfied"]:
        out.append("EVERY stopping condition is now met — the job is DONE. Finish NOW with the "
                   "accounting; do not continue past it.")
    else:
        out.append('A met LIMIT condition ("only diagnose", "stop once X is verified") means '
                   "finish NOW rather than continue past it.")
    return "\n".join(out)


# ------------------------------------------------------------------- the accounting contract --

def unaccounted(summary: str, routine_dir: Path, *, phase: str = "") -> list[str]:
    """The ACTIVE condition ids a finish summary fails to mention — the deterministic check the
    finish gate enforces (semantics stay the model's). Dormant conditions are never demanded.
    """
    return [c["id"] for c in active(load(routine_dir), phase=phase)
            if f"[{c['id']}]" not in (summary or "")]


def read_accounting(summary: str) -> dict[str, tuple[str, str]]:
    """`{id: (met|unmet, note)}` parsed from a finish summary. Pure — the writer's other half,
    testable without touching disk.

    Scanned over the WHOLE summary rather than line by line, because models routinely put two
    entries on one line ("[s1] met — verified; [s2] met — no fix attempted"). Each note runs to
    the next entry or the end of its line, whichever comes first: unbounded, the first note
    swallows every entry after it on the line; unbounded across lines, it swallows the rest of
    the summary.
    """
    text = summary or ""
    hits = list(_ACCOUNT_RE.finditer(text))
    out: dict[str, tuple[str, str]] = {}
    for i, m in enumerate(hits):
        eol = text.find("\n", m.end())
        stop = len(text) if eol == -1 else eol
        if i + 1 < len(hits):
            stop = min(stop, hits[i + 1].start())
        out[m.group(1).lower()] = (m.group(2).lower(),
                                   text[m.end():stop].strip().strip(";,.").strip())
    return out


def record_accounting(routine_dir: Path, summary: str, *, run_id: str, now: str,
                      disputes: dict[str, str] | None = None) -> list[str]:
    """Stamp the model's verdict back into the store; returns the ids newly marked met.

    This is what makes every reader agree. Without it a condition sat at `open` forever however
    often a run reported it met, so the panel could only ever show a stale list — the reason
    D98's status column was dead until now.

    Only `met` transitions: `unmet` leaves the condition open (recording the reason, which is
    what the next run and the user actually want to read). `met` is STICKY — a later run does
    not silently reopen a goal the user has already been told is done; the user reopens it.

    `disputes` are the v2 verifier's standing objections to verdicts the run RE-ASSERTED after
    being challenged (engine/verifier.py). The verdict still lands — the model keeps the last
    word, because an engine that could veto it forever would just hang the run — but the
    objection is stored beside it so the panel and the user can see the two disagreed.
    """
    doc = load(routine_dir)
    verdicts = read_accounting(summary)
    if not verdicts:
        return []
    changed = []
    for c in doc["conditions"]:
        got = verdicts.get(c["id"])
        if got is None or c["status"] != "open":
            continue
        state, note = got
        c["note"] = note
        c["disputed"] = (disputes or {}).get(c["id"], "")
        if state == "met":
            c["status"] = "met"
            c["resolved_ts"] = now
            c["resolved_run"] = run_id
            changed.append(c["id"])
    _store(routine_dir).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_store(routine_dir), doc)
    return changed
