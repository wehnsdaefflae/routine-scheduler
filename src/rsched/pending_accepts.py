"""The HELD settings accept — a multi-field accept pressed while a run was active (D178).

The settings page's accept stays REFUSED during a run: it writes several owners at once
(`routine.yaml` through the validated PATCH writer, the triggers list, the goal document) and
a live run keeps the configuration it booted with, so a half-applied proposal would be neither
the old settings nor the new (`web/routines_common.guard_not_active`). The operator's decision
(2026-10-12, D178): *"Keep refusing, but persist the attempt as a visible pending accept with
the run's expected end, so you can see it is waiting and need not click again"*.

What was wrong is not that the refusal was unanswered — the accept bar has always toasted the
409 (`static/components/accept-bar.js`, and `util.js toast()` traces every error toast). It is
that the answer said only "try again after it ends", named no WHEN, and left nothing behind: on
2026-10-11 the operator pressed the accept on `freelance-radar` THREE TIMES IN ONE SECOND while
that run was 78 minutes from ending, and concluded the button was broken (F668). So this module
holds the attempt, carries the run's expected end, and the console renders it.

**ONE record per routine, addressed by a fixed name.** The entry is `pa-current.json`, not a
chrono name: a second press REPLACES the first (`spool.write(name=…)`, the id-addressed form),
so three clicks in one second leave exactly one held accept — the newest, which is the one the
operator meant. The older spools here are append-queues because every entry is a separate edit;
a held accept is a STATE ("these changes wait"), and a queue of them would apply three times.

**The expected end is the run's wall-clock ceiling, not an estimate.** `expected_end` is the
run's start plus its `max_wall_clock_min` budget. Nothing in this product estimates when a run
will finish, and inventing an estimator for a toast would be inventing a number: this is the
UPPER bound the instance already enforces, so the operator reads "by 14:12 at the latest". A
routine whose wall-clock budget is unlimited (-1) has no such bound and the record says so with
`None` — the surfaces then say the accept applies when the run ends, without a time.

**Applies or expires at run end** (the operator's words). The web layer owns both halves,
because the validated config writer is its own (`web/held_accepts.py`): it replays the held
accept through the SAME endpoint an idle accept takes, so a held accept and an immediate one
land identically. It EXPIRES — is not applied — when the stored settings moved under it while
the run held it: the record keeps the `base` the accept was computed against, and a field whose
saved value is no longer that base is a value somebody else decided since. Applying half of an
accept, or overwriting a newer decision with an older click, is worse than telling the operator
his accept did not land; `stale_keys` names exactly which fields moved.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import spool
from .ids import now_iso
from .paths import read_json

#: The spool family under `<routines_home>/.control/`, and THE entry — one per routine.
FAMILY = "pending-accepts"
ENTRY = "pa-current.json"
#: What `spool.pending`/`slugs_with` glob for (`pa-*.json` matches ENTRY).
PREFIX = "pa"


def path(routines_home: Path, slug: str) -> Path:
    return spool.spool_dir(routines_home, FAMILY, slug) / ENTRY


def expected_end(run_ts: str, budgets: dict | None) -> str | None:
    """When the active run must be over: its start plus `max_wall_clock_min`, or None when
    that budget is unlimited (-1) or the stamp is unreadable.

    `run_ts` is a run directory's `YYYYMMDD-HHMMSS` id, which `ids.run_ts` writes in UTC.
    Time a run spends waiting on the operator is credited back to the budget
    (`static/components/budgetfields.js`), so this is a ceiling the run can beat — never a
    prediction it can overrun.
    """
    from .config import DEFAULT_BUDGETS

    wall = (budgets or {}).get("max_wall_clock_min", DEFAULT_BUDGETS["max_wall_clock_min"])
    try:
        minutes = int(wall)
    except (TypeError, ValueError):
        return None
    if minutes < 0:
        return None
    try:
        start = datetime.strptime(run_ts, "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    return (start + timedelta(minutes=minutes)).astimezone().isoformat(timespec="seconds")


def hold(routines_home: Path, slug: str, *, changes: dict[str, Any], pattern: str | None,
         base: dict[str, Any], run_id: str, ends: str | None) -> dict:
    """Persist the refused accept, REPLACING any accept already held for this routine.

    `base` is what each changed field held when the accept was pressed — the stored values the
    person was looking at. It is what `stale_keys` reads at run end.
    """
    record = {"slug": slug, "queued": now_iso(), "run_id": run_id, "expected_end": ends,
              "changes": dict(changes), "pattern": pattern, "base": dict(base)}
    spool.write(routines_home, FAMILY, slug, record, name=ENTRY)
    return record


def read(routines_home: Path, slug: str) -> dict | None:
    """The held accept, or None. A record with no changes and no pattern switch holds
    nothing and reads as none — the same rule `patterns/drafts.read` applies.
    """
    rec = read_json(path(routines_home, slug))
    if not isinstance(rec, dict) or not isinstance(rec.get("changes"), dict):
        return None
    if not rec["changes"] and not rec.get("pattern"):
        return None
    return rec


def clear(routines_home: Path, slug: str) -> None:
    path(routines_home, slug).unlink(missing_ok=True)


def expire(routines_home: Path, slug: str, stale: list[str], *, error: str = "") -> dict | None:
    """Mark the held accept as EXPIRED, in place — it stays on the page instead of vanishing.

    An accept that disappeared at run end would read exactly like one that applied, which is
    the failure mode F668 is about. `stale` names the fields that moved under it (empty when
    the accept was refused for another reason, which `error` then states). The record is no
    longer drained: `read` still returns it, and the operator's next accept replaces it.
    """
    rec = read(routines_home, slug)
    if rec is None:
        return None
    rec = {**rec, "expired": sorted(stale), "expired_at": now_iso()}
    if error:
        rec["error"] = error
    spool.write(routines_home, FAMILY, slug, rec, name=ENTRY)
    return rec


def slugs_held(routines_home: Path) -> list[str]:
    """Every routine holding an accept, in name order — what the drain iterates."""
    return spool.slugs_with(routines_home, FAMILY, PREFIX)


def stale_keys(record: dict, saved: dict) -> list[str]:
    """The changed fields whose SAVED value is no longer the one the accept was computed
    against — somebody decided those since the click. Empty means the accept still applies.
    """
    from .patterns import fields

    base = record.get("base") or {}
    return sorted(k for k in (record.get("changes") or {})
                  if k in fields.BY_KEY and not fields.equal(k, base.get(k), saved.get(k)))
