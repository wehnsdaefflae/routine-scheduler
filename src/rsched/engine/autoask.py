"""The ask seam's autonomous-apply branch (D191): a `config_patch` a routine's own
`config_confirm` dial permits is QUEUED for apply instead of filed as a decision card.

The verdict is `configdial`'s, never this module's. A taken patch comes back as a normal
`ask_user` result carrying `config_applied: "queued"` and the fields, so the model learns on that
turn it needs no answer; the drainer records the outcome, and a refusal at the write files the
ordinary card under the same qid. A patch not permitted takes its ordinary path.

Deliberately narrow:

- **Only the routine's OWN config** — a `config_target` patch is the operator's decision.
- **Only a patch-ONLY ask** — one that is blocking, offers `options` or carries an access
  `request` also needs an answer, so it keeps its card.
- **Never the SERVER surface** — `config_confirm` is a per-routine dial and does not speak for
  the instance.
- **A child run never applies** — it holds no capabilities of its own.
"""
from __future__ import annotations

from .. import autoapply, configdial
from ..configflow import SERVER_KEY


def may_queue(ctx, patch: dict | None, *, mode: str, options: list,
              request_ids: list | None, config_target: str) -> list[str]:
    """Why this ask may NOT apply autonomously — empty when it may.

    Reasons, not a bool, so the caller can put the first one on the run's observation.
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

    No transcript event is added for a taken patch: the caller's `question` event and the
    drainer's outcome already record it.
    """
    if not patch:
        return {}, ""
    why = may_queue(ctx, patch, mode=mode, options=options, request_ids=request_ids,
                    config_target=config_target)
    if why:
        return {}, why[0]
    # A full spool is not a refusal to report as policy: the ask simply keeps its card.
    return (queue(ctx, patch, qid=qid, question=question) or {}), ""
