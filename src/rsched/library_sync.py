"""The scheduled library sync — a plain daemon job, deliberately NOT a routine.

It is the exact same two operations every time (no judgment, no LLM): stage the instance
into the ONE library repo's working tree, then commit / pull --rebase / push. The retired
`library-sync` seed routine did precisely this through the `instance-export` + `git-sync`
utils; the daemon now runs it natively on the `library_sync:` schedule in config.yaml
(Settings → Library sync). The utils remain in the library for routines that need the
same primitives.

The outcome of every sync lands in <routines_home>/.control/library-sync.json (atomic,
read by the Settings page), and a failure additionally logs a health event.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import yaml

from .config import ServerConfig
from .health_events import log_health_event
from .ids import now_iso
from .paths import atomic_write_json, read_json

log = logging.getLogger("rsched.library_sync")

STATUS_FILE = "library-sync.json"
# transient run state that never leaves the instance; everything else in a routine dir syncs
EXCLUDE = {"runs", ".git", "inbox", "questions", "status.json"}
REDACT_KEYS = {"token", "api_key"}
GIT_IDENTITY = ["-c", "user.name=routine-scheduler",
                "-c", "user.email=noreply@routine-scheduler.local"]


def _wanted_files(routine_dir: Path) -> list[Path]:
    """Every file under routine_dir except the transient-state names, at any depth."""
    return [p for p in sorted(routine_dir.rglob("*"))
            if p.is_file()
            and not any(part in EXCLUDE for part in p.relative_to(routine_dir).parts)]


def export_routines(routines_home: Path, dest_routines: Path) -> dict:
    """Mirror each routine's persistent tree into dest_routines/<slug>/ — rsync-like:
    unchanged files are left alone, vanished files are pruned.
    """
    exported, skipped = [], []
    desired: set[Path] = set()
    if routines_home.is_dir():
        for rdir in sorted(p for p in routines_home.iterdir() if p.is_dir()):
            if rdir.name.startswith("."):
                skipped.append(rdir.name)
                continue
            copied = unchanged = 0
            for src in _wanted_files(rdir):
                rel = Path(rdir.name) / src.relative_to(rdir)
                desired.add(rel)
                dst = dest_routines / rel
                data = src.read_bytes()
                if dst.is_file() and dst.read_bytes() == data:
                    unchanged += 1
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data)
                copied += 1
            exported.append({"slug": rdir.name, "copied": copied, "unchanged": unchanged})
    removed = 0
    if dest_routines.is_dir():
        for p in sorted(dest_routines.rglob("*"), reverse=True):
            if p.is_file() and p.relative_to(dest_routines) not in desired:
                p.unlink()
                removed += 1
            elif p.is_dir() and not any(p.iterdir()):
                p.rmdir()
    return {"exported": len(exported), "removed": removed, "skipped": skipped}


def _redact(obj) -> int:
    """Recursively blank secret values: any token/api_key entry with a non-empty value
    becomes REDACTED (empty stays empty — it honestly says 'nothing was set').
    """
    hits = 0
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in REDACT_KEYS and isinstance(val, (str, int, float)) and str(val).strip():
                obj[key] = "REDACTED"
                hits += 1
            else:
                hits += _redact(val)
    elif isinstance(obj, list):
        for item in obj:
            hits += _redact(item)
    return hits


def export_config(config_path: Path, dest_dir: Path) -> dict:
    """Sanitized server config → dest_dir/config.yaml (parsed as YAML, never regexed)."""
    if not config_path.is_file():
        return {"exported": False, "reason": f"{config_path} not found"}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"exported": False, "reason": f"{config_path} is not a YAML mapping"}
    redacted = _redact(data)
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "config.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"exported": True, "redacted_values": redacted}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=120, check=False)


def git_sync(repo: Path, message: str = "instance sync") -> dict:
    """Commit local changes → pull --rebase from origin → push. Aborts cleanly on a
    rebase conflict (pull_error) and never tries to resolve it.
    """
    if not (repo / ".git").is_dir():
        raise ValueError(f"{repo} is not a git repository")
    _git(repo, "add", "-A")
    status = _git(repo, "status", "--porcelain").stdout.strip()
    committed = False
    if status:
        committed = _git(repo, *GIT_IDENTITY, "commit", "-qm", message).returncode == 0
    has_remote = bool(_git(repo, "remote").stdout.strip())
    branch = _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip() or "main"
    pulled = pushed = False
    pull_error = ""
    if has_remote:
        r = _git(repo, *GIT_IDENTITY, "pull", "--rebase", "--quiet", "origin", branch)
        pulled = r.returncode == 0
        if not pulled:
            _git(repo, "rebase", "--abort")
            pull_error = (r.stderr or r.stdout).strip()[:200]
        pushed = _git(repo, "push", "--quiet", "origin", branch).returncode == 0
    return {"committed": committed, "had_changes": bool(status), "has_remote": has_remote,
            "pulled": pulled, "pushed": pushed,
            **({"pull_error": pull_error} if pull_error else {})}


def _status_path(server: ServerConfig) -> Path:
    return server.routines_home / ".control" / STATUS_FILE


def read_status(server: ServerConfig) -> dict | None:
    """The last sync outcome, or None before the first one."""
    got = read_json(_status_path(server))
    return got if isinstance(got, dict) else None


def run_sync(server: ServerConfig) -> dict:
    """One full sync. status: 'ok' (everything clean), 'partial' (exported but the pull
    hit a conflict / config skipped), 'error' (nothing synced). Never raises.
    """
    from .paths import config_file

    result: dict = {"ts": now_iso(), "status": "ok"}
    try:
        dest = server.libraries_home
        if not dest.is_dir():
            raise ValueError(f"library repo {dest} does not exist yet (Settings → Library)")
        result["routines"] = export_routines(server.routines_home, dest / "routines")
        result["config"] = export_config(server.source or config_file(), dest / "config")
        result["sync"] = git_sync(dest)
        if result["sync"].get("pull_error") or not result["config"].get("exported"):
            result["status"] = "partial"
    except Exception as exc:  # a sync must never take the daemon down
        result["status"] = "error"
        result["error"] = str(exc)[:300]
    if result["status"] != "ok":
        log_health_event(server.routines_home, "library_sync_" + result["status"],
                         routine="(daemon)", run_id="",
                         detail=result.get("error") or result.get("sync", {}).get("pull_error", ""))
        log.warning("library sync %s: %s", result["status"],
                    result.get("error") or result.get("sync", {}))
    else:
        log.info("library sync ok: %s", result.get("sync"))
    atomic_write_json(_status_path(server), result)
    return result
