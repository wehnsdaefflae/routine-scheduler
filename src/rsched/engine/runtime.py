"""Top-level run entry: load the routine, make sure its workflow is materialized into
main.md, wire the transcript, and drive one EngineLoop to completion.

The daemon (via `rsched engine-run`) and `rsched run-once` enter here. A routine is
self-contained at run time — nothing is read from the workflow library; the recipe was
decomposed into the routine's own main.md + steps/ at creation (or here, on first run,
for routines created as workflow + instruction only).
"""

from __future__ import annotations

from pathlib import Path

from ..config import ServerConfig, load_routine
from ..endpoints import EndpointRegistry
from ..frontmatter import load as load_frontmatter
from ..ids import run_ts as make_run_ts
from .loop import EngineLoop
from .run_context import Budgets, RunContext
from .transcript import Transcript


def _ensure_decomposed(routine_dir: Path, cfg, server) -> None:
    """A routine created as (workflow + instruction) but not yet turned into files — the wizard's
    clarify session is exactly this — has no main.md. Decompose its workflow against its instruction
    now (the SAME operation scaffold does at creation), so the run follows tailored MARKDOWN, never a
    raw pattern. Degrades to the whole workflow rendered as main.md if no endpoint is available."""
    if (routine_dir / "main.md").exists() or not cfg.workflow_slug:
        return
    from .. import frontmatter
    from ..workflows import library
    from ..workflows.adapt import decompose

    instruction = (routine_dir / "instruction.md").read_text(encoding="utf-8") \
        if (routine_dir / "instruction.md").exists() else ""
    result = decompose(server, cfg.workflow_slug, instruction)
    try:
        meta, _, _ = library.read_workflow(server.library_home, cfg.workflow_slug)
    except FileNotFoundError:
        meta = {}
    main_meta = {"name": cfg.name, "slug": cfg.slug,
                 "materialized_from": {"slug": cfg.workflow_slug,
                                       "commit": library.head_commit(server.library_home),
                                       "version": meta.get("version", 0)},
                 "modules": sorted(result["modules"])}
    if meta.get("tools") is not None:
        main_meta["tools"] = meta["tools"]
    if meta.get("includes"):
        main_meta["includes"] = list(meta["includes"])
    (routine_dir / "steps").mkdir(exist_ok=True)
    for mod_name, mod_body in result["modules"].items():
        (routine_dir / "steps" / f"{mod_name}.md").write_text(mod_body.rstrip() + "\n", encoding="utf-8")
    (routine_dir / "main.md").write_text(frontmatter.dump(main_meta, result["main"]), encoding="utf-8")


def load_workflow(routine_dir, cfg) -> tuple[str, str, dict, list[str] | None]:
    """Load the routine's OWN main.md body (the recipe was materialized into it at generation)
    plus its active FRAGMENTS. Returns (main_body, fragments_text, provenance, allowed_tools).

    A routine is self-contained: nothing is read from the workflow library at run time. The model
    reads the step modules under steps/ on demand via read_file (main.md routes to them); fragments
    are the routine's editable copies under fragments/."""
    from .. import fragments_lib

    main = routine_dir / "main.md"
    if not main.exists():
        raise RuntimeError(f"routine {cfg.slug!r} has no main.md — cannot run")
    meta, mbody = load_frontmatter(main)
    body = mbody.strip()
    src = meta.get("materialized_from") if isinstance(meta.get("materialized_from"), dict) else {}
    prov = {"slug": src.get("slug", cfg.workflow_slug),
            "commit": src.get("commit", cfg.workflow_commit), "version": src.get("version", 0)}

    frag_dir = routine_dir / "fragments"
    files = sorted(frag_dir.glob("*.md")) if frag_dir.is_dir() else []
    parts = [fragments_lib.fragment_body(p.read_text(encoding="utf-8")).strip() for p in files]
    tools = meta.get("tools") if isinstance(meta.get("tools"), list) else None
    return body, "\n\n".join(parts), prov, tools


def run_routine(routine_dir: Path, server: ServerConfig, *, run_ts: str | None = None,
                model_overrides: dict | None = None, on_event=None,
                resume_from: str | None = None) -> tuple[str, Path]:
    """Execute one run of the routine at routine_dir. Returns (final status, run dir).
    on_event(obj) is called for every transcript event (used by `rsched run-once`). When
    resume_from is a prior run's ts, that run dir is reused and its transcript is rehydrated
    into the prompt so the run continues where it left off (with a fresh budget window)."""
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
    run_dir = routine_dir / "runs" / ts
    if resume_from and not run_dir.is_dir():
        raise RuntimeError(f"cannot resume {ts}: run dir not found")
    run_dir.mkdir(parents=True, exist_ok=True)
    # append mode — resume adds after the tail; on_event echoes every event to the caller
    transcript = Transcript(run_dir / "transcript.jsonl", on_event=on_event)
    _, orch_ref = registry.for_model("main", cfg.models)
    ctx = RunContext(routine=cfg, server=server, registry=registry, run_ts=ts,
                     run_dir=run_dir, transcript=transcript,
                     budgets=Budgets.from_config(cfg.budgets))
    if not resume_from:
        _ensure_decomposed(routine_dir, cfg, server)   # workflow + instruction → main.md, if not yet
    body, fragments_text, prov, allowed_tools = load_workflow(routine_dir, cfg)
    instruction = (routine_dir / "instruction.md").read_text(encoding="utf-8")
    if not resume_from:            # a resumed run keeps the original header (transcript is append-only)
        transcript.header(run_id=ctx.run_id, routine=cfg.slug, workflow=prov,
                          orchestrator={"endpoint": orch_ref.endpoint, "model": orch_ref.model})
    status = EngineLoop(ctx, body, instruction, fragments_text=fragments_text,
                        allowed_tools=allowed_tools, resume=bool(resume_from)).run()
    return status, run_dir
