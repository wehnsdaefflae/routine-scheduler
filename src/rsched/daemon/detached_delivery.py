"""DELIVERING a finished background task back to the conversation that started it.

Split out of `detached.py` (F393), the same way `control.py` holds helpers lifted out of the
engine loop: the manager owns the task LIFECYCLE, this owns the hand-back.

The ordering here is the part worth protecting. The inbox message is written BEFORE the
`delivered.json` marker, with no await between, so a consumer can never see the marker without
the message; a crash in the gap re-delivers the same deterministic filename, so the owner still
ends up with exactly one. Artefacts land in `artifacts/from-bg-<id>/` — namespaced so a
delivery never clobbers the conversation's own, and idempotent on a re-delivery.

Delivery never starts a run: an idle owner is WOKEN to drain its inbox, and a live one drains it
at its next turn boundary on its own.
"""

# Asked by the wake decision: an owner is woken only when something is
# actually waiting for it, so a delivery never starts a run for nothing.
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from .. import registry
from ..config import load_routine
from ..ids import now_iso
from ..paths import atomic_write_json
from .runner_state import _pid_alive


def _has_pending_inbox(routine_dir: Path) -> bool:
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return False
    return any(p.is_file() and not p.name.startswith("answer-") for p in inbox.iterdir())

def _has_pending_bg_message(owner_dir: Path, taskid: str) -> bool:
    return owner_dir.is_dir() and (owner_dir / "inbox" / f"msg-bg-{taskid}.json").exists()


log = logging.getLogger("rsched.daemon.detached_delivery")


async def deliver(mgr, catalog: dict[str, registry.RoutineInfo]) -> None:
    for taskid, info in catalog.items():
        if (info.cfg.dir / "delivered.json").exists():
            continue
        state = terminal_state(mgr, info)
        if state is None:
            continue
        try:
            await deliver_one(taskid, info, state)
        except Exception:
            log.exception("detached: delivery of %s failed — will retry next tick", taskid)


def terminal_state(mgr, info: registry.RoutineInfo) -> str | None:
    """The finished state to deliver, or None if the task is still live. A task counts as
    terminal when its status.json says so OR — for a task that survived a restart and is no
    longer tracked — when its pid is dead (crashed/orphaned without a finish).
    """
    last = info.last_run
    if last is None:
        return None
    if last.state in registry.TERMINAL_STATES:
        return last.state
    if info.slug not in mgr.runner.active and not _pid_alive(last.pid):
        return "failed"
    return None


async def deliver_one(taskid: str, info: registry.RoutineInfo, state: str) -> None:
    task_dir = info.cfg.dir
    owner = info.cfg.owner or {}
    owner_dir = Path(owner.get("dir", ""))
    if not owner.get("dir") or not (owner_dir / "routine.yaml").exists():
        atomic_write_json(task_dir / "delivered.json", {"ts": now_iso(), "owner": "missing"})
        log.info("detached: owner of %s missing at delivery — dropped", taskid)
        return
    copied = await copy_artifacts(task_dir, owner_dir, taskid)
    # msg FIRST, delivered.json SECOND, both without an await between → a consumer (a later
    # resume) can never see the msg before the marker; a crash in the tiny gap re-delivers
    # the same deterministic filename, so still exactly one pending message.
    atomic_write_json(owner_dir / "inbox" / f"msg-bg-{taskid}.json",
                      {"text": delivery_text(info, state, taskid, copied),
                       "ts": now_iso(), "via": "background"})
    atomic_write_json(task_dir / "delivered.json",
                      {"ts": now_iso(), "state": state, "owner": owner.get("slug")})
    log.info("detached delivered task=%s state=%s owner=%s artifacts=%d",
             taskid, state, owner.get("slug"), copied)


async def copy_artifacts(task_dir: Path, owner_dir: Path, taskid: str) -> int:
    src = task_dir / "artifacts"
    if not src.is_dir() or not any(src.iterdir()):
        return 0
    dst = owner_dir / "artifacts" / f"from-bg-{taskid}"
    # namespaced + overwrite: never clobber the conversation's own artifacts, and idempotent
    # on re-delivery. Blocking fs op → off the event loop.
    await asyncio.to_thread(shutil.copytree, src, dst, dirs_exist_ok=True)
    return sum(1 for _ in dst.rglob("*") if _.is_file())


def delivery_text(info: registry.RoutineInfo, state: str, taskid: str,
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


async def wake(mgr, catalog: dict[str, registry.RoutineInfo]) -> None:
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
        await wake_owner(mgr, owner_dir, slug)


async def wake_owner(mgr, owner_dir: Path, slug: str) -> None:
    if mgr.runner.is_active(slug) or mgr.runner.draining:
        return
    if not _has_pending_inbox(owner_dir):
        return
    owner_cfg, _ = load_routine(owner_dir)
    if owner_cfg is None:
        return
    # a non-terminal last run means a live reply is already draining the message
    rid = await mgr.runner.resume_terminal(owner_cfg, reason="detached")
    if rid:
        log.info("detached woke owner=%s run=%s", slug, rid)
