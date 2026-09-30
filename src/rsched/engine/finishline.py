"""The FINISH LINE — when a routine is done for good (`state/finish-line.json`).

A routine either runs until the operator switches it off, or it has a finish line: zero or more
OUTCOMES, all of which must be reached, plus an optional `until` date after which it stops
either way ("submitted — or the deadline has passed"). Every outcome names its JUDGE, because
the three kinds of "done" are decided by three different parties:

- `date` — the calendar. The scheduler checks it itself; no model and no run is involved.
- `run`  — the run proves it: it claims `met` with evidence at its finish, the claim is checked
  against its own transcript (`verifier.py`), and a Decisions card confirms the retirement.
- `you`  — only the operator can judge it. A run reports the DISTANCE remaining and may never
  claim it met; the operator's own click on the routine page decides.

Reaching the finish line RETIRES the routine; nothing about that writes config: the
scheduler derives `retired` from this document (`registry.RoutineInfo.retired`) and one
`goal-reached` card on the Decisions page makes it permanent (`goalreached.py`). So the document
is the OPERATOR's: the web is its only writer for what a person sets; no run may write the
file at all (`fileops` seals it by path). A run's contribution is data the engine stamps from
the run's finish accounting — a distance line per open outcome and `met` for an outcome the run
proves — never an edit.

What one finished run delivers is a different question with a different owner and lives in the
recipe (`donewhen.py`); what a run must never do is guarded before the action, by permissions,
rules and reminders. This document holds only the end of the routine.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from ..paths import atomic_write_json, read_json

FILE = Path("state") / "finish-line.json"
JUDGES = ("run", "you", "date")
STATUSES = ("open", "met")
_ID_RE = re.compile(r"^g\d+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Written by the ENGINE from a run's finish accounting, never by the operator's save — a save
#: carries them forward, so editing an outcome's text never erases what the runs reported.
ENGINE_OWNED = ("distance", "distance_run", "distance_ts", "met_run", "met_ts", "evidence",
                "disputed")


def path(routine_dir: Path) -> Path:
    return Path(routine_dir) / FILE


def load(routine_dir: Path) -> dict:
    """The document, normalized to `{outcomes, until}`. A missing or unreadable file is the empty
    finish line: a routine that runs until it is switched off.
    """
    raw = read_json(path(routine_dir))
    return normalize(raw if isinstance(raw, dict) else {})


def normalize(raw: dict) -> dict:
    outcomes = []
    for o in raw.get("outcomes") or []:
        if not isinstance(o, dict) or not str(o.get("text") or "").strip():
            continue
        judge = o["judge"] if o.get("judge") in JUDGES else "you"
        date = str(o.get("date") or "")
        outcomes.append({
            "id": str(o["id"]) if _ID_RE.match(str(o.get("id") or "")) else "",
            "text": str(o["text"]).strip(),
            "judge": judge,
            "date": date if judge == "date" and _DATE_RE.match(date) else "",
            "status": o["status"] if o.get("status") in STATUSES else "open",
            **{k: str(o.get(k) or "") for k in ENGINE_OWNED},
        })
    until = str(raw.get("until") or "")
    return {"outcomes": outcomes, "until": until if _DATE_RE.match(until) else ""}


_WORDS_RE = re.compile(r"^\s*(run|you|\d{4}-\d{2}-\d{2})\s*:\s*(\S.*)$", re.IGNORECASE)
_UNTIL_RE = re.compile(r"^\s*until\s+(\d{4}-\d{2}-\d{2})\s*$", re.IGNORECASE)


def from_words(entries: list[str]) -> dict:
    """A finish line from the lines a creation conversation settled with the person:
    `run: <outcome>` (the run proves it), `you: <outcome>` (they decide), `YYYY-MM-DD:
    <outcome>` (reached on that date), `until YYYY-MM-DD` (stop after that date either way).
    A line in no form is an outcome only the person can judge — their words, kept.
    """
    outcomes, until = [], ""
    for raw in entries:
        if m := _UNTIL_RE.match(str(raw)):
            until = m[1]
        elif m := _WORDS_RE.match(str(raw)):
            head = m[1].lower()
            judge = head if head in ("run", "you") else "date"
            outcomes.append({"text": m[2].strip(), "judge": judge,
                             "date": m[1] if judge == "date" else ""})
        elif str(raw).strip():
            outcomes.append({"text": str(raw).strip(), "judge": "you"})
    return normalize({"outcomes": outcomes, "until": until})


def problems(doc: dict) -> list[str]:
    """What an operator's document gets wrong, as sentences — empty when it is sound."""
    out = [f"{o['id'] or o['text'][:40]}: a date outcome needs its date (YYYY-MM-DD)"
           for o in doc["outcomes"] if o["judge"] == "date" and not o["date"]]
    if len(doc["outcomes"]) > 12:
        out.append("at most 12 outcomes — a finish line nobody can read is not a finish line")
    return out


def assign_ids(outcomes: list[dict]) -> None:
    """Give every outcome without an id the next free `g<n>`, in order and in place — so a
    document written without ids reads the same as the one its save stores.
    """
    used = {o["id"] for o in outcomes if o["id"]}
    n = 1
    for o in outcomes:
        if not o["id"]:
            while f"g{n}" in used:
                n += 1
            o["id"] = f"g{n}"
            used.add(o["id"])


def save(routine_dir: Path, doc: dict, *, now: str) -> dict:
    """Persist the OPERATOR's document: new outcomes get the next free id, the engine-owned
    fields of every outcome that already exists are carried forward and a new outcome starts
    with none — whatever the caller passed, a save can neither invent a verdict nor erase one.
    """
    prior = {o["id"]: o for o in load(routine_dir)["outcomes"] if o["id"]}
    out = normalize(doc)
    assign_ids(out["outcomes"])
    for o in out["outcomes"]:
        was = prior.get(o["id"])
        for key in ENGINE_OWNED:
            o[key] = was[key] if was is not None else ""
        if was is not None and o["status"] == "open" and was["status"] == "met" \
                and o["judge"] == "run":
            o["met_run"] = o["met_ts"] = ""
        if o["status"] == "met" and not o["met_ts"]:
            o["met_ts"] = now                     # the operator's own tick
    _write(routine_dir, out)
    return out


def _write(routine_dir: Path, doc: dict) -> None:
    target = path(routine_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, doc)


def today() -> _dt.date:
    return _dt.datetime.now().astimezone().date()


def is_reached(outcome: dict, on: _dt.date) -> bool:
    if outcome["judge"] == "date":
        return bool(outcome["date"]) and _dt.date.fromisoformat(outcome["date"]) <= on
    return outcome["status"] == "met"


def open_outcomes(doc: dict) -> list[dict]:
    """The outcomes a run accounts for at its finish: those a run or the operator judges and
    nobody has reached yet. A date outcome is the calendar's business.
    """
    return [o for o in doc["outcomes"] if o["judge"] != "date" and o["status"] == "open"]


def reached(doc: dict, on: _dt.date | None = None) -> str:
    """Why this routine is finished — "" while it is not. The `until` date ends it whatever the
    outcomes say; otherwise every outcome must be reached (a routine with no outcomes and no
    `until` runs until it is switched off).
    """
    on = on or today()
    if doc["until"] and _dt.date.fromisoformat(doc["until"]) < on:
        return f"its end date {doc['until']} has passed"
    if doc["outcomes"] and all(is_reached(o, on) for o in doc["outcomes"]):
        return "every outcome of its finish line is reached"
    return ""


def reached_by_calendar(doc: dict, on: _dt.date | None = None) -> str:
    """Why this finish line stays reached whoever reopens it — its `until` date has passed, or
    every outcome is a date whose day has come. "" when reopening would put the routine back.
    """
    return reached({**doc, "outcomes": [{**o, "status": "open"} for o in doc["outcomes"]]}, on)


def goal_reached(routine_dir: Path, on: _dt.date | None = None) -> bool:
    """Is this routine finished for good? The one question the scheduler and the retirement card
    both ask, so neither re-derives it.
    """
    return bool(reached(load(routine_dir), on))


def record(routine_dir: Path, verdicts: dict[str, tuple[str, str]], *, run_id: str, now: str,
           disputes: dict[str, str] | None = None) -> list[str]:
    """Stamp a run's finish accounting onto the open outcomes; returns the ids newly met.

    `distance` is kept per outcome, so the next run, the routine header and the dashboard read
    where the routine stands. `met` lands only on an outcome the RUN judges — the accounting
    refuses the claim on any other before it gets here — and it stays met: the operator reopens
    it, a later run does not.
    """
    doc = load(routine_dir)
    newly = []
    for o in doc["outcomes"]:
        got = verdicts.get(o["id"])
        if got is None or o["status"] != "open" or o["judge"] == "date":
            continue
        verdict, note = got
        if verdict == "met" and o["judge"] == "run":
            o.update(status="met", met_run=run_id, met_ts=now, evidence=note,
                     disputed=(disputes or {}).get(o["id"], ""))
            newly.append(o["id"])
        elif verdict == "distance":
            o.update(distance=note, distance_run=run_id, distance_ts=now)
    _write(routine_dir, doc)
    return newly


def reopen(routine_dir: Path) -> list[str]:
    """"Not yet — keep going": every met outcome goes back to open, evidence kept. Returns the
    ids. A finish line that ended by DATE cannot be reopened this way; its date is edited.
    """
    doc = load(routine_dir)
    reopened = []
    for o in doc["outcomes"]:
        if o["status"] == "met":
            o["status"] = "open"
            reopened.append(o["id"])
    if reopened:
        _write(routine_dir, doc)
    return reopened
