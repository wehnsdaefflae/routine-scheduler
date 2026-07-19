"""write_file's observation carries the file's TOTAL on-disk size after the write, so an
`append` that silently overwrote (size == bytes written, not prior+written) is provable from
the observation alone — the diagnostic that was missing when a routine reported an append
had clobbered a file's existing content.
"""

from __future__ import annotations

from rsched.config import ServerConfig, load_routine
from rsched.engine.executor import do_write_file
from rsched.engine.observations import format_observation
from rsched.engine.run_context import Budgets, RunContext
from rsched.engine.transcript import Transcript
from rsched.grants import GrantPolicy


def _ctx(make_routine, tmp_path) -> RunContext:
    d = make_routine()
    cfg, _problems = load_routine(d)
    assert cfg is not None
    run_dir = d / "runs" / "20260716-070000"
    run_dir.mkdir(parents=True)
    server = ServerConfig()
    server.libraries_home = tmp_path / "libraries"
    ctx = RunContext(routine=cfg, server=server, registry=None, run_ts="20260716-070000",
                     run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    ctx.grants = GrantPolicy()
    return ctx


def test_write_file_append_reports_grown_total_size(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    target = ctx.routine.dir / "state" / "note.md"
    first = do_write_file({"path": str(target), "content": "header\n"}, ctx)
    assert first["size"] == first["bytes"] == len(b"header\n")
    second = do_write_file(
        {"path": str(target), "content": "added\n", "append": True}, ctx)
    assert second["append"] is True
    assert second["bytes"] == len(b"added\n")
    # `size` is the TOTAL after appending — grown, NOT overwritten
    assert second["size"] == len(b"header\nadded\n")
    assert f"file now {second['size']} bytes" in format_observation(second)


def test_write_file_overwrite_size_equals_payload(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    target = ctx.routine.dir / "state" / "note.md"
    do_write_file({"path": str(target), "content": "aaaaaa\n"}, ctx)
    over = do_write_file({"path": str(target), "content": "bb\n"}, ctx)
    assert not over.get("append")
    assert over["size"] == over["bytes"] == len(b"bb\n")
