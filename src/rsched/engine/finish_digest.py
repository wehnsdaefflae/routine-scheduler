"""The digest's FINISH block — the run's goals, the finish line, the accounting contract and what
the last run left unmet. Prompt text only; `docs/prompt-anatomy.md` pins the wording and
`tests/test_prompt_anatomy.py` fails on drift.

Said ONCE, in the state digest at boot: no per-turn meter, no countdown. A signal repeated into
every turn becomes what runs optimize against — the lesson of the 85% budget warning — so the
run reads where its routine stands at the start and answers for it at the end.
"""

from __future__ import annotations

from pathlib import Path

from ..paths import read_json
from . import accounting, donewhen, finishline
from . import goals as goals_mod

_JUDGE = {"run": "the run proves it", "you": "the operator decides", "date": "on its date"}


def _outcome(o: dict) -> str:
    mark = "✓" if o["status"] == "met" else "○"
    when = f" {o['date']}" if o["judge"] == "date" else ""
    line = f"  {mark} [{o['id']}] {o['text']} — {_JUDGE[o['judge']]}{when}"
    if o["status"] == "open" and o["distance"]:
        line += f" · last distance: {o['distance']}"
    return line


def _last_unmet(routine_dir: Path) -> list[str]:
    runs = sorted((routine_dir / "runs").glob("*/status.json")) if (
        routine_dir / "runs").is_dir() else []
    for status in reversed(runs):
        doc = read_json(status)
        if isinstance(doc, dict) and doc.get("accounting"):
            return accounting.unmet_residuals(doc["accounting"])
    return []


def digest_section(routine_dir: Path, *, goals: list[dict] | None = None,
                   conversation: bool = False) -> str:
    """The block, or "" for a run that owes nothing and was asked nothing.

    The run's GOALS come first (engine/goals.py). A routine run whose goals began with the
    operator's brief answers for them instead of its recipe's Done when; a CONVERSATION owes
    neither a Done when nor a finish line — only its goals, and only on a reply it declares
    final.
    """
    ledger = goals or []
    out: list[str] = []
    if goal_sec := goals_mod.digest_section(ledger, conversation=conversation):
        out.append(goal_sec)
    if conversation:
        if goals_mod.open_goals(ledger):
            out.append("A reply with `final: true` carries `accounting` with `b<n> met: "
                       "<evidence>` for every open goal, checked against this conversation's "
                       "own transcript; one with `final: false` owes nothing and the goals stay "
                       "open.")
        return "\n".join(out)
    briefed = any(g.get("source") == "brief" for g in ledger)
    open_goals = goals_mod.open_goals(ledger)
    doc = finishline.load(routine_dir)
    done = [] if briefed else donewhen.read(routine_dir)
    if doc["outcomes"] or doc["until"]:
        out.append("FINISH LINE (the operator's — when this ROUTINE is done for good; reaching "
                   "it stops the routine and asks the operator to confirm):")
        out += [_outcome(o) for o in doc["outcomes"]]
        if doc["until"]:
            out.append(f"  The routine stops after {doc['until']} whatever else is reached.")
    owed = finishline.open_outcomes(doc)
    if open_goals or done or owed:
        what = []
        if open_goals:
            what.append("one per open goal — `b<n> met: <evidence>` or `b<n> unmet: <what "
                        "remains>`")
        if done:
            what.append("one entry per line of your recipe's `## Done when` — `d<n> met: "
                        "<evidence>`, `d<n> unmet: <what remains>`, `d<n> not due: <how that "
                        "was established>`")
        if owed:
            what.append("one per open finish-line outcome — `g<n> distance: <what remains>`, "
                        "or `g<n> met: <evidence>` for an outcome the run proves (never for one "
                        "the operator decides)")
        out.append("At your finish, the `accounting` field carries " + "; and ".join(what)
                   + ". A `met` claim is checked against this run's own transcript.")
    if residuals := _last_unmet(routine_dir):
        out.append("THE LAST RUN LEFT UNMET (its own words — pick these up first):\n"
                   + "\n".join(f"  - {r}" for r in residuals))
    return "\n".join(out)
