"""The REVISE path of `create_routine` — rewriting an EXISTING routine's recipe in place,
behind the operator's approval (D198).

`create_routine` with `revises: true` is the path and this module the handler. A routine's OWN
recipe has three other mechanisms (`write_recipe`, `engine/revise.py`,
`engine/recipeproposal.py`); this one answers "who may rewrite WHOSE recipe", replacing the
ungated route of editing another routine's `main.md` through a write root: the operator decides,
and the edit is made by the run that owns the recipe (`rsched/reciperevise.py`).

- the CALLER needs `write_recipe` — the capability that already means "trusted with recipe
  prose"; no second capability.
- every proposal files ONE `revise-routine` decision card. Approving it hands the lines to the
  target's own next run under the one-leg unlock; the write is never the proposer's, and
  `routine.yaml` is never touched — a revision that needs a config change files it as its own
  decision.
- a routine may not revise ITSELF through this path — that would be a second, unrecorded way
  to do what the own-recipe mechanisms already do openly.
"""

from __future__ import annotations

from ..ids import is_slug
from .run_context import RunContext

KIND = "revise-routine"
#: What a PROPOSER must hold to propose a revision at all: the SAME capability that lets a
#: routine rewrite its own recipe (`grants.CAPABILITY_ACTIONS`), not a new one.
PROPOSER_CAPABILITY = "write_recipe"
#: How many revision lines one proposal carries — matches `recipeproposal.MAX_LINES`.
MAX_LINES = 8


def _lines(action: dict) -> list[str]:
    """The revision, one line per change. `prompt` is the field the action already carries for
    "what this routine should do", so the revise path reads it as the revision rather than
    growing the schema.
    """
    raw = action.get("prompt")
    text = raw if isinstance(raw, str) else ""
    return [ln for ln in (part.strip(" \t-*") for part in text.splitlines()) if ln][:MAX_LINES]


def _problem(ctx: RunContext, slug: str, lines: list[str]) -> str:
    """Why this revision may not be proposed at all — "" when it may. One ladder, each rung its
    own teaching exit.
    """
    home = ctx.server.routines_home
    if ctx.depth > 0:
        return ("a recipe revision is proposed by the run that reasoned it, never by a child "
                "run — hand it back in your finish summary and let the run that started you "
                "decide")
    if PROPOSER_CAPABILITY not in ((ctx.routine.capabilities or {}).get("actions")
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
    """Propose a revision to `target`'s recipe: file ONE decision card (module docstring)."""
    from ..pending import queue

    slug = str(action.get("target") or "").strip()
    lines = _lines(action)
    if why := _problem(ctx, slug, lines):
        return _refused(slug, why)
    first = lines[0] if len(lines) == 1 else f"{lines[0]} (+{len(lines) - 1} more)"
    rec = queue(ctx.server, kind=KIND, routine=ctx.routine.slug, run_id=ctx.run_id,
                fields={"target": slug, "lines": lines, "run_id": ctx.run_id},
                summary=f"{ctx.routine.slug} proposes a revision to {slug}'s recipe — {first}")
    return {"kind": "create_routine", "slug": slug, "revises": True, "queued": True,
            "id": rec["id"], "lines": lines,
            "next": (f"Nothing is written, and nothing will be until the operator approves it: "
                     f"this went to the Decisions page as one card ({rec['id']}). Approving it "
                     f"hands the lines to {slug}'s next run, which makes the edit itself. Do "
                     "NOT re-issue it — a second call queues a second card. Say in your summary "
                     "that the revision is awaiting approval.")}
