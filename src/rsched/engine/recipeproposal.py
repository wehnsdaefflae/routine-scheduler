"""A finishing run's own RECIPE REVISIONS — asked by the engine, filed as a proposal (D200).

The operator's requirement (2026-10-19) was *"In the future I want recipes to suggest a change
in their recipes themselves when they finish"*, and his answer to where it lives (D200): **the
engine finish seam — every finishing run is asked for its own recipe revisions, like D195's
known-flaw prompt**, over a general rule (which reaches only routines whose config holds it) and
over per-recipe text.

Modelled on D195 in both halves, because that is what the answer names:

- **The ASK is engine prose a finishing run cannot miss.** D195 put its honesty instruction in
  `engine/harness.py`'s message 0 — never evicted by `window.clamp_to_cap`, unconditional, no
  capability or kind projection can drop it. This ask sits one section further down, in the
  FINISH block of the state digest (`engine/finish_digest.py`): the same never-evicted system
  message, and the block that already states what a finish owes. It is NOT in the harness, for
  one measured reason: the harness, the schema and the example are the always-on ENGINE CONTRACT
  held to a SHRINK-ONLY budget (`tests/prompt_budget.json`, an equality assert whose `--update`
  refuses to raise), so always-on prose there is paid for by cutting always-on prose elsewhere —
  and the digest's finish block is where this belongs anyway, said once per run.
- **The ANSWER rides the run record.** D195's flaw answer is prose in the `say` and the summary;
  nothing new carries it. So does this one: one line per revision in the finish summary, each
  beginning `RECIPE REVISION:`. No new action field, therefore no new always-on schema prose
  (`ACTION_SCHEMA` is `additionalProperties: false`, so an undeclared field would be silently
  dropped, and declaring one grows the budgeted contract in a file at its size ceiling).

**Why a PROPOSAL and not an edit** — the constraint the decision row records: a scheduled run
holds no `write_recipe` (`grants.GATED_KINDS`), and vodafone-outage-claims' own `main.md` d17
asked exactly this of itself on 2026-10-20 and the engine REFUSED the edit. So the lines a run
writes are filed as ONE standing proposal on the operator's Decisions page
(`rsched/pending.py`, the surface that already shows what a run proposes), which he applies —
approving ARMS the revise flow that already exists (`engine/revise.py`): the proposing run is
resumed with recipe self-write granted for that leg, which is the "grant the capability at that
moment" half of the same constraint.

A run that ALREADY holds recipe self-write (the recipe-authoring capability, or a revise leg the
operator asked for) is asked the same question and files no card: it can make the edit, the
autocommit carries it, and a proposal to do what has been done would be a second copy of the
same change. That is the one branch here — the seam neither breaks nor duplicates self-editing.
"""

from __future__ import annotations

import logging

from .. import pending

log = logging.getLogger("rsched.recipeproposal")

KIND = "recipe-revision"

#: The line prefix the ask asks for, and the one thing `extract` keys on. Upper case and
#: followed by a colon so it cannot collide with prose a summary would write anyway.
MARKER = "RECIPE REVISION:"

#: How many revision lines one proposal carries. A run with more to say than this is writing a
#: redesign, not a revision, and the card has to stay readable at a glance.
MAX_LINES = 8


def digest_ask(*, unlocked: bool) -> str:
    """The engine's ask, for the digest's FINISH block — every finishing routine run reads it.

    Two wordings, by what the run may actually DO about its own recipe, because a run told to
    propose a change it is allowed to make would route around its own capability, and a run told
    to edit a recipe the engine will refuse to write is told a falsehood it will obey (the F165
    failure, one layer along).
    """
    if unlocked:
        return ("RECIPE REVISIONS: before you finish, say whether your own recipe should "
                "change — it IS writable to you this run, so make the edit and say in the "
                "summary what you changed and what this run showed. A recipe that served this "
                "run as written needs no change.")
    return ("RECIPE REVISIONS: before you finish, say whether your own recipe should change — "
            f"one line in the summary per revision, beginning `{MARKER}` and naming the file, "
            "the change, and what THIS run showed. You hold no recipe-writing capability, so "
            "those lines are filed as one proposal on the operator's Decisions page; approving "
            "it resumes this run with your recipe writable, so the change is made where it was "
            "reasoned. A recipe that served this run as written needs no line.")


def extract(summary: str) -> list[str]:
    """The revision lines a summary carries — what follows `MARKER` on each, trimmed.

    Tolerant on the left of the marker (a run writes `- RECIPE REVISION: …` inside a list as
    readily as a bare line) and strict about the marker itself, so the prose around it is never
    mistaken for a proposal. Empty lines are dropped and the list is capped at `MAX_LINES`.
    """
    out: list[str] = []
    for raw in (summary or "").splitlines():
        head, sep, rest = raw.partition(MARKER)
        if not sep or head.strip(" \t-*>#"):
            continue
        if text := rest.strip():
            out.append(text)
    return out[:MAX_LINES]


def maybe_propose(ctx, summary: str) -> str:
    """From the close-out every ending shares (`engine/loopend.finish_run`): file the one card
    for the revisions this run's summary named. Returns its proposal id, or "".

    Best-effort and deliberately narrow, in the shape `goalreached.maybe_propose_retirement`
    established: a store write must never turn a finished run into a failed one, a CHILD run has
    no recipe of its own (its task is its parent's brief), and a run that may write its own
    recipe files nothing because it could simply make the edit.
    """
    if ctx.depth > 0:
        return ""
    grants = getattr(ctx, "grants", None)
    if grants is not None and getattr(grants, "recipe_unlocked", False):
        return ""
    lines = extract(summary)
    if not lines:
        return ""
    slug = ctx.routine.slug
    first = lines[0] if len(lines) == 1 else f"{lines[0]} (+{len(lines) - 1} more)"
    try:
        rec = pending.queue(
            ctx.server.routines_home, kind=KIND, routine=slug, run_id=ctx.run_id,
            fields={"lines": lines, "run_id": ctx.run_id},
            summary=f"{ctx.routine.name or slug} proposes a revision to its own recipe — "
                    f"{first}")
    except OSError as exc:
        log.warning("recipe-revision: could not queue %s's proposal: %s", slug, exc)
        return ""
    # `stopping_update` is the event the finish already files about what a run leaves standing
    # (engine/goalreached.py uses it for the retirement card) — so this records the proposal
    # without touching the transcript's event vocabulary, which five surfaces read.
    ctx.transcript.event("stopping_update", {"recipe_proposal": rec["id"],
                                             "lines": lines, "run_id": ctx.run_id})
    log.info("recipe-revision: %s proposed %d revision(s) to its recipe, card %s",
             slug, len(lines), rec["id"])
    return str(rec["id"])
