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
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ..config import DEFAULT_BUDGETS, ServerConfig, load_routine
from ..ids import now_iso
from ..paths import atomic_write, atomic_write_json, read_json
from . import registry
from .runner import Runner, _pid_alive

log = logging.getLogger("rsched.detached")
# A detached task gets a background-sized budget, NOT the owner conversation's per-reply window
# (max_turns 10) which would starve a 20-minute job. Everything else (perms/models/fs-roots) is
# copied from the owner; budgets are deliberately its own.
BACKGROUND_BUDGETS = {**DEFAULT_BUDGETS, "max_wall_clock_min": 60}
# A fire-and-forget task authoring library utils, writing throwaway .memory notes, or nesting
# another detach is pure waste (and write_util triggers a blocking approval ask no one answers),
# so these gated kinds are stripped from the capabilities copied off the owner.
_STRIP_ACTIONS = ("write_util", "memory_read", "memory_write", "detach")
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
        """
        try:
            await self._intake()
            catalog = registry.scan(self.server, self.home)
            await self._deliver(catalog)
            await self._wake(catalog)
            self._rebuild_digests(catalog)
            self._gc(catalog)
        except Exception:
            log.exception("detached tick failed")

    async def reconcile(self) -> None:
        """Boot pass: re-attempt any undelivered terminal task (run after the scheduler's
        recover_orphans(background_home) has closed out dead-pid mid-run tasks). Same work as
        a tick — the design is deliberately idempotent.
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
        from ..engine.childrun import materialize_to_disk

        owner = req["owner"]
        owner_dir = Path(owner["dir"])
        if not (owner_dir / "routine.yaml").exists():
            log.warning("detached: owner %s of %s is gone — dropping request",
                        owner.get("slug"), taskid)
            return True  # nothing to run for; treat as handled so the request is removed
        workflow = str(req.get("workflow") or "general-task")
        for sub in ("state", "inbox", "artifacts"):
            (task_dir / sub).mkdir(parents=True, exist_ok=True)
        materialize_to_disk(self.server, workflow, task_dir, str(req.get("prompt") or ""))
        self._write_task_yaml(taskid, task_dir, req, owner_dir, workflow)
        cfg, problems = load_routine(task_dir)
        if cfg is None:
            log.error("detached: task %s has an unloadable routine.yaml (%s)",
                      taskid, "; ".join(problems))
            return True  # don't spin on a broken dir; the request is consumed
        rid = await self.runner.fire(cfg, reason="detached")
        if rid:
            log.info("detached fired task=%s workflow=%s owner=%s run=%s",
                     taskid, workflow, owner.get("slug"), rid)
            return True
        return False  # draining / transient — keep the request for the next tick

    def _write_task_yaml(self, taskid: str, task_dir: Path, req: dict, owner_dir: Path,
                         workflow: str) -> None:
        from ..workflows.library import head_commit

        raw = yaml.safe_load((owner_dir / "routine.yaml").read_text(encoding="utf-8")) or {}
        label = str(req.get("label") or taskid)
        caps = _strip_capabilities(raw.get("capabilities"))
        cfg: dict = {
            "name": label,
            "slug": taskid,
            "description": f"background task for {req['owner'].get('slug', '?')}: {label}",
            "enabled": True,
            "schedule": {"cron": "", "tz": raw.get("schedule", {}).get("tz", "Europe/Berlin"),
                         "catchup": "skip"},
            "workflow": {"library_slug": workflow,
                         "library_commit": head_commit(self.server.library_home)},
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
        atomic_write(task_dir / "routine.yaml",
                     yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))

    # -- 2. deliver -------------------------------------------------------------------------

    async def _deliver(self, catalog: dict[str, registry.RoutineInfo]) -> None:
        for taskid, info in catalog.items():
            if (info.cfg.dir / "delivered.json").exists():
                continue
            state = self._terminal_state(info)
            if state is None:
                continue
            try:
                await self._deliver_one(taskid, info, state)
            except Exception:
                log.exception("detached: delivery of %s failed — will retry next tick", taskid)

    def _terminal_state(self, info: registry.RoutineInfo) -> str | None:
        """The finished state to deliver, or None if the task is still live. A task counts as
        terminal when its status.json says so OR — for a task that survived a restart and is no
        longer tracked — when its pid is dead (crashed/orphaned without a finish).
        """
        last = info.last_run
        if last is None:
            return None
        if last.state in registry.TERMINAL_STATES:
            return last.state
        if info.slug not in self.runner.active and not _pid_alive(last.pid):
            return "failed"
        return None

    async def _deliver_one(self, taskid: str, info: registry.RoutineInfo, state: str) -> None:
        task_dir = info.cfg.dir
        owner = info.cfg.owner or {}
        owner_dir = Path(owner.get("dir", ""))
        if not owner.get("dir") or not (owner_dir / "routine.yaml").exists():
            atomic_write_json(task_dir / "delivered.json", {"ts": now_iso(), "owner": "missing"})
            log.info("detached: owner of %s missing at delivery — dropped", taskid)
            return
        copied = await self._copy_artifacts(task_dir, owner_dir, taskid)
        # msg FIRST, delivered.json SECOND, both without an await between → a consumer (a later
        # resume) can never see the msg before the marker; a crash in the tiny gap re-delivers
        # the same deterministic filename, so still exactly one pending message.
        atomic_write_json(owner_dir / "inbox" / f"msg-bg-{taskid}.json",
                          {"text": self._delivery_text(info, state, taskid, copied),
                           "ts": now_iso(), "via": "background"})
        atomic_write_json(task_dir / "delivered.json",
                          {"ts": now_iso(), "state": state, "owner": owner.get("slug")})
        log.info("detached delivered task=%s state=%s owner=%s artifacts=%d",
                 taskid, state, owner.get("slug"), copied)
        await self._maybe_ping_discord(owner_dir, info.cfg.name or taskid, state)

    async def _maybe_ping_discord(self, owner_dir: Path, label: str, state: str) -> None:
        """A best-effort nudge to the owner's Discord channel (the RESULT is in the conversation;
        this just tells an away user to look). Gated on the owner holding `communication`;
        the send goes through the one outbound seam (rsched.notify).
        """
        try:
            from .. import notify

            raw = yaml.safe_load((owner_dir / "routine.yaml").read_text(encoding="utf-8")) or {}
            if not notify.discord_enabled(self.server, permissions=raw.get("permissions") or []):
                return
            verb = {"finished": "finished", "aborted": "was cancelled"}.get(state, "failed")
            msg = f"🔔 Background task “{label}” {verb} — open the conversation to see the result."
            await asyncio.to_thread(notify.send, self.server, msg)
        except Exception:
            log.info("detached: discord ping skipped for %s", owner_dir.name)

    async def _copy_artifacts(self, task_dir: Path, owner_dir: Path, taskid: str) -> int:
        src = task_dir / "artifacts"
        if not src.is_dir() or not any(src.iterdir()):
            return 0
        dst = owner_dir / "artifacts" / f"from-bg-{taskid}"
        # namespaced + overwrite: never clobber the conversation's own artifacts, and idempotent
        # on re-delivery. Blocking fs op → off the event loop.
        await asyncio.to_thread(shutil.copytree, src, dst, dirs_exist_ok=True)
        return sum(1 for _ in dst.rglob("*") if _.is_file())

    def _delivery_text(self, info: registry.RoutineInfo, state: str, taskid: str,
                       copied: int) -> str:
        label = info.cfg.name or taskid
        verb = {"finished": "finished", "failed": "failed",
                "aborted": "was cancelled"}.get(state, state)
        summary = (info.last_run.summary if info.last_run else "") or "(no summary was written.)"
        lines = [f"[background task {verb}] The detached task “{label}” {verb}.", "", summary]
        if copied:
            lines += ["", f"Its {copied} artifact(s) were copied to `artifacts/from-bg-{taskid}/`."]
        lines += ["", "Relay this result to me. (Full status of your background tasks is in "
                  "`state/background.json`.)"]
        return "\n".join(lines)

    # -- 3. wake ----------------------------------------------------------------------------

    async def _wake(self, catalog: dict[str, registry.RoutineInfo]) -> None:
        """Resume any owner that is idle (terminal last run) with a pending inbox message and is
        not active/draining — state-driven, so it also catches the race where the owner finished
        a reply just after we wrote the message. A live owner is skipped: its running reply drains
        the message at the next turn boundary. Idempotent vs the message endpoint's own resume
        (both go through runner.resume, which refuses a second concurrent resume).
        """
        seen: set[str] = set()
        for info in catalog.values():
            if not (info.cfg.dir / "delivered.json").exists():
                continue
            owner = info.cfg.owner or {}
            slug = str(owner.get("slug") or "")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            owner_dir = Path(owner.get("dir", ""))
            if not (owner_dir / "routine.yaml").exists():
                continue
            await self._wake_owner(owner_dir, slug)

    async def _wake_owner(self, owner_dir: Path, slug: str) -> None:
        if self.runner.is_active(slug) or self.runner.draining:
            return
        if not _has_pending_inbox(owner_dir):
            return
        owner_cfg, _ = load_routine(owner_dir)
        if owner_cfg is None:
            return
        # a non-terminal last run means a live reply is already draining the message
        rid = await self.runner.resume_terminal(owner_cfg, reason="detached")
        if rid:
            log.info("detached woke owner=%s run=%s", slug, rid)

    # -- 4. digest --------------------------------------------------------------------------

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

    # -- 5. gc ------------------------------------------------------------------------------

    def _gc(self, catalog: dict[str, registry.RoutineInfo]) -> None:
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
            shutil.rmtree(info.cfg.dir, ignore_errors=True)
            if owner.get("slug") and owner.get("dir"):
                cleared_owners[str(owner["slug"])] = Path(owner["dir"])
            log.info("detached gc removed task=%s", taskid)
        # an owner whose LAST task was just GC'd needs its digest emptied
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


def _has_pending_inbox(routine_dir: Path) -> bool:
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return False
    return any(p.is_file() and not p.name.startswith("answer-") for p in inbox.iterdir())


def _has_pending_bg_message(owner_dir: Path, taskid: str) -> bool:
    return owner_dir.is_dir() and (owner_dir / "inbox" / f"msg-bg-{taskid}.json").exists()
