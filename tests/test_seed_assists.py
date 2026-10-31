"""The ENTRY-LEVEL carry of a seed rule's `assists:` declarations into the live library.

`bootstrap.sync_seed_library_docs` is add-only at the FILE level, which is right for prose and
wrong for a declaration the engine reads: `evidence-discipline` declared assists in
`library-seed/` from 0.471.0, the live copy declared none, and the hook points steps 6-8 wired
fired against ZERO candidates on this instance for three releases. A green gate cannot see it —
`tests/test_assists.py` reads the SEED directory, never the live library.

0.477.0's one-shot `migrate_rule_assists` closed that gap once and could never close another:
it refused a rule whose live copy already declared assists, which is every rule it had just
fixed. So the mechanism is a recurring sync at the granularity the problem has — the ENTRY —
and these are the four things it must refuse while doing it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from rsched.seed_assists import sync_seed_rule_assists

RULE = "evidence-discipline"


def _rule_text(entries: list[tuple[str, str]], *, prose: str = "the body is the operator's") -> str:
    block = ""
    if entries:
        block = "assists:\n" + "".join(
            f"  - id: {name}\n    moment: {moment}\n    predicate: p-{name}\n"
            f"    payload: note\n    line: the line for {name}\n"
            for name, moment in entries)
    return (f"---\neffect:\n  when: always\n{block}tags: [reporting]\n---\n"
            f"# rule: {RULE} — claims trace to observations\n\n{prose}\n")


def _seed(tmp_path: Path, entries: list[tuple[str, str]]) -> Path:
    """A repo-shaped tree whose `library-seed/rules/` carries the seed rule."""
    repo = tmp_path / "repo"
    (repo / "library-seed" / "rules").mkdir(parents=True)
    (repo / "library-seed" / "rules" / f"{RULE}.md").write_text(_rule_text(entries),
                                                                encoding="utf-8")
    return repo


def _live(tmp_path: Path, entries: list[tuple[str, str]], *, git: bool = False,
          prose: str = "the body is the operator's") -> Path:
    home = tmp_path / "library"
    (home / "rules").mkdir(parents=True)
    (home / "rules" / f"{RULE}.md").write_text(_rule_text(entries, prose=prose),
                                               encoding="utf-8")
    if git:
        subprocess.run(["git", "init", "-q"], cwd=home, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=home, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=home, check=True)
        subprocess.run(["git", "add", "-A"], cwd=home, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=home, check=True)
    return home


def _ids(home: Path) -> list[str]:
    text = (home / "rules" / f"{RULE}.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---")[1]) or {}
    return [a["id"] for a in (meta.get("assists") or [])]


def _patch_root(monkeypatch, repo: Path) -> None:
    monkeypatch.setattr("rsched.seed_assists.repo_root", lambda: repo)


def test_a_missing_declaration_is_carried_into_the_live_rule(tmp_path, monkeypatch):
    """The gap itself: the live rule has two of the seed's three declarations and the third —
    a new release's hook point — can never arrive through the file-level sync.
    """
    repo = _seed(tmp_path, [("one", "child-exit"), ("two", "run-end"), ("three", "restart")])
    home = _live(tmp_path, [("one", "child-exit"), ("two", "run-end")], git=True)
    _patch_root(monkeypatch, repo)
    changed = sync_seed_rule_assists(home, routines_home=tmp_path / "routines")
    assert changed == [f"rules/{RULE}.md"]
    assert _ids(home) == ["one", "two", "three"]


def test_the_prose_below_the_frontmatter_is_untouched(tmp_path, monkeypatch):
    """The body is the operator's — a revision that already reached every holder."""
    repo = _seed(tmp_path, [("one", "restart")])
    home = _live(tmp_path, [], git=True, prose="HIS OWN PARAGRAPH, which the seed lacks")
    _patch_root(monkeypatch, repo)
    sync_seed_rule_assists(home, routines_home=tmp_path / "routines")
    text = (home / "rules" / f"{RULE}.md").read_text(encoding="utf-8")
    assert "HIS OWN PARAGRAPH, which the seed lacks" in text
    assert _ids(home) == ["one"]


def test_an_entry_the_live_rule_already_has_is_left_exactly_as_it_is(tmp_path, monkeypatch):
    """His declaration outranks the seed's — the rule the one-shot migration held at the file
    level, kept here at the entry level.
    """
    repo = _seed(tmp_path, [("one", "restart")])
    home = _live(tmp_path, [("one", "restart")], git=True)
    live_file = home / "rules" / f"{RULE}.md"
    before = live_file.read_text(encoding="utf-8").replace(
        "the line for one", "HIS OWN WORDING for one")
    live_file.write_text(before, encoding="utf-8")
    _patch_root(monkeypatch, repo)
    assert sync_seed_rule_assists(home, routines_home=tmp_path / "routines") == []
    assert live_file.read_text(encoding="utf-8") == before


def test_a_declaration_removed_from_this_rule_is_never_resurrected(tmp_path, monkeypatch):
    """Add-only mechanisms that cannot see a deletion are how a deleted doc comes back at every
    restart — `libgit.path_was_deleted` is the same rule one level up, for files.
    """
    repo = _seed(tmp_path, [("one", "child-exit"), ("gone", "restart")])
    home = _live(tmp_path, [("one", "child-exit"), ("gone", "restart")], git=True)
    live_file = home / "rules" / f"{RULE}.md"
    live_file.write_text(_rule_text([("one", "child-exit")]), encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "remove the gone declaration"], cwd=home, check=True)
    _patch_root(monkeypatch, repo)
    assert sync_seed_rule_assists(home, routines_home=tmp_path / "routines") == []
    assert _ids(home) == ["one"]


def test_a_rule_missing_from_the_live_library_is_never_created(tmp_path, monkeypatch):
    """He may have deleted it; the file-level sync owns that question."""
    repo = _seed(tmp_path, [("one", "restart")])
    home = tmp_path / "library"
    (home / "rules").mkdir(parents=True)
    _patch_root(monkeypatch, repo)
    assert sync_seed_rule_assists(home, routines_home=tmp_path / "routines") == []
    assert not (home / "rules" / f"{RULE}.md").exists()


def test_a_live_rule_with_no_block_at_all_gains_one(tmp_path, monkeypatch):
    """The 2026-10-30 case exactly: the live copy declared nothing and three wired points had
    no candidates.
    """
    repo = _seed(tmp_path, [("one", "restart")])
    home = _live(tmp_path, [], git=True)
    _patch_root(monkeypatch, repo)
    assert sync_seed_rule_assists(home, routines_home=tmp_path / "routines") == [
        f"rules/{RULE}.md"]
    assert _ids(home) == ["one"]


def test_the_result_is_a_rule_the_live_reader_accepts(tmp_path, monkeypatch):
    """The declarations have to be READABLE after the insert, not merely present — the point of
    carrying the seed's own lines rather than re-dumping parsed YAML.
    """
    from rsched import assists as lib

    repo = _seed(tmp_path, [("one", "child-exit"), ("two", "restart")])
    home = _live(tmp_path, [("one", "child-exit")], git=True)
    _patch_root(monkeypatch, repo)
    sync_seed_rule_assists(home, routines_home=tmp_path / "routines")
    text = (home / "rules" / f"{RULE}.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---")[1])
    assert [a["moment"] for a in meta["assists"]] == ["child-exit", "restart"]
    # the live library's own reader walks the directory; the names must survive the round trip
    assert {a.key.split(":")[-1] for a in lib.read_library_assists(home / "rules")} <= {
        "one", "two"}


def test_it_is_idempotent_across_boots(tmp_path, monkeypatch):
    """It runs at EVERY boot — unlike the one-shot it replaces, whose record was its guard. So
    running it twice must change nothing the second time.
    """
    repo = _seed(tmp_path, [("one", "restart")])
    home = _live(tmp_path, [], git=True)
    _patch_root(monkeypatch, repo)
    assert sync_seed_rule_assists(home, routines_home=tmp_path / "routines") == [
        f"rules/{RULE}.md"]
    after = (home / "rules" / f"{RULE}.md").read_text(encoding="utf-8")
    assert sync_seed_rule_assists(home, routines_home=tmp_path / "routines") == []
    assert (home / "rules" / f"{RULE}.md").read_text(encoding="utf-8") == after


def test_the_seed_rule_this_release_ships_declares_the_restart_assist():
    """The declaration half of step 9: the point fires, and the library says WHEN. Without this
    the wired point has no candidates, which is what 0.477.0 found for three earlier steps.
    """
    seed = Path(__file__).resolve().parent.parent / "library-seed" / "rules" / f"{RULE}.md"
    meta = yaml.safe_load(seed.read_text(encoding="utf-8").split("---")[1])
    restart = [a for a in meta["assists"] if a["moment"] == "restart"]
    assert len(restart) == 1, "the seed rule must declare exactly one restart assist"
    assert restart[0]["predicate"] == "work-cut-by-daemon-exit"
    assert restart[0]["payload"] == "note"
