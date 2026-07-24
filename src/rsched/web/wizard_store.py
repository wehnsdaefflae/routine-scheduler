"""On-disk store for new-routine wizard sessions: the dot-hidden .wizard-* dirs under
routines_home — creation, meta persistence, state snapshots, listing, and archival.
No FastAPI in here; api_wizard keeps the route handlers thin on top of this.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from .. import registry
from ..config import load_tuning, write_tuning
from ..daemon.runner import _pid_alive
from ..ids import now_iso
from ..ids import run_ts as make_run_ts
from ..paths import atomic_write, atomic_write_json, read_json
from ..schedule import server_tz
from ..schema_guard import loads_tolerant
from ..workflows.scaffold import GITIGNORE

WIZARD_BUDGETS = {"max_turns": 25, "max_wall_clock_min": 30, "max_total_tokens": 200_000,
                  "max_subruns": 0, "max_subrun_depth": 0, "ask_timeout_min": 120}

# The protected 'clarification' template routine: every clarify session copies its budgets,
# models, and traits/ from this dir when it exists (seeded via routine-seed/, adopted at
# boot). Absent — a deploy the seed hasn't reached, or tests — the hardcoded WIZARD_BUDGETS
# above stay the fallback, so the wizard never depends on the template being there.
TEMPLATE_SLUG = "clarification"


def template_dir(server) -> Path | None:
    d = server.routines_home / TEMPLATE_SLUG
    return d if (d / "routine.yaml").is_file() else None


def template_defaults(server) -> tuple[dict, dict, str]:
    """(budgets, models, deliberation) a new session copies from the clarification template
    routine — the RAW yaml, not load_routine, because the loader backfills DEFAULT_BUDGETS
    (routine defaults, e.g. a 5-minute ask timeout) for omitted keys; here an omitted key
    must keep its WIZARD_BUDGETS value. Only known budget keys pass; models pass through
    as-is (empty = system-model fallback); deliberation comes from the template's
    tuning.yaml ("" = default).
    """
    d = template_dir(server)
    if d is not None:
        try:
            raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            raw = None
        if isinstance(raw, dict):
            raw_budgets = raw.get("budgets")
            raw_models = raw.get("models")
            budgets = raw_budgets if isinstance(raw_budgets, dict) else {}
            models = raw_models if isinstance(raw_models, dict) else {}
            budgets = {k: v for k, v in budgets.items()
                       if k in WIZARD_BUDGETS and isinstance(v, int)}
            tuning, _problems = load_tuning(d)
            return ({**WIZARD_BUDGETS, **budgets}, dict(models),
                    tuning.get("deliberation", ""))
    return dict(WIZARD_BUDGETS), {}, ""


def sessions(app_state) -> dict:
    """In-memory handles of live clarify processes ({wid: {proc, run_ts, dir}}); snapshots fall
    back to disk for sessions this process never saw (after a reload / daemon restart).
    """
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


def clarify_run_dir(server, d: Path, ts: str) -> Path:
    """Where a session's clarify run lives. New sessions land it under the REAL clarification
    routine — `routines_home/clarification/runs/<ts>` — so the run has a valid
    `clarification:<ts>` id and every standard run surface (run page, SSE tail, registry,
    orphan recovery) applies with no bridge (D13=B). Legacy sessions, and deploys the
    template has not reached, keep the run session-local under `<session>/runs/<ts>`.
    """
    real = server.routines_home / TEMPLATE_SLUG / "runs" / ts
    return real if real.is_dir() else d / "runs" / ts


def clarify_run_id(server, d: Path, ts: str | None) -> str:
    """`clarification:<ts>` when this session's run lives under the template (D13=B) — the
    standard run page renders it, so every surface links there. Empty for a legacy
    session-local run (no navigable run page; the session can only be canceled).
    """
    if not ts:
        return ""
    rd = clarify_run_dir(server, d, ts)
    return f"{TEMPLATE_SLUG}:{ts}" if rd.parent.parent.name == TEMPLATE_SLUG else ""


def session_inbox_dir(server, run_dir: Path) -> Path:
    """The inbox a run-page message (inject/converse) must land in so a LIVE run actually
    polls it. For a D13=B clarify run the artifact dir is `clarification/runs/<ts>` but the
    engine executes the session in the hidden throwaway workspace `.wizard-<ts>` and polls
    THAT dir's inbox — so a message routed to `clarification/inbox` would never be seen.
    Redirect to the workspace inbox when this run is a clarify run (its artifact dir sits
    under the clarification template) and the `.wizard-<ts>` workspace still exists. Every
    other run — ordinary routines, and legacy session-local clarify runs whose run_dir is
    already under `.wizard-<ts>` — falls through to the normal `routine_dir/inbox`.
    """
    routine_dir = run_dir.parent.parent
    if routine_dir.name == TEMPLATE_SLUG:
        workspace = server.routines_home / f".wizard-{run_dir.name}"
        if workspace.is_dir():
            return workspace / "inbox"
    return routine_dir / "inbox"


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
        # `run_id` is the NEW routine's first run (run_now); `clarify_run_id` stays the
        # session's own clarify run — the run page the setup panel lives on.
        return {"wid": d.name, "run_ts": meta.get("run_ts", ""), "created": meta.get("created", ""),
                "draft": draft_preview(d),
                "clarify_run_id": clarify_run_id(app_state.server, d, meta.get("run_ts")),
                "stage": fin["state"], "state": fin["state"], "has_result": True,
                "slug": fin.get("slug"), "run_id": fin.get("run_id"), "error": fin.get("error"),
                # F192: live build progress (written by the decompose pipeline's callback)
                "step": fin.get("step"), "done": fin.get("done"), "total": fin.get("total"),
                "question": None, "alive": None}
    result = read_result(d)
    has_result = isinstance(result, dict) and bool(result.get("refined_instruction"))
    ts = ((sessions(app_state).get(d.name) or {}).get("run_ts") or meta.get("run_ts")
          or latest_run_ts(d))
    rd = clarify_run_dir(app_state.server, d, ts) if ts else None
    run = registry.read_run(rd, d.name.lstrip(".")) if rd is not None and rd.is_dir() else None
    state = run.state if run else "unknown"
    stage = "suggest" if has_result else ("error" if state in registry.TERMINAL_STATES else "chat")
    # Only meaningful while clarifying — a DEAD pid there means the session is stuck (needs
    # cancel). A missing pid is NOT death: the boot status (create_session) carries no pid
    # until the engine takes over, and the pre-run decompose can hold that phase for a
    # while — report unknown (None), never dead, or the UI shows a false "no longer running".
    alive = _pid_alive(run.pid) if (stage == "chat" and run is not None and run.pid) else None
    snap = {"wid": d.name, "run_ts": ts, "created": meta.get("created", ""),
            "draft": draft_preview(d),
            "clarify_run_id": clarify_run_id(app_state.server, d, ts),
            "stage": stage, "state": state, "has_result": has_result,
            "question": run.question if run else None, "alive": alive}
    if stage == "error":
        # the full draft, so the error screen can offer "retry with the same draft" —
        # losing the user's text to a failed clarify run is the real cost of the dead end
        try:
            snap["draft_full"] = (d / "instruction.md").read_text(encoding="utf-8").strip()
        except OSError:
            snap["draft_full"] = ""
    return snap


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
            except Exception:  # noqa: S112 — a half-written dir must never break the list
                continue
            if snap.get("stage") != "done":
                out.append(snap)
    return out


def recover_orphan_builds(server) -> list[str]:
    """Reconcile wizard BUILDS orphaned by a server restart/crash. api_wizard._build_routine runs
    as a web-process background task (asyncio.create_task) with NO persistence: if the process dies
    between finalize.json='building' and the terminal 'done'/'error' write (a self-restart drains
    engine runs but not in-flight builds, plus crashes/SIGKILL), the build is stranded forever —
    finalize.json stuck at 'building', a half-scaffolded routine dir that never got its
    routine.yaml, and nothing to finish it (Runner.recover_orphans covers engine RUNS only,
    never builds). The setup then 'never finishes' with no error surfaced.

    Called once at boot: a fresh process owns no build task, so ANY 'building' state is by
    definition orphaned. Mark each 'error' (recoverable — the user retries or cancels from
    the wizard) and remove a half-built routine dir that lacks routine.yaml (mirrors
    _build_routine's except handler).
    Returns the recovered wids.
    """
    home = server.routines_home
    recovered: list[str] = []
    if not home.is_dir():
        return recovered
    for d in sorted(home.glob(".wizard-*")):
        if not d.is_dir():
            continue
        fin = read_json(d / "state" / "finalize.json")
        if not (isinstance(fin, dict) and fin.get("state") == "building"):
            continue
        slug = fin.get("slug")
        if slug:
            partial = home / str(slug)
            if partial.is_dir() and not (partial / "routine.yaml").exists():
                shutil.rmtree(partial, ignore_errors=True)
        atomic_write_json(d / "state" / "finalize.json",
                          {"state": "error", "slug": slug,
                           "error": "build interrupted by a server restart — please retry"})
        recovered.append(d.name)
    return recovered


def create_session(server, draft: str) -> tuple[str, str, Path]:
    """Materialize a session dir on disk (blocking I/O — call via to_thread) and return
    (wid, run_ts, dir), ready for the engine subprocess to take over.

    Clarify is a library workflow (clarify-instruction) APPLIED to the raw draft — the same
    operation as any (workflow + task → routine). The session is created as (workflow +
    instruction) with NO main.md; the engine decomposes it on run, so this throwaway
    clarification routine follows tailored markdown (reliable) instead of a raw pattern.
    Its `tools:` allowlist carries through the decompose.
    """
    from ..workflows import library

    ts = make_run_ts()
    wid = f".wizard-{ts}"
    d = server.routines_home / wid
    (d / "state").mkdir(parents=True, exist_ok=True)   # same-second double-create: reuse
    (d / "inbox").mkdir(exist_ok=True)
    commit = library.head_commit(server.libraries_home)
    (d / "instruction.md").write_text(draft.rstrip() + "\n", encoding="utf-8")
    (d / "LEDGER.md").write_text("# LEDGER — wizard session\n", encoding="utf-8")
    (d / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    budgets, models, level = template_defaults(server)
    tpl = template_dir(server)
    if tpl is not None and (tpl / "traits").is_dir():
        # the template's practice modules ride into every session (traits are files, not yaml)
        shutil.copytree(tpl / "traits", d / "traits", dirs_exist_ok=True)
    atomic_write(d / "routine.yaml", yaml.safe_dump({
        # With the template present the session IS a clarification run: the engine composes
        # run_id from this slug, so status/transcript/usage all stamp `clarification:<ts>`.
        "name": "New-routine wizard",
        "slug": TEMPLATE_SLUG if tpl is not None else f"wizard-{ts}", "enabled": False,
        "description": "New-routine clarification wizard session.",
        "schedule": {"cron": "", "tz": server_tz(), "catchup": "skip"},
        "workflow": {"library_slug": "clarify-instruction", "library_commit": commit},
        "budgets": budgets, **({"models": models} if models else {}),
        "permissions": [], "capabilities": {},   # the clarify session holds nothing gated;
        # its tools allowlist narrows further
    }, sort_keys=False))
    if level:   # tuning rides its own file, mirroring the template
        write_tuning(d, {"deliberation": level})
    # Persist the session's meta so it survives a daemon/container restart: /api/wizard can
    # list it without depending on the client or in-memory state.
    atomic_write_json(d / "state" / "wizard_meta.json",
                      {"wid": wid, "run_ts": ts, "created": now_iso()})
    write_candidates(server, d)   # the workflow patterns the clarifier suggests + marries against
    # An initial status so the client sees "starting" while the engine decomposes then runs — the
    # engine takes over ownership of status.json once it boots.
    run_dir = ((server.routines_home / TEMPLATE_SLUG / "runs" / ts) if tpl is not None
               else d / "runs" / ts)
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"{TEMPLATE_SLUG if tpl is not None else wid}:{ts}",
                       "state": "starting", "started": ts,
                       "updated": now_iso(), "turn": 0, "question": None,
                       "usage": {"in": 0, "out": 0}})
    return wid, ts, d


def candidate_patterns(server) -> list[dict]:
    from ..workflows import library
    return list(library.list_workflows(server.libraries_home))


def write_candidates(server, d: Path) -> None:
    """Write the workflow patterns the clarifier chooses from into the session's state/, so it can
    suggest one (and marry the task to it) by reading a single file — its `tools` allowlist permits
    read_file but not library discovery. Each pattern is inlined with its full control flow.
    """
    from ..workflows import library

    parts = ["# Candidate workflow patterns", "",
             "Pick the ONE whose control flow best fits this task (that is your suggestion),",
             "or choose",
             "to generate a new one. A pattern's parameter contract is its dummy imports.", ""]
    for w in candidate_patterns(server):
        try:
            _, raw = library.read_workflow(server.libraries_home, w["slug"])
        except FileNotFoundError:
            continue
        parts += [f"## {w['slug']} — {w['description']}", f"when_to_use: {w['when_to_use']}", "",
                  "```python", raw.strip(), "```", ""]
    (d / "state" / "candidates.md").write_text("\n".join(parts), encoding="utf-8")


def archive_session(home: Path, d: Path, name: str) -> Path:
    """Move a session dir into routines_home/.archive under `name`, uniquified if taken —
    canceled and completed sessions both leave the in-flight set this way.
    """
    archive = home / ".archive"
    archive.mkdir(exist_ok=True)
    dest = archive / name
    if dest.exists():
        dest = archive / f"{name}-{make_run_ts()}"
    shutil.move(str(d), str(dest))
    return dest
