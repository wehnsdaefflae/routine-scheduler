"""What a supervisor is told the run is FOR — its goal and its stopping conditions.

Split out of `ladder.py` (F803) because the two readers that produced them were reading a
config object that cannot carry what they asked it for, and the explanation of why is longer
than the code.

## The defect this module exists to end

`ladder._goal` probed `ctx.routine` for `instruction`, then `task`, then `name`; `_stopping`
probed it for `done_when` and `finish_line`. **`RoutineConfig` declares none of those four**
(`config/routine.py`), and `_Config` sets `extra="ignore"` (`config/base.py`), so an undeclared
key cannot survive as an attribute either. Measured on the live tree at 0.474.1:

    >>> RoutineConfig(slug="self-audit", name="Self audit", dir=...,
    ...               instruction="REAL GOAL TEXT", done_when=["d1 ..."])
    >>> ladder._goal(ctx)      -> 'Self audit'     # the display label, via the `name` fallback
    >>> ladder._stopping(ctx)  -> []               # every time, for every run

So every rung ever dispatched judged drift against a two-word routine name, with an empty list
of stopping conditions — while `_goal`'s own docstring promised "the instruction it was started
with, never a paraphrase: a supervisor judging drift against a summary of the goal cannot see
drift INTO that summary". A display name is the most extreme summary available.

It survived a green suite because `tests/test_ladder_trigger.py`'s `_Routine` fake carries
exactly those three forbidden attributes, so the suite proved the readers against a shape
production never produces. That is the same class as R2348/R2352 in this same file, where the
fake said `subs` and so did `ladder.py`, and every rung on every run died of an AttributeError
for months behind green tests. The `getattr` defaults were not a safety net: they are what
absorbed the mismatch silently. There are none here — the sources below either exist or the
reader returns nothing, visibly.

## Where the real values live

- the GOAL: a top-level run's goal is its RECIPE, not a transient string. `instruction.md` is a
  compile seed that real routines do not persist (`engine/runtime.py` reads it as `""`), so
  `loop.instruction` is non-empty only for a CHILD run (its `prompt`) and a conversation. For a
  scheduled run the honest answer is the recipe's own `## Done when` — which is exactly what the
  FINISH is held to (`engine/finishgate.py`).
- the STOPPING conditions: `donewhen.read(dir)` for the recipe's `## Done when`,
  `finishline.load(dir)`/`open_outcomes` for the routine's finish line, and `goals.owed(...)`
  for what a person asked that no run has met yet. One composition, the same three sources the
  accounting uses, so the supervisor and the finish gate can never disagree about what this run
  is held to.
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
