"""The one-shot migration that carries a seed rule's `assists:` block to a LIVE library.

Why it has to exist at all, measured on this instance 2026-10-30: `evidence-discipline` has
declared two assists in `library-seed/` since 0.471.0 (`child-fell-short`, `thin-child-brief`),
and the live copy has NONE. `bootstrap.sync_seed_library_docs` is add-only by design — it never
overwrites a live doc, because these files are user-editable and an overwrite would discard an
operator's edit — so both hook points steps 6 and 7 wired have been firing against zero
candidates ever since. A point that fires nothing is indistinguishable from a dead one.

`migrate_converse_goals` solves the same problem for a PATTERN by replacing the file whole,
guarded by the hash of the version it supersedes. That cannot work here: the live rule's PROSE
has been revised since it was seeded (the rules library is a multi-writer directory), so a whole
replacement would silently revert a revision that reached every holder. So this migration edits
the ONE thing it owns — it inserts the seed's `assists:` block into the live frontmatter and
leaves every other byte alone — and refuses when the live rule already declares one.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from rsched.migrate_rule_assists import RECORD, run_migration

LIVE_EDITED = """\
---
effect:
  with: claims only what an observation in this run showed
  without: may report intentions as facts
  when: always
tags: [reporting, verification]
---
# rule: evidence-discipline — every claim traced to an observation in this run

A sentence the operator added after this rule was seeded, which no migration may discard.
"""


def _server(tmp_path, monkeypatch):
    lib = tmp_path / "libraries"
    (lib / "rules").mkdir(parents=True)
    (tmp_path / "routines" / ".control").mkdir(parents=True)
    monkeypatch.setattr("rsched.libgit.commit",
                        lambda *a, **k: type("R", (), {"failed": False,
                                                       "describe": lambda s: "ok"})())
    return type("S", (), {"routines_home": tmp_path / "routines", "libraries_home": lib})()


def _seed_block() -> list[dict]:
    from rsched.bootstrap import repo_root

    seed = repo_root() / "library-seed" / "rules" / "evidence-discipline.md"
    return yaml.safe_load(seed.read_text(encoding="utf-8").split("---")[1])["assists"]


def test_the_block_is_inserted_and_the_live_prose_is_untouched(tmp_path, monkeypatch):
    """The whole point: the live revision survives and the assists arrive."""
    server = _server(tmp_path, monkeypatch)
    live = server.libraries_home / "rules" / "evidence-discipline.md"
    live.write_text(LIVE_EDITED, encoding="utf-8")

    record = run_migration(server)

    text = live.read_text(encoding="utf-8")
    assert "A sentence the operator added" in text, "the live revision was discarded"
    assert record.get("carried") == ["rules/evidence-discipline.md"]
    meta = yaml.safe_load(text.split("---")[1])
    assert meta["assists"] == _seed_block()
    assert meta["effect"]["when"] == "always", "the live frontmatter was rewritten"
    assert [a["predicate"] for a in meta["assists"]] == [
        "child-fell-short", "children-killed-at-end", "thin-child-brief"]


def test_the_result_is_a_rule_the_live_linter_accepts(tmp_path, monkeypatch):
    """An inserted block that the rule linter rejects would be worse than none: the rule layer
    drops it and nothing says why. So the migration's output is checked by the real linter.
    """
    from rsched.assists import normalize_assists

    server = _server(tmp_path, monkeypatch)
    live = server.libraries_home / "rules" / "evidence-discipline.md"
    live.write_text(LIVE_EDITED, encoding="utf-8")
    run_migration(server)

    meta = yaml.safe_load(live.read_text(encoding="utf-8").split("---")[1])
    got, problems = normalize_assists(meta["assists"], rule="evidence-discipline")
    assert problems == []
    assert {a.moment for a in got} == {"run-end", "child-exit", "spawn"}


def test_a_live_rule_that_already_declares_assists_is_left_alone(tmp_path, monkeypatch):
    """Its declaration outranks the seed's, exactly as `sync_seed_library_docs` treats a live
    doc: the operator's library is the authority on what his rules say.
    """
    server = _server(tmp_path, monkeypatch)
    live = server.libraries_home / "rules" / "evidence-discipline.md"
    live.write_text(LIVE_EDITED.replace("tags:", "assists: []\ntags:"), encoding="utf-8")
    before = live.read_text(encoding="utf-8")

    record = run_migration(server)

    assert live.read_text(encoding="utf-8") == before
    assert record["carried"] == []
    assert "already declares" in str(record["left"])


def test_a_missing_live_rule_is_recorded_and_never_created(tmp_path, monkeypatch):
    """A rule the operator DELETED stays deleted — the add-only sync has the same rule, and
    resurrecting a deleted doc is the defect `libgit.path_was_deleted` exists to prevent.
    """
    server = _server(tmp_path, monkeypatch)
    record = run_migration(server)
    assert not (server.libraries_home / "rules" / "evidence-discipline.md").exists()
    assert record["carried"] == []


def test_it_runs_once_and_then_does_nothing(tmp_path, monkeypatch):
    server = _server(tmp_path, monkeypatch)
    live = server.libraries_home / "rules" / "evidence-discipline.md"
    live.write_text(LIVE_EDITED, encoding="utf-8")
    assert run_migration(server)["carried"] == ["rules/evidence-discipline.md"]
    assert (Path(server.routines_home) / RECORD).exists()

    live.write_text(LIVE_EDITED, encoding="utf-8")      # undone by hand
    assert run_migration(server) == {}
    assert "assists:" not in live.read_text(encoding="utf-8")
