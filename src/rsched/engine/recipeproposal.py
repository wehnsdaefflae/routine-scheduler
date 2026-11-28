"""A finishing run's own RECIPE REVISIONS — asked by the engine, filed as a proposal (D200).

Every finishing run is asked, at the engine's finish seam (modelled on D195's known-flaw prompt),
whether its own recipe should change:

- **The ASK** sits in the FINISH block of the state digest (`engine/finish_digest.py`): a
  never-evicted system message, said once per run. Not in the harness, whose always-on engine
  contract is held to a shrink-only budget (`tests/prompt_budget.json`).
- **The ANSWER rides the run record**: one line per revision in the finish summary, each
  beginning `RECIPE REVISION:`. No new action field, so no new always-on schema prose.

**Why a PROPOSAL and not an edit**: a scheduled run holds no `write_recipe`
(`grants.GATED_KINDS`). The lines are filed as ONE proposal on the operator's Decisions page
(`rsched/pending.py`); approving ARMS the existing revise flow (`engine/revise.py`), resuming the
proposing run with recipe self-write for that leg.

A run that ALREADY holds recipe self-write is asked the same question and files no card: it can
make the edit itself, and a proposal would duplicate it.
"""

from __future__ import annotations

import logging
import re

from .. import pending

log = logging.getLogger("rsched.recipeproposal")

KIND = "recipe-revision"

#: The line prefix the ask asks for, and the one thing `extract` keys on. Upper case and
#: followed by a colon so it cannot collide with prose a summary would write anyway.
MARKER = "RECIPE REVISION:"

#: What the event's `revisions` says when the run ANSWERED the ask and named no revision. A
#: token, not a sentence: a reader counting answered asks matches it, and the three states a
#: finishing run can leave are then `<a card id>` / `NONE` / no event at all.
NONE = "none"

#: How many revision lines one proposal carries. A run with more to say than this is writing a
#: redesign, not a revision, and the card has to stay readable at a glance.
MAX_LINES = 8

#: A revision VERDICT that says there is NO revision (R3086), after punctuation and emphasis
#: are stripped. Deliberately a closed set of wordings runs actually wrote, not a pattern:
#: guessing at "looks empty" would drop changes the operator needs to see.
_NULL = frozenset({
    "none", "no change", "no changes", "nothing", "not needed", "n/a", "na",
    "none needed", "no revision", "no revisions", "no revision needed",
    "no revisions needed", "none this run", "no change needed", "no changes needed",
    "nothing to change", "nothing to add", "unchanged", "recipe unchanged",
    "no recipe change", "no recipe changes",
})


#: What ends a revision line's VERDICT clause. The null set is matched against what stands
#: BEFORE the first of these, never the whole line: a complying run writes its verdict AND its
#: reason (`none — the recipe served this run as written; …`), while a real revision's opening
#: clause names the FILE it would change. A full stop ends the clause only where it ends a
#: sentence, not inside a file name like `main.md`.
_CLAUSE_END = re.compile(r"—|–|;|:|,| - |\.(?!\w)")
#: A null verdict may name WHERE nothing changes (`none needed in main.md/stages/`) and stays
#: null only while that scope is nothing but files and recipe words.
_SCOPED = re.compile(r"\s+(?:in|to|for)\s+")
_SCOPE_WORDS = frozenset({"the", "my", "its", "this", "any", "recipe", "main", "stages",
                          "stage", "modules", "module", "file", "files", "and", "or"})
_SCOPE_FILE = re.compile(r"[\w.-]+\.(?:md|ya?ml)")


def _verdict(text: str) -> str:
    """A revision line's opening clause, normalised the way `_NULL` is written."""
    found = _CLAUSE_END.search(text)
    return text[:found.start() if found else len(text)].strip(" \t.!*_`-—–").casefold()


def _is_null(verdict: str) -> bool:
    """Does this opening clause say there is NO revision — bare, or scoped to the recipe?"""
    return verdict in _NULL or any(
        verdict[:at.start()] in _NULL and all(
            word in _SCOPE_WORDS or _SCOPE_FILE.fullmatch(word)
            for word in re.split(r"[\s/,&+`]+", verdict[at.end():]) if word)
        for at in _SCOPED.finditer(verdict))


def asks_for_revisions(ctx) -> bool:
    """Whether THIS run is asked for its own recipe revisions at all — the ONE predicate both
    seams read, so the ask and the card can never disagree about who has a recipe.

    A run is asked only when it HAS a recipe of its own to revise. Three kinds do not:

    * a CHILD run (`ctx.depth > 0`) — its task is its parent's brief;
    * a CONVERSATION — its "recipe" is the shared `converse` pattern, not its own document;
    * a DETACHED BACKGROUND TASK — a one-shot brief with no recipe to edit.

    A predicate over the run's KIND rather than a flag at each seam.
    """
    from . import runkind
    return (ctx.depth == 0 and not runkind.is_conversation(ctx)
            and not runkind.is_detached_run(ctx))


def digest_ask(*, unlocked: bool) -> str:
    """The engine's ask, for the digest's FINISH block — every finishing routine run reads it.

    Two wordings, by what the run may actually DO: told to propose a change it may make, it
    routes around its capability; told to edit a recipe the engine refuses, it obeys a falsehood.
    """
    if unlocked:
        return ("RECIPE REVISIONS: before you finish, say whether your own recipe should "
                "change — it IS writable to you this run, so make the edit and say in the "
                "summary what you changed and what this run showed. A recipe that served this "
                "run as written needs no change.")
    return ("RECIPE REVISIONS: before you finish, say whether your own recipe should change — "
            f"one line in the summary per revision, beginning `{MARKER}` and naming the file, "
            "the change, and what THIS run showed — the change a general rule with its reason, "
            "not this run's incident and no dates. You hold no recipe-writing capability, so "
            "those lines are filed as one proposal on the operator's Decisions page; approving "
            "it resumes this run with your recipe writable, so the change is made where it was "
            "reasoned. A recipe that served this run as written needs no line.")


def extract(summary: str) -> list[str]:
    """The revision lines a summary carries — what follows `MARKER` on each, trimmed.

    Tolerant on the left of the marker (a run writes `- RECIPE REVISION: …` inside a list as
    readily as a bare line) and strict about the marker itself, so the prose around it is never
    mistaken for a proposal. Empty lines are dropped and the list is capped at `MAX_LINES`.

    A NULL REVISION FILES NOTHING (R3086): a run may SAY its recipe needed no change
    (`RECIPE REVISION: none.`), and that must not become a card. Only a verdict that says
    *there is no revision* is dropped: "none of the stages name the cursor" survives.
    """
    out: list[str] = []
    for raw in (summary or "").splitlines():
        head, sep, rest = raw.partition(MARKER)
        if not sep or head.strip(" \t-*>#"):
            continue
        text = rest.strip()
        if text and not _is_null(_verdict(text)):
            out.append(text)
    return out[:MAX_LINES]


def null_verdict(summary: str) -> bool:
    """Did this summary ANSWER the ask with a verdict that says there is NO revision?

    `extract` returns nothing in two cases a reader must be able to tell apart, and after the
    null card was removed nothing in the record did: the run SAID its recipe needed no change,
    and the run never addressed the ask at all. The ask is what distinguishes "I looked and
    nothing was contradicted" from not having looked, and silence is the expensive direction —
    a recipe that keeps serving badly, run after run, with no run saying so, is
    indistinguishable from a recipe that is fine.

    The same strictness as `extract` on both sides of the marker, so prose about a revision is
    never read as a verdict about one, and a bare marker with nothing after it answers nothing.
    """
    for raw in (summary or "").splitlines():
        head, sep, rest = raw.partition(MARKER)
        if not sep or head.strip(" \t-*>#"):
            continue
        text = rest.strip()
        if text and _is_null(_verdict(text)):
            return True
    return False


def maybe_propose(ctx, summary: str) -> str:
    """From the close-out every ending shares (`engine/loopend.finish_run`): file the one card
    for the revisions this run's summary named. Returns its proposal id, or "".

    Best-effort, like `goalreached.maybe_propose_retirement`: a store write never fails a
    finished run, and a run that may write its own recipe files nothing.
    """
    if not asks_for_revisions(ctx):
        return ""
    grants = getattr(ctx, "grants", None)
    if grants is not None and getattr(grants, "recipe_unlocked", False):
        return ""
    lines = extract(summary)
    if not lines:
        # A null VERDICT is an answer and is recorded as one — no card, no Decisions row, one
        # event. Without it an answered ask and an unaddressed ask are byte-identical in the
        # record, so nothing can measure whether runs answer the ask at all.
        if null_verdict(summary):
            ctx.transcript.event("stopping_update", {"recipe_proposal": "",
                                                     "revisions": NONE,
                                                     "run_id": ctx.run_id})
        return ""
    slug = ctx.routine.slug
    first = lines[0] if len(lines) == 1 else f"{lines[0]} (+{len(lines) - 1} more)"
    try:
        rec = pending.queue(
            ctx.server, kind=KIND, routine=slug, run_id=ctx.run_id,
            fields={"lines": lines, "run_id": ctx.run_id},
            summary=f"{ctx.routine.name or slug} proposes a revision to its own recipe — "
                    f"{first}")
    except OSError as exc:
        log.warning("recipe-revision: could not queue %s's proposal: %s", slug, exc)
        return ""
    # `stopping_update` is the existing event for what a run leaves standing: no new event type
    ctx.transcript.event("stopping_update", {"recipe_proposal": rec["id"],
                                             "lines": lines, "run_id": ctx.run_id})
    log.info("recipe-revision: %s proposed %d revision(s) to its recipe, card %s",
             slug, len(lines), rec["id"])
    return str(rec["id"])
