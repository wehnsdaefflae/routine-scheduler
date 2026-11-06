"""Where a RELATIVE action path lands — the contract the action schema states and the
resolver did not keep.

A run whose working directory was its routine dir, with the source repo among its granted
read roots, ran `read_file path=docs/designs.md` and got `[Errno 2] No such file or
directory: '<routine dir>/docs/designs.md'`; the same read by absolute path worked.
`paths.resolve_rel` consulted the extra roots only as a permission check on an ABSOLUTE
path, so a relative one could never land in a granted root — while the schema prose mounted
in every run's prompt promised "path relative to your working directory (or an allowed
root)".

The rule pinned here (`paths.resolve_rel(..., prefer_existing=True)`, which
`run_context.resolve_action_path` passes for every file action — read, write, edit, delete,
move, mkdir, view_image): the first root holding the path, else the first root holding its
PARENT directory (what makes a CREATING write symmetric), else `base / rel`. `base` comes
first in every pass, so nothing that already worked moves.

The seals are unaffected and stay tested where they live: resolution still only ever returns
a path inside a root, so `_write_gate` sees what it always saw. The one gate whose REACH this
widens is the overwrite gate — a file outside the working dir can now be named relatively —
so that it still fires is pinned below.
"""

from __future__ import annotations

from helpers import action_ctx
from rsched.engine import grounding
from rsched.engine.fileops import do_read_file, do_write_file
from rsched.engine.fsops import do_mkdir
from rsched.engine.run_context import RunContext


def _ctx_with_root(make_routine, tmp_path) -> tuple[RunContext, object]:
    """A run working in its routine dir with `tmp_path/repo` granted as a write root (every
    write root is readable too, read_roots) and `repo/docs` the only `docs/` on disk."""
    ctx = action_ctx(make_routine(), tmp_path)
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    ctx.routine.fs_write_roots = [repo]
    return ctx, repo


# ---- the read side: the defect as it was reported -------------------------------------------


def test_read_file_reaches_a_granted_root_by_a_relative_path(make_routine, tmp_path):
    """The reported defect, at the action level."""
    ctx, repo = _ctx_with_root(make_routine, tmp_path)
    (repo / "docs" / "designs.md").write_text("the designs\n", encoding="utf-8")

    obs = do_read_file({"kind": "read_file", "path": "docs/designs.md"}, ctx)

    assert "error" not in obs, obs.get("error")
    assert obs["content"] == "the designs"


def test_the_working_directory_wins_over_a_granted_root(make_routine, tmp_path):
    """Precedence, so nothing that works today moves: the same relative path exists in both
    and the working directory's copy is the one that is read."""
    ctx, repo = _ctx_with_root(make_routine, tmp_path)
    (repo / "docs" / "designs.md").write_text("the repo copy\n", encoding="utf-8")
    (ctx.routine.dir / "docs").mkdir()
    (ctx.routine.dir / "docs" / "designs.md").write_text("my own copy\n", encoding="utf-8")

    assert do_read_file({"path": "docs/designs.md"}, ctx)["content"] == "my own copy"


# ---- the write side: creation is symmetric --------------------------------------------------


def test_a_creating_write_lands_in_the_root_that_holds_the_directory(make_routine, tmp_path):
    """A brand-new file exists nowhere, so the PARENT decides: `docs/` is in the granted root
    and nowhere else, so that is where the new file's siblings are."""
    ctx, repo = _ctx_with_root(make_routine, tmp_path)

    obs = do_write_file({"kind": "write_file", "path": "docs/new.md", "content": "fresh\n"}, ctx)

    assert "error" not in obs, obs.get("error")
    assert (repo / "docs" / "new.md").read_text(encoding="utf-8") == "fresh\n"
    assert not (ctx.routine.dir / "docs").exists()


def test_a_creating_write_prefers_the_working_directorys_own_directory(make_routine, tmp_path):
    """Creation precedence: when the working directory holds that directory too, it wins —
    which is every `state/x`, `artifacts/y` and bare filename a run writes today."""
    ctx, repo = _ctx_with_root(make_routine, tmp_path)
    (ctx.routine.dir / "docs").mkdir()

    do_write_file({"path": "docs/new.md", "content": "fresh\n"}, ctx)

    assert (ctx.routine.dir / "docs" / "new.md").read_text(encoding="utf-8") == "fresh\n"
    assert not (repo / "docs" / "new.md").exists()


def test_a_write_whose_parent_exists_nowhere_still_lands_under_the_working_dir(make_routine,
                                                                              tmp_path):
    """Pass 3 is today's behaviour exactly: `write_file path=newdir/f.txt` still creates the
    directory under the working directory, never in a granted root."""
    ctx, repo = _ctx_with_root(make_routine, tmp_path)

    do_write_file({"path": "newdir/f.txt", "content": "x"}, ctx)

    assert (ctx.routine.dir / "newdir" / "f.txt").is_file()
    assert not (repo / "newdir").exists()


def test_mkdir_parents_is_unchanged_when_the_parent_exists_nowhere(make_routine, tmp_path):
    """`mkdir parents: true` legitimately names a path whose parent exists nowhere — the
    parent test must not divert it into a granted root."""
    ctx, repo = _ctx_with_root(make_routine, tmp_path)

    obs = do_mkdir({"kind": "mkdir", "path": "newtree/sub", "parents": True}, ctx)

    assert obs["created"] is True
    assert (ctx.routine.dir / "newtree" / "sub").is_dir()
    assert not (repo / "newtree").exists()


# ---- the gate whose reach this widens -------------------------------------------------------


def test_the_overwrite_gate_still_fires_on_a_file_reached_relatively(make_routine, tmp_path):
    """write_file refuses to replace a file outside the working dir this run has not read.
    That file can now be NAMED relatively, so the refusal has to fire on the resolved path —
    and reading it first grounds the overwrite, exactly as for the absolute form."""
    ctx, repo = _ctx_with_root(make_routine, tmp_path)
    target = repo / "docs" / "designs.md"
    target.write_text("the real designs\n", encoding="utf-8")

    refused = do_write_file({"path": "docs/designs.md", "content": "mine now\n"}, ctx)

    assert refused["error"] == grounding.OVERWRITE_REFUSAL
    assert target.read_text(encoding="utf-8") == "the real designs\n"

    assert do_read_file({"path": "docs/designs.md"}, ctx)["content"] == "the real designs"
    assert "error" not in do_write_file({"path": "docs/designs.md",
                                         "content": "mine now\n"}, ctx)
    assert target.read_text(encoding="utf-8") == "mine now\n"
