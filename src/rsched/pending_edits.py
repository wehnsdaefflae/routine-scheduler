"""Mid-run edit queue — the durable spool a web routine edit lands in while a run is
active, replayed at run end (D78 option A).

The web layer edits a routine's config/files only between runs: a live run's autocommit
races the web-side git commit on the index, the loser failing silently (see
web/routines_common.guard_not_active). Historically the web bounced such an edit with a
409 "busy" toast — and an operator tuning a routine WHILE it runs hit ~20 of those in
40 minutes (F279). Option A (operator-selected 2026-08-06): accept the edit, hold it
durably, and APPLY it once the run ends, when no writer contends for the git index.

Ownership mirrors triggers.py's event spool (and restart.request, the background
.requests/ idiom): the WEB layer only RECORDS a pending edit — one JSON file per edit
under `<routines_home>/.control/pending-edits/<slug>/pe-*.json` (atomic, chronologically
sortable) — and the DAEMON replays them in order at the reap that always follows a
finish (daemon/runner.Runner._reap), so the git write happens single-writer, off the
run. Only NON-destructive config/file edits queue: destructive ops (archive, conversation
teardown) keep their hard 409, because "apply this deletion after the run" is not a safe
default.

The appliers here are the ONE implementation both paths use: an endpoint applies an edit
directly when the routine is idle, and `apply_pending` replays the SAME applier at reap —
so a queued edit and an immediate edit have identical effect. Appliers take a routine_dir
and the edit's typed payload and never touch FastAPI, so the daemon can import this
module (web imports daemon, never the reverse).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from . import libgit, recipes
from .ids import now_iso, run_ts
from .paths import atomic_write, atomic_write_json, read_json, resolve_rel

# Edit kinds that may be queued. Keep in sync with APPLIERS below and the web endpoints
# that queue them; a kind with no applier is rejected at queue time (fail closed).
QUEUEABLE_KINDS = ("file", "recipe_revert", "trigger_create", "trigger_update",
                   "trigger_delete")
MAX_PENDING_EDITS = 64   # spool cap per routine — past it the web rejects with 429


# -- appliers: pure (routine_dir, payload) -> result dict; raise on invalid ------------

def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _save_yaml(path: Path, raw: dict) -> None:
    atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))


def apply_file(routine_dir: Path, payload: dict) -> dict:
    """Write one of the routine's own files (main.md, a stage/trait module, state, or
    routine.yaml) and commit it — the replay of put_routine_file.
    """
    rel = str(payload["path"])
    p = resolve_rel(routine_dir, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(payload.get("content", "")), encoding="utf-8")
    libgit.commit(routine_dir, f"edit {rel} via web (queued mid-run)")
    return {"path": rel}


def apply_recipe_revert(routine_dir: Path, payload: dict) -> dict:
    """Roll the recipe back to before a commit — the replay of revert_recipe. Delegates
    to the same recipes.revert_recipe the endpoint uses (it self-commits under the lock).
    """
    return recipes.revert_recipe(routine_dir, str(payload["commit"]))


def apply_trigger_create(routine_dir: Path, payload: dict) -> dict:
    """Append a server-built trigger entry to routine.yaml. The entry (with its token/id)
    was built at request time so the URL could be returned then; the applier only lands it.
    """
    entry = dict(payload["entry"])
    path = routine_dir / "routine.yaml"
    raw = _load_yaml(path)
    entries = [t for t in raw.get("triggers") or [] if isinstance(t, dict)]
    # a report trigger is unique per routine — if one slipped in meanwhile, keep the first
    if entry.get("type") == "report" and any(t.get("type") == "report" for t in entries):
        return {"skipped": "a report trigger already exists", "id": entry.get("id")}
    entries.append(entry)
    raw["triggers"] = entries
    _save_yaml(path, raw)
    libgit.commit(routine_dir, f"add trigger {entry.get('id')} via web (queued mid-run)")
    return {"id": entry.get("id")}


def apply_trigger_update(routine_dir: Path, payload: dict) -> dict:
    """Retune a live trigger's fields (cooldown/day-cap) in place."""
    trigger_id = str(payload["trigger_id"])
    fields = dict(payload.get("fields") or {})
    path = routine_dir / "routine.yaml"
    raw = _load_yaml(path)
    entries = [t for t in raw.get("triggers") or [] if isinstance(t, dict)]
    target = next((t for t in entries if str(t.get("id")) == trigger_id), None)
    if target is None:
        return {"skipped": f"no trigger {trigger_id!r}", "id": trigger_id}
    target.update(fields)
    raw["triggers"] = entries
    _save_yaml(path, raw)
    libgit.commit(routine_dir, f"retune trigger {trigger_id} via web (queued mid-run)")
    return {"id": trigger_id}


def apply_trigger_delete(routine_dir: Path, payload: dict) -> dict:
    """Remove a trigger by id."""
    trigger_id = str(payload["trigger_id"])
    path = routine_dir / "routine.yaml"
    raw = _load_yaml(path)
    entries = [t for t in raw.get("triggers") or [] if isinstance(t, dict)]
    kept = [t for t in entries if str(t.get("id")) != trigger_id]
    if len(kept) == len(entries):
        return {"skipped": f"no trigger {trigger_id!r}", "id": trigger_id}
    raw["triggers"] = kept
    _save_yaml(path, raw)
    libgit.commit(routine_dir, f"remove trigger {trigger_id} via web (queued mid-run)")
    return {"id": trigger_id}


APPLIERS: dict[str, Callable[[Path, dict], dict]] = {
    "file": apply_file,
    "recipe_revert": apply_recipe_revert,
    "trigger_create": apply_trigger_create,
    "trigger_update": apply_trigger_update,
    "trigger_delete": apply_trigger_delete,
}


# -- the spool --------------------------------------------------------------------------

def spool_dir(routines_home: Path, slug: str) -> Path:
    return routines_home / ".control" / "pending-edits" / slug


def pending(routines_home: Path, slug: str) -> list[Path]:
    """Unapplied edit files, oldest first (filename sorts chronologically)."""
    d = spool_dir(routines_home, slug)
    return sorted(d.glob("pe-*.json")) if d.is_dir() else []


def pending_count(routines_home: Path, slug: str) -> int:
    return len(pending(routines_home, slug))


def queue(routines_home: Path, slug: str, kind: str, payload: dict[str, Any]) -> Path:
    """Record one pending edit durably (atomic). Raises ValueError for an unknown kind
    (fail closed) — every caller validates the kind is queueable first.
    """
    if kind not in QUEUEABLE_KINDS:
        raise ValueError(f"not a queueable edit kind: {kind!r}")
    name = f"pe-{run_ts()}-{uuid.uuid4().hex[:6]}.json"
    return atomic_write_json(spool_dir(routines_home, slug) / name,
                             {"kind": kind, "payload": payload, "ts": now_iso()})


def apply_pending(routine_dir: Path, routines_home: Path, slug: str) -> list[dict]:
    """Replay every queued edit for `slug`, oldest first, dropping each file as it is
    applied. Called from the daemon reap after a CLEAN finish — no run is active, so the
    git index is uncontended. A single edit that raises is RECORDED (surfaced, not
    swallowed) and its file dropped so one bad edit can't wedge the queue; the rest still
    apply. Returns one result row per edit for the caller to log.
    """
    results: list[dict] = []
    for path in pending(routines_home, slug):
        rec = read_json(path)
        if not isinstance(rec, dict):
            path.unlink(missing_ok=True)
            continue
        kind = str(rec.get("kind") or "")
        applier = APPLIERS.get(kind)
        row: dict = {"kind": kind, "ts": rec.get("ts")}
        try:
            if applier is None:
                raise ValueError(f"unknown edit kind {kind!r}")
            row["result"] = applier(routine_dir, dict(rec.get("payload") or {}))
            row["ok"] = True
        except (KeyError, ValueError, OSError, recipes.RecipeError) as exc:
            row["ok"] = False
            row["error"] = f"{type(exc).__name__}: {exc}"
        results.append(row)
        path.unlink(missing_ok=True)
    return results
