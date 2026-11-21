"""The digest's FINISH block — the run's goals, the finish line, the accounting contract and what
the last run left unmet. Prompt text only; `docs/prompt-anatomy.md` pins the wording and
`tests/test_prompt_anatomy.py` fails on drift.

Said ONCE, in the state digest at boot: no per-turn meter, no countdown — a signal repeated
into every turn becomes what runs optimize against.
"""

from __future__ import annotations

from pathlib import Path

from ..paths import read_json
from . import accounting, donewhen, finishline, recipeproposal
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
                   conversation: bool = False, recipe_unlocked: bool = False,
                   recipe_ask: bool = True) -> str:
    """The block — never empty for a routine run, which is always asked for its own recipe
    revisions (D200); "" for a conversation that owes nothing and was asked nothing.

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
                        "remains>`, or, when its next move is not yours, `b<n> waits on <who>: "
                        "<the act it waits on>` — `operator` (it becomes his Decisions card), "
                        "`external` (a release, a reply, a date), or the slug of the routine "
                        "that owes it (tell that routine with an addressed `report`)")
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
    # D200: every finishing ROUTINE run is asked for its own recipe revisions — here, not in
    # the shrink-only harness contract, because this block already says what a finish owes.
    # `recipe_ask` is `recipeproposal.asks_for_revisions(ctx)` (false for a child, conversation
    # or detached task); the same predicate governs the card, so the seams cannot disagree.
    if recipe_ask:
        out.append(recipeproposal.digest_ask(unlocked=recipe_unlocked))
        # the process-overhead invariant reaches the run beside the recipe-revision ask, the
        # one place it can act on it (readmodels/overhead_reading.py)
        if line := _overhead_line(routine_dir):
            out.append(line)
    return "\n".join(out)


def _overhead_line(routine_dir: Path) -> str:
    """One line when this routine's runs are over the process-overhead bar, else ""."""
    from ..readmodels import overhead_reading

    r = overhead_reading.reading(routine_dir.parent, routine_dir.name, routine_dir)
    if r is None or not r["over"]:
        return ""
    # Names the measured causes: naming only bookkeeping, runs judged their recipe fine.
    return (f"PROCESS OVERHEAD: for this routine, {overhead_reading.describe(r)} rather than its "
            "task. The usual causes: reading every rule or stage up front instead of at its "
            "moment, one file or memory note per turn where one batched read serves, re-reading "
            "a spilled output a tighter command would not have spilled, records the task does "
            "not need, and actions that fail or are refused. Make your recipe revisions the ones "
            "that remove what THIS run spent that way, naming the turns.")
