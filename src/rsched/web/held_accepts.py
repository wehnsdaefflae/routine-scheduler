"""The HOLD and the REPLAY of a settings accept refused mid-run (D178 + F668).

Two halves of one mechanism, in `web/` because both ends belong to the web layer: the refusal
is an endpoint's, and the apply goes through the one validated config writer this layer owns
(`api_settings.apply_settings` → `api_routine_patch.apply_updates`). A second writer in
`daemon/` or the engine would be the "second legitimate-looking route to the same boundary"
R2702 warns about — so `rsched/pending_accepts.py` holds the RECORD (web-free, the spool
vocabulary) and this performs.

**The refusal stays.** The operator chose it (D178, 2026-10-12): *"Keep refusing, but persist
the attempt as a visible pending accept with the run's expected end, so you can see it is
waiting and need not click again"*. A multi-field accept writes `routine.yaml`, the triggers
list and the goal document; a live run keeps the configuration it booted with, so half an
accept is neither the old settings nor the new. What this adds is the rest of the ANSWER —
when it can land, and a record the page renders — not an auto-apply.

**Replay goes through the endpoint, not around it.** `drain_one` calls `apply_settings` with
the held changes and a request shim (`autoapply_drain`'s idiom: the call path reaches
`request.app.state` and nothing further), so a held accept and an immediate one are validated
by the same code and land identically. Nothing re-implements the accept.

**It EXPIRES rather than overwrite a newer decision.** The record keeps the `base` each
changed field held when the accept was pressed. If a field's stored value is no longer that
base when the run ends, somebody decided it since — a grant answered on the Decisions page, a
pattern applied, another accept — and the older click does not win. The whole accept expires
(an accept is one document: applying the half that still matches would land exactly the
half-applied state the refusal exists to prevent), the record stays visible with `expired`
naming the fields that moved, and the operator re-accepts what he still wants. An expired
record is replaced by his next accept; there is no separate discard.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from fastapi import HTTPException

from .. import pending_accepts, registry
from ..ids import parse_run_id
from ..patterns import fields

log = logging.getLogger("rsched.web.held_accepts")


def _request_shim(app):
    """What the accept path needs of a `Request`: `.app.state` (server, scheduler, runner).
    Nothing in it reaches further, so the drain hands it the app rather than faking an HTTP
    request — a fake with more surface than the callee uses is a fake that drifts.
    """
    return SimpleNamespace(app=app)


def when_words(ends: str | None) -> str:
    """The expected end as a person reads it — "2026-07-08 09:05" — or "" when the routine's
    wall clock is unlimited and there is no ceiling to name.
    """
    if not ends:
        return ""
    date, _, rest = ends.partition("T")
    return f"{date} {rest[:5]}" if rest else date


def _active(request, info: registry.RoutineInfo) -> bool:
    return bool(info.active_run or request.app.state.runner.is_active(info.slug))


def hold_or_refuse(request, info: registry.RoutineInfo, *, changes: dict,
                   pattern: str | None, base: dict) -> None:
    """While a run is active: persist the accept and raise the 409 that SAYS so. No-op when
    the routine is idle, so the caller falls through to applying the accept now.

    Replaces a bare `guard_not_active` on the settings accept. The status stays 409 — nothing
    was applied, and the console's error path already renders `detail` — but the detail now
    names when the accept can land, and the attempt itself survives the click.
    """
    if not _active(request, info):
        return
    run = info.active_run
    run_id, ends = "", None
    if run is not None:
        try:
            _, ts = parse_run_id(run.run_id)
        except ValueError:
            ts = ""
        run_id = run.run_id
        ends = pending_accepts.expected_end(ts, info.cfg.budgets) if ts else None
    home = request.app.state.server.routines_home
    pending_accepts.hold(home, info.slug, changes=changes, pattern=pattern, base=base,
                         run_id=run_id, ends=ends)
    words = when_words(ends)
    raise HTTPException(409, f"{info.slug!r} is busy (a run is active) — your accept is held "
                             f"and waiting: it applies when the run ends"
                             + (f", expected by {words}" if words
                                else " (this routine's runs have no wall-clock limit)")
                             + ". You need not click again.")


def held(request, slug: str) -> dict | None:
    """The held accept as the settings payload carries it, or None.

    Pruned like a pending proposal (`patterns/drafts.prune`): a change the stored settings now
    hold is no change, and a record with nothing left to apply is cleared rather than shown —
    otherwise an accept that landed would go on claiming to be waiting. `expected_end_words`
    is the rendered time, so the page keeps no second copy of that formatting.
    """
    from .routines_common import _info, _state

    home = _state(request).server.routines_home
    rec = pending_accepts.read(home, slug)
    if rec is None:
        return None
    info = _info(request, slug)
    saved = fields.snapshot(info.cfg)
    left = {k: v for k, v in (rec.get("changes") or {}).items()
            if k in fields.BY_KEY and not fields.equal(k, saved.get(k), v)}
    switching = bool(rec.get("pattern")) and rec["pattern"] != info.cfg.pattern
    if not left and not switching:
        pending_accepts.clear(home, slug)
        return None
    labels = [fields.BY_KEY[k].label for k in sorted(left)]
    return {**rec, "changes": left, "labels": labels,
            "expected_end_words": when_words(rec.get("expected_end"))}


def drain_one(app, slug: str) -> list[dict]:
    """Apply or expire the accept held for one routine, once its run has ended.

    Returns one row (empty list when nothing is held, or the routine is still running). Never
    raises: a drain that can take the background loop down is worse than a late apply.
    """
    server = app.state.server
    home = server.routines_home
    rec = pending_accepts.read(home, slug)
    if rec is None or rec.get("expired") is not None or rec.get("error"):
        return []        # nothing held, or already settled as expired and left on the page
    info = registry.info(server, home, slug)
    if info is None:
        pending_accepts.clear(home, slug)
        log.warning("held accept dropped: no routine %r", slug)
        return [{"slug": slug, "ok": False, "error": f"no routine {slug!r}"}]
    if _active(_request_shim(app), info):
        # Still running — keep waiting, but keep the WHEN honest: a run that STARTED after the
        # accept was held makes the stored expected end another run's, and a time that lies is
        # the harm F668 recorded.
        run = info.active_run
        if run is not None and run.run_id != rec.get("run_id"):
            try:
                _, ts = parse_run_id(run.run_id)
            except ValueError:
                ts = ""
            pending_accepts.hold(
                home, slug, changes=dict(rec.get("changes") or {}), pattern=rec.get("pattern"),
                base=dict(rec.get("base") or {}), run_id=run.run_id,
                ends=pending_accepts.expected_end(ts, info.cfg.budgets) if ts else None)
        return []
    saved = fields.snapshot(info.cfg)
    if stale := pending_accepts.stale_keys(rec, saved):
        pending_accepts.expire(home, slug, stale)
        log.info("held accept for %s expired: %s moved under it", slug, ", ".join(stale))
        return [{"slug": slug, "ok": False, "expired": stale}]
    from .api_settings import ApplyBody, apply_settings

    body = ApplyBody(changes=dict(rec.get("changes") or {}), pattern=rec.get("pattern"))
    try:
        result = apply_settings(_request_shim(app), slug, body)
    except (HTTPException, OSError, ValueError) as exc:
        detail = getattr(exc, "detail", None) or str(exc)
        pending_accepts.expire(home, slug, [], error=str(detail))
        log.warning("held accept for %s could not be applied: %s", slug, detail)
        return [{"slug": slug, "ok": False, "error": str(detail)}]
    pending_accepts.clear(home, slug)
    applied = result.get("applied") or []
    log.info("held accept for %s applied at run end: %s", slug, ", ".join(applied) or "nothing")
    return [{"slug": slug, "ok": True, "applied": applied}]


def drain_all(app) -> list[dict]:
    rows: list[dict] = []
    for slug in pending_accepts.slugs_held(app.state.server.routines_home):
        rows.extend(drain_one(app, slug))
    return rows


#: How often the drain looks. Short, because this is the operator WAITING on a change he has
#: already pressed: the page tells him it applies when the run ends, so "when the run ends"
#: must not mean a minute later. One directory listing when nothing is held.
INTERVAL_S = 20


async def maintain(app, *, interval_s: float = INTERVAL_S) -> None:
    """Drain in the background for the life of the process (the app's lifespan owns the task).

    Each pass runs in a thread: it reads a directory, validates and writes `routine.yaml` under
    the repo lock — blocking work, on a loop that serves every stream and scheduler tick. A
    failing pass is logged and the loop survives it; `CancelledError` is not caught, because
    cancelling is how the lifespan stops this task.
    """
    while True:
        try:
            await asyncio.to_thread(drain_all, app)
        except asyncio.CancelledError:
            raise
        except Exception as exc:               # the loop must survive a bad pass
            log.warning("held-accept drain pass failed: %s", exc)
        await asyncio.sleep(interval_s)
