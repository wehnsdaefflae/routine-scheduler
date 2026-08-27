"""The `create_routine` action handler — a CONVERSATION graduates the work it just
clarified with the user into a real scheduled routine (D58), in TWO steps (D92):
preview first, materialize only after the user has confirmed the draft.

The operator's rule: routine creation is initiated from a conversation ONLY. Instead of a
retired standalone wizard page, the conversation agent (or a `/create_routine` slash
command) clarifies the task WITH the user in the normal chat, then emits this action. The
FIRST call VALIDATES the named workflow against the live library, then stores a DRAFT (slug,
name, instruction, workflow) under the conversation's own
`state/routine-draft.json` and returns a preview observation carrying the pattern catalog —
nothing is created yet; the
agent relays the preview and finishes its reply. When the user answers, the agent calls
`create_routine` again with the SAME fields, and only then does the handler materialize —
through the same `workflows.scaffold` path the retired wizard's build half called
(decompose the chosen workflow into main.md + stages/, adapt rules, write routine.yaml,
init the auto-push git repo — one materializer only).

The confirm gate is structural, not honor-system: a conversation reply runs as its own
engine process, so a draft written by THIS process cannot be confirmed by this process —
the confirming call must come from a LATER leg, which a root conversation only gets after
the user (or a background delivery) speaks. A changed field on the confirming call is a
DESIGN CHANGE, not a confirmation: it replaces the draft and restarts the round-trip.

Structural rule (mirrors `detach`): valid ONLY from a ROOT CONVERSATION (depth 0, dir directly
under conversations_home). A scheduled routine has no user in the loop to design a new routine
with, and a within-reply child must not create routines as a side effect. The engine ALSO only
surfaces the kind to a root conversation (loop.allowed_tools injection), so this is defence in
depth, not the only gate.

Unlike `detach` (which drops an intent for the daemon because an HTTP request cannot block on a
minute-long build), the handler scaffolds SYNCHRONOUSLY: an engine turn already does long async
work and returns an observation, the conversation user is waiting for the routine now, and the
daemon's registry rescan (`registry_rescan_s`) picks the new dir up on its own timer — so no new
daemon manager is needed. The slow step is the workflow decompose inside scaffold, which degrades
to a no-LLM fallback when no endpoint is available (tests, offline).
"""

from __future__ import annotations

import os
from pathlib import Path

from ..ids import now_iso
from ..paths import atomic_write_json, read_json
from .detach import _is_root_conversation
from .run_context import RunContext

# The default pattern when the conversation does not name one — the sane general-purpose
# workflow, same default the spawn/subtask/detach actions use.
DEFAULT_WORKFLOW = "general-task"

#: `generate` is a SUBTASK capability (`subtask` with `workflow: "generate"`, gated by the
#: `workflows: generate` permission — docs/child-runs.md), never a library pattern. Naming it
#: here used to store cleanly and blow up at materialize, i.e. AFTER the user confirmed
#: (F387/R493); it is rejected at draft time with the reason.
GENERATE_PSEUDO_SLUG = "generate"

#: Where a conversation's pending routine draft lives (relative to the conversation dir).
#: ONE draft per conversation: a new slug simply replaces the old draft — the flow is a
#: linear chat, not a queue.
DRAFT_RELPATH = Path("state") / "routine-draft.json"


def _draft_path(ctx: RunContext) -> Path:
    return ctx.routine.dir / DRAFT_RELPATH


def _load_draft(ctx: RunContext) -> dict | None:
    path = _draft_path(ctx)
    if not path.is_file():
        return None
    draft = read_json(path)
    return draft if isinstance(draft, dict) and draft.get("slug") else None


def _catalog(server) -> list[dict]:
    """Every library pattern, one line each: what the draft observation shows so the choice
    is made against the real catalog rather than from memory (F383).
    """
    from ..workflows import library

    return [{"slug": w["slug"], "description": w["description"] or w["name"],
             **({"when_to_use": w["when_to_use"]} if w["when_to_use"] else {})}
            for w in library.list_workflows(server.libraries_home)]


def _unknown_workflow_obs(slug: str, workflow_slug: str, catalog: list[dict]) -> dict:
    """A draft naming a pattern the library does not hold is refused HERE — before the user
    is asked to confirm — instead of at the expensive materialize step (F387/R493).
    """
    why = (f"{GENERATE_PSEUDO_SLUG!r} is not a library pattern: drafting a NEW pattern is a "
           'subtask capability (`subtask` with workflow: "generate"), not something '
           "create_routine can materialize from. Pick a pattern from the catalog below, or "
           "generate one in a subtask first and name the slug it wrote."
           if workflow_slug == GENERATE_PSEUDO_SLUG else
           f"no workflow {workflow_slug!r} exists in the library.")
    return {"kind": "create_routine", "slug": slug, "workflow": workflow_slug,
            "rejected": True, "reason": why, "workflow_catalog": catalog}


def _preview_obs(draft: dict, catalog: list[dict], *, updated: bool,
                 blocked_same_leg: bool = False) -> dict:
    """The draft/preview observation: what WILL be created, the catalog the choice was made
    against, and the exact next step. The teaching copy is the contract — a same-leg confirm
    attempt gets told why it was held.
    """
    instruction = draft["instruction"]
    obs = {"kind": "create_routine", "slug": draft["slug"], "name": draft["name"],
           "workflow": draft["workflow"], "draft": True, "updated": updated,
           "instruction_chars": len(instruction),
           "instruction_preview": instruction[:600],
           # F383: the pattern catalog rides the observation so the relay compares against
           # what the library actually holds — the choice stops being an unexamined default.
           "workflow_catalog": catalog,
           "next": ("Nothing is created yet. Relay this draft to the user in your reply and "
                    "finish the reply. The relay must state, in the user's words: what the "
                    "routine PRODUCES each run, what DONE looks like for one run, the chosen "
                    "workflow pattern AND one alternative from workflow_catalog with why this "
                    "one fits better. If any of those is YOUR inference rather than something "
                    "the user settled, do not present it as decided — say which one is open "
                    "and ask it, and let the confirming call follow that answer. If the user "
                    "confirms, call create_routine again with the SAME fields to materialize "
                    "it; a call with changed fields updates the draft and restarts the "
                    "confirmation.")}
    if blocked_same_leg:
        obs["held"] = ("This reply already drafted the routine — the confirming call must "
                       "follow the user's answer, in their next message. Show the draft and "
                       "finish the reply.")
    return obs


def handle_create_routine(ctx: RunContext, action: dict) -> dict:
    """Store/refresh the draft (first step) or materialize it (confirmed second step), or
    reject when not a root conversation. Returns the observation dict the loop records and
    renders.

    Action fields (all reused from the shared schema — no create_routine-only fields):
      target   — the new routine's kebab-case slug (required)
      name     — its human display name (required)
      prompt   — the clarified task instruction, decomposed into the routine's stages (required)
      workflow — the library workflow pattern to materialize from (optional; DEFAULT_WORKFLOW)
    """
    if not _is_root_conversation(ctx):
        return {"kind": "create_routine", "rejected": True,
                "reason": "create_routine is only available from a top-level conversation — a "
                          "scheduled routine or a within-reply child cannot create routines. "
                          "Routine creation is initiated by a conversation, with the user."}
    from ..workflows.scaffold import scaffold

    slug = str(action.get("target") or "").strip()
    name = str(action.get("name") or "").strip()
    instruction = str(action.get("prompt") or "").strip()
    workflow_slug = str(action.get("workflow") or "").strip() or DEFAULT_WORKFLOW
    server = ctx.server

    if (server.routines_home / slug).exists():
        return {"kind": "create_routine", "slug": slug, "already_exists": True}

    # F387: the pattern is checked against the LIVE library at draft time. The old flow
    # stored any string and only failed inside scaffold — after the user had confirmed.
    catalog = _catalog(server)
    if workflow_slug not in {w["slug"] for w in catalog}:
        return _unknown_workflow_obs(slug, workflow_slug, catalog)

    draft = _load_draft(ctx)
    fields = {"slug": slug, "name": name, "instruction": instruction,
              "workflow": workflow_slug}
    if draft is None or any(draft.get(k) != v for k, v in fields.items()):
        # First step, or a design change: (re)write the draft and ask for the round-trip.
        record = {**fields, "pid": os.getpid(), "created_at": now_iso()}
        atomic_write_json(_draft_path(ctx), record)
        return _preview_obs(record, catalog, updated=draft is not None)
    if draft.get("pid") == os.getpid():
        # Same reply that drafted it — no user has seen the preview yet. Hold, teach.
        return _preview_obs(draft, catalog, updated=False, blocked_same_leg=True)

    # Confirmed: identical fields, a later leg — the user has spoken since the preview.
    try:
        routine_dir = scaffold(server, slug=slug, name=name, instruction=instruction,
                               workflow_slug=workflow_slug, description=name)
    except ValueError as exc:
        # bad slug, unknown workflow, or a dir that appeared mid-flight — a teaching rejection,
        # corrected by the model, never a crash
        return {"kind": "create_routine", "slug": slug, "error": str(exc)}
    except OSError as exc:
        # the filesystem shifted under the build (R478: the user deleted the half-made dir
        # while the slow workflow decompose ran; the write that followed hit FileNotFoundError
        # and, uncaught, orphaned the whole conversation run rc=1) — surface it as an
        # actionable error observation instead of crashing the engine
        return {"kind": "create_routine", "slug": slug,
                "error": f"materialization failed mid-build ({exc}); nothing usable was "
                         "created — check the routines home and try again"}
    _draft_path(ctx).unlink(missing_ok=True)
    return {"kind": "create_routine", "slug": slug, "name": name,
            "workflow": workflow_slug, "created": True, "dir": str(routine_dir),
            "rescan_s": server.registry_rescan_s}
