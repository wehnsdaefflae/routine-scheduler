"""The `create_routine` action handler — a CONVERSATION graduates the work it just
clarified with the user into a real scheduled routine (D58).

The operator's rule: routine creation is initiated from a conversation ONLY. Instead of a
standalone new-routine wizard page, the conversation agent (or a `/create_routine` slash
command) clarifies the task WITH the user in the normal chat, then emits this one action to
materialize the routine. It reuses the SAME `workflows.scaffold` path the wizard's build half
calls — decompose the chosen workflow into the routine's own main.md + stages/, adapt its
rules, write routine.yaml, init the auto-push git repo — so there is exactly one materializer.

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

from .detach import _is_root_conversation
from .run_context import RunContext

# The default pattern when the conversation does not name one — the sane general-purpose
# workflow, same default the spawn/subtask/detach actions use.
DEFAULT_WORKFLOW = "general-task"


def handle_create_routine(ctx: RunContext, action: dict) -> dict:
    """Materialize a new scheduled routine from this conversation (or reject when not a root
    conversation). Returns the observation dict the loop records and renders.

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
    try:
        routine_dir = scaffold(server, slug=slug, name=name, instruction=instruction,
                               workflow_slug=workflow_slug, description=name)
    except ValueError as exc:
        # bad slug, unknown workflow, or a dir that appeared mid-flight — a teaching rejection,
        # corrected by the model, never a crash
        return {"kind": "create_routine", "slug": slug, "error": str(exc)}
    return {"kind": "create_routine", "slug": slug, "name": name,
            "workflow": workflow_slug, "created": True, "dir": str(routine_dir)}
