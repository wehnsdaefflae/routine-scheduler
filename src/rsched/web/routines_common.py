"""Shared routine-endpoint plumbing: template guards, catalog lookups, the locked
git-commit, and the permission-layer detail — imported by api_routines,
api_routine_edit, api_conversations, api_hooks, and api_runs alike (it used to live
inside api_routines, which every sibling then reached into).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

from .. import registry
from ..grants import EMPTY_CAPABILITIES, GATED_KINDS
from ..ids import parse_run_id
from .wizard_store import TEMPLATE_SLUG


def guard_template(slug: str, refusal: str) -> None:
    """The 'clarification' routine is the wizard's protected template — configuration the
    user edits, never a runnable/removable routine. 403 keeps it on the page regardless.
    """
    if slug == TEMPLATE_SLUG:
        raise HTTPException(403, f"{slug!r} is the protected clarification template — {refusal}")


def _state(request: Request):
    return request.app.state


def _catalog(request: Request) -> dict[str, registry.RoutineInfo]:
    return registry.scan(_state(request).server)


def _info(request: Request, slug: str) -> registry.RoutineInfo:
    info = _catalog(request).get(slug)
    if info is None:
        raise HTTPException(404, f"no routine {slug!r}")
    return info


def guard_not_active(request: Request, info: registry.RoutineInfo,
                     noun: str = "routine") -> None:
    """409 while a run is active — the web layer edits config/files only between runs
    (shared with conversations, where the 'run' is a live reply).
    """
    if info.active_run or request.app.state.runner.is_active(info.slug):
        raise HTTPException(409, f"{noun} {info.slug!r} is busy (a run is active) "
                                 "— try again after it ends")


def permission_layers_detail(server, cfg, *,
                             routine_only: list[str] | None = None) -> tuple[list[dict], dict]:
    """The two permission layers of a detail payload (shared with conversations): every
    library conduct doc as a toggle row (held ones active; `routine_only` marks the docs a
    conversation greys out), plus the machine-enforced capabilities mapping + its vocabulary.
    """
    from .. import library_docs

    all_perms = library_docs.list_docs(server.permissions_home)
    held = set(cfg.permissions)
    permissions = [{"slug": p["slug"], "summary": p["summary"], "title": p["title"],
                    "requires": p["requires"], "active": p["slug"] in held,
                    **({"routine_only": p["slug"] in routine_only}
                       if routine_only is not None else {})}
                   for p in all_perms]
    own_caps = cfg.capabilities or {}
    reservable = sorted({u for p in all_perms for u in (p["requires"].get("utils") or [])}
                        | set(own_caps.get("utils") or []))
    capabilities = {"active": {**EMPTY_CAPABILITIES, **own_caps},
                    "vocabulary": {"actions": list(GATED_KINDS), "utils": reservable}}
    return permissions, capabilities


def _git_commit(routine_dir: Path, message: str) -> None:
    """Commit a web-side routine-dir edit under the SAME per-repo lock the engine's
    autocommit takes (libgit) — a trait edit is allowed during a LIVE run and used to
    race the engine on git's index, the loser failing silently.
    """
    if not (routine_dir / ".git").exists():
        return
    from ..libgit import commit
    commit(routine_dir, message)


def active_run_dir(info: registry.RoutineInfo) -> Path | None:
    """The live run's directory, or None when nothing is running — so a trait edit can
    reach a run already in flight. Shared by both homes (a conversation's reply is a run
    like any other).
    """
    if not info.active_run:
        return None
    try:
        _, ts = parse_run_id(info.active_run.run_id)
    except ValueError:
        return None
    d = info.cfg.dir / "runs" / ts
    return d if d.is_dir() else None
