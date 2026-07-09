"""Routine CRUD: dashboard cards, detail, config edits (409 while a run is active),
manual fire, archive, file reads."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import schedule
from ..daemon import registry
from ..ids import now_iso, run_ts
from ..paths import resolve_rel
from .sse import TERMINAL_STATES  # noqa: F401  (re-exported for api_runs)

router = APIRouter(tags=["routines"])

MAX_FILE_BYTES = 400_000


def _state(request: Request):
    return request.app.state


def _catalog(request: Request) -> dict[str, registry.RoutineInfo]:
    return registry.scan(_state(request).server)


def _info(request: Request, slug: str) -> registry.RoutineInfo:
    info = _catalog(request).get(slug)
    if info is None:
        raise HTTPException(404, f"no routine {slug!r}")
    return info


def _guard_not_active(request: Request, info: registry.RoutineInfo) -> None:
    if info.active_run or _state(request).runner.is_active(info.slug):
        raise HTTPException(409, f"routine {info.slug!r} has an active run — try again after it ends")


def _git_commit(routine_dir: Path, message: str) -> None:
    if not (routine_dir / ".git").exists():
        return
    subprocess.run(["git", "add", "-A"], cwd=routine_dir, capture_output=True, timeout=30)
    subprocess.run(["git", "commit", "-qm", message], cwd=routine_dir,
                   capture_output=True, timeout=30)


def _card(request: Request, info: registry.RoutineInfo) -> dict:
    sched = _state(request).scheduler
    last = info.last_run
    return {
        "slug": info.slug,
        "name": info.cfg.name,
        "enabled": info.cfg.enabled,
        "tags": info.cfg.tags,
        "cron": info.cfg.cron,
        "tz": info.cfg.tz,
        "schedule_desc": schedule.describe(info.cfg.cron),
        "next_fire": (sched.next_fires.get(info.slug).isoformat()
                      if sched.next_fires.get(info.slug) else None),
        "active_run": info.active_run.run_id if info.active_run else None,
        "active_state": info.active_run.state if info.active_run else None,
        "last_run": ({"run_id": last.run_id, "ts": last.ts, "state": last.state,
                      "summary": last.summary[:280]} if last else None),
        "open_questions": len(info.open_questions),
        "problems": info.problems,
    }


@router.get("/routines")
def list_routines(request: Request) -> list[dict]:
    return [_card(request, info) for info in _catalog(request).values()]


@router.get("/routines/{slug}")
def routine_detail(request: Request, slug: str) -> dict:
    from .. import fragments_lib

    info = _info(request, slug)
    server = _state(request).server
    d = info.cfg.dir
    ledger = d / "LEDGER.md"
    ledger_tail = ""
    if ledger.exists():
        lines = ledger.read_text(encoding="utf-8").splitlines()
        ledger_tail = "\n".join(lines[-100:])
    # editable routine files by directory (playbook step files + fragment copies)
    files = {}
    for sub in ("playbook", "fragments", "state"):
        subdir = d / sub
        files[sub] = ([p.name for p in sorted(subdir.iterdir()) if p.is_file() and p.suffix == ".md"]
                      if subdir.is_dir() else [])
    # all library fragments → toggle list; active ones are this routine's
    all_frags = fragments_lib.list_fragments(server.fragments_home)
    active = set(info.cfg.fragments)
    fragments = [{"slug": f["slug"], "summary": f["summary"], "title": f["title"],
                  "active": f["slug"] in active} for f in all_frags]
    return {
        **_card(request, info),
        "schedule_friendly": schedule.cron_to_friendly(info.cfg.cron),
        "server_tz": schedule.server_tz(),
        "confirm_util_changes": info.cfg.confirm_utils(server),
        "workflow_ref": {"slug": info.cfg.workflow_slug, "commit": info.cfg.workflow_commit},
        "fragments": fragments,
        "instruction": (d / "instruction.md").read_text(encoding="utf-8")
        if (d / "instruction.md").exists() else "",
        "ledger_tail": ledger_tail,
        "files": files,
        "questions": info.open_questions,
        "runs": [{"run_id": r.run_id, "ts": r.ts, "state": r.state,
                  "summary": r.summary[:200], "turn": r.turn, "usage": r.usage}
                 for r in info.runs[:50]],
        "budgets": info.cfg.budgets,
    }


@router.get("/routines/{slug}/file")
def get_routine_file(request: Request, slug: str, path: str) -> dict:
    info = _info(request, slug)
    try:
        p = resolve_rel(info.cfg.dir, path)
        return {"path": path, "content": p.read_text(encoding="utf-8")}
    except (PermissionError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc


class RoutineFileBody(BaseModel):
    path: str
    content: str


@router.put("/routines/{slug}/file")
def put_routine_file(request: Request, slug: str, body: RoutineFileBody) -> dict:
    """Edit any routine file EXCEPT the workflow (which lives only in the library)."""
    info = _info(request, slug)
    _guard_not_active(request, info)
    if Path(body.path).name == "workflow.md" or body.path.startswith("workflow"):
        raise HTTPException(400, "the workflow is edited only in the Library tab, not per routine")
    try:
        p = resolve_rel(info.cfg.dir, body.path)
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.content, encoding="utf-8")
    _git_commit(info.cfg.dir, f"edit {body.path} via web")
    return {"ok": True}


class FragmentsBody(BaseModel):
    active: list[str]


@router.put("/routines/{slug}/fragments")
def set_fragments(request: Request, slug: str, body: FragmentsBody) -> dict:
    """Set the routine's active fragments: copy newly-active ones from the library into
    fragments/, remove deactivated ones, and record the list in routine.yaml."""
    from .. import fragments_lib

    info = _info(request, slug)
    _guard_not_active(request, info)
    server = _state(request).server
    available = set(fragments_lib.slugs(server.fragments_home))
    active = [f for f in body.active if f in available]
    frag_dir = info.cfg.dir / "fragments"
    frag_dir.mkdir(exist_ok=True)
    for slug_f in active:
        target = frag_dir / f"{slug_f}.md"
        if not target.exists():
            content = fragments_lib.read_fragment(server.fragments_home, slug_f)
            if content:
                target.write_text(content, encoding="utf-8")
    for existing in frag_dir.glob("*.md"):
        if existing.stem not in active:
            existing.unlink()
    path = info.cfg.dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["fragments"] = active
    raw.pop("self", None)
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _git_commit(info.cfg.dir, f"fragments: {', '.join(active)}")
    return {"ok": True, "active": active}


class RoutinePatch(BaseModel):
    enabled: bool | None = None
    schedule: dict | None = None            # {"friendly": {...}} — converted to cron server-side
    budgets: dict | None = None
    confirm_util_changes: bool | None = None
    endpoints: dict | None = None
    notifications: str | None = None
    name: str | None = None
    tags: list[str] | None = None           # freeform filter tags (e.g. ["meta"])


@router.patch("/routines/{slug}")
def patch_routine(request: Request, slug: str, patch: RoutinePatch) -> dict:
    info = _info(request, slug)
    _guard_not_active(request, info)
    path = info.cfg.dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    updates = patch.model_dump(exclude_none=True)
    # Translate the friendly schedule → cron + the server's own timezone (never asked of the user).
    if "schedule" in updates and "friendly" in updates["schedule"]:
        try:
            cron = schedule.friendly_to_cron(updates["schedule"]["friendly"])
        except ValueError as exc:
            raise HTTPException(400, f"invalid schedule: {exc}") from exc
        raw.setdefault("schedule", {})
        raw["schedule"].update(cron=cron, tz=schedule.server_tz())
        updates.pop("schedule")
    for key, val in updates.items():
        if isinstance(val, dict) and isinstance(raw.get(key), dict):
            raw[key].update(val)
        else:
            raw[key] = val
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _git_commit(info.cfg.dir, f"routine.yaml edit via web ({', '.join(updates)})")
    _state(request).scheduler.rescan()
    return {"ok": True, "updated": list(updates)}


class FileBody(BaseModel):
    content: str


@router.put("/routines/{slug}/instruction")
def put_instruction(request: Request, slug: str, body: FileBody) -> dict:
    return _put_doc(request, slug, "instruction.md", body.content)


def _put_doc(request: Request, slug: str, filename: str, content: str) -> dict:
    info = _info(request, slug)
    _guard_not_active(request, info)
    (info.cfg.dir / filename).write_text(content, encoding="utf-8")
    _git_commit(info.cfg.dir, f"{filename} edit via web")
    return {"ok": True}


@router.post("/routines/{slug}/run")
async def run_now(request: Request, slug: str) -> dict:
    info = _info(request, slug)
    run_id = await _state(request).runner.fire(info.cfg, reason="manual")
    if run_id is None:
        raise HTTPException(409, f"routine {slug!r} already has an active run")
    return {"run_id": run_id}


@router.post("/routines/{slug}/archive")
def archive_routine(request: Request, slug: str) -> dict:
    info = _info(request, slug)
    _guard_not_active(request, info)
    home = _state(request).server.routines_home
    target = home / ".archive" / f"{slug}-{run_ts()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(info.cfg.dir), str(target))
    _state(request).scheduler.rescan()
    return {"ok": True, "archived_to": str(target), "ts": now_iso()}


@router.get("/routines/{slug}/files")
def read_file(request: Request, slug: str, path: str) -> dict:
    info = _info(request, slug)
    try:
        p = resolve_rel(info.cfg.dir, path)
        data = p.read_bytes()[:MAX_FILE_BYTES]
    except (PermissionError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"path": path, "content": data.decode("utf-8", "replace"),
            "truncated": p.stat().st_size > MAX_FILE_BYTES}
