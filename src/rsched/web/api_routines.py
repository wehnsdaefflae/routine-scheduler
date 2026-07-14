"""Routine CRUD: dashboard cards, detail, config edits (409 while a run is active),
manual fire, archive, file reads."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import schedule
from ..config import MODEL_KINDS
from ..daemon import registry
from ..ids import now_iso, run_ts
from ..paths import atomic_write_json, read_json, resolve_rel

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
    if info.active_run or _state(request).runner.is_busy(info.slug):
        raise HTTPException(409, f"routine {info.slug!r} is busy (a run or recompile is in progress) "
                                 "— try again after it ends")


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
        "description": info.cfg.description,
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
                      "summary": last.summary[:280], "turns": last.turn,
                      "usage": last.usage, "elapsed_s": last.elapsed_s} if last else None),
        "open_questions": sum(1 for q in info.open_questions if not q.get("answered")),
        "problems": info.problems,
        "improve": info.cfg.improve,
    }


@router.get("/routines")
def list_routines(request: Request) -> list[dict]:
    return [_card(request, info) for info in _catalog(request).values()]


@router.get("/routines/{slug}")
def routine_detail(request: Request, slug: str) -> dict:
    from .. import library_docs

    info = _info(request, slug)
    server = _state(request).server
    d = info.cfg.dir
    ledger = d / "LEDGER.md"
    ledger_tail = ""
    if ledger.exists():
        lines = ledger.read_text(encoding="utf-8").splitlines()
        ledger_tail = "\n".join(lines[-100:])
    # editable routine files by directory (step modules + the routine's own trait copies + state)
    files = {}
    for sub in ("steps", "traits", "state"):
        subdir = d / sub
        files[sub] = ([p.name for p in sorted(subdir.iterdir()) if p.is_file() and p.suffix == ".md"]
                      if subdir.is_dir() else [])
    # the two permission layers: all library conduct docs → toggle list (held ones are
    # this routine's), plus the machine-enforced capabilities mapping + its vocabulary
    from ..grants import EMPTY_CAPABILITIES, GATED_KINDS

    from ..workflows import provenance

    instruction = (d / "instruction.md").read_text(encoding="utf-8") \
        if (d / "instruction.md").exists() else ""
    all_perms = library_docs.list_docs(server.permissions_home)
    held = set(info.cfg.permissions)
    permissions = [{"slug": p["slug"], "summary": p["summary"], "title": p["title"],
                    "requires": p["requires"], "active": p["slug"] in held} for p in all_perms]
    own_caps = info.cfg.capabilities or {}
    reservable = sorted({u for p in all_perms for u in (p["requires"].get("utils") or [])}
                        | set(own_caps.get("utils") or []))
    capabilities = {"active": {**EMPTY_CAPABILITIES, **own_caps},
                    "vocabulary": {"actions": list(GATED_KINDS), "utils": reservable}}
    sm = server.system_model
    in_library = bool(info.cfg.workflow_slug) and \
        (server.library_home / "workflows" / f"{info.cfg.workflow_slug}.py").exists()
    return {
        **_card(request, info),
        "schedule_friendly": schedule.cron_to_friendly(info.cfg.cron),
        "server_tz": schedule.server_tz(),
        # Provenance is a CLAIM ("generated from") — in_library says whether the referenced
        # pattern actually exists in the current library, so the UI never implies a findable
        # workflow that isn't there (hand-authored recipes carry an empty slug).
        "workflow_ref": {"slug": info.cfg.workflow_slug, "commit": info.cfg.workflow_commit,
                         "in_library": in_library},
        # the seed = instruction.md; drift tells the UI whether it has been edited since the
        # steps were compiled, or the steps changed under it. Recompilable only when a source
        # workflow is actually present to re-derive from.
        "seed": provenance.drift(d, instruction),
        "recompilable": in_library,
        # Per-routine models (main/subroutine/tool_call). A kind left null falls back to the
        # server system_model, shown so the UI can label the effective model.
        "models": {k: ({"endpoint": r.endpoint, "model": r.model, "effort": r.effort}
                       if (r := info.cfg.models.get(k)) else None) for k in MODEL_KINDS},
        "endpoints": list(server.endpoints.keys()),
        "system_model": {"endpoint": sm.endpoint, "model": sm.model} if sm else None,
        "permissions": permissions,
        "capabilities": capabilities,
        "instruction": instruction,
        "ledger_tail": ledger_tail,
        "files": files,
        "questions": info.open_questions,
        "runs": [{"run_id": r.run_id, "ts": r.ts, "state": r.state,
                  "summary": r.summary[:200], "turn": r.turn, "usage": r.usage,
                  "elapsed_s": r.elapsed_s}
                 for r in info.runs[:50]],
        "budgets": info.cfg.budgets,
    }


@router.get("/routines/{slug}/stategraph")
def stategraph(request: Request, slug: str) -> dict:
    """The routine's state graph (parsed from its own main.md) + the current phase — the
    UI's live diagram; phase transitions arrive over the run SSE `state` events."""
    from .. import statemap

    info = _info(request, slug)
    return statemap.state_graph(info.cfg.dir)


@router.get("/routines/{slug}/artifacts")
def list_artifacts(request: Request, slug: str) -> list[dict]:
    """Everything under <routine>/artifacts/ — the routine's deliverables, newest first
    (the conversations panel's counterpart)."""
    info = _info(request, slug)
    art = info.cfg.dir / "artifacts"
    out = []
    if art.is_dir():
        for p in art.rglob("*"):
            if p.is_file():
                st = p.stat()
                out.append({"path": str(p.relative_to(info.cfg.dir)), "name": p.name,
                            "size": st.st_size, "mtime": int(st.st_mtime)})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


@router.get("/routines/{slug}/artifact")
def get_artifact(request: Request, slug: str, path: str):
    """Serve one artifact raw (blob-rendered client-side). ONLY artifacts/ is servable
    here — routine config/recipe reads stay on the JSON /file endpoint."""
    import mimetypes

    from fastapi.responses import FileResponse

    from ..paths import within

    info = _info(request, slug)
    try:
        p = resolve_rel(info.cfg.dir, path.lstrip("/"))
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc
    # the check runs on the RESOLVED path — 'artifacts/../routine.yaml' must not pass
    if not within(info.cfg.dir / "artifacts", p):
        raise HTTPException(400, "only artifacts/ files are served")
    if not p.is_file():
        raise HTTPException(404, f"no file {path!r}")
    media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(p, media_type=media, filename=p.name)


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
    """Edit any of the routine's own files — main.md, step modules, traits, instruction, state.
    A routine owns its recipe (materialized in), so main.md, steps/ and traits/ ARE editable here."""
    info = _info(request, slug)
    _guard_not_active(request, info)
    try:
        p = resolve_rel(info.cfg.dir, body.path)
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.content, encoding="utf-8")
    _git_commit(info.cfg.dir, f"edit {body.path} via web")
    return {"ok": True}


class PermissionsBody(BaseModel):
    active: list[str]
    capabilities: dict | None = None   # omitted → keep the routine's current mapping as base


def resolve_permission_layers(server, body: PermissionsBody, current: dict) -> tuple[list, dict]:
    """Validate + cascade one permissions update (shared with conversations): unknown doc
    slugs are dropped, the capabilities mapping is normalized (422 on junk), then RAISED
    until every active doc's requires are covered — so the invariant 'held docs' needs
    are on' holds regardless of what the client sent. Deactivation cascades live in the
    UI (dropping a capability there also unticks the docs requiring it)."""
    from .. import library_docs
    from ..grants import capabilities_for, normalize_capabilities, read_library_requires

    available = set(library_docs.slugs(server.permissions_home))
    active = [p for p in body.active if p in available]
    base, problems = normalize_capabilities(
        body.capabilities if body.capabilities is not None else current)
    if body.capabilities is not None and problems:
        raise HTTPException(422, "; ".join(problems))
    caps = capabilities_for(active, read_library_requires(server.permissions_home), base)
    return active, caps


@router.put("/routines/{slug}/permissions")
def set_permissions(request: Request, slug: str, body: PermissionsBody) -> dict:
    """Set both permission layers (user-only; a routine can never change its own): the
    held conduct docs AND the capabilities mapping. Pure routine.yaml config, read at run
    start, so changes take effect at the next run. Traits are NOT toggleable here: they
    became the routine's own files at creation."""
    info = _info(request, slug)
    _guard_not_active(request, info)
    server = _state(request).server
    active, caps = resolve_permission_layers(server, body, info.cfg.capabilities or {})
    path = info.cfg.dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["permissions"] = active
    raw["capabilities"] = caps
    raw.pop("fragments", None)   # pre-split key, retired
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _git_commit(info.cfg.dir, f"permissions: {', '.join(active) or '(none)'}")
    return {"ok": True, "active": active, "capabilities": caps}


class RoutinePatch(BaseModel):
    enabled: bool | None = None
    schedule: dict | None = None            # {"friendly": {...}} — converted to cron server-side
    budgets: dict | None = None
    models: dict | None = None              # {main|subroutine|tool_call: {endpoint, model, effort?}}
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None           # freeform filter tags (e.g. ["meta"])
    improve: bool | None = None             # include in the routine-improver's passes (default on)


@router.patch("/routines/{slug}")
def patch_routine(request: Request, slug: str, patch: RoutinePatch) -> dict:
    info = _info(request, slug)
    _guard_not_active(request, info)
    path = info.cfg.dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    updates = patch.model_dump(exclude_none=True)
    # Validate per-routine models: known kinds, pointing at configured endpoints. Models REPLACE
    # wholesale (not merge) so blanking a kind clears it back to the system_model fallback.
    if "models" in updates:
        server = _state(request).server
        for kind, spec in (updates["models"] or {}).items():
            if kind not in MODEL_KINDS:
                raise HTTPException(400, f"unknown model kind {kind!r} (expected one of {MODEL_KINDS})")
            if not isinstance(spec, dict) or spec.get("endpoint") not in server.endpoints:
                raise HTTPException(400, f"models.{kind}: 'endpoint' must be a configured endpoint")
        raw["models"] = updates.pop("models")
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


@router.post("/routines/{slug}/recompile")
async def recompile(request: Request, slug: str) -> dict:
    """Re-derive main.md + steps/ from the CURRENT instruction (the seed) × the routine's workflow —
    the slow decompose runs in the BACKGROUND (state/recompile.json: building | done | error, plus a
    bus event), mirroring the new-routine wizard. The slug is RESERVED so no scheduled run fires
    mid-recompile. 400 if there's no source workflow to re-derive from; 409 if the routine is busy."""
    info = _info(request, slug)
    _guard_not_active(request, info)
    server = _state(request).server
    if not info.cfg.workflow_slug:
        raise HTTPException(400, "this routine was written directly (no source workflow) — "
                                 "edit main.md / steps below instead")
    if not (server.library_home / "workflows" / f"{info.cfg.workflow_slug}.py").exists():
        raise HTTPException(400, f"its workflow {info.cfg.workflow_slug!r} is not in this library "
                                 "— nothing to recompile from")
    from ..endpoints import EndpointRegistry
    try:   # decompose needs a system model — fail fast rather than degrade to a main-only flatten
        EndpointRegistry(server).for_system()
    except Exception as exc:  # noqa: BLE001 — surface any resolution failure as a clear 400
        raise HTTPException(400, f"no system model configured to recompile with: {exc}") from exc
    runner = _state(request).runner
    runner.reserved.add(slug)   # guard already ensured not busy; single-threaded → no race to add
    atomic_write_json(info.cfg.dir / "state" / "recompile.json",
                      {"state": "building", "started": now_iso()})
    asyncio.create_task(_run_recompile(request.app.state, info.cfg.dir, slug))
    return {"recompiling": True, "slug": slug}


async def _run_recompile(app_state, routine_dir: Path, slug: str) -> None:
    """Background: run the (blocking) recompile off the loop, record the outcome to
    state/recompile.json + a bus event, and ALWAYS release the reservation."""
    from ..config import load_routine
    from ..workflows.recompile import recompile_routine

    server, bus, runner = app_state.server, app_state.bus, app_state.runner
    status_path = routine_dir / "state" / "recompile.json"
    try:
        cfg, _ = load_routine(routine_dir)
        if cfg is None:
            raise RuntimeError("routine config is invalid")
        summary = await asyncio.to_thread(recompile_routine, server, routine_dir, cfg)
        await asyncio.to_thread(_git_commit, routine_dir, "recompile main.md + steps from the seed")
        atomic_write_json(status_path, {"state": "done", "finished": now_iso(), **summary})
        bus.publish({"event": "routine_recompiled", "slug": slug, **summary})
    except Exception as exc:  # noqa: BLE001 — any failure leaves the routine's files untouched-enough
        atomic_write_json(status_path, {"state": "error", "finished": now_iso(), "error": str(exc)[:300]})
        bus.publish({"event": "routine_recompile_failed", "slug": slug, "error": str(exc)[:300]})
    finally:
        runner.reserved.discard(slug)
        app_state.scheduler.rescan()


@router.get("/routines/{slug}/recompile")
def recompile_status(request: Request, slug: str) -> dict:
    """Poll target for the background recompile (the wizard-style fallback to the bus event)."""
    info = _info(request, slug)
    st = read_json(info.cfg.dir / "state" / "recompile.json", {}) or {}
    state = st.get("state", "idle")
    if state == "building" and not _state(request).runner.is_busy(slug):
        state = "stale"   # the daemon restarted mid-recompile — never completed
    return {"state": state, "error": st.get("error"),
            **{k: st[k] for k in ("modules", "removed") if k in st}}


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
