"""MIGRATION(expires=2026-12-01) guard: the live `status-page` rule gets an answerable card tab.

The seed sync is add-only, so the rule text a holder actually reads is the LIBRARY copy and a
rewritten seed reaches nobody. What is asserted here is the pair: the seed asks for a heading the
prompt can supply — the name its DOMAIN keeps in the shared store — and the one-shot carries that
paragraph into a live copy while leaving prose it did not come for, including a section somebody
has since revised, exactly as it found it.
"""
from __future__ import annotations

from pathlib import Path

from rsched.migrate_status_page_rule import (
    CONVERGED,
    HEADING,
    INSTALLED,
    OLD_SECTION,
    migrate,
    run,
)

SEED_RULES = Path(__file__).resolve().parents[1] / "library-seed" / "rules"
SEED_TEXT = (SEED_RULES / "status-page.md").read_text(encoding="utf-8")
SEED_SECTION = SEED_TEXT[SEED_TEXT.find(HEADING):]


def _live(tmp_path: Path, text: str) -> Path:
    """A stand-in library holding one rule."""
    home = tmp_path / "rules"
    home.mkdir(parents=True, exist_ok=True)
    (home / "status-page.md").write_text(text, encoding="utf-8")
    return home


def test_the_seed_asks_for_a_heading_a_run_can_produce():
    # The prompt names the shared store and the routines beside it; it spells no domain NAME,
    # so the members keep one in the store. A rule that asked for a name nothing tells the run
    # is a rule no holder can obey.
    assert "steward-hub-tab.txt" in SEED_SECTION
    assert "DOMAIN" in SEED_SECTION
    assert "ROUTINE GROUP" not in SEED_TEXT
    # …and this migration is worth running: the seed no longer carries the text it replaces.
    assert OLD_SECTION not in SEED_TEXT


def test_it_carries_the_paragraph_into_a_live_copy(tmp_path):
    home = _live(tmp_path, SEED_TEXT[: SEED_TEXT.find(HEADING)] + OLD_SECTION)
    assert migrate(home, SEED_RULES) == f"status-page: {INSTALLED}"
    assert (home / "status-page.md").read_text(encoding="utf-8") == SEED_TEXT


def test_it_replaces_the_card_section_and_nothing_else(tmp_path):
    body = "# rule: status page — a copy an operator has been revising\n\nkeep this line\n\n"
    home = _live(tmp_path, body + OLD_SECTION)
    assert migrate(home, SEED_RULES) == f"status-page: {INSTALLED}"
    assert (home / "status-page.md").read_text(encoding="utf-8") == body + SEED_SECTION


def test_running_it_again_changes_nothing(tmp_path):
    home = _live(tmp_path, SEED_TEXT[: SEED_TEXT.find(HEADING)] + OLD_SECTION)
    assert run(home, SEED_RULES) == 1
    before = (home / "status-page.md").read_text(encoding="utf-8")
    assert migrate(home, SEED_RULES) == f"status-page: {CONVERGED}"
    assert run(home, SEED_RULES) == 0
    assert (home / "status-page.md").read_text(encoding="utf-8") == before


def test_a_card_section_somebody_edited_is_left_alone_and_named(tmp_path):
    edited = OLD_SECTION.replace("The hub shows one card per project",
                                 "The hub shows one card per project, sorted his way")
    home = _live(tmp_path, edited)
    note = migrate(home, SEED_RULES)
    assert "has been edited" in note
    assert (home / "status-page.md").read_text(encoding="utf-8") == edited


def test_a_rule_without_the_card_section_is_left_alone_and_named(tmp_path):
    text = "---\ntags: [steward]\n---\n# rule: status page\n\nnothing about the card here\n"
    home = _live(tmp_path, text)
    assert "no '## Say what is true on the card' section" in migrate(home, SEED_RULES)
    assert (home / "status-page.md").read_text(encoding="utf-8") == text


def test_a_library_without_the_rule_is_named_rather_than_raising(tmp_path):
    empty = tmp_path / "empty-rules"
    empty.mkdir()
    assert migrate(empty, SEED_RULES).startswith("status-page: skipped — ")
    assert run(empty, SEED_RULES) == 0


def test_a_seed_without_the_section_carries_nothing(tmp_path):
    seed = _live(tmp_path / "seed", "# rule: status page\n\nno card section\n")
    home = _live(tmp_path / "live", OLD_SECTION)
    assert "nothing to carry" in migrate(home, seed)
    assert (home / "status-page.md").read_text(encoding="utf-8") == OLD_SECTION
