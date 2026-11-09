"""Which API routers the app mounts, and in what ORDER.

Router order is load-bearing and is why this is worth reading rather than skimming: a path with
a `{param}` segment will match a sibling literal route registered after it, so the modules
carrying literal sub-paths go first. The deliberately unauthenticated routers (webhook ingest,
the OAuth callback, the screen relays' websocket half) are mounted apart from the bearer-guarded
set so each exemption is visible rather than a missing argument.

Its own module since 2026-11-09: it had grown `appwiring.py` to the size bar, and new routes were
being registered by merging lines to fit rather than where they belong.
"""

from __future__ import annotations

from fastapi import FastAPI


def include_api_routers(app: FastAPI, deps: list) -> None:
    from . import (
        api_audit,
        api_background,
        api_branches,
        api_browser,
        api_changes,
        api_conversation_config,
        api_conversation_create,
        api_conversation_grants,
        api_conversation_playbooks,
        api_conversation_secrets,
        api_conversations,
        api_credential_names,
        api_debug,
        api_desktops,
        api_finishline,
        api_fs,
        api_gate,
        api_health,
        api_hooks,
        api_items,
        api_jobs,
        api_lanes,
        api_llm_tasks,
        api_messages,
        api_patch_applied,
        api_pending,
        api_playbooks,
        api_push,
        api_question_decline,
        api_question_deliver,
        api_questions,
        api_recipe_revision,
        api_refusal_flag,
        api_routine_edit,
        api_routine_files,
        api_routine_patch,
        api_routine_secrets,
        api_routines,
        api_run_control,
        api_runs,
        api_schedule,
        api_search,
        api_settings,
        api_stats,
        api_traces,
        api_workflows,
        settings,
    )

    for module in (api_push, api_routines, api_jobs, api_routine_edit, api_routine_patch,
                   api_routine_files, api_routine_secrets, api_settings, api_gate,
                   api_conversation_create,   # before api_conversations: its
                   # /conversations/defaults must be matched before /{slug}
                   api_conversations,
                   api_conversation_config,
                   api_conversation_playbooks,
                   api_conversation_secrets,   # D216 rank 1: the write surface the scoped
                   # store always served for a conversation but no route could reach
                   api_conversation_grants,   # F783 + D216: a conversation's `grants:` rows —
                   # the deny tombstones, the grants it holds, and (part a) the shared-store
                   # exposure dial; only the routine page had any of the three
                   api_background, api_branches, api_browser, api_desktops, api_runs,
                   api_run_control,
                   api_refusal_flag,
                   api_schedule, api_stats, api_changes, api_health, api_finishline,
                   api_credential_names,
                   api_questions,
                   api_question_deliver,   # /questions/{qid}/deliver|settle — an UNDELIVERED answer
                   api_patch_applied,   # /questions/{qid}/patch-applied — R2778's record fix
                   api_question_decline,   # /questions/{qid}/decline — R2197's first-class "no"
                   api_audit,
                   api_items, api_messages,
                   # before api_pending: /pending-creations/{pid}/revise is D200's own
                   # approval (it resumes a run, it does not write config) and must be
                   # matched before that module's sibling {pid} routes
                   api_recipe_revision, api_pending,
                   api_traces,
                   settings,
                   api_workflows, api_playbooks, api_llm_tasks,
                   api_lanes, api_search, api_fs, api_debug):
        app.include_router(module.router, prefix="/api", dependencies=deps)
    # The ONE deliberately unauthenticated API route: webhook trigger ingest. Third
    # parties call it, so the per-trigger URL token is the auth (constant-time compare,
    # rate-limited, size-capped — see api_hooks), never the global bearer.
    app.include_router(api_hooks.hooks_router, prefix="/api")
    # The run's live tail is a WebSocket (F606): no bearer dependency, because an HTTP
    # dependency fails on a websocket scope — `streams.serve` admits it by its stream ticket.
    app.include_router(api_runs.ws_router, prefix="/api")
    # The OAuth provider redirect target — also unauthenticated (a browser redirect carries no
    # bearer), guarded instead by the unguessable per-flow `state`. Mounted at /oauth/callback
    # (NOT under /api), like the index/static routes.
    app.include_router(settings.oauth.callback_router)
    # The noVNC screens — the shared browser's and every agent desktop's — relayed SAME-ORIGIN
    # (F527) by one implementation (api_screen_view). Mounted at /browser-view and /desktop-view,
    # not under /api, because the browser loads them as a document + assets rather than as an
    # API: noVNC builds its own relative asset paths, and any prefix it does not know about would
    # send them somewhere else.
    from . import api_screen_view

    # The asset half keeps the console's bearer dependency — a relayed noVNC asset is exactly
    # as protected as any other route (noVNC's own fetches from inside the frame carry no
    # header, so require_auth's `screen_for_path` pass-cookie branch is what admits them).
    app.include_router(api_screen_view.router, dependencies=deps)
    # The websocket half must NOT carry it: a FastAPI HTTP dependency applied to a websocket
    # route fails at connect time with "require_auth() missing 1 required positional argument:
    # 'request'" (a websocket scope has no Request). It validates the same pass cookie itself.
    # The two are separate routers precisely so this exemption cannot spread to the GETs and
    # quietly unauthenticate a signed-in browser session.
    app.include_router(api_screen_view.ws_router)
    # Minting a screen's pass is an OPERATOR act, so those routes sit on the /api surface with
    # the console's bearer dependency — unlike the two above, which are reached by the frame
    # itself and carry the pass instead (F530).
    app.include_router(api_screen_view.pass_router, prefix="/api", dependencies=deps)
