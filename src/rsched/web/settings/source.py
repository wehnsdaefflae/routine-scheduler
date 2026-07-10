"""Scheduler source repository (where self-audit commits + pushes code) and the git-remote
reachability probe behind the Settings 'Test' button."""

from __future__ import annotations

import os
import subprocess

from fastapi import APIRouter, Request

from .common import RemoteBody, remote_of, server_of, update_config

router = APIRouter()


@router.get("/settings/source")
def get_source_repo(request: Request) -> dict:
    s = server_of(request)
    home = s.source_repo
    is_git = (home / ".git").is_dir()
    branch = ""
    if is_git:
        r = subprocess.run(["git", "-C", str(home), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True)
        branch = r.stdout.strip() if r.returncode == 0 else ""
    return {"home": str(home), "remote": remote_of(home) or s.source_remote,
            "exists": is_git, "branch": branch or "main"}


@router.put("/settings/source")
def set_source_remote(request: Request, body: RemoteBody) -> dict:
    s = server_of(request)
    home = s.source_repo
    update_config(request, lambda raw: raw.update(source_remote=body.remote))
    s.source_remote = body.remote
    # point origin at it — SAFE: set-url (add if absent), never remove; this is the live code repo
    result = {"ok": True, "pushed": False}
    if body.remote and (home / ".git").is_dir():
        set_url = subprocess.run(["git", "-C", str(home), "remote", "set-url", "origin", body.remote],
                                 capture_output=True, text=True)
        if set_url.returncode != 0:                     # no origin yet → add it
            subprocess.run(["git", "-C", str(home), "remote", "add", "origin", body.remote],
                           capture_output=True)
        push = subprocess.run(["git", "-C", str(home), "push", "-u", "origin", "HEAD"],
                              capture_output=True, text=True, timeout=60)
        result["pushed"] = push.returncode == 0
        if push.returncode != 0:
            result["push_error"] = push.stderr.strip()[:200]
    return result


@router.post("/settings/test-remote")
def test_remote(_request: Request, body: RemoteBody) -> dict:
    """Validate that a git remote is reachable AND authorized, for the Settings 'Test' button.
    Runs `git ls-remote` with prompts disabled so a private repo without credentials fails fast
    (rather than hanging), and surfaces the git error verbatim (auth failure, no such repo, DNS)."""
    url = body.remote.strip()
    if not url:
        return {"ok": False, "error": "no remote URL configured"}
    # GIT_TERMINAL_PROMPT=0 → never block on a username/password prompt; fail with the auth error.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    try:
        r = subprocess.run(["git", "ls-remote", "--heads", url],
                           capture_output=True, text=True, timeout=30, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out after 30s — host unreachable?"}
    if r.returncode == 0:
        branches = [ln.split("refs/heads/")[-1] for ln in r.stdout.splitlines() if ln.strip()]
        return {"ok": True, "branches": len(branches),
                "detail": f"reachable — {len(branches)} branch(es)" + (f": {branches[0]}…" if branches else "")}
    raw = r.stderr.strip() or "git ls-remote failed"
    last = raw.splitlines()[-1][:300]
    low = raw.lower()
    # actionable hints for the two errors users actually hit on first setup
    if any(s in low for s in ("could not read username", "authentication failed", "terminal prompts disabled")):
        return {"ok": False, "error": "authentication required — is it a private repo? run "
                "`gh auth login` in the container (see deploy/SETUP.md)", "detail": last}
    if "not found" in low:
        return {"ok": False, "error": "repository not found (or no access) — check the URL and auth",
                "detail": last}
    return {"ok": False, "error": last}
