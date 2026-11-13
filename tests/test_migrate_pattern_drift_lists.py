"""The pattern migration's LIST edit, on its own — what it may extend and what it refuses.

Beside `tests/test_migrate_pattern_drift.py` (which owns the convergence itself): this file
owns the one list the migration touches, because the operator's answer makes the list the
dangerous half — *"leaving any list a pattern legitimately narrowed alone"* — and because a
migration whose other half is *"recording what it changed"* may never record a change it did
not make.
"""

from __future__ import annotations

from pathlib import Path

from helpers import server_config
from rsched.migrate_pattern_drift import ADDED_RULE, _add_rule, run_migration

BLOCK = """\
settings:
  rules:
  - ask-policy
  - change-scope
  - work-order
  keep_runs: 30
"""


def test_the_entry_lands_in_the_place_the_seed_keeps_it():
    """The seed keeps `rules:` alphabetical, so an entry appended at the end would make every
    converted file differ from its twin — which the byte-for-byte test would catch, and this
    one explains.
    """
    out = _add_rule(BLOCK, ADDED_RULE)

    assert out is not None
    assert out == BLOCK.replace("  - work-order\n",
                                f"  - {ADDED_RULE}\n  - work-order\n")


def test_an_entry_sorting_last_still_lands_inside_its_own_list():
    """The branch with no later item to insert before: it must go after the list's last entry
    and never after `keep_runs`, which is a different key.
    """
    out = _add_rule(BLOCK, "zz-last")

    assert out is not None
    assert out == BLOCK.replace("  - work-order\n", "  - work-order\n  - zz-last\n")


def test_an_inline_list_is_refused_rather_than_silently_left_unchanged():
    """A `rules: [a, b]` is a list this text edit cannot extend. Returning the body unchanged
    would read exactly like a successful insert at the call site, and the record would claim a
    change nobody made.
    """
    assert _add_rule("settings:\n  rules: [ask-policy, work-order]\n", ADDED_RULE) is None
    assert _add_rule("settings:\n  keep_runs: 30\n", ADDED_RULE) is None
    assert _add_rule("settings:\n  rules:\n  keep_runs: 30\n", ADDED_RULE) is None


def test_a_pattern_whose_rules_list_is_inline_is_recorded_as_left(tmp_path, monkeypatch):
    """End to end: the refusal above reaches the RECORD as a left entry, and the two scalar
    keys the answer names still converge — the file is not abandoned because one key could not
    be carried.
    """
    from rsched import migrate_pattern_drift

    seed = """\
title: T
summary: s
workflow: general-task
settings:
  rules:
  - ask-policy
  - outcome-first
  budgets:
    max_total_turns: 0
  ladder_turn_interval: 50
"""
    live = """\
title: T
summary: s
workflow: general-task
settings:
  rules: [ask-policy]
  budgets:
    max_total_turns: -1
"""
    home, library, repo = tmp_path / "routines", tmp_path / "library", tmp_path / "repo"
    (library / "patterns").mkdir(parents=True)
    (repo / "library-seed" / "patterns").mkdir(parents=True)
    (home / ".control").mkdir(parents=True)
    (repo / "library-seed" / "patterns" / "t.yaml").write_text(seed, encoding="utf-8")
    (library / "patterns" / "t.yaml").write_text(live, encoding="utf-8")
    monkeypatch.setattr(migrate_pattern_drift, "repo_root", lambda: repo)

    record = run_migration(server_config(routines_home=home, libraries_home=library))

    text = (Path(library) / "patterns" / "t.yaml").read_text(encoding="utf-8")
    assert "max_total_turns: 0" in text and "ladder_turn_interval: 50" in text
    assert "rules: [ask-policy]" in text, "the inline list must be untouched"
    assert record["converted"]["patterns/t.yaml"] == [
        "budgets.max_total_turns: -1 -> 0", "ladder_turn_interval"]
    assert "not a block list" in record["left"]["patterns/t.yaml"]
