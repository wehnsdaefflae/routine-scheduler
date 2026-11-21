"""What a supervisor is told the run is FOR — its goal and its stopping conditions.

`RoutineConfig` carries no instruction, Done-when or finish line (and `extra="ignore"` drops
undeclared keys), so these are read from where they really live (F803) — never from a
`getattr` default, which once had every rung judge drift against the routine's display name.
A supervisor judging drift against a summary of the goal cannot see drift INTO that summary.

## Where the real values live

- the GOAL: `loop.instruction` is non-empty only for a CHILD run and a conversation; a
  scheduled run's goal is its recipe's `## Done when` — what the FINISH is held to.
- the STOPPING conditions: `donewhen.read`, `finishline.open_outcomes` and `goals.owed` — the
  same three sources the accounting uses, so the supervisor and the finish gate never disagree.
"""

from __future__ import annotations

from . import donewhen, finishline, goals


def goal(loop) -> str:
    """The run's goal VERBATIM — never a paraphrase, and never its display label.

    A child run and a conversation carry the real text on `loop.instruction` (the `prompt` they
    were started with). A top-level routine run has no such string by design: its goal is the
    recipe, so the lines it will be held to at the finish ARE the goal, in recipe order.
    """
    if instruction := str(getattr(loop, "instruction", "") or "").strip():
        return instruction
    rows = donewhen.read(loop.ctx.routine.dir)
    if not rows:
        return ""
    lines = "\n".join(f"- {row['id']} — {row['text']}" for row in rows)
    return f"This run is held to its recipe's own Done-when lines:\n{lines}"


def stopping(loop) -> list[str]:
    """What would make this run DONE: the recipe's Done-when lines, the routine's open
    finish-line outcomes, and the open goals a person asked of it — the three sources
    `finishgate` composes.

    Each line keeps its id, because a directive that names `d2` or `b1` is actionable and one
    that paraphrases the outcome is the summary this mechanism exists to avoid.
    """
    ctx = loop.ctx
    out = [f"{row['id']} — {row['text']}" for row in donewhen.read(ctx.routine.dir)]
    out += [f"{o['id']} ({o['judge']} judges) — {o['text']}"
            for o in finishline.open_outcomes(finishline.load(ctx.routine.dir))]
    out += [f"{g['id']} (asked by a person) — {g['text']}"
            for g in goals.owed(list(getattr(ctx, "goals", None) or []))]
    return out
