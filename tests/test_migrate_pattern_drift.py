"""The installed patterns converge on the seed's three fixed keys — the add-only sync cannot.

D283, the operator's answer verbatim (2026-11-12): *"A one-shot migration converts all fourteen
installed patterns (max_total_turns -1 -> 0, the three ladder keys, outcome-first added),
leaving any list a pattern legitimately narrowed alone and recording what it changed"*.

F883 corrected the SEED patterns, and `bootstrap.sync_seed_library_docs` is add-only by design:
it installs a doc the live library LACKS and never overwrites one it has, because these files
are operator-editable. So the corrected seed reaches a fresh instance and never this one — and
every installed pattern still reads `max_total_turns: -1`, the exact value
`migrate_total_turns.py` exists to remove, so each new routine created from a pattern
re-introduced it past that migration's own marker.

What is pinned here is the NARROWNESS, because that is what makes a text migration safe over
operator-editable library files:

* it converts only the three keys the answer names, on a pattern whose SEED TWIN says what the
  shipped value is;
* it leaves every other byte alone — a converted file is its seed, byte for byte;
* a list that differs from the seed in any other way is NARROWED and is left alone with its
  reason, so the migration can never widen a deliberate choice back out;
* a value that is not the drift (an operator's own `max_total_turns`, his own `ladder:`) is not
  this migration's business;
* it runs ONCE — the record is the guard, as with every migration in this family — and the
  record names what it changed, per file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from helpers import server_config
from rsched.migrate_pattern_drift import RECORD, run_migration

SEED = """\
title: Code maintainer
summary: Sweeps a codebase, fixes what it finds, verifies and pushes, and reports.
workflow: maintain-a-codebase
when: The job is keeping someone's repositories or website healthy.
asks:
- field: fs_write_roots
  question: Which repositories may it change?
settings:
  improve: true
  permissions:
  - util-authoring
  rules:
  - ask-policy
  - change-scope
  - outcome-first
  - work-order
  budgets:
    ask_timeout_min: 480
    max_total_turns: 0
    max_turns: 200
  keep_runs: 30
  deliberation: standard
  ladder:
    enabled: false
    rung_height: 3
  ladder_turn_interval: 50
  oversight_turns: null
  tags: []
"""

#: The same document as the live library carries it: the three drifted keys and nothing else.
LIVE = SEED.replace("  - outcome-first\n", "") \
           .replace("max_total_turns: 0", "max_total_turns: -1") \
           .replace("""  ladder:
    enabled: false
    rung_height: 3
  ladder_turn_interval: 50
  oversight_turns: null
""", "")


def _setup(tmp_path: Path, monkeypatch, *, live: str | None = LIVE, seed: str = SEED,
           slug: str = "code-maintainer"):
    """A server whose library holds the live pattern and whose repo ships the seed twin."""
    from rsched import migrate_pattern_drift

    home, library, repo = tmp_path / "routines", tmp_path / "library", tmp_path / "repo"
    (library / "patterns").mkdir(parents=True)
    (repo / "library-seed" / "patterns").mkdir(parents=True)
    (home / ".control").mkdir(parents=True)
    (repo / "library-seed" / "patterns" / f"{slug}.yaml").write_text(seed, encoding="utf-8")
    if live is not None:
        (library / "patterns" / f"{slug}.yaml").write_text(live, encoding="utf-8")
    monkeypatch.setattr(migrate_pattern_drift, "repo_root", lambda: repo)
    return server_config(routines_home=home, libraries_home=library)


def _live(server, slug: str = "code-maintainer") -> Path:
    return Path(server.libraries_home) / "patterns" / f"{slug}.yaml"


def test_the_three_drifted_keys_converge_and_nothing_else_moves(tmp_path, monkeypatch):
    """The whole item, in one assertion: a drifted pattern becomes its seed BYTE FOR BYTE.

    Anything else the migration touched — a reflowed list, a re-sorted key, a dropped comment —
    shows up here, which is why the assertion is the whole file rather than the three keys.
    """
    server = _setup(tmp_path, monkeypatch)

    record = run_migration(server)

    assert _live(server).read_text(encoding="utf-8") == SEED
    assert record["converted"]["patterns/code-maintainer.yaml"] == [
        "budgets.max_total_turns: -1 -> 0", "ladder", "ladder_turn_interval",
        "oversight_turns", "rules: + outcome-first"]
    assert not record["left"] and not record["failed"], record


def test_a_converted_pattern_is_still_one_the_library_can_use(tmp_path, monkeypatch):
    """A migration that leaves a pattern the store cannot read would break creation itself."""
    from rsched.patterns import store

    server = _setup(tmp_path, monkeypatch)
    run_migration(server)

    doc = store.read(server.libraries_home, "code-maintainer")
    assert doc is not None and doc["problems"] == []
    assert doc["settings"]["budgets"]["max_total_turns"] == 0
    assert "outcome-first" in doc["settings"]["rules"]
    assert doc["settings"]["ladder"] == {"enabled": False, "rung_height": 3}
    assert doc["settings"]["ladder_turn_interval"] == 50


def test_a_list_the_pattern_narrowed_is_left_alone(tmp_path, monkeypatch):
    """The operator's own clause. A `rules:` list that is not simply the seed's minus the one
    entry being added has been narrowed (or otherwise edited) by hand, and widening it is the
    silent discard of an operator's edit the add-only rule exists to prevent. The two SCALAR
    keys he named still converge: the clause protects the list, not the drift.
    """
    narrowed = LIVE.replace("  - change-scope\n", "")
    server = _setup(tmp_path, monkeypatch, live=narrowed)

    record = run_migration(server)

    text = _live(server).read_text(encoding="utf-8")
    assert "outcome-first" not in text
    assert "max_total_turns: 0" in text and "ladder_turn_interval: 50" in text
    assert record["converted"]["patterns/code-maintainer.yaml"] == [
        "budgets.max_total_turns: -1 -> 0", "ladder", "ladder_turn_interval", "oversight_turns"]
    assert "narrowed" in record["left"]["patterns/code-maintainer.yaml"]


def test_a_value_that_is_not_the_drift_is_not_this_migrations_business(tmp_path, monkeypatch):
    """An operator's own cap and his own ladder block stand: the migration converts the drift
    the seed twin names, never whatever it finds.
    """
    his = LIVE.replace("max_total_turns: -1", "max_total_turns: 900").replace(
        "  deliberation: standard\n",
        "  deliberation: standard\n  ladder:\n    enabled: true\n    rung_height: 5\n")
    server = _setup(tmp_path, monkeypatch, live=his)

    record = run_migration(server)

    text = _live(server).read_text(encoding="utf-8")
    assert "max_total_turns: 900" in text
    assert "enabled: true" in text and "rung_height: 5" in text
    left = record["left"]["patterns/code-maintainer.yaml"]
    assert "max_total_turns" in left and "ladder" in left
    # the two keys that ARE absent still arrive — add-only, per key
    assert record["converted"]["patterns/code-maintainer.yaml"] == [
        "ladder_turn_interval", "oversight_turns", "rules: + outcome-first"]


def test_a_pattern_with_no_seed_twin_is_left_alone(tmp_path, monkeypatch):
    """"Save as new pattern" on a routine page writes a pattern from that routine's own
    settings — a `-1` there is his deliberate uncapping, carried from a routine that holds it.
    No seed twin, no shipped value to converge on, nothing this migration may touch.
    """
    server = _setup(tmp_path, monkeypatch)
    his_own = Path(server.libraries_home) / "patterns" / "his-own.yaml"
    his_own.write_text(LIVE, encoding="utf-8")

    record = run_migration(server)

    assert his_own.read_text(encoding="utf-8") == LIVE
    assert "no seed twin" in record["left"]["patterns/his-own.yaml"]


def test_a_pattern_missing_from_the_live_library_stays_missing(tmp_path, monkeypatch):
    """He may have deleted it. Resurrecting a deleted library doc is the one thing the add-only
    sync already refuses, and a migration must not route around that.
    """
    server = _setup(tmp_path, monkeypatch, live=None)

    record = run_migration(server)

    assert not _live(server).exists()
    assert "deleted patterns stay deleted" in record["left"]["patterns/code-maintainer.yaml"]


def test_it_runs_once(tmp_path, monkeypatch):
    server = _setup(tmp_path, monkeypatch)

    first = run_migration(server)
    assert first["converted"]
    assert (Path(server.routines_home) / RECORD).exists()

    _live(server).write_text(LIVE, encoding="utf-8")      # a file that somehow regressed
    assert run_migration(server) == {}
    assert _live(server).read_text(encoding="utf-8") == LIVE


def test_an_unreadable_record_is_never_licence_to_run_again(tmp_path, monkeypatch):
    """A corrupt guard fails CLOSED: a migration that re-runs over operator-edited files
    because it could not read its own record is worse than one that never runs.
    """
    server = _setup(tmp_path, monkeypatch)
    broken = Path(server.routines_home) / RECORD
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{not json", encoding="utf-8")

    assert run_migration(server) == {}
    assert _live(server).read_text(encoding="utf-8") == LIVE


def test_the_record_is_readable_json_naming_what_it_changed(tmp_path, monkeypatch):
    """"Recording what it changed" is half the operator's answer: a migration nobody can read
    the outcome of is a migration nobody can trust ran.
    """
    server = _setup(tmp_path, monkeypatch)
    run_migration(server)

    doc = json.loads((Path(server.routines_home) / RECORD).read_text(encoding="utf-8"))
    assert doc["started"]
    assert doc["converted"]["patterns/code-maintainer.yaml"][0] == \
        "budgets.max_total_turns: -1 -> 0"


def test_the_four_attribute_boot_stand_in_cannot_kill_the_boot(tmp_path, monkeypatch):
    """The trap this family has paid for twice. A boot migration is handed whatever
    `load_server_config` returns, and the fresh-container boot test
    (`tests/test_bootstrap.py::test_a_fresh_containers_first_boot_seeds_utils_and_docs`) hands
    it a SimpleNamespace — so a hard attribute access on a home that stand-in does not carry
    kills the WHOLE DAEMON BOOT from inside a migration that had nothing to migrate.
    """
    from rsched import migrate_pattern_drift

    lib = tmp_path / "lib"
    (lib / "patterns").mkdir(parents=True)
    (tmp_path / "routines").mkdir()
    monkeypatch.setattr(migrate_pattern_drift, "repo_root", lambda: tmp_path / "norepo")
    stand_in = SimpleNamespace(libraries_home=lib, libraries_remote="", bind="127.0.0.1",
                               port=8321, routines_home=tmp_path / "routines",
                               permissions_home=lib / "permissions")

    assert run_migration(stand_in) == {}        # no seed tree: nothing to converge on


def _drifted(seed_text: str) -> str:
    """A live copy as this instance actually carries it: the seed MINUS what F883 added.

    The drift was measured over all fourteen installed patterns on 2026-11-12 and re-measured
    before this was written — every one reads `max_total_turns: -1`, carries none of the three
    ladder keys and omits `outcome-first`, and nothing else differs (each live file has exactly
    one list entry fewer than its twin: 183 against 197). Applying that drift to the SHIPPED
    documents is what makes this fixture the production input rather than a hand-written
    approximation of it.
    """
    text = seed_text.replace("max_total_turns: 0", "max_total_turns: -1")
    text = text.replace("  - outcome-first\n", "")
    return re.sub(r"^  ladder:\n(?:    [^\n]*\n)+  ladder_turn_interval: [^\n]*\n"
                  r"  oversight_turns: [^\n]*\n", "", text, flags=re.MULTILINE)


def test_all_fourteen_shipped_patterns_convert(tmp_path, monkeypatch):
    """The operator's answer names a COUNT — "all fourteen installed patterns" — so the real
    shipped documents are the fixture, not one hand-written stand-in.

    Each is converted back to its twin byte for byte, which is the only assertion that can see
    a reflowed list, a re-sorted key or a dropped blank line.
    """
    from rsched import migrate_pattern_drift
    from rsched.paths import repo_root

    seed_root = repo_root() / "library-seed" / "patterns"
    shipped = sorted(seed_root.glob("*.yaml"))
    assert len(shipped) == 14, [p.name for p in shipped]

    library, home = tmp_path / "library", tmp_path / "routines"
    (library / "patterns").mkdir(parents=True)
    (home / ".control").mkdir(parents=True)
    for seed in shipped:
        seed_text = seed.read_text(encoding="utf-8")
        drifted = _drifted(seed_text)
        assert drifted != seed_text, f"{seed.name}: the drift fixture changed nothing"
        (library / "patterns" / seed.name).write_text(drifted, encoding="utf-8")
    monkeypatch.setattr(migrate_pattern_drift, "repo_root", repo_root)
    server = server_config(routines_home=home, libraries_home=library)

    record = run_migration(server)

    assert not record["left"] and not record["failed"], record
    assert len(record["converted"]) == 14, record["converted"]
    for seed in shipped:
        got = (library / "patterns" / seed.name).read_text(encoding="utf-8")
        assert got == seed.read_text(encoding="utf-8"), seed.name
        assert record["converted"][f"patterns/{seed.name}"] == [
            "budgets.max_total_turns: -1 -> 0", "ladder", "ladder_turn_interval",
            "oversight_turns", "rules: + outcome-first"], seed.name


def test_every_converted_shipped_pattern_is_still_one_the_library_can_use(tmp_path, monkeypatch):
    """A migration that left one of the fourteen unreadable would break creation itself."""
    from rsched import migrate_pattern_drift
    from rsched.paths import repo_root
    from rsched.patterns import store

    seed_root = repo_root() / "library-seed" / "patterns"
    library, home = tmp_path / "library", tmp_path / "routines"
    (library / "patterns").mkdir(parents=True)
    (home / ".control").mkdir(parents=True)
    for seed in sorted(seed_root.glob("*.yaml")):
        (library / "patterns" / seed.name).write_text(
            _drifted(seed.read_text(encoding="utf-8")), encoding="utf-8")
    monkeypatch.setattr(migrate_pattern_drift, "repo_root", repo_root)

    run_migration(server_config(routines_home=home, libraries_home=library))

    usable = store.list_all(library)
    assert len(usable) == 14, [p["slug"] for p in usable]
    assert store.unusable(library) == {}
    for doc in usable:
        assert doc["problems"] == [], (doc["slug"], doc["problems"])
        assert doc["settings"]["budgets"]["max_total_turns"] == 0, doc["slug"]
        assert "outcome-first" in doc["settings"]["rules"], doc["slug"]
        assert doc["settings"]["ladder"] == {"enabled": False, "rung_height": 3}, doc["slug"]
        assert doc["settings"]["ladder_turn_interval"] == 50, doc["slug"]


def test_the_boot_sequence_calls_it_before_anything_loads_a_pattern():
    """A migration nothing calls converges nothing. Pinned on the boot module's text, the way
    the order of that sequence is load-bearing and commented as such.
    """
    from rsched.paths import repo_root

    boot = (repo_root() / "src" / "rsched" / "cli_daemon.py").read_text(encoding="utf-8")
    assert "migrate_pattern_drift" in boot
    assert boot.index("migrate_pattern_drift") > boot.index("sync_seed_library_docs"), \
        "the pattern migration runs AFTER the add-only seed sync, on the files it left behind"
