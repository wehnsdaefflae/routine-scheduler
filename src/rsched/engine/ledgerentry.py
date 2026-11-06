"""The engine's LEDGER entry for one finished routine run leg (P1, `rsched/runledger.py`).

Called once from the close-out every ending shares (`engine/loopend.finish_run`), after
`result.md` is written and before the autocommit, so the entry is committed with the run it
records. Built ONLY from what the run already produced — the summary as result.md has it, the
accounting the finish gate stored on the context, the revision lines the summary named — so the
record costs the run no turn and says nothing the run did not say.

Who keeps one: a depth-0 ROUTINE run. A child's record is its hand-back to the parent; a
conversation's is its own transcript, which every reply already replays (and which is why a
conversation is no longer seeded a LEDGER.md at all — nothing read one but the digest, and 134
of 138 live ones still held only their seed line); a detached background task is a one-shot
brief that ends by delivering its result. `recipeproposal.asks_for_revisions` is that same
predicate — a run with a recipe of its own — so it is read rather than restated.

Best-effort like every store write at an ending: a failure becomes a transcript `error`, never a
failed run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import runledger
from ..paths import read_jsonl
from . import recipeproposal

if TYPE_CHECKING:
    from .run_context import RunContext


def _leg(loop) -> int:
    """Which leg of its run this is: 1 for a fresh run, otherwise the number of `finish`
    events its transcript now holds (this leg's included) — at least 2, because a resumed leg
    whose predecessor was killed before it finished left no finish behind.
    """
    if not getattr(loop, "resume", False):
        return 1
    rows = read_jsonl(loop.ctx.run_dir / "transcript.jsonl")
    return max(2, sum(1 for r in rows if r.get("type") == "finish"))


def entry(ctx: RunContext, *, status: str, summary: str, leg: int = 1) -> str:
    """The entry's text, heading first (the shape `rsched/runledger.py` documents)."""
    turns = f"{ctx.turn} turn{'' if ctx.turn == 1 else 's'}"
    leg_part = f"leg {leg} · " if leg > 1 else ""
    parts = [f"### {ctx.run_ts} · {leg_part}{status} · {turns}",
             runledger.guard_body(summary.strip()) or "(no summary)"]
    if ctx.accounting:
        parts.append("Accounting:\n" + "\n".join(
            f"- {runledger.guard_body(' '.join(str(a).split()))}" for a in ctx.accounting))
    if revisions := recipeproposal.extract(summary):
        parts.append("Recipe revisions:\n" + "\n".join(f"- {r}" for r in revisions))
    return "\n\n".join(parts) + "\n"


def record(loop, status: str, summary: str) -> None:
    """Append this leg's entry to the routine's LEDGER.md (and rotate it) — routine runs only."""
    ctx = loop.ctx
    if not recipeproposal.asks_for_revisions(ctx):
        return
    try:
        text = entry(ctx, status=status, summary=summary, leg=_leg(loop))
        runledger.append(ctx.routine.dir, text, title=ctx.routine.name or ctx.routine.slug)
    except OSError as exc:
        ctx.transcript.event("error", {"where": "ledger", "error": str(exc)})
