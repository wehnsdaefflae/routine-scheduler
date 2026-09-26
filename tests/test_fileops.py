"""The `.memory/` seal, on the RESOLVED path.

`engine/actions.py` tests it lexically, on the string the model supplied (`rel == ".memory"
or rel.startswith(".memory/")`) — the cheap schema-retry correction that costs no turn. Every
sibling seal in `fileops` is on the resolved path (`runs/`, `.util_outputs/`, `routine.yaml`),
and `.memory/` had no such backstop: `state/../.memory/INDEX.md` and the absolute form both
walk past the string test, and `resolve_rel` puts them right back inside the routine's own dir
where `_write_gate` passed them.

What that bypassed is the whole reason the index is engine-owned: a model-authored index once
named files the engine then RENAMED, and 36 % of live history reads returned ENOENT. It also
bypassed the 100-line note cap and the `memory` capability gate — a routine that does not hold
the memory permission could read and rewrite its own `.memory/` through a file action.
"""

from __future__ import annotations

from rsched.config import ServerConfig, load_routine
from rsched.engine.budgets_config import Budgets
from rsched.engine.fileops import (
    do_delete,
    do_edit_file,
    do_mkdir,
    do_move,
    do_read_file,
    do_write_file,
)
from rsched.engine.run_context import RunContext
from rsched.engine.transcript import Transcript
from rsched.grantpolicy import GrantPolicy


def _ctx(make_routine, tmp_path) -> RunContext:
    d = make_routine()
    cfg, _problems = load_routine(d)
    assert cfg is not None
    run_dir = d / "runs" / "20260922-070000"
    run_dir.mkdir(parents=True)
    (d / ".memory").mkdir()
    (d / ".memory" / "INDEX.md").write_text("# INDEX\n- topic — engine-written\n",
                                            encoding="utf-8")
    server = ServerConfig()
    server.libraries_home = tmp_path / "libraries"
    ctx = RunContext(routine=cfg, server=server, registry=None, run_ts="20260922-070000",
                     run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    ctx.grants = GrantPolicy()
    return ctx


TRAVERSAL = "state/../.memory/INDEX.md"


def test_a_traversal_write_into_memory_is_refused(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    obs = do_write_file({"kind": "write_file", "path": TRAVERSAL, "content": "mine now"}, ctx)
    assert "memory_read / memory_write" in obs["error"]
    assert (ctx.routine.dir / ".memory" / "INDEX.md").read_text().startswith("# INDEX")


def test_an_absolute_write_into_memory_is_refused(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    absolute = str(ctx.routine.dir / ".memory" / "INDEX.md")
    obs = do_write_file({"kind": "write_file", "path": absolute, "content": "mine now"}, ctx)
    assert "memory_read / memory_write" in obs["error"]


def test_every_write_shaped_action_holds_the_seal(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    assert "memory_read" in do_edit_file(
        {"kind": "edit_file", "path": TRAVERSAL, "anchor": "INDEX",
         "replacement": "x"}, ctx)["error"]
    assert "memory_read" in do_delete({"kind": "delete", "path": TRAVERSAL}, ctx)["error"]
    assert "memory_read" in do_mkdir(
        {"kind": "mkdir", "path": "state/../.memory/new"}, ctx)["error"]
    moved = do_move({"kind": "move", "src": "state", "dst": "state/../.memory/stolen"}, ctx)
    assert "memory_read" in moved["error"]
    assert (ctx.routine.dir / ".memory" / "INDEX.md").is_file()


def test_the_seal_covers_reads_too(make_routine, tmp_path):
    """`memory_read` is gated by the `memory` capability and caps what it returns; a
    `read_file` around it is the same bypass in the other direction."""
    ctx = _ctx(make_routine, tmp_path)
    obs = do_read_file({"kind": "read_file", "path": TRAVERSAL}, ctx)
    assert "memory_read / memory_write" in obs["error"]


def test_ordinary_paths_are_untouched(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    obs = do_write_file({"kind": "write_file", "path": "state/notes.md", "content": "hi"}, ctx)
    assert "error" not in obs
    # a sibling directory whose NAME starts the same way is not the memory dir
    ok = do_write_file({"kind": "write_file", "path": ".memory-notes/x.md", "content": "hi"},
                       ctx)
    assert "error" not in ok


def test_writing_state_phase_json_records_the_stage_as_entered(make_routine, tmp_path):
    """A run that works a stage WITHOUT re-reading its module is not a run that skipped it.

    The read stamp only fires on a `read_file` of `stages/<name>.md`, so coverage measured
    module RE-READS and the experienced routines — the ones whose recipe the model already
    holds — reported every stage skipped (F563: 24 of 73 fleet runs on 2026-09-26). The run's
    own cursor is the second, stronger source: it wrote the phase down deliberately.
    """
    ctx = _ctx(make_routine, tmp_path)
    d = ctx.routine.dir
    (d / "stages").mkdir(exist_ok=True)
    for stem in ("orient", "act"):
        (d / "stages" / f"{stem}.md").write_text(f"# Step: {stem}\n", encoding="utf-8")
    (d / "main.md").write_text("## Run flow\n1. `stages/orient.md`\n2. `stages/act.md`\n",
                               encoding="utf-8")

    # Never opens either module — it routes by writing its own cursor, as a stage-driven
    # recipe does once the model knows the flow.
    assert do_write_file({"kind": "write_file", "path": "state/phase.json",
                          "content": {"phase": "orient"}}, ctx).get("error") is None
    assert ctx.phases_recorded == ["orient"]
    assert ctx.phases_entered == []            # nothing was re-read, and that is the point
    assert ctx.stage_coverage()["skipped"] == ["act"]   # NOT ["orient", "act"]

    # An in-place patch of the cursor counts the same way, and never double-counts.
    do_edit_file({"kind": "edit_file", "path": "state/phase.json",
                  "anchor": '"orient"', "replacement": '"act"'}, ctx)
    assert ctx.phases_recorded == ["orient", "act"]
    assert ctx.stage_coverage()["entered"] == ["orient", "act"]
    assert ctx.stage_coverage()["skipped"] == []

    # A cursor carrying no usable phase is not evidence, and must never break the write.
    # (Prose cannot get in: F460's format check refuses to turn a valid JSON file into
    # non-JSON, so the only shapes that reach the hook are JSON ones.)
    assert do_write_file({"kind": "write_file", "path": "state/phase.json",
                          "content": {"phase": ""}}, ctx).get("error") is None
    assert do_write_file({"kind": "write_file", "path": "state/phase.json",
                          "content": {"note": "no phase key at all"}}, ctx).get("error") is None
    assert do_write_file({"kind": "write_file", "path": "state/phase.json",
                          "content": ["not an object"]}, ctx).get("error") is None
    assert ctx.phases_recorded == ["orient", "act"]   # unchanged by all three
