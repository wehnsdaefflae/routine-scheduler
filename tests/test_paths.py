"""`paths` is the cross-process file seam — the contracts here are about HOW it reads and
writes, not about any one caller."""

from __future__ import annotations

import pytest
import yaml

from rsched import paths


def test_read_yaml_parses_with_libyaml(tmp_path, monkeypatch):
    """The C parser is part of the contract, not an optimisation to drop quietly.

    Measured on the deployment over the 35 live routine.yaml files (87 KB): 583 ms with the
    pure-Python SafeLoader against 59 ms with CSafeLoader. Every config read-modify-write on
    the request path pays it, on a daemon whose slow-request log is already GIL-bound.
    """
    seen: dict[str, object] = {}
    real = yaml.load

    def spy(stream, Loader):  # noqa: N803 — PyYAML's own keyword name
        seen["loader"] = Loader
        return real(stream, Loader=Loader)

    monkeypatch.setattr(yaml, "load", spy)
    source = tmp_path / "routine.yaml"
    source.write_text("slug: uir\nbudgets:\n  max_turns: 12\n", encoding="utf-8")

    assert paths.read_yaml(source) == {"slug": "uir", "budgets": {"max_turns": 12}}
    assert seen["loader"] is yaml.CSafeLoader


def test_read_yaml_reads_an_empty_document_as_the_default(tmp_path):
    (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
    assert paths.read_yaml(tmp_path / "empty.yaml", {}) == {}


def test_read_yaml_lets_a_broken_file_raise(tmp_path):
    """The asymmetry with read_json: a default handed back for an unparseable file would
    rewrite the operator's hand-broken config FROM that default."""
    (tmp_path / "broken.yaml").write_text("a: [1, 2\nb: }\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        paths.read_yaml(tmp_path / "broken.yaml", {})


def test_append_jsonl_round_trips_through_jsonl_records(tmp_path):
    """The stream pair: one writer, one reader. A value holding U+2028 stays on its line —
    `str.splitlines` would cut it there, so the reader splits on the newline byte alone."""
    path = tmp_path / "deep" / "stream.jsonl"
    rows = [{"n": 1, "text": "a b\u0085c"}, {"n": 2, "text": "plain"}]
    paths.append_jsonl(path, *rows)
    paths.append_jsonl(path)                          # nothing to append writes nothing
    assert paths.read_jsonl(path) == rows
    assert path.read_text(encoding="utf-8").count("\n") == 2


def test_jsonl_records_keeps_every_good_row_around_a_bad_one(tmp_path):
    """Hand-trimmed, restored or torn by a writer that died mid-line: one bad row never hides
    the rest, and a missing file reads as empty."""
    path = tmp_path / "s.jsonl"
    path.write_bytes(b'{"a": 1}\n\n[1, 2]\n"text"\nnot json\n{"b": 2}\n{"c": "\xe2\x80')
    assert paths.read_jsonl(path) == [{"a": 1}, {"b": 2}]
    assert paths.read_jsonl(tmp_path / "missing.jsonl") == []


def test_append_jsonl_hands_the_kernel_one_write(tmp_path, monkeypatch):
    """Several processes append to the same streams; a line reaches the file in ONE write(2),
    the unit an O_APPEND file takes whole, never in buffer-sized pieces another line can land
    between."""
    import os

    writes: list[int] = []
    real = os.write

    def spy(fd, data):
        writes.append(len(data))
        return real(fd, data)

    monkeypatch.setattr(os, "write", spy)
    paths.append_jsonl(tmp_path / "s.jsonl", {"big": "x" * 70_000}, {"next": 1})
    assert len(writes) == 1


# ---- resolve_rel: where a RELATIVE path lands ------------------------------------------------
#
# `extra_roots` used to be consulted only as a permission check on an ABSOLUTE path: a relative
# one was joined to `base` and nothing else, so it could never land in an allowed root and died
# at open() with ENOENT — while the action schema promised "relative to your working directory
# (or an allowed root)". `prefer_existing` keeps that promise in three passes, `base` first in
# each: the first root holding the path, then the first root holding its PARENT (so a creating
# write is symmetric), else `base / rel`. The action layer passes it; every other caller keeps
# today's behaviour by default. The action-level contract is in test_relative_root_paths.py.


def _two_roots(tmp_path):
    base, root = tmp_path / "base", tmp_path / "root"
    (base / "state").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    return base, root


def test_resolve_rel_finds_a_relative_path_in_an_extra_root(tmp_path):
    base, root = _two_roots(tmp_path)
    (root / "docs" / "designs.md").write_text("x", encoding="utf-8")
    got = paths.resolve_rel(base, "docs/designs.md", [root], prefer_existing=True)
    assert got == (root / "docs" / "designs.md").resolve()


def test_resolve_rel_prefers_base_over_an_extra_root(tmp_path):
    """The same relative path in both: `base` wins, so nothing that works today moves."""
    base, root = _two_roots(tmp_path)
    (base / "docs").mkdir()
    for d in (base, root):
        (d / "docs" / "designs.md").write_text("x", encoding="utf-8")
    got = paths.resolve_rel(base, "docs/designs.md", [root], prefer_existing=True)
    assert got == (base / "docs" / "designs.md").resolve()


def test_resolve_rel_places_a_new_file_where_its_directory_already_is(tmp_path):
    """Creation: the file exists nowhere, so the PARENT decides — `docs/` is only in the extra
    root, so that is where a new sibling belongs."""
    base, root = _two_roots(tmp_path)
    got = paths.resolve_rel(base, "docs/new.md", [root], prefer_existing=True)
    assert got == (root / "docs" / "new.md").resolve()


def test_resolve_rel_prefers_bases_own_directory_for_a_new_file(tmp_path):
    base, root = _two_roots(tmp_path)
    (base / "docs").mkdir()
    got = paths.resolve_rel(base, "docs/new.md", [root], prefer_existing=True)
    assert got == (base / "docs" / "new.md").resolve()


def test_resolve_rel_falls_back_to_the_base_join(tmp_path):
    """Pass 3 — neither the path nor its parent exists anywhere: today's behaviour exactly."""
    base, root = _two_roots(tmp_path)
    got = paths.resolve_rel(base, "newdir/f.txt", [root], prefer_existing=True)
    assert got == (base / "newdir" / "f.txt").resolve()


def test_resolve_rel_still_refuses_a_path_outside_every_root(tmp_path):
    base, root = _two_roots(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    with pytest.raises(PermissionError, match="outside the allowed roots"):
        paths.resolve_rel(base, "../elsewhere/x.md", [root], prefer_existing=True)


def test_resolve_rel_never_falls_back_to_an_ancestor_of_base(tmp_path):
    """A root CONTAINING `base` is an ancestor, not an alternative — `base` is the more
    specific place for that name.

    This is the job-workspace contract (`run_context.work_dir`): while a job is open the run
    works in `jobs/<id>/`, a fresh directory with no `state/`, while the ROUTINE's own dir —
    which `resolve_action_path` always appends as a root — always has one. Without this,
    `write_file state/progress.md` from an open job landed in the routine's state/ instead of
    the workspace's (caught by tests/test_jobops.py).
    """
    routine = tmp_path / "routines" / "fau"
    (routine / "state").mkdir(parents=True)
    workspace = routine / "jobs" / "alpha"
    workspace.mkdir(parents=True)
    got = paths.resolve_rel(workspace, "state/progress.md", [routine], prefer_existing=True)
    assert got == (workspace / "state" / "progress.md").resolve()


def test_resolve_rel_leaves_every_other_caller_on_the_base_join(tmp_path):
    """Default off: the web file APIs, pending edits, boot and authoring resolve a relative
    path under `base` alone, whatever an extra root happens to hold."""
    base, root = _two_roots(tmp_path)
    (root / "docs" / "designs.md").write_text("x", encoding="utf-8")
    assert paths.resolve_rel(base, "docs/designs.md", [root]) == \
        (base / "docs" / "designs.md").resolve()


def test_resolve_rel_treats_an_absolute_path_exactly_as_before(tmp_path):
    """An absolute path keeps the permission-check behaviour under either setting."""
    base, root = _two_roots(tmp_path)
    inside = root / "docs" / "designs.md"
    inside.write_text("x", encoding="utf-8")
    outside = tmp_path / "elsewhere" / "x.md"
    for kw in ({}, {"prefer_existing": True}):
        assert paths.resolve_rel(base, str(inside), [root], **kw) == inside.resolve()
        with pytest.raises(PermissionError):
            paths.resolve_rel(base, str(outside), [root], **kw)
