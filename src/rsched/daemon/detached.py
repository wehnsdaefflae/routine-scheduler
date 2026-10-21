"""DetachedManager — the lifecycle of detached background tasks (the `detach` action).

A **detached task** is a long fire-and-forget job a conversation launches with `detach`: a
routine-shaped dir under `background_home` whose `routine.yaml` records its `owner`
conversation. Unlike a within-reply `subtask` (a thread that dies with the reply's process),
it runs as its OWN daemon-managed `engine-run` subprocess, so it survives the conversation's
reply-finishes, and reports its result back asynchronously.

This manager is the SINGLE writer of `background_home`. Ticked from the scheduler after the
cron-fire loop, it:
  1. **intake** — drains the intent files the engine drops in `.requests/`, materializes each
     task dir, writes its `routine.yaml`, and `runner.fire`s it on the runner's background pool;
  2. **deliver** — polls each task's `status.json`; on terminal, copies its artifacts into the
     owner conversation and writes a durable inbox message (guarded by a `delivered.json` marker
     + a deterministic filename, so delivery is exactly-once across restarts);
  3. **wake** — resumes an idle owner so the result reaches the user (else a live reply drains it);
  4. **digest** — rebuilds each owner's `state/background.json` (the "how's the scrape
     going?" source);
  5. **gc** — removes delivered task dirs past a grace window.

ALL state lives on disk, so the manager is stateless across ticks and restart-safe: a `tick()`
and the boot `reconcile()` are the same idempotent pass.

**The GLOBAL PAUSE (D34) is enforced HERE, per phase, not by the caller** (F721). The pause's
whole promise is that the scheduler starts NOTHING on its own, and this lifecycle starts runs at
exactly two points: intake's `runner.fire` and wake's `resume_terminal`. Those two are HELD while
the sentinel exists; **deliver, digest and gc proceed**, because a finished task's hand-back
starts nothing — it copies artifacts and writes a durable inbox message, and the owner reads it
whenever it next runs. Holding delivery too would buy nothing and lose something: the result is
already paid for, and a pause lifted days later would hand back artifacts the gc had meanwhile
removed.

The split is per-PHASE and not per-tick because "delivery" is not uniformly free of starting
things: the wake is part of hand-back in prose but `resume_terminal` is a run leg, and a leg
resumed under a pause is not held by `engine/control.pause_gate` either — that gate holds only
runs MARKED at the pause click (`web/settings/pause.py`), and a fresh resume carries no mark. So
the wake waits. Nothing is lost by waiting: `wake` is state-driven (a delivered marker plus a
pending message), so the first tick after the resume wakes the owner, and intake's request file
is never consumed unfired — the same promise the one-shot intake makes.

The gate lives in this module rather than in the scheduler's `if not is_paused:` block so that
the boot path inherits it: `_boot` calls `reconcile()` BEFORE the pause-checking `boot_catchup`,
so a restart under a pause fired every undelivered task, and a guard at the single call site in
the tick loop would not have covered it.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from .. import registry
from ..config import DEFAULT_BUDGETS, RoutineConfig, ServerConfig, load_routine
from ..paths import atomic_write_json, atomic_write_yaml, read_json, read_yaml
from ..schedule import server_tz
from . import detached_delivery, pause
from .detached_delivery import _has_pending_bg_message
from .runner import Runner

log = logging.getLogger("rsched.detached")
# A detached task gets a background-sized budget, NOT the owner conversation's per-reply window,
# which is sized for an interactive handover and would starve a 20-minute job. Everything else
# (perms/models/fs-roots) is copied from the owner; budgets are deliberately its own.
BACKGROUND_BUDGETS = {**DEFAULT_BUDGETS, "max_wall_clock_min": 60}
# A fire-and-forget task authoring library utils, writing throwaway .memory notes, or nesting
# another detach is pure waste (and write_util triggers a blocking approval ask no one answers),
# so these gated kinds are stripped from the capabilities copied off the owner.
_STRIP_ACTIONS = ("write_util", "remove_util", "memory_read", "memory_write", "detach")
DELIVERED_GRACE_S = 3600  # remove a delivered task dir this long after delivery


class DetachedManager:
    """Owns the detached-task lifecycle; constructed with the shared server + runner and
    ticked by the Scheduler. Single writer of background_home.
    """

    def __init__(self, server: ServerConfig, runner: Runner):
        self.server = server
        self.runner = runner
        self.home = server.background_home
        self.requests_dir = self.home / ".requests"
        self.requests_dir.mkdir(parents=True, exist_ok=True)

    # -- public entry points ----------------------------------------------------------------

    async def tick(self, _now: datetime | None = None) -> None:
        """One lifecycle pass: intake → deliver → wake → digest → gc. Never raises into the
        scheduler loop (a per-task failure is logged and skipped).

        A global PAUSE (D34) holds the two phases that START a run — intake's `runner.fire` and
        the wake's `resume_terminal` — and lets deliver, digest and gc proceed (F721). The
        module docstring says why the line is drawn there, and why this gate is here rather
        than at the scheduler's call site.

        Neither hold files a health event: nothing is DROPPED, so neither is "work that was due
        did not happen" (`readmodels.health_stream.BLOCKED_EVENTS`) — it is deferred exactly as
        trigger and one-shot intake are, and each deferral has its own durable record on disk
        (a held fire keeps its `.requests/<taskid>.json`, a held wake its `delivered.json` plus
        the owner's pending inbox message). They log at DEBUG and not INFO because this tick
        runs every 5 s: a line per held tick would be hours of noise for one standing fact.
        """
        try:
            held = pause.paused(self.server)
            if not held:
                await self._intake()
            elif waiting := sorted(p.stem for p in self.requests_dir.glob("*.json")):
                log.debug("scheduling paused — detached intake held: %s", ", ".join(waiting))
            catalog = registry.scan(self.server, self.home)
            await detached_delivery.deliver(self, catalog)
            if not held:
                await detached_delivery.wake(self, catalog)
            else:
                log.debug("scheduling paused — owner wake held; deliveries still hand back")
            self._rebuild_digests(catalog)
            await self._gc(catalog)
        except Exception:
            log.exception("detached tick failed")

    async def reconcile(self) -> None:
        """Boot pass: re-attempt any undelivered terminal task (run after the scheduler's
        recover_orphans(background_home) has closed out dead-pid mid-run tasks). Same work as
        a tick — the design is deliberately idempotent, pause hold included: `_boot` calls this
        BEFORE the pause-checking `boot_catchup`, so a restart under a pause once fired every
        undelivered task (F721). It still DELIVERS them; it starts nothing.
        """
        await self.tick()

    # -- 1. intake --------------------------------------------------------------------------

    async def _intake(self) -> None:
        for req_path in sorted(self.requests_dir.glob("*.json")):
            req = read_json(req_path)
            if not isinstance(req, dict) or not req.get("taskid") or not req.get("owner"):
                log.warning("detached: dropping malformed request %s", req_path.name)
                req_path.unlink(missing_ok=True)
                continue
            taskid = str(req["taskid"])
            task_dir = self.home / taskid
            try:
                if self._already_fired(taskid, task_dir):
                    req_path.unlink(missing_ok=True)  # crash-after-fire: run exists, don't re-fire
                    continue
                if await self._materialize_and_fire(taskid, task_dir, req):
                    req_path.unlink(missing_ok=True)  # delete only once a run exists
            except Exception:
                log.exception("detached: intake of %s failed — leaving request for retry", taskid)

    def _already_fired(self, taskid: str, task_dir: Path) -> bool:
        """Idempotency is keyed on RUN existence, not dir existence: a crash between
        materialize and fire leaves a dir but no run, and must still be fired.
        """
        if self.runner.is_active(taskid):
            return True
        runs = task_dir / "runs"
        return runs.is_dir() and any((d / "status.json").exists()
                                     for d in runs.iterdir() if d.is_dir())

    async def _materialize_and_fire(self, taskid: str, task_dir: Path, req: dict) -> bool:
        """True once the request is handled — fired, or dropped as unrunnable — so it can be
        removed. The disk work (the library read, a `git rev-parse` in it, the task's own files)
        runs in a thread: on the loop thread it stalled every request and tick while it ran.
        """
        cfg = await asyncio.to_thread(self._materialize, taskid, task_dir, req)
        if cfg is None:
            return True  # nothing to run (owner gone, or a dir that does not load): consumed
        rid = await self.runner.fire(cfg, reason="detached")
        if rid:
            log.info("detached fired task=%s workflow=%s owner=%s run=%s", taskid,
                     cfg.workflow_slug, req["owner"].get("slug"), rid)
            return True
        return False  # draining / transient — keep the request for the next tick

    def _materialize(self, taskid: str, task_dir: Path, req: dict) -> RoutineConfig | None:
        """Write the task dir and its routine.yaml; the loaded config, or None when there is
        nothing to run (a broken dir is not retried: the request is consumed).
        """
        from ..engine.childrun import materialize_to_disk

        owner = req["owner"]
        owner_dir = Path(owner["dir"])
        if not (owner_dir / "routine.yaml").exists():
            log.warning("detached: owner %s of %s is gone — dropping request",
                        owner.get("slug"), taskid)
            return None
        workflow = str(req.get("workflow") or "general-task")
        for sub in ("state", "inbox", "artifacts"):
            (task_dir / sub).mkdir(parents=True, exist_ok=True)
        materialize_to_disk(self.server, workflow, task_dir, str(req.get("prompt") or ""))
        self._write_task_yaml(taskid, task_dir, req, owner_dir, workflow)
        cfg, problems = load_routine(task_dir)
        if cfg is None:
            log.error("detached: task %s has an unloadable routine.yaml (%s)",
                      taskid, "; ".join(problems))
        return cfg

    def _write_task_yaml(self, taskid: str, task_dir: Path, req: dict, owner_dir: Path,
                         workflow: str) -> None:
        from ..workflows.library import head_commit

        raw = read_yaml(owner_dir / "routine.yaml", {})
        label = str(req.get("label") or taskid)
        caps = _strip_capabilities(raw.get("capabilities"))
        cfg: dict = {
            "name": label,
            "slug": taskid,
            "description": f"background task for {req['owner'].get('slug', '?')}: {label}",
            "enabled": True,
            "schedule": {"cron": "", "tz": raw.get("schedule", {}).get("tz", server_tz()),
                         "catchup": "skip"},
            "workflow": {"library_slug": workflow,
                         "library_commit": head_commit(self.server.libraries_home)},
            "owner": {"slug": str(req["owner"].get("slug", "")), "dir": str(owner_dir)},
            **({"models": raw["models"]} if raw.get("models") else {}),
            "permissions": list(raw.get("permissions") or []),
            "capabilities": caps,
            "budgets": dict(BACKGROUND_BUDGETS),
            "retention": {"keep_runs": 5},
        }
        for key in ("fs_read_roots", "fs_write_roots"):
            if raw.get(key):
                cfg[key] = list(raw[key])
        atomic_write_yaml(task_dir / "routine.yaml", cfg)

    # -- 2. digest --------------------------------------------------------------------------

    def _rebuild_digests(self, catalog: dict[str, registry.RoutineInfo]) -> None:
        by_owner: dict[str, tuple[Path, list[dict]]] = {}
        for taskid, info in catalog.items():
            owner = info.cfg.owner or {}
            slug = str(owner.get("slug") or "")
            owner_dir = Path(owner.get("dir", ""))
            if not slug or not (owner_dir / "routine.yaml").exists():
                continue
            last = info.last_run
            row = {"taskid": taskid, "label": info.cfg.name or taskid,
                   "state": (last.state if last else "pending"),
                   "run_id": (last.run_id if last else ""),
                   "started": (last.ts if last else ""),
                   "delivered": (info.cfg.dir / "delivered.json").exists()}
            by_owner.setdefault(slug, (owner_dir, []))[1].append(row)
        for owner_dir, rows in by_owner.values():
            rows.sort(key=lambda r: r["started"])
            self._write_digest_if_changed(owner_dir, rows)

    @staticmethod
    def _write_digest_if_changed(owner_dir: Path, rows: list[dict]) -> None:
        path = owner_dir / "state" / "background.json"
        if read_json(path) == rows:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, rows)

    # -- 3. gc ------------------------------------------------------------------------------

    async def _gc(self, catalog: dict[str, registry.RoutineInfo]) -> None:
        now = datetime.now(UTC).timestamp()
        cleared_owners: dict[str, Path] = {}
        for taskid, info in catalog.items():
            marker = info.cfg.dir / "delivered.json"
            if not marker.exists():
                continue
            try:
                age = now - marker.stat().st_mtime
            except OSError:
                continue
            if age < DELIVERED_GRACE_S:
                continue
            owner = info.cfg.owner or {}
            if _has_pending_bg_message(Path(owner.get("dir", "")), taskid):
                continue  # owner hasn't drained the delivery yet — keep the dir until it does
            # a whole task dir — its runs and artifacts — so off the loop thread
            await asyncio.to_thread(shutil.rmtree, info.cfg.dir, ignore_errors=True)
            if owner.get("slug") and owner.get("dir"):
                cleared_owners[str(owner["slug"])] = Path(owner["dir"])
            log.info("detached gc removed task=%s", taskid)
        # an owner whose LAST task was just GC'd needs its digest emptied — a second scan of
        # the home, so only on a tick that removed something
        if not cleared_owners:
            return
        remaining = {str((i.cfg.owner or {}).get("slug") or "")
                     for i in registry.scan(self.server, self.home).values()}
        for slug, owner_dir in cleared_owners.items():
            if slug not in remaining and (owner_dir / "routine.yaml").exists():
                self._write_digest_if_changed(owner_dir, [])


def _strip_capabilities(caps: object) -> dict:
    """Copy the owner's capabilities but remove the gated kinds a fire-and-forget task should
    never use. A non-dict (or missing) capabilities block copies to an empty surface.
    """
    if not isinstance(caps, dict):
        return {}
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in caps.items()}
    if isinstance(out.get("actions"), list):
        out["actions"] = [a for a in out["actions"] if a not in _STRIP_ACTIONS]
    return out


