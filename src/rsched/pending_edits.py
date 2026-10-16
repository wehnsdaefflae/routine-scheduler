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

from . import libgit, recipes, spool
from .ids import now_iso
from .paths import atomic_write, read_json, resolve_rel

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
    # SCOPED to the edited file (`paths=` + `only=True`): an unscoped stage swept up whatever
    # else happened to be dirty in the routine's tree and shipped it under this message.
    # library-sync commit 5baf069 "edit stages/export.md via web" carried that routine's
    # pending run-retention changes — deleted and gzipped `runs/*` plus a status.json — so the
    # history says a stage module was edited and the diff says something else entirely.
    libgit.commit(routine_dir, f"edit {rel} via web" + (" (queued mid-run)" if queued else ""),
                  routines_home=routines_home, paths=[rel], only=True)
    return {"path": rel}


def apply_recipe_revert(routine_dir: Path, payload: dict, routines_home: Path) -> dict:
    """Roll the recipe back to before a commit — the replay of revert_recipe. Delegates
    to the same recipes.revert_recipe the endpoint uses (it self-commits under the lock).
    """
    return recipes.revert_recipe(routine_dir, str(payload["commit"]),
                                 routines_home=routines_home)


APPLIERS: dict[str, Callable[[Path, dict, Path], dict]] = {
    "file": apply_file,
    "recipe_revert": apply_recipe_revert,
}
# The three `trigger_*` appliers (and the `_read_triggers`/`_write_triggers` pair they shared)
# lived here to drain spools written before 0.369.0, when the Triggers card moved onto the
# settings accept and the CRUD routes that queued them were deleted. Their
# deprecation (marker expiry 2026-10-15) came due and the drain is complete: no spool file is on the
# instance and `trigger_*` was never in QUEUEABLE_KINDS, so nothing could queue one. A stale
# spooled edit of a kind with no applier is still safe — `apply_pending` raises ValueError for
# it, records the row and drops the file, which is the same path a malformed edit takes.


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
