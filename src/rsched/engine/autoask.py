"""The ask seam's autonomous-apply branch (D191 step 3): a `config_patch` a routine's own
`config_confirm` dial permits is QUEUED for apply instead of filed as a decision card.

Split out of `interact.py` rather than added to it: that file is the ask protocol and is at
its size bar, and this is one question asked of one action — *may this patch apply without
the operator?* The answer is `configdial`'s, never this module's own judgement.

What the run sees. A taken patch comes back as a normal `ask_user` result carrying
`config_applied: "queued"` and the fields, so the model learns on that turn that it does not
need an answer — the very friction the operator named on 2026-10-16 ("they ask for permission
to do their job, every fucking time"). The RECORD still exists: the drainer writes the
applied-request outcome, and a refusal at the write files the ordinary card under the same
qid. A `config_patch` that is not permitted is untouched here and takes its ordinary path.

Deliberately narrow:

- **Only the routine's OWN config.** A patch naming another routine (`config_target`) is the
  operator's decision whatever this dial says — `configdial.refusals(cross_routine=True)`.
- **Only a patch-ONLY ask.** An ask whose question also needs an answer (it carries `options`,
  or it is BLOCKING, or it carries a typed access `request`) keeps its card: the patch is not
  the whole of what the run asked, and applying half of it would settle a record whose other
  half nobody answered.
- **Never the SERVER surface.** `{"server": {...}}` is the INSTANCE's config, not the
  routine's own; `config_confirm` is a per-routine dial and no value of it speaks for the
  instance. That stays a click until the operator decides otherwise (it is not in his
  2026-10-19 wording, which says "a decision's config_patch may name {"server": {...}}" for
  the SURFACE and "applied autonomously only for fields on that list" for the dial).
- **A child run never applies.** It runs with capabilities off by design and its ask already
  lands on the routine's page; a dial it does not hold must not reach its parent's config.
"""

from __future__ import annotations

from .. import autoapply, configdial
from ..configflow import SERVER_KEY


def may_queue(ctx, patch: dict | None, *, mode: str, options: list,
              request_ids: list | None, config_target: str) -> list[str]:
    """Why this ask may NOT apply autonomously — empty when it may.

    The reasons are returned rather than a bool so the caller can put the first one on the
    run's observation: a run told only "no" asks again next week.
    """
    if not patch:
        return ["the patch names no routine field to apply."]
    if SERVER_KEY in patch:
        return [(f"a {SERVER_KEY!r} patch is the INSTANCE's config: `config_confirm` is a "
                 f"per-routine dial, so an instance change stays the operator's decision.")]
    if ctx.depth > 0:
        return [("a child run's ask is the routine's decision, not the child's: a sub-run "
                 "holds no capabilities of its own.")]
    if mode == "blocking" or options or request_ids:
        return [("this ask needs an ANSWER as well as a patch (it is blocking, offers options "
                 "or carries an access request), so its record stays a decision.")]
    level = configdial.level_of(getattr(ctx.routine, "capabilities", None))
    return configdial.refusals(patch, level, cross_routine=bool(config_target))


def queue(ctx, patch: dict, *, qid: str, question: str) -> dict | None:
    """Queue the apply request and return the run's observation fields, or None when the
    spool is full (the caller then files the ordinary card, which is the safe fallback).
    """
    level = configdial.level_of(getattr(ctx.routine, "capabilities", None))
    path = autoapply.queue(ctx.server.routines_home, ctx.routine.slug, patch=patch,
                           level=level, qid=qid, run_id=ctx.run_id, question=question)
    if path is None:
        return None
    return {"config_applied": "queued", "config_fields": sorted(patch),
            "config_confirm": level}


def taken(ctx, patch: dict | None, *, qid: str, question: str, mode: str, options: list,
          request_ids: list | None, config_target: str) -> tuple[dict, str]:
    """The ask seam's whole question, answered once: `({run observation fields}, "")` when the
    patch was TAKEN for autonomous apply, or `({}, why)` when it was not — `why` empty for an
    ask that carries no patch at all, so the caller says nothing about a surface it never used.

    No transcript event is added for a taken patch: the `question` event the caller already
    wrote records what was asked under this qid, and the apply's outcome is the drainer's own
    record. A new event type would have to be registered in five surfaces at once (EVENT_TYPES,
    the console renderer, cli_render, CLAUDE.md, the search sources) to record one line that is
    already recorded twice.
    """
    if not patch:
        return {}, ""
    why = may_queue(ctx, patch, mode=mode, options=options, request_ids=request_ids,
                    config_target=config_target)
    if why:
        return {}, why[0]
    # A full spool is not a refusal to report as policy: the ask simply keeps its card.
    return (queue(ctx, patch, qid=qid, question=question) or {}), ""
