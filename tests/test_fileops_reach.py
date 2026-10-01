"""The file-action seals hold on every routine dir a run can REACH, compared resolved.

Two ways the seals in `engine/fileops.py` used to open, both because they were anchored on
`ctx.routine.dir` as given:

- A CHILD run's own dir is its workspace, `runs/<ts>/sub/<n>/`, but its roots extend the
  parent's tree (F185). Every seal was anchored on the workspace, so a sub-workflow — which
  holds no capability at all — could rewrite the routine's recipe, its `.memory/` index and
  its finish line, write the run's `control.json` (whose `config_change` the parent adopts
  live: grants, budgets) and `status.json`, and read earlier runs despite having no run
  history.
- `resolve_rel` hands back a RESOLVED path, but the dir it was compared with was not. Behind a
  symlinked routines home (or a relative CLI dir path) nothing matched: `runs/` was writable,
  `.memory/` and the recipe were guarded only by the read-before-overwrite rule, and the
  stage tracker never fired.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rsched.config import ServerConfig, load_routine
from rsched.engine.budgets_config import Budgets
from rsched.engine.childrun import _sub_routine
from rsched.engine.fileops import do_read_file, do_write_file
from rsched.engine.fsops import do_delete, do_move
from rsched.engine.mediaops import do_view_image
from rsched.engine.run_context import RunContext
from rsched.engine.transcript import Transcript
from rsched.grantpolicy import GrantPolicy

TS = "20260922-070000"
EARLIER = "20260901-000000"


def _routine(make_routine):
    d = make_routine()
    (d / ".memory").mkdir()
    (d / ".memory" / "INDEX.md").write_text("# INDEX\n", encoding="utf-8")
    (d / "stages").mkdir()
    (d / "stages" / "scan.md").write_text("scan\n", encoding="utf-8")
    (d / "state" / "finish-line.json").write_text('{"outcomes": []}\n', encoding="utf-8")
    (d / ".util_outputs").mkdir()
    (d / "runs" / EARLIER).mkdir(parents=True)
    (d / "runs" / EARLIER / "transcript.jsonl").write_text('{"type": "header"}\n',
                                                           encoding="utf-8")
    (d / "runs" / TS).mkdir(parents=True)
    (d / "runs" / TS / "transcript.jsonl").write_text('{"type": "header"}\n', encoding="utf-8")
    return d


def _top(d, tmp_path) -> RunContext:
    cfg, _ = load_routine(d)
    assert cfg is not None
    server = ServerConfig()
    server.libraries_home = tmp_path / "libraries"
    server.routines_home = d.parent
    run_dir = d / "runs" / TS
    ctx = RunContext(routine=cfg, server=server, registry=None, run_ts=TS, run_dir=run_dir,
                     transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    ctx.grants = GrantPolicy(run_history="last")
    return ctx


def _child(parent: RunContext, n: int = 1) -> RunContext:
    """A child exactly as `childrun.build_child` wires one: its workspace under the parent's
    run dir, the parent's roots extended by the parent's dir, capabilities off."""
    sub = parent.run_dir / "sub" / str(n)
    (sub / "state").mkdir(parents=True)
    ctx = RunContext(routine=_sub_routine(parent.routine, sub, SimpleNamespace(name="m")),
                     server=parent.server, registry=None, run_ts=parent.run_ts, run_dir=sub,
                     transcript=Transcript(sub / "transcript.jsonl"),
                     budgets=parent.budgets, depth=parent.depth + 1)
    ctx.grants = GrantPolicy(is_subrun=True, run_history="none")
    return ctx


def _write(ctx, path, content="x"):
    return do_write_file({"kind": "write_file", "path": str(path), "content": content}, ctx)


@pytest.mark.parametrize("rel", [f"runs/{TS}/control.json", f"runs/{TS}/status.json",
                                 f"runs/{TS}/transcript.jsonl",
                                 f"runs/{EARLIER}/result.md", ".util_outputs/x.out"])
def test_a_child_cannot_write_what_the_engine_owns_in_the_routine(make_routine, tmp_path, rel):
    d = _routine(make_routine)
    child = _child(_top(d, tmp_path))
    before = (d / rel).read_bytes() if (d / rel).exists() else None
    obs = _write(child, d / rel, {"config_change": {"ts": "1", "fields": ["grants"]}})
    assert "engine-owned" in obs["error"]
    assert ((d / rel).read_bytes() if (d / rel).exists() else None) == before


def test_a_child_cannot_rewrite_the_routines_recipe_memory_or_finish_line(make_routine,
                                                                         tmp_path):
    d = _routine(make_routine)
    child = _child(_top(d, tmp_path))
    for target, refusal in ((d / "main.md", "own recipe"),
                            (d / "stages" / "scan.md", "own recipe"),
                            (d / "state" / "finish-line.json", "finish line"),
                            (d / ".memory" / "INDEX.md", "memory_read / memory_write")):
        do_read_file({"kind": "read_file", "path": str(target)}, child)   # grounded, still sealed
        before = target.read_text(encoding="utf-8")
        assert refusal in _write(child, target, "rewritten by a child")["error"], target
        assert target.read_text(encoding="utf-8") == before
    # the traversal form reaches the same file and the same seal
    assert "own recipe" in _write(child, "../../../../main.md")["error"]
    moved = do_move({"kind": "move", "src": str(d / "LEDGER.md"),
                     "dst": str(d / ".memory" / "ledger.md")}, child)
    assert "memory_read" in moved["error"]
    gone = do_delete({"kind": "delete", "path": str(d / "runs" / EARLIER), "recursive": True},
                     child)
    assert "engine-owned" in gone["error"]
    assert (d / "runs" / EARLIER).is_dir()


def test_a_child_still_works_in_its_workspace_and_the_parents_state(make_routine, tmp_path):
    """F185 stands: the seals close engine- and operator-owned paths, not the tree."""
    d = _routine(make_routine)
    parent = _top(d, tmp_path)
    child = _child(parent)
    assert "error" not in _write(child, "state/progress.md")
    assert "error" not in _write(child, "artifacts/report.md")
    (d / "state" / "shared.json").write_text("{}\n", encoding="utf-8")
    do_read_file({"kind": "read_file", "path": str(d / "state" / "shared.json")}, child)
    assert "error" not in _write(child, d / "state" / "shared.json", {"from": "child"})
    # a grandchild reaches its parent's workspace (the roots chain), but not what is sealed
    grand = _child(child)
    assert "error" not in _write(grand, child.routine.dir / "state" / "handoff.md")
    assert "own recipe" in _write(grand, child.routine.dir / "main.md")["error"]
    assert "engine-owned" in _write(grand, d / "runs" / TS / "status.json")["error"]


def test_a_child_reads_its_own_run_but_no_earlier_one(make_routine, tmp_path):
    d = _routine(make_routine)
    child = _child(_top(d, tmp_path))
    earlier = do_read_file({"kind": "read_file",
                            "path": str(d / "runs" / EARLIER / "transcript.jsonl")}, child)
    assert "sub-workflows run without run history" in earlier["error"]
    own = do_read_file({"kind": "read_file",
                        "path": str(d / "runs" / TS / "transcript.jsonl")}, child)
    assert "error" not in own


def test_the_seals_hold_behind_a_symlinked_home(make_routine, tmp_path):
    real = _routine(make_routine)
    link = tmp_path / "linked-home"
    link.symlink_to(real.parent)
    ctx = _top(link / real.name, tmp_path)
    assert str(ctx.routine.dir).startswith(str(link))
    assert "engine-owned" in _write(ctx, f"runs/{TS}/status.json")["error"]
    assert "memory_read" in _write(ctx, "state/../.memory/INDEX.md")["error"]
    assert "own recipe" in _write(ctx, "state/../main.md")["error"]
    assert "finish line" in _write(ctx, "state/finish-line.json")["error"]
    do_read_file({"kind": "read_file", "path": "stages/scan.md"}, ctx)
    assert ctx.phase == "scan"                 # the stage tracker sees through the link too


def test_view_image_holds_the_memory_seal(make_routine, tmp_path):
    d = _routine(make_routine)
    (d / ".memory" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    ctx = _top(d, tmp_path)
    obs = do_view_image({"kind": "view_image", "path": "state/../.memory/shot.png"}, ctx)
    assert "memory_read" in obs["files"][0]["error"]
