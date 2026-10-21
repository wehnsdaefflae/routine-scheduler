"""The APPLY half of D191 step 3: drain the autonomous-apply spool through the one
validated config writer.

This lives in `web/` because the writer does: `api_routine_patch.apply_updates` validates
every field, writes `routine.yaml` atomically, commits under the repo lock, rescans the fire
table and tells a live run what changed (F337). A second writer anywhere else would be the
"second legitimate-looking route to the same boundary" R2702 warns about — so the engine
queues (`rsched/autoapply.py`) and this performs.

**The boundary check is HERE.** `configdial.refusals` is read again with the routine's CURRENT
dial and the patch as queued: the ask-time check exists to tell the run at once, this one
exists to make it true. A request from a routine whose dial has since been turned down does
not apply, and neither does one whose keys are not on the allow-list.

**A refusal or a failure becomes an ordinary decision card** under the request's own `qid`, so
the operator sees the change he would have seen anyway, with a line saying the autonomous
apply was refused and why. The one state this must never reach is a config change that neither
applied nor asked.

**Who drives it.** `maintain` is a background loop started by the app's own lifespan, beside
the search maintainer and the push listener (`web/appwiring.py`) — the web layer drives the
work the web layer owns, so `daemon/` still imports nothing from `web/` and the scheduler's
tick is untouched. One daemon process runs it, so the drain is single-writer by construction;
each pass is one directory listing when there is nothing to do.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from .. import autoapply, configdial, registry
from ..engine import inbox_questions
from ..ids import now_iso

log = logging.getLogger("rsched.autoapply")


def _request_shim(app):
    """What `apply_updates` needs of a `Request`: `.app.state`. Nothing in the call path
    reaches further (it uses `_state(request).server`, `.scheduler` and the git commit
    helper), so the drain hands it the app itself rather than faking an HTTP request —
    a fake with more surface than the callee uses is a fake that drifts.
    """
    return SimpleNamespace(app=app)


def _file_card(server, slug: str, rec: dict, why: str) -> None:
    """Write the decision record the ask would have filed, plus WHY it is a card after all."""
    routine_dir = server.routines_home / slug
    question = str(rec.get("question") or "").strip() or "a config change this run proposed"
    inbox_questions.file_question(
        routine_dir, str(rec.get("qid") or f"aa-{now_iso()}"),
        f"{question}\n\n[the autonomous apply was refused: {why}]",
        [], str(rec.get("ts") or now_iso()),
        qtype="question", config_patch=dict(rec.get("patch") or {}))


def drain_one(app, slug: str) -> list[dict]:
    """Apply every queued request for one routine, oldest first; drop each file as it is
    handled. Never raises: a drain that can take the tick down is worse than a late apply.
    """
    server = app.state.server
    rows: list[dict] = []
    for path in autoapply.pending(server.routines_home, slug):
        rec = autoapply.read_request(path)
        patch = dict(rec.get("patch") or {})
        try:
            info = registry.info(server, server.routines_home, slug)
            if info is None:
                raise LookupError(f"no routine {slug!r}")
            # The CURRENT dial, not the one the request was queued under: a dial turned down
            # between the ask and the drain must stop the apply.
            level = configdial.level_of(info.cfg.capabilities)
            if refusals := configdial.refusals(patch, level):
                _file_card(server, slug, rec, refusals[0])
                rows.append({"slug": slug, "ok": False, "refused": refusals[0]})
            else:
                from .api_routine_patch import apply_updates

                result = apply_updates(
                    _request_shim(app), info, dict(patch),
                    message=f"routine.yaml autonomous apply ({', '.join(sorted(patch))}) "
                            f"— config_confirm: {level}")
                rows.append({"slug": slug, "ok": True,
                             "updated": result.get("updated") or []})
                log.info("auto-apply for %s: %s", slug,
                         ", ".join(result.get("updated") or []))
        except Exception as exc:               # a drain must never raise into the loop
            log.warning("auto-apply for %s failed: %s", slug, exc)
            try:
                _file_card(server, slug, rec, f"the apply failed: {exc}")
            except Exception as inner:         # the fallback card is best-effort too
                log.warning("auto-apply fallback card for %s failed: %s", slug, inner)
            rows.append({"slug": slug, "ok": False, "error": str(exc)})
        path.unlink(missing_ok=True)
    return rows


def drain_all(app) -> list[dict]:
    """Every routine with queued requests, oldest first within each."""
    rows: list[dict] = []
    for slug in autoapply.slugs_with_pending(app.state.server.routines_home):
        rows.extend(drain_one(app, slug))
    return rows


#: How often the drain looks. A queued apply is not urgent — the run that filed it has already
#: been told the patch was taken, and the value reaches that routine at its NEXT run — but a
#: minute keeps "autonomous" honest on the console too: the operator who looks sees the change
#: landed, not a card he has to wait for.
INTERVAL_S = 60


async def maintain(app, *, interval_s: float = INTERVAL_S) -> None:
    """Drain in the background for the life of the process (the app's lifespan owns the task).

    Each pass runs in a thread: it reads a directory, validates, writes `routine.yaml`, and
    commits under the repo lock — all blocking work, and the loop serves every stream and
    every scheduler tick. A failing pass is logged and the loop survives it: a drain that can
    end the task would leave every later request queued forever, which is worse than one late
    apply. `CancelledError` is NOT caught — the lifespan stops this task by cancelling it.
    """
    while True:
        try:
            await asyncio.to_thread(drain_all, app)
        except asyncio.CancelledError:
            raise
        except Exception as exc:               # the loop must survive a bad pass
            log.warning("auto-apply drain pass failed: %s", exc)
        await asyncio.sleep(interval_s)
