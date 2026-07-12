"""The library repository (ONE git repo: workflows/ + traits/ + permissions/ + utils/): status,
remote wiring, and first-run provisioning (clone existing, or seed + create)."""

from __future__ import annotations

import shutil
import subprocess

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .common import RemoteBody, remote_of, server_of, update_config

router = APIRouter()

LIBRARY_NAME = "library"


def _has_content(home) -> bool:
    """True once the library actually holds items — the daemon auto-git-inits an empty
    scaffold, so a bare .git is NOT 'set up'. This drives the provision-vs-remote UI."""
    from ... import library_docs, utils_lib
    from ...workflows.library import list_workflows
    try:
        return bool(list_workflows(home) or library_docs.list_docs(home / "traits")
                    or library_docs.list_docs(home / "permissions")
                    or utils_lib.list_utils(home))
    except Exception:
        return False


@router.get("/settings/libraries")
def list_libraries(request: Request) -> dict:
    s = server_of(request)
    home = s.libraries_home
    return {"libraries": [{"name": LIBRARY_NAME, "home": str(home),
                           "remote": remote_of(home) or s.libraries_remote,
                           "exists": (home / ".git").is_dir(),
                           "provisioned": _has_content(home)}]}


@router.put("/settings/libraries/{name}")
def set_library_remote(request: Request, name: str, body: RemoteBody) -> dict:
    s = server_of(request)
    if name != LIBRARY_NAME:
        raise HTTPException(404, f"unknown library {name!r}")
    home = s.libraries_home
    update_config(request, lambda raw: raw.update(libraries_remote=body.remote))
    s.libraries_remote = body.remote
    # point the local repo's origin at it (best-effort)
    result = {"ok": True, "pushed": False}
    if body.remote and (home / ".git").is_dir():
        subprocess.run(["git", "-C", str(home), "remote", "remove", "origin"], capture_output=True)
        subprocess.run(["git", "-C", str(home), "remote", "add", "origin", body.remote], capture_output=True)
        push = subprocess.run(["git", "-C", str(home), "push", "-u", "origin", "main"],
                              capture_output=True, text=True, timeout=60)
        result["pushed"] = push.returncode == 0
        if push.returncode != 0:
            result["push_error"] = push.stderr.strip()[:200]
    return result


class Provision(BaseModel):
    repo: str                 # "owner/name", "name", or a full URL
    mode: str                 # "clone" (existing content) | "create" (new private repo, seeded)


@router.post("/settings/libraries/{name}/provision")
def provision_library(request: Request, name: str, body: Provision) -> dict:
    from ... import bootstrap
    s = server_of(request)
    if name != LIBRARY_NAME:
        raise HTTPException(404, f"unknown library {name!r}")
    home = s.libraries_home
    repo = body.repo.strip()
    if not repo:
        raise HTTPException(400, "enter a repo (owner/name or URL)")
    if _has_content(home):
        raise HTTPException(409, "the library already has content — use its remote field instead")
    if not shutil.which("gh"):
        raise HTTPException(400, "the `gh` CLI is not available")
    if body.mode == "clone":
        home.mkdir(parents=True, exist_ok=True)
        for item in list(home.iterdir()):          # drop any empty scaffold so the clone target is clean
            shutil.rmtree(item) if item.is_dir() else item.unlink()
        r = subprocess.run(["gh", "repo", "clone", repo, str(home)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise HTTPException(502, f"clone failed (connect GitHub first?): {r.stderr.strip()[:300]}")
        for k, v in (("user.name", "routine-scheduler"), ("user.email", "noreply@routine-scheduler.local")):
            subprocess.run(["git", "-C", str(home), "config", k, v], capture_output=True)  # so later commits work
        bootstrap.install_push_hook(home)
    elif body.mode == "create":
        try:
            bootstrap.seed_libraries(home)          # copy defaults + git init + commit + hook
        except OSError as exc:
            raise HTTPException(500, f"could not seed the library: {exc}") from exc
        r = subprocess.run(["gh", "repo", "create", repo, "--private", "--source", str(home),
                            "--remote", "origin", "--push"], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise HTTPException(502, f"repo create failed (connect GitHub first?): {r.stderr.strip()[:300]}")
    else:
        raise HTTPException(400, "mode must be 'clone' or 'create'")
    return {"ok": True, "mode": body.mode, "remote": remote_of(home)}
