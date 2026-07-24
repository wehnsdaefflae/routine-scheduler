"""A child's allowed fs roots keep the parent's dir (F185): its own dir moves to sub/<n>,
so without an explicit extra root a subrun tasked on the parent's state files could not
reach them (ards sub/1, 2026-07-24: every read_file of the routine's own state/ failed
'outside the allowed roots')."""

from pathlib import Path
from types import SimpleNamespace

from rsched.engine.childrun import _sub_routine


def _parent(tmp_path):
    return SimpleNamespace(dir=tmp_path / "routine", models={"subroutine": "m"},
                           permissions=["shell"], capabilities={"utils": ["shell"]},
                           fs_read_roots=[Path("/data/in")], fs_write_roots=[],
                           deliberation="low")


def test_sub_routine_keeps_parent_dir_in_roots(tmp_path):
    parent = _parent(tmp_path)
    sub_dir = tmp_path / "routine" / "runs" / "1" / "sub" / "1"
    ref = SimpleNamespace(name="sub-model")
    r = _sub_routine(parent, sub_dir, ref)
    assert r.dir == sub_dir
    # the parent routine dir is reachable again — configured roots still apply
    assert parent.dir in r.fs_read_roots and Path("/data/in") in r.fs_read_roots
    assert parent.dir in r.fs_write_roots
    # extension happened on COPIES — the parent's own config is untouched
    assert parent.fs_read_roots == [Path("/data/in")]
    assert parent.fs_write_roots == []


def test_sub_routine_chains_roots_for_grandchildren(tmp_path):
    parent = _parent(tmp_path)
    ref = SimpleNamespace(name="sub-model")
    child = _sub_routine(parent, tmp_path / "routine" / "runs" / "1" / "sub" / "1", ref)
    grand = _sub_routine(child, tmp_path / "routine" / "runs" / "1" / "sub" / "2", ref)
    # a grandchild still reaches the top routine dir AND its direct parent's dir
    assert parent.dir in grand.fs_read_roots
    assert child.dir in grand.fs_read_roots
