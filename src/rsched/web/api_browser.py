"""A conversation's live browser session (R262 pt2 / D86: the `browser-session` util's PoC
contract, surfaced in the UI).

The util persists a session handle at ``state/browser-session.json`` (or
``state/browser-session-<name>.json`` per ``--name``) inside the conversation's working dir,
and refreshes a screenshot "view" PNG (default ``state/browser-view.png``) after every
action. This router is the UI's window onto that contract: list the handles with a cheap
liveness probe of the recorded CDP port, serve the latest view image, and STOP a session —
the one thing the UI cannot do itself (killing the detached browser's process group is a
host-side act, mirroring ``gu browser-session stop``). Split out of api_conversations, the
same shape as api_background.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter(tags=["browser"])

_PREFIX = "browser-session"


def _handle_files(conv_dir: Path) -> list[Path]:
    state = conv_dir / "state"
    if not state.is_dir():
        return []
    return sorted(p for p in state.iterdir()
                  if p.name.startswith(_PREFIX) and p.suffix == ".json")


def _load_handle(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _port_alive(host: str | None, port, timeout: float = 0.4) -> bool:
    """One TCP connect to the recorded CDP port — cheap and dependency-free. A refused or
    unroutable port means the browser is gone; a listening one is treated as alive (the
    handle records who bound it).
    """
    try:
        with socket.create_connection((str(host or "127.0.0.1"), int(port)), timeout=timeout):
            return True
    except (OSError, ValueError, TypeError):
        return False


def _resolve_view(conv_dir: Path, handle: dict) -> Path | None:
    """The handle's view PNG resolved INSIDE the conversation dir. The handle is
    model-written state, so a path that escapes the dir is rejected loudly rather than
    served — None only means "no view recorded".
    """
    raw = str(handle.get("view") or "")
    if not raw:
        return None
    base = conv_dir.resolve()
    path = (Path(raw) if Path(raw).is_absolute() else base / raw).resolve()
    if path != base and base not in path.parents:
        raise HTTPException(400, "view path escapes the conversation dir")
    return path


def browser_rows(conv_dir: Path) -> list[dict]:
    """One row per persisted session handle — the UI's browser rail section."""
    rows: list[dict] = []
    for hf in _handle_files(conv_dir):
        handle = _load_handle(hf)
        if handle is None:
            continue
        try:
            view_path = _resolve_view(conv_dir, handle)
        except HTTPException:
            view_path = None
        view = None
        if view_path is not None and view_path.is_file():
            view = {"mtime": int(view_path.stat().st_mtime)}
        rows.append({
            "name": str(handle.get("name") or "default"),
            "cdp": str(handle.get("cdp") or ""),
            "url": str(handle.get("url") or ""),
            "pid": handle.get("pid"),
            "started": handle.get("started"),
            "alive": _port_alive(handle.get("host"), handle.get("port") or 0),
            "view": view,
        })
    return rows


def _handle_for(conv_dir: Path, name: str) -> tuple[Path, dict]:
    for hf in _handle_files(conv_dir):
        handle = _load_handle(hf)
        if handle is not None and str(handle.get("name") or "default") == name:
            return hf, handle
    raise HTTPException(404, f"no browser session {name!r} for this conversation")


@router.get("/conversations/{slug}/browser")
def list_browser(request: Request, slug: str) -> list[dict]:
    """The conversation's persisted browser sessions — for the right-rail 'browser' section."""
    from .api_conversations import conversation_info

    info = conversation_info(request, slug)   # 404 if the conversation is gone
    return browser_rows(info.cfg.dir)


@router.get("/conversations/{slug}/browser/view")
def browser_view(request: Request, slug: str, name: str = "default"):
    """The session's latest screenshot (fetched with the auth header, blob-rendered)."""
    from .api_conversations import conversation_info

    info = conversation_info(request, slug)
    _, handle = _handle_for(info.cfg.dir, name)
    view_path = _resolve_view(info.cfg.dir, handle)
    if view_path is None or not view_path.is_file():
        raise HTTPException(404, "no view captured yet")
    return FileResponse(view_path, media_type="image/png")


@router.post("/conversations/{slug}/browser/{name}/stop")
def stop_browser(request: Request, slug: str, name: str) -> dict:
    """SIGTERM (then SIGKILL) the detached browser's process group recorded in the handle
    and delete the handle — the server-side twin of ``gu browser-session stop``, which is
    the whole reason D86 needed an endpoint: the UI has no host access of its own.

    Guard: the browser was launched with ``start_new_session=True`` so it owns its process
    group; a handle whose pid resolves into the DAEMON'S group is wrong or forged, and
    signalling it would kill the scheduler — refuse the kill, still drop the handle.
    """
    from .api_conversations import conversation_info

    info = conversation_info(request, slug)
    hf, handle = _handle_for(info.cfg.dir, name)
    pid = handle.get("pid")
    killed = False
    if isinstance(pid, int) and pid > 1:
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, OSError):
            pgid = None
        if pgid is not None and pgid != os.getpgid(0):
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pgid, sig)
                    killed = True
                    time.sleep(0.3)
                    if not _port_alive(handle.get("host"), handle.get("port") or 0):
                        break
                except (ProcessLookupError, OSError):
                    break
    hf.unlink(missing_ok=True)
    return {"ok": True, "stopped": True, "killed_process": killed}
