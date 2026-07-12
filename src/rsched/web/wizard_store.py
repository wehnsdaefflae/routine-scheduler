"""On-disk store for new-routine wizard sessions: the dot-hidden .wizard-* dirs under
routines_home — creation, meta persistence, state snapshots, listing, and archival.
No FastAPI in here; api_wizard keeps the route handlers thin on top of this."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from ..daemon import registry
from ..daemon.runner import _pid_alive
from ..ids import now_iso, run_ts as make_run_ts
from ..paths import atomic_write_json, read_json
from ..schema_guard import loads_tolerant
from ..workflows.scaffold import GITIGNORE

WIZARD_BUDGETS = {"max_turns": 25, "max_wall_clock_min": 30, "max_total_tokens": 200_000,
                  "max_subruns": 0, "max_subrun_depth": 0, "ask_timeout_min": 120}

TERMINAL = ("finished", "failed", "aborted")


def sessions(app_state) -> dict:
    """In-memory handles of live clarify processes ({wid: {proc, run_ts, dir}}); snapshots fall
    back to disk for sessions this process never saw (after a reload / daemon restart)."""
    if not hasattr(app_state, "wizards"):
        app_state.wizards = {}
    return app_state.wizards


def read_result(d: Path) -> dict | None:
    """state/wizard_result.json is LLM-authored — read it tolerantly (control chars in strings)."""
    try:
        obj = loads_tolerant((d / "state" / "wizard_result.json").read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except (OSError, ValueError):
        return None


def read_meta(d: Path) -> dict:
    obj = read_json(d / "state" / "wizard_meta.json")
    return obj if isinstance(obj, dict) else {}


def latest_run_ts(d: Path) -> str | None:
    runs = sorted((d / "runs").glob("*")) if (d / "runs").is_dir() else []
    return runs[-1].name if runs else None


def draft_preview(d: Path) -> str:
    try:
        text = (d / "instruction.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text[:140] + ("…" if len(text) > 140 else "")


def snapshot(app_state, d: Path) -> dict:
    """Reconstruct a wizard session's live state from disk — works in a fresh process that never
    saw it (after a reload / daemon restart). The stage is derived, not stored, so it is always
    consistent with what is actually on disk:
      chat     → clarify run is live and has not produced a result yet
      suggest  → state/wizard_result.json holds a refined instruction (ready to pick + create)
      building → the routine is being scaffolded in the background (finalize.json state)
      done     → the routine was created (finalize.json state; excluded from the in-flight list)
      error    → the clarify run, or the build, failed
    """
    meta = read_meta(d)
    fin = read_json(d / "state" / "finalize.json")
    if isinstance(fin, dict) and fin.get("state"):        # finalize started → its state wins
        return {"wid": d.name, "run_ts": meta.get("run_ts", ""), "created": meta.get("created", ""),
                "draft": draft_preview(d),
                "stage": fin["state"], "state": fin["state"], "has_result": True,
                "slug": fin.get("slug"), "run_id": fin.get("run_id"), "error": fin.get("error"),
                "question": None, "alive": None}
    result = read_result(d)
    has_result = isinstance(result, dict) and bool(result.get("refined_instruction"))
    ts = (sessions(app_state).get(d.name) or {}).get("run_ts") or meta.get("run_ts") or latest_run_ts(d)
    run = registry.read_run(d / "runs" / ts, d.name.lstrip(".")) if ts and (d / "runs" / ts).is_dir() else None
    state = run.state if run else "unknown"
    stage = "suggest" if has_result else ("error" if state in TERMINAL else "chat")
    # Only meaningful while clarifying — a DEAD pid there means the session is stuck (needs
    # cancel). A missing pid is NOT death: the boot status (create_session) carries no pid
    # until the engine takes over, and the pre-run decompose can hold that phase for a
    # while — report unknown (None), never dead, or the UI shows a false "no longer running".
    alive = _pid_alive(run.pid) if (stage == "chat" and run is not None and run.pid) else None
    return {"wid": d.name, "run_ts": ts, "created": meta.get("created", ""),
            "draft": draft_preview(d),
            "stage": stage, "state": state, "has_result": has_result,
            "question": run.question if run else None, "alive": alive}


def list_sessions(app_state) -> list[dict]:
    """Every in-flight session (newest first); completed builds ('done') are not in-flight."""
    home = app_state.server.routines_home
    out: list[dict] = []
    if home.is_dir():
        for d in sorted(home.glob(".wizard-*"), key=lambda p: p.name, reverse=True):
            if not d.is_dir():
                continue
            try:
                snap = snapshot(app_state, d)
            except Exception:  # a half-written dir must never break the list
                continue
            if snap.get("stage") != "done":
                out.append(snap)
    return out


def create_session(server, draft: str) -> tuple[str, str, Path]:
    """Materialize a session dir on disk (blocking I/O — call via to_thread) and return
    (wid, run_ts, dir), ready for the engine subprocess to take over.

    Clarify is a library workflow (clarify-instruction) APPLIED to the raw draft — the same
    operation as any (workflow + task → routine). The session is created as (workflow +
    instruction) with NO main.md; the engine decomposes it on run, so this throwaway
    clarification routine follows tailored markdown (reliable) instead of a raw pattern.
    Its `tools:` allowlist carries through the decompose."""
    from ..workflows import library

    ts = make_run_ts()
    wid = f".wizard-{ts}"
    d = server.routines_home / wid
    (d / "state").mkdir(parents=True)
    (d / "inbox").mkdir()
    commit = library.head_commit(server.library_home)
    (d / "instruction.md").write_text(draft.rstrip() + "\n", encoding="utf-8")
    (d / "LEDGER.md").write_text("# LEDGER — wizard session\n", encoding="utf-8")
    (d / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (d / "routine.yaml").write_text(yaml.safe_dump({
        "name": "New-routine wizard", "slug": f"wizard-{ts}", "enabled": False,
        "description": "New-routine clarification wizard session.",
        "schedule": {"cron": "", "tz": "Europe/Berlin", "catchup": "skip"},
        "workflow": {"library_slug": "clarify-instruction", "library_commit": commit},
        "budgets": WIZARD_BUDGETS,
        "permissions": [],   # the clarify session holds no grants; its tools allowlist narrows further
    }, sort_keys=False), encoding="utf-8")
    # Persist the session's meta so it survives a daemon/container restart: /api/wizard can
    # list it without depending on the client or in-memory state.
    atomic_write_json(d / "state" / "wizard_meta.json",
                      {"wid": wid, "run_ts": ts, "created": now_iso()})
    write_candidates(server, d)   # the workflow patterns the clarifier suggests + marries against
    # An initial status so the client sees "starting" while the engine decomposes then runs — the
    # engine takes over ownership of status.json once it boots.
    (d / "runs" / ts).mkdir(parents=True, exist_ok=True)
    atomic_write_json(d / "runs" / ts / "status.json",
                      {"run_id": f"{wid}:{ts}", "state": "starting", "started": ts,
                       "updated": now_iso(), "turn": 0, "question": None, "usage": {"in": 0, "out": 0}})
    return wid, ts, d


def candidate_patterns(server) -> list[dict]:
    from ..workflows import library
    return [w for w in library.list_workflows(server.library_home)
            if w.get("status") == "stable" and "meta" not in (w.get("tags") or [])]


def write_candidates(server, d: Path) -> None:
    """Write the workflow patterns the clarifier chooses from into the session's state/, so it can
    suggest one (and marry the task to it) by reading a single file — its `tools` allowlist permits
    read_file but not library discovery. Each pattern is inlined with its full control flow."""
    from ..workflows import library

    parts = ["# Candidate workflow patterns", "",
             "Pick the ONE whose control flow best fits this task (that is your suggestion), or choose",
             "to generate a new one. A pattern's parameter contract is its dummy imports.", ""]
    for w in candidate_patterns(server):
        try:
            _, _, raw = library.read_workflow(server.library_home, w["slug"])
        except FileNotFoundError:
            continue
        parts += [f"## {w['slug']} — {w['description']}", f"when_to_use: {w['when_to_use']}", "",
                  "```python", raw.strip(), "```", ""]
    (d / "state" / "candidates.md").write_text("\n".join(parts), encoding="utf-8")


def archive_session(home: Path, d: Path, name: str) -> Path:
    """Move a session dir into routines_home/.archive under `name`, uniquified if taken —
    canceled and completed sessions both leave the in-flight set this way."""
    archive = home / ".archive"
    archive.mkdir(exist_ok=True)
    dest = archive / name
    if dest.exists():
        dest = archive / f"{name}-{make_run_ts()}"
    shutil.move(str(d), str(dest))
    return dest
