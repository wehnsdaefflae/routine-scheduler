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


def test_append_outside_own_dir_preserves_existing_content(make_routine, tmp_path):
    """R1 regression: append:true on an existing file OUTSIDE the routine dir (an
    fs_write_root — the reported case was a conversation LEDGER) passes the grounding
    gate UNREAD (append adds without destroying) and keeps every original byte; the
    observation proves it (size == prior + bytes, never == bytes)."""
    ctx = _ctx(make_routine, tmp_path)
    ext = tmp_path / "conversations" / "c-20260719-162554"
    ext.mkdir(parents=True)
    ctx.routine.fs_write_roots = [tmp_path / "conversations"]
    target = ext / "LEDGER.md"
    original = "# LEDGER — conversation\n\n### seed — conversation created\n"
    target.write_text(original, encoding="utf-8")

    entry = "### 20260719-190013 — appended entry\n"
    obs = do_write_file({"path": str(target), "content": entry, "append": True}, ctx)
    assert "error" not in obs                       # never read this run, still allowed
    text = target.read_text(encoding="utf-8")
    assert text == original + entry                 # appended, NOT overwritten
    assert obs["append"] is True
    assert obs["size"] == len((original + entry).encode("utf-8"))
    assert obs["size"] == len(original.encode("utf-8")) + obs["bytes"]
