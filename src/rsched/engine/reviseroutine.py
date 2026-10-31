"""The REVISE path of `create_routine` — rewriting an EXISTING routine's recipe in place,
gated behind a per-routine approval dial (D198).

The operator's answer, 2026-10-19, verbatim: *"Add a revise path to `create_routine` (or a
sibling action) gated by a per-routine dial, like `config_confirm`"*. `create_routine` with
`revises: true` is that path, `rsched/revisedial.py` is that dial, and this module is its
handler — deliberately `candidate.py`'s sibling in shape, because the two are the same kind of
act on the same action: that one REPLACES a routine under a new slug, this one REVISES the one
that exists.

WHAT WAS ALREADY BUILT, so this adds a path rather than a third copy of one:

- a routine rewriting its OWN recipe — three ways already: the `write_recipe` capability
  (standing), `engine/revise.py` (the operator's on-demand one-leg unlock from the run view),
  and `engine/recipeproposal.py` + `web/api_recipe_revision.py` (D200: a finishing run proposes
  revisions, the operator approves, the proposing run is resumed with its recipe writable);
- `replaces: <slug>` — `engine/candidate.py`, shipped 2026-10-17, which is a REPLACEMENT under
  a new slug and was not what this answer asked for.

The hole all of them leave is the one the decision's title names — "who may rewrite WHOSE
recipe". A routine holding `write_recipe` whose `fs_write_roots` cover `~/routines`
(routine-improver does, both) can already write ANOTHER routine's `main.md` with `edit_file`:
the recipe seal is scoped to the run's own routine dirs (`engine/fileops._write_gate` →
`engine/reach.routine_dirs`, which is `[own]` at depth 0), so outside them nothing but a write
root is consulted. That is the ungated route this path replaces: silent, unrecorded, decided by
the editor, and invisible to the routine whose instructions changed. Here the SUBJECT's dial
decides, the operator sees a card unless he has said otherwise, and the edit is made by the run
that owns the recipe (`rsched/reciperevise.py`).

THE SHAPE, and why each piece is where it is:

- the CALLER needs `write_recipe` — the capability that already means "the operator trusts this
  routine with recipe prose". A new capability would be a second decision his sentence does not
  make, and `grants.py` is at its size ceiling with no room for one.
- the DIAL belongs to the TARGET (`revisedial`, which gives the reasons).
- `always` (the default) files ONE `revise-routine` decision card; `invited` arms the
  invitation at once. Either way the write is the target's own next run under the one-leg
  unlock, never the proposer's.
- a routine may not revise ITSELF through this path: that is what the three own-recipe
  mechanisms above are for, and routing it here would hand a routine with `write_recipe` a
  second, unrecorded way to do what it can already do openly.
"""

from __future__ import annotations

from .. import reciperevise, revisedial
from ..ids import is_slug
from .run_context import RunContext

KIND = "revise-routine"
#: How many revision lines one proposal carries — `recipeproposal.MAX_LINES`, because the
#: operator reads both on the same page and a card has to stay readable at a glance.
MAX_LINES = 8


def _lines(action: dict) -> list[str]:
    """The revision, one line per change. `prompt` is the field the action already carries for
    "what this routine should do", so the revise path reads it as the revision rather than
    adding a field the schema would have to grow for (`actionschema.py` has zero slack).
    """
    raw = action.get("prompt")
    text = raw if isinstance(raw, str) else ""
    return [ln for ln in (part.strip(" \t-*") for part in text.splitlines()) if ln][:MAX_LINES]


def _problem(ctx: RunContext, slug: str, lines: list[str]) -> str:
    """Why this revision may not be proposed at all — "" when it may. One ladder, each rung its
    own teaching exit (`candidate._problem`'s shape and `authoring`'s reason for it).
    """
    home = ctx.server.routines_home
    if ctx.depth > 0:
        return ("a recipe revision is proposed by the run that reasoned it, never by a child "
                "run — hand it back in your finish summary and let the run that started you "
                "decide")
    if revisedial.PROPOSER_CAPABILITY not in ((ctx.routine.capabilities or {}).get("actions")
                                              or []):
        return ("revising another routine's recipe needs the recipe-authoring permission "
                "(`write_recipe`), which this routine does not hold — file a report to that "
                "routine instead, which it reads on its next run")
    if not is_slug(slug) or not (home / slug / "routine.yaml").is_file():
        return f"{slug!r} is not a routine to revise"
    if slug == ctx.routine.slug:
        return ("this is your OWN recipe: `revises` is for another routine's. Say what should "
                "change in your finish summary (the engine files it as your own revision "
                "proposal), or edit the files directly if you hold recipe-authoring")
    if not lines:
        return ("say what should change: put the revision in `prompt`, one line per change, "
                "naming the file and what THIS run showed — the target's own run makes the "
                "edit from those words, so a line it cannot act on changes nothing")
    return ""


def _refused(slug: str, why: str) -> dict:
    return {"kind": "create_routine", "slug": slug, "revises": True, "rejected": True,
            "reason": why}


def revise(ctx: RunContext, action: dict) -> dict:
    """Propose a revision to `target`'s recipe: arm it when that routine's dial says so, else
    file ONE decision card (module docstring).
    """
    from ..config import load_routine
    from ..pending import queue

    slug = str(action.get("target") or "").strip()
    lines = _lines(action)
    if why := _problem(ctx, slug, lines):
        return _refused(slug, why)
    home = ctx.server.routines_home
    cfg, _problems = load_routine(home / slug)
    caps = (cfg.capabilities if cfg is not None else None) or {}
    level = revisedial.level_of(caps)
    first = lines[0] if len(lines) == 1 else f"{lines[0]} (+{len(lines) - 1} more)"

    if revisedial.armed_without_a_card(caps):
        entry = reciperevise.invite(home, slug, lines=lines, by=ctx.routine.slug,
                                    run_id=ctx.run_id, approved_by=f"{revisedial.DIAL}={level}")
        if entry is None:
            return _refused(slug, f"{slug} already has {reciperevise.MAX_PENDING} revisions "
                                  "waiting for its next run — nothing is lost, but file no "
                                  "more until it has run")
        return {"kind": "create_routine", "slug": slug, "revises": True, "armed": True,
                "lines": lines, "dial": level,
                "next": (f"{slug}'s own next run will make this edit: its recipe is unlocked "
                         "for that leg, it is told the lines are yours, and it may refuse a "
                         "line that is wrong about what its runs do. Nothing is written yet, "
                         f"and you never write {slug}'s files yourself. Do NOT re-issue this — "
                         "a second call queues a second revision. Finish the work that does "
                         "not depend on the edit, and say in your summary that it is armed.")}

    rec = queue(home, kind=KIND, routine=ctx.routine.slug, run_id=ctx.run_id,
                fields={"target": slug, "lines": lines, "run_id": ctx.run_id},
                summary=f"{ctx.routine.slug} proposes a revision to {slug}'s recipe — {first}")
    return {"kind": "create_routine", "slug": slug, "revises": True, "queued": True,
            "id": rec["id"], "lines": lines, "dial": level,
            "next": (f"Nothing is written, and nothing will be until the operator approves it: "
                     f"{slug}'s `{revisedial.DIAL}` dial is {level!r}, so this went to the "
                     f"Decisions page as one card ({rec['id']}). Approving it hands the lines "
                     f"to {slug}'s next run, which makes the edit itself. Do NOT re-issue it — "
                     "a second call queues a second card. Say in your summary that the "
                     "revision is awaiting approval.")}
