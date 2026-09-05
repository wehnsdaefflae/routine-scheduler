"""Mounting the API and running the app's LIFESPAN.

Split out of `app.py` (F393): `create_app` builds an app; these two decide what is attached to
it and what happens either side of serving.

Router ORDER is load-bearing and is why this is worth reading rather than skimming: a path with
a `{param}` segment will match a sibling literal route registered after it, so the modules
carrying literal sub-paths go first. The two deliberately unauthenticated routers (webhook
ingest, the OAuth callback) are mounted apart from the bearer-guarded set so that exemption is
visible rather than a missing argument.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from fastapi import FastAPI

from ..config import ServerConfig
from ..daemon.events import EventBus
from ..endpoints.instrument import set_sink
from ..llm_tasks import DaemonSink, TaskCenter


def _observe(task: asyncio.Task, name: str) -> None:
    """A lifespan background task must never die silently: without this, an exception in
    e.g. the scheduler task unwinds it while the web app keeps serving — the daemon looks
    alive with its heart stopped. The tick body has its own guard; this catches startup
    crashes and anything the guard can't.
    """
    def _done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.critical("background task %r died: %s", name, exc, exc_info=exc)
    task.add_done_callback(_done)


log = logging.getLogger("rsched.web.appwiring")


def _make_lifespan(server: ServerConfig, bus: EventBus, task_center: TaskCenter,
                   *, with_scheduler: bool):
    """The app's startup/shutdown seam, built before the FastAPI instance exists (it only
    needs the shared server/bus/center; the scheduler is reached via app.state at runtime).
    """

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        from .. import library_docs, utils_lib
        from ..docs_build import ensure_docs

        # bootstrap the library repo (clone from remote if configured + absent, else init/leave),
        # then make sure its rules/ + permissions/ subdirs exist.
        try:
            utils_lib.ensure_library(server.libraries_home, remote=server.libraries_remote)
            library_docs.ensure_dir(server.rules_home)
            library_docs.ensure_dir(server.permissions_home)
        except Exception as exc:  # never block startup on a library hiccup
            log.warning("library bootstrap %s: %s", server.libraries_home, exc)
        # regenerate the Help tab's content (pdoc + guides) when the source changed — in a
        # thread, and ensure_docs never raises, so startup is never blocked on it
        docs_task = asyncio.create_task(asyncio.to_thread(ensure_docs, server.source_repo))
        task = None
        if with_scheduler and not os.environ.get("RSCHED_NO_SCHEDULER"):
            task = asyncio.create_task(app.state.scheduler.run_forever())
            _observe(task, "scheduler")
        # Web Push sender: idles until a browser subscribes, then pushes new decisions
        from . import push as push_mod

        push_task = asyncio.create_task(push_mod.bus_listener(server, bus))
        _observe(push_task, "web-push listener")
        # The LLM task manager sink: every instrumented complete() (run in threadpool/to_thread
        # workers) marshals its lifecycle records onto THIS loop, where the task center + bus live.
        set_sink(DaemonSink(task_center, asyncio.get_running_loop()))
        # Full-text search: keep the FTS index warm in the background (bounded passes in a
        # thread) so query-time freshness top-ups stay cheap. The index is a pure cache
        # under <routines_home>/.control/ — this process is its only writer.
        from . import api_search

        search_task = asyncio.create_task(api_search.maintain(app.state.search))
        _observe(search_task, "search maintainer")
        yield
        set_sink(None)
        search_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await search_task
        app.state.search.close()
        docs_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await docs_task
        push_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await push_task
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return lifespan

def _include_api_routers(app: FastAPI, deps: list) -> None:
    from . import (
        api_audit,
        api_background,
        api_branches,
        api_browser,
        api_conversation_config,
        api_conversation_create,
        api_conversation_playbooks,
        api_conversations,
        api_fs,
        api_groups,
        api_hooks,
        api_items,
        api_llm_tasks,
        api_messages,
        api_pending,
        api_playbooks,
        api_push,
        api_questions,
        api_routine_edit,
        api_routine_patch,
        api_routine_secrets,
        api_routines,
        api_run_control,
        api_runs,
        api_schedule,
        api_search,
        api_stats,
        api_stopping,
        api_traces,
        api_workflows,
        settings,
    )

    for module in (api_push, api_routines, api_routine_edit, api_routine_patch,
                   api_routine_secrets,
                   api_conversation_create,   # before api_conversations: its
                   # /conversations/defaults must be matched before /{slug}
                   api_conversations,
                   api_conversation_config,
                   api_conversation_playbooks,
                   api_background, api_branches, api_browser, api_runs, api_run_control,
                   api_schedule, api_stats, api_stopping, api_questions, api_audit,
                   api_items, api_messages, api_pending,
                   api_traces,
                   settings,
                   api_workflows, api_playbooks, api_llm_tasks, api_hooks,
                   api_groups, api_search, api_fs):
        app.include_router(module.router, prefix="/api", dependencies=deps)
    # The ONE deliberately unauthenticated API route: webhook trigger ingest. Third
    # parties call it, so the per-trigger URL token is the auth (constant-time compare,
    # rate-limited, size-capped — see api_hooks), never the global bearer.
    app.include_router(api_hooks.hooks_router, prefix="/api")
    # The OAuth provider redirect target — also unauthenticated (a browser redirect carries no
    # bearer), guarded instead by the unguessable per-flow `state`. Mounted at /oauth/callback
    # (NOT under /api), like the index/static routes.
    app.include_router(settings.oauth.callback_router)
