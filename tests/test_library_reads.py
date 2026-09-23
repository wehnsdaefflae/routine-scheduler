"""The library's parsed documents, read once per change — and the bound that decides whether
that memo survives a busy boot.

`GET /api/domains` parsed the permission library twice PER DOMAIN (11 walks, ~275 frontmatter
parses for six domains) and `GET /api/library` re-linted every workflow, rule, permission,
template, reminder and playbook plus 110 util headers, on EVERY call — 3.7-4.3 s each on the
live instance, and both are fetched when any routine page opens.
"""
from __future__ import annotations

import json
from pathlib import Path

from rsched.readmodels import library_reads, memo


def _library(tmp_path: Path) -> Path:
    lib = tmp_path / "lib"
    for sub in ("permissions", "rules", "workflows", "templates", "reminders", "playbooks"):
        (lib / sub).mkdir(parents=True)
    (lib / "permissions" / "memory.md").write_text(
        "---\ntags: [a, b, c]\nrequires:\n  actions: [memory_read]\n---\n"
        "# permission: memory — the notebook\nbody\n", encoding="utf-8")
    util = lib / "utils" / "ping"
    util.mkdir(parents=True)
    util.joinpath("main.py").write_text(
        '"""ping — say hello\n\nusage: gu ping\ntags: a, b, c\ncalls: (none)\n'
        'net: none\nfs: none\nsecrets: (none)\n"""\n', encoding="utf-8")
    return lib


def test_the_library_is_parsed_once_per_change_not_once_per_request(tmp_path, monkeypatch):
    """The whole point: a second read of an unchanged library does no parsing at all, and the
    next write is seen immediately (the fingerprint is inode+mtime+size over the tree, and the
    library is git-committed, so every change moves a file).
    """
    lib = _library(tmp_path)
    memo.reset()
    parses = []
    import rsched.library_docs as library_docs_mod
    real = library_docs_mod.list_docs
    monkeypatch.setattr(library_docs_mod, "list_docs",
                        lambda home: (parses.append(home), real(home))[1])

    first = library_reads.docs(lib / "permissions")
    assert [d["slug"] for d in first] == ["memory"]
    for _ in range(5):
        assert library_reads.docs(lib / "permissions") is first
    assert len(parses) == 1, "five reads of an unchanged library must parse once"

    (lib / "permissions" / "sandbox.md").write_text(
        "---\ntags: [a, b, c]\n---\n# permission: sandbox — doc\nbody\n", encoding="utf-8")
    assert [d["slug"] for d in library_reads.docs(lib / "permissions")] == ["memory", "sandbox"]
    assert len(parses) == 2


def test_the_whole_library_lint_invalidates_on_any_kind_it_walks(tmp_path):
    """`lint_all` reads six directories; the fingerprint has to name all six, or a bad
    template lands on a page still showing the pre-edit verdict.
    """
    lib = _library(tmp_path)
    memo.reset()
    assert "reminders/rem-x.json" not in library_reads.lint(lib)
    (lib / "reminders" / "rem-x.json").write_text(
        json.dumps({"id": "rem-x", "regex": "^util:", "description": ""}), encoding="utf-8")
    problems = library_reads.lint(lib)
    assert problems["reminders/rem-x.json"], "a reminder added after the first read must be seen"
    assert library_reads.utils(lib) == library_reads.utils(lib)
    assert [u["name"] for u in library_reads.utils(lib)] == ["ping"]


def test_the_memo_bound_evicts_the_least_recently_used(tmp_path, monkeypatch):
    """A Python dict does not reorder on re-assignment, so eviction by insert order fixes an
    entry's position at its FIRST insert. The expensive keys are the ones fetched EARLY — the
    library lint and the util catalog land on the first routine-page open of a boot — while
    the cheap per-dir keys (one per routine, per conversation, per run) arrive later and in
    bulk. Insert-order eviction would therefore throw out a 4-second recompute to keep a
    dozen stats.
    """
    memo.reset()
    monkeypatch.setattr(memo, "_MAX_ENTRIES", 4)
    src = tmp_path / "f"
    src.write_text("x", encoding="utf-8")
    computed: list[str] = []

    def read(key: str) -> str:
        return memo.memoized(key, [src], lambda: (computed.append(key), key)[1])

    read("hot")
    for n in range(3):
        read(f"cold-{n}")
    read("hot")                       # keeps it young
    for n in range(3, 6):
        read(f"cold-{n}")             # pushes the bound past four entries
    computed.clear()
    read("hot")
    assert computed == [], "the recently used entry must survive a burst of cheap ones"
