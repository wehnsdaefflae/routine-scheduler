"""Shared routine-endpoint plumbing: catalog lookups, the locked
git-commit, and the permission-layer detail — imported by api_routines,
api_routine_edit, api_conversations, api_hooks, and api_runs alike (it used to live
inside api_routines, which every sibling then reached into).
"""

# the ONE web->engine signal seam: control.json is merged, never overwritten, so no endpoint can
# drop a sibling's pending signal.
from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException, Request

from .. import registry
from ..grants import EMPTY_CAPABILITIES, GATED_KINDS
from ..ids import now_iso, parse_run_id
from ..paths import atomic_write_json, read_json

#: The only inbox filenames a web write endpoint may address. `answer-*` files belong to the
#: Decisions page and a path segment belongs to nobody: this pattern, not any caller, is what
#: keeps both out of a message PUT or DELETE.
_MSG_ID_RE = re.compile(r"^msg-[\w.+-]+$")


def merge_control(run_dir: Path, updates: dict) -> None:
    """Merge `updates` into the run's web-owned control.json (read-modify-write, atomic).
    ONE writer path for every mid-run signal — pause, switch_model, set_deliberation,
    add_rules / drop_rules — so no endpoint can drop a sibling's pending signal.
    """
    ctrl = read_json(run_dir / "control.json")
    ctrl = dict(ctrl) if isinstance(ctrl, dict) else {}
    ctrl.update(updates)
    atomic_write_json(run_dir / "control.json", ctrl)


def queued_message(inbox: Path, msg_id: str, *, via: str = "",
                   noun: str = "message") -> tuple[Path, dict]:
    """Resolve a message id to its still-queued inbox file, or 404.

    ONE implementation for every endpoint that rewrites or withdraws a queued inbox file —
    the routine Messages page and the audit feedback editor address the SAME directory, so a
    second copy of the id pattern is a guard that gets hardened on one endpoint and left
    alone on the other. `via` narrows resolution to the messages one channel wrote: the audit
    editor passes "web-audit" so it can only ever re-format its own tagged feedback, never a
    routine-page injection or a question answer. That keyword is stated at the audit call
    site and must stay there — the default here is NO filter, so dropping it silently widens
    the feedback editor to every queued file in the inbox. `noun` names the thing in
    both 404s.
    """
    if not _MSG_ID_RE.fullmatch(msg_id):
        raise HTTPException(404, f"malformed {noun} id {msg_id!r}")
    path = inbox / f"{msg_id}.json"
    obj = read_json(path)
    if not (isinstance(obj, dict) and (not via or obj.get("via") == via)):
        raise HTTPException(404,
                            f"this {noun} is no longer queued — a run already consumed it")
    return path, obj



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


def queue_or_apply(request: Request, info: registry.RoutineInfo, kind: str,
                   payload: dict, apply_now) -> dict:
    """D78-A: a non-destructive routine edit made while a run is active is HELD in the
    durable pending-edit spool and replayed at run end (daemon reap), instead of being
    bounced with a 409 'busy' toast (F279). When no run is active, `apply_now()` runs
    immediately and its result is returned verbatim.

    `kind`/`payload` are the pending_edits record shape (`payload` must be JSON-round-trip
    plain data — it is written to disk and replayed by the daemon, which never sees the
    request). `apply_now` is a zero-arg callable doing the live edit (the same effect the
    applier will have at replay). Returns either that result, or `{ok, queued:true, …}`.
    """
    from .. import pending_edits

    if not (info.active_run or request.app.state.runner.is_active(info.slug)):
        return apply_now()
    home = request.app.state.server.routines_home
    if pending_edits.pending_count(home, info.slug) >= pending_edits.MAX_PENDING_EDITS:
        raise HTTPException(429, f"{info.slug!r} already has "
                                 f"{pending_edits.MAX_PENDING_EDITS} edits queued for run "
                                 "end — wait for the active run to finish")
    pending_edits.queue(home, info.slug, kind, payload)
    return {"ok": True, "queued": True,
            "pending": pending_edits.pending_count(home, info.slug),
            "detail": "a run is active — this edit is queued and applied when the run ends"}


def permission_layers_detail(server, cfg, *,
                             routine_only: list[str] | None = None) -> tuple[list[dict], dict]:
    """The two permission layers of a detail payload (shared with conversations): every
    library conduct doc as a toggle row (held ones active; `routine_only` marks the docs a
    conversation greys out), plus the machine-enforced capabilities mapping + its vocabulary.
    """
    from .. import library_docs

    all_perms = library_docs.list_docs(server.permissions_home)
    held = set(cfg.permissions)
    permissions = [{"slug": p["slug"], "summary": p["summary"], "effect": p["effect"],
                    "title": p["title"],
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
    autocommit takes (libgit) — a rule change is allowed during a LIVE run and used to
    race the engine on git's index, the loser failing silently.
    """
    if not (routine_dir / ".git").exists():
        return
    from ..libgit import commit
    commit(routine_dir, message)


def active_run_dir(info: registry.RoutineInfo) -> Path | None:
    """The live run's directory, or None when nothing is running — so a rule change can
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


def signal_config_change(info, fields: list[str], values: dict) -> bool:
    """Tell a LIVE run that its config just changed (F337). No-op when nothing is running.

    The delivery seam is the one that already exists for reaching a running run — a signal in
    control.json, applied at the next turn boundary by `engine/switches.apply_config_change`,
    which adopts the live-classified fields and appends an ENGINE NOTE naming every changed
    field and which half it is in. Never a second invisible mutation path: whatever happens,
    the run is TOLD, which is what F337 records as missing.
    """
    run_dir = active_run_dir(info)
    if run_dir is None or not fields:
        return False
    merge_control(run_dir, {"config_change": {"fields": list(fields),
                                              "values": {k: v for k, v in values.items()
                                                         if k in fields},
                                              "ts": now_iso()}})
    return True
