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
finish (daemon/runner_reap.reap), so the git write happens single-writer, off the
run. Only NON-destructive config/file edits queue: destructive ops (archive, conversation
teardown) keep their hard 409, because "apply this deletion after the run" is not a safe
default.

A queued edit and an immediate edit must land identically — the same atomic write, the same
commit — so each endpoint's idle-path edit (the closure it hands
`web/routines_common.queue_or_apply`) CALLS the code its applier replays: put_routine_file
calls `apply_file`, revert_recipe calls `recipes.revert_recipe`. The closure adds only what a
web response needs. Appliers take a routine_dir and the edit's typed payload and never touch
FastAPI, so the daemon can import this module (web imports daemon, never the reverse).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from . import libgit, recipes, spool
from .ids import now_iso
from .paths import atomic_write, atomic_write_yaml, read_json, read_yaml, resolve_rel

# Edit kinds that may be queued — each one an endpoint that calls queue_or_apply. Every kind
# here has an applier below; a kind without one is rejected at queue time (fail closed).
QUEUEABLE_KINDS = ("file", "recipe_revert")
MAX_PENDING_EDITS = 64   # spool cap per routine — past it the web rejects with 429


# -- appliers: (routine_dir, payload, routines_home) -> result dict; raise on invalid ----
# `routines_home` is the instance whose health stream hears about a commit that did not land.

def apply_file(routine_dir: Path, payload: dict, routines_home: Path, *,
               queued: bool = True) -> dict:
    """Write one of the routine's own files (main.md, a stage module, a script, a note) and
    commit it — put_routine_file's edit itself: the endpoint calls this with `queued=False`
    on an idle routine and the reap replays it as queued, so the commit message is the only
    difference between the two. Atomic: a scheduler scan or a starting run reads the old
    recipe or the new one, never half of it. (The endpoint refused the files it does not own
    — routine.yaml among them — before anything was queued.)
    """
    rel = str(payload["path"])
    atomic_write(resolve_rel(routine_dir, rel), str(payload.get("content", "")))
    libgit.commit(routine_dir, f"edit {rel} via web" + (" (queued mid-run)" if queued else ""),
                  routines_home=routines_home)
    return {"path": rel}


def apply_recipe_revert(routine_dir: Path, payload: dict, routines_home: Path) -> dict:
    """Roll the recipe back to before a commit — the replay of revert_recipe. Delegates
    to the same recipes.revert_recipe the endpoint uses (it self-commits under the lock).
    """
    return recipes.revert_recipe(routine_dir, str(payload["commit"]),
                                 routines_home=routines_home)


def _read_triggers(routine_dir: Path) -> tuple[dict, list[dict]]:
    """routine.yaml and its trigger entries — the read half of every trigger applier.

    A file that does not parse (ValueError) or parses to something other than a mapping
    (TypeError) fails THIS edit: `apply_pending` records it and drops it. Raw, a YAMLError
    escaped the replay (and the reap around it) with the edit still spooled, so every later
    run's end failed on the same file and every edit queued behind it never applied.
    """
    try:
        raw = read_yaml(routine_dir / "routine.yaml", {})
    except yaml.YAMLError as exc:
        raise ValueError(f"routine.yaml does not parse: {exc}") from exc
    if not isinstance(raw, dict):
        raise TypeError("routine.yaml is not a mapping")
    return raw, [t for t in raw.get("triggers") or [] if isinstance(t, dict)]


def _write_triggers(routine_dir: Path, raw: dict, entries: list[dict], message: str,
                    routines_home: Path) -> None:
    raw["triggers"] = entries
    atomic_write_yaml(routine_dir / "routine.yaml", raw)
    libgit.commit(routine_dir, f"{message} via web (queued mid-run)",
                  routines_home=routines_home)


def apply_trigger_create(routine_dir: Path, payload: dict, routines_home: Path) -> dict:
    """Append a server-built trigger entry to routine.yaml. The entry (with its token/id)
    was built at request time so the URL could be returned then; the applier only lands it.
    """
    entry = dict(payload["entry"])
    raw, entries = _read_triggers(routine_dir)
    # a report trigger is unique per routine — if one slipped in meanwhile, keep the first
    if entry.get("type") == "report" and any(t.get("type") == "report" for t in entries):
        return {"skipped": "a report trigger already exists", "id": entry.get("id")}
    _write_triggers(routine_dir, raw, [*entries, entry], f"add trigger {entry.get('id')}",
                    routines_home)
    return {"id": entry.get("id")}


def apply_trigger_update(routine_dir: Path, payload: dict, routines_home: Path) -> dict:
    """Retune a live trigger's fields (cooldown/day-cap) in place."""
    trigger_id = str(payload["trigger_id"])
    fields = dict(payload.get("fields") or {})
    raw, entries = _read_triggers(routine_dir)
    target = next((t for t in entries if str(t.get("id")) == trigger_id), None)
    if target is None:
        return {"skipped": f"no trigger {trigger_id!r}", "id": trigger_id}
    target.update(fields)
    _write_triggers(routine_dir, raw, entries, f"retune trigger {trigger_id}", routines_home)
    return {"id": trigger_id}


def apply_trigger_delete(routine_dir: Path, payload: dict, routines_home: Path) -> dict:
    """Remove a trigger by id."""
    trigger_id = str(payload["trigger_id"])
    raw, entries = _read_triggers(routine_dir)
    kept = [t for t in entries if str(t.get("id")) != trigger_id]
    if len(kept) == len(entries):
        return {"skipped": f"no trigger {trigger_id!r}", "id": trigger_id}
    _write_triggers(routine_dir, raw, kept, f"remove trigger {trigger_id}", routines_home)
    return {"id": trigger_id}


APPLIERS: dict[str, Callable[[Path, dict, Path], dict]] = {
    "file": apply_file,
    "recipe_revert": apply_recipe_revert,
    # MIGRATION(expires=2026-10-15): the trigger CRUD routes that queued these lost their last
    # caller in 0.369.0 (2026-09-30, the Triggers card moved onto the settings accept) and were
    # deleted after it, so nothing queues a trigger edit any more (they are not QUEUEABLE). A
    # run parked on a person since before then can still hold one in its spool, replayed at
    # that run's reap — these three appliers drain it. Delete them, `_read_triggers` and
    # `_write_triggers` once the date passes.
    "trigger_create": apply_trigger_create,
    "trigger_update": apply_trigger_update,
    "trigger_delete": apply_trigger_delete,
}


# -- the spool --------------------------------------------------------------------------

def spool_dir(routines_home: Path, slug: str) -> Path:
    return spool.spool_dir(routines_home, "pending-edits", slug)


def pending(routines_home: Path, slug: str) -> list[Path]:
    """Unapplied edit files, oldest first (filename sorts chronologically)."""
    return spool.pending(routines_home, "pending-edits", slug, "pe")


def pending_count(routines_home: Path, slug: str) -> int:
    return len(pending(routines_home, slug))


def queue(routines_home: Path, slug: str, kind: str, payload: dict[str, Any]) -> Path:
    """Record one pending edit durably (atomic). Raises ValueError for an unknown kind
    (fail closed) — every caller validates the kind is queueable first.
    """
    if kind not in QUEUEABLE_KINDS:
        raise ValueError(f"not a queueable edit kind: {kind!r}")
    # F298: the name must sort in QUEUE order — spool.chrono_name carries the contract.
    return spool.write(routines_home, "pending-edits", slug,
                       {"kind": kind, "payload": payload, "ts": now_iso()}, prefix="pe")


def apply_pending(routine_dir: Path, routines_home: Path, slug: str) -> list[dict]:
    """Replay every queued edit for `slug`, oldest first, dropping each file as it is
    applied. Called from the daemon reap after the run ends, in ANY terminal state (a config
    edit does not depend on the run's success) — no run is active, so the git index is
    uncontended. A single edit that raises is RECORDED (surfaced, not swallowed) and its file
    dropped so one bad edit can't wedge the queue; the rest still apply. Returns one result
    row per edit for the caller to log.
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
            row["result"] = applier(routine_dir, dict(rec.get("payload") or {}),
                                    routines_home)
            row["ok"] = True
        except (KeyError, TypeError, ValueError, OSError, recipes.RecipeError) as exc:
            row["ok"] = False
            row["error"] = f"{type(exc).__name__}: {exc}"
        results.append(row)
        path.unlink(missing_ok=True)
    return results
