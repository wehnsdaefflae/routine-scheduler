"""The `create_routine` action handler — a CONVERSATION graduates the work it just
clarified with the user into a real scheduled routine (D58), in TWO steps (D92):
preview first, materialize only after the user has confirmed the draft.

The operator's rule: routine creation is initiated from a conversation ONLY. Instead of a
retired standalone wizard page, the conversation agent (or a `/create_routine` slash
command) clarifies the task WITH the user in the normal chat, then emits this action. The
FIRST call stores a DRAFT (slug, name, instruction, workflow) under the conversation's own
`state/routine-draft.json` and returns a preview observation — nothing is created yet; the
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


def _preview_obs(draft: dict, *, updated: bool, blocked_same_leg: bool = False) -> dict:
    """The draft/preview observation: what WILL be created, and the exact next step. The
    teaching copy is the contract — a same-leg confirm attempt gets told why it was held.
    """
    instruction = draft["instruction"]
    obs = {"kind": "create_routine", "slug": draft["slug"], "name": draft["name"],
           "workflow": draft["workflow"], "draft": True, "updated": updated,
           "instruction_chars": len(instruction),
           "instruction_preview": instruction[:600],
           "next": ("Nothing is created yet. Relay this draft to the user in your reply — "
                    "slug, name, workflow pattern, and what the routine will do — and finish "
                    "the reply. If the user confirms, call create_routine again with the SAME "
                    "fields to materialize it; a call with changed fields updates the draft "
                    "and restarts the confirmation.")}
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

    draft = _load_draft(ctx)
    fields = {"slug": slug, "name": name, "instruction": instruction,
              "workflow": workflow_slug}
    if draft is None or any(draft.get(k) != v for k, v in fields.items()):
        # First step, or a design change: (re)write the draft and ask for the round-trip.
        record = {**fields, "pid": os.getpid(), "created_at": now_iso()}
        atomic_write_json(_draft_path(ctx), record)
        return _preview_obs(record, updated=draft is not None)
    if draft.get("pid") == os.getpid():
        # Same reply that drafted it — no user has seen the preview yet. Hold, teach.
        return _preview_obs(draft, updated=False, blocked_same_leg=True)

    # Confirmed: identical fields, a later leg — the user has spoken since the preview.
    try:
        routine_dir = scaffold(server, slug=slug, name=name, instruction=instruction,
                               workflow_slug=workflow_slug, description=name)
    except ValueError as exc:
        # bad slug, unknown workflow, or a dir that appeared mid-flight — a teaching rejection,
        # corrected by the model, never a crash
        return {"kind": "create_routine", "slug": slug, "error": str(exc)}
    _draft_path(ctx).unlink(missing_ok=True)
    return {"kind": "create_routine", "slug": slug, "name": name,
            "workflow": workflow_slug, "created": True, "dir": str(routine_dir)}
