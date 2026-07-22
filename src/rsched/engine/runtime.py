"""Top-level run entry: load the routine, make sure its workflow is materialized into
main.md, wire the transcript, and drive one EngineLoop to completion.

The daemon (via `rsched engine-run`) and `rsched run-once` enter here. A routine is
self-contained at run time — nothing is read from the workflow library; the recipe was
decomposed into the routine's own main.md + stages/ at creation (or here, on first run,
for routines created as workflow + instruction only).
"""

from __future__ import annotations

import logging
from pathlib import Path

import frontmatter
import yaml

from ..config import ServerConfig, load_routine
from ..endpoints import EndpointRegistry
from ..ids import run_ts as make_run_ts
from .loop import EngineLoop
from .run_context import Budgets, RunContext
from .transcript import Transcript

log = logging.getLogger("rsched.runtime")


def _ensure_decomposed(routine_dir: Path, cfg, server) -> None:
    """A routine created as (workflow + instruction) but not yet turned into files — the wizard's
    clarify session is exactly this — has no main.md. Decompose its workflow against its
    instruction now (the SAME operation scaffold does at creation), so the run follows tailored
    MARKDOWN, never a raw pattern. Degrades to the whole workflow rendered as main.md if no
    endpoint is available.
    """
    if (routine_dir / "main.md").exists() or not cfg.workflow_slug:
        return
    from ..workflows import library
    from ..workflows.adapt import decompose, dump_markdown

    instruction = (routine_dir / "instruction.md").read_text(encoding="utf-8") \
        if (routine_dir / "instruction.md").exists() else ""
    traits_dir = routine_dir / "traits"
    traits = sorted(p.stem for p in traits_dir.glob("*.md")) if traits_dir.is_dir() else []
    result = decompose(server, cfg.workflow_slug, instruction, traits=traits)
    try:
        meta, _, _ = library.read_workflow(server.library_home, cfg.workflow_slug)
    except FileNotFoundError:
        meta = {}
    main_meta = {"name": cfg.name, "slug": cfg.slug,
                 "materialized_from": {"slug": cfg.workflow_slug,
                                       "commit": library.head_commit(server.library_home),
                                       "version": meta.get("version", 0)},
                 "stages": sorted(result["stages"])}
    if meta.get("tools") is not None:
        main_meta["tools"] = meta["tools"]
    (routine_dir / "stages").mkdir(exist_ok=True)
    for stage_name, stage_body in result["stages"].items():
        (routine_dir / "stages" / f"{stage_name}.md").write_text(stage_body.rstrip() + "\n",
                                                                 encoding="utf-8")
    (routine_dir / "main.md").write_text(
        dump_markdown(main_meta, result["main"]), encoding="utf-8")


def load_workflow(routine_dir, cfg) -> tuple[str, dict, list[str] | None]:
    """Load the routine's OWN main.md body (the recipe was materialized into it at generation).
    Returns (main_body, provenance, allowed_tools).

    A routine is self-contained: nothing is read from the workflow library at run time. The model
    reads the stage modules under stages/ and the practice modules under traits/ on demand via
    read_file (main.md routes to them).
    """
    main = routine_dir / "main.md"
    if not main.exists():
        raise RuntimeError(f"routine {cfg.slug!r} has no main.md — cannot run")
    try:
        meta, mbody = frontmatter.parse(main.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # fail loud: silently losing meta would drop the tools allowlist
        raise RuntimeError(
            f"routine {cfg.slug!r}: main.md frontmatter is invalid YAML: {exc}") from exc
    body = mbody.strip()
    raw_src = meta.get("materialized_from")
    src = raw_src if isinstance(raw_src, dict) else {}
    prov = {"slug": src.get("slug", cfg.workflow_slug),
            "commit": src.get("commit", cfg.workflow_commit), "version": src.get("version", 0)}
    raw_tools = meta.get("tools")
    tools = raw_tools if isinstance(raw_tools, list) else None
    return body, prov, tools


def run_routine(routine_dir: Path, server: ServerConfig, *, run_ts: str | None = None,
                model_overrides: dict | None = None, on_event=None,
                resume_from: str | None = None, run_dir: Path | None = None) -> tuple[str, Path]:
    """Execute one run of the routine at routine_dir. Returns (final status, run dir).
    on_event(obj) is called for every transcript event (used by `rsched run-once`). When
    resume_from is a prior run's ts, that run dir is reused and its transcript is rehydrated
    into the prompt so the run continues where it left off (with a fresh budget window).
    run_dir overrides the default `<routine_dir>/runs/<ts>` artifact location — the wizard's
    clarify sessions run their hidden throwaway workspace but land the run itself under the
    real `clarification` routine, so it has a valid run id and the standard run surfaces.
    """
    cfg, problems = load_routine(routine_dir)
    if cfg is None:
        raise RuntimeError("; ".join(problems))
    fatal = [p for p in problems if "missing" in p]
    if fatal:
        raise RuntimeError(f"routine {routine_dir.name}: " + "; ".join(fatal))
    if model_overrides:
        cfg.models.update(model_overrides)
    registry = EndpointRegistry(server)
    ts = resume_from or run_ts or make_run_ts()
    run_dir = run_dir or routine_dir / "runs" / ts
    if resume_from and not run_dir.is_dir():
        raise RuntimeError(f"cannot resume {ts}: run dir not found")
    run_dir.mkdir(parents=True, exist_ok=True)
    # append mode — resume adds after the tail; on_event echoes every event to the caller
    transcript = Transcript(run_dir / "transcript.jsonl", on_event=on_event)
    _, orch_ref = registry.for_model("main", cfg.models)
    ctx = RunContext(routine=cfg, server=server, registry=registry, run_ts=ts,
                     run_dir=run_dir, transcript=transcript,
                     budgets=Budgets.from_config(cfg.budgets))
    # Stamp the recipe version that produces this run (recipes.current_recipe_commit —
    # snapshots any uncommitted recipe edits first, e.g. the routine-improver's). None
    # for unversioned dirs (conversations). Lands in status.json + the usage record.
    from ..recipes import current_recipe_commit

    ctx.recipe_commit = current_recipe_commit(routine_dir)
    if not resume_from:
        _ensure_decomposed(routine_dir, cfg, server)   # workflow + instruction → main.md, if needed
    body, prov, allowed_tools = load_workflow(routine_dir, cfg)
    # instruction.md is only a transient compile seed (real routines don't persist it; the wizard's
    # throwaway clarify dir does). A top-level run never puts it in the prompt — main.md + stages/
    # are self-contained — so a missing seed is normal.
    instruction = ((routine_dir / "instruction.md").read_text(encoding="utf-8")
                   if (routine_dir / "instruction.md").exists() else "")
    if not resume_from:            # a resumed run keeps the original header (append-only)
        transcript.header(run_id=ctx.run_id, routine=cfg.slug, workflow=prov,
                          orchestrator={"endpoint": orch_ref.endpoint, "model": orch_ref.model})
    # Mount every bound machine's `share` (sshfs) at <routine>/mnt/<name>/ for the run's
    # lifetime, so local filesystem utils act on remote files (compute goes via `remote exec`;
    # this is the filesystem half). Best-effort; unmounted in the finally on EVERY exit path.
    from .. import machines as machines_mod

    mounts = machines_mod.mount_routine_shares(cfg, server)
    try:
        status = EngineLoop(ctx, body, instruction,
                            allowed_tools=allowed_tools, resume=bool(resume_from)).run()
        from ..health_events import log_workflow_usage

        log_workflow_usage(server.routines_home, routine=cfg.slug, run_id=ctx.run_id,
                           workflow=prov.get("slug") or "", depth=0, status=status,
                           turns=ctx.turn,
                           tokens=int(ctx.usage.get("in", 0)) + int(ctx.usage.get("out", 0)),
                           cost=float(ctx.usage.get("cost") or 0.0), referrals=ctx.referrals,
                           recipe_commit=ctx.recipe_commit, utils=ctx.util_stats,
                           asks_deferred=ctx.asks_deferred)
        # Refresh the persisted util-stats snapshot (the single source of truth the Stats tab
        # and the util-review routine both read) now that this run's usage record has landed.
        # Best-effort: a telemetry write must never break a finished run.
        try:
            from ..util_stats import write_util_stats_snapshot
            write_util_stats_snapshot(server)
        except Exception:  # stats telemetry must never break a run — but leave a breadcrumb
            log.warning("util-stats snapshot refresh failed at run finish", exc_info=True)
        return status, run_dir
    finally:
        machines_mod.unmount_routine_shares(mounts)
