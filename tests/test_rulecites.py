"""`rsched validate` reports prose citing a rule the library retired.

The library's 2026-09-30 consolidation deleted 24 rules, and nine days later 40 citations in
26 files still named them; nothing checked, so runs found out by a failed `read_rule`. The
shapes below are the ones routine-improver's sweep met in the wild.
"""

import subprocess
from types import SimpleNamespace

from rsched import cli
from rsched.workflows.rulecites import citation_notes, citations, retired_rules

RETIRED = {"status-page", "ai-writing-tells", "error-recovery"}
LIVE = {"fix-the-cause", "write-as-the-principal"}


def test_an_explicit_read_rule_of_a_rule_the_library_lacks_is_a_citation():
    text = "intro\n- `read_rule status-page` — binds this routine\nthen read_rule name=no-such-rule"
    assert citations(text, RETIRED, LIVE) == [(2, "status-page"), (3, "no-such-rule")]
    assert citations("read_rule name=fix-the-cause", RETIRED, LIVE) == []


def test_a_retired_slug_needs_a_cue_to_count():
    # a rule word near it, either side
    assert citations("Follow the `status-page` rule's three checks.", RETIRED, LIVE) == [
        (1, "status-page")]
    # a reading verb just before it, with no rule word anywhere
    assert citations("Read `ai-writing-tells` before composing a single word.", RETIRED,
                     LIVE) == [(1, "ai-writing-tells")]
    # bold, no backticks
    assert citations("Read the **ai-writing-tells** standing practice.", RETIRED, LIVE) == [
        (1, "ai-writing-tells")]
    # the same word naming a script is not a rule
    assert citations("Then run the `error-recovery` helper script on the batch.", RETIRED,
                     LIVE) == []
    # a slug that was never a rule is never guessed at, cue or not
    assert citations("The `web-request` rule of thumb applies.", RETIRED, LIVE) == []


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


def _library_retiring(lib, slug: str) -> None:
    """A library repo whose history added then deleted `rules/<slug>.md`."""
    _git(lib, "init", "-q")
    (lib / "rules" / f"{slug}.md").write_text("# rule: x — y\n", encoding="utf-8")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "add")
    (lib / "rules" / f"{slug}.md").unlink()
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "retire")


def test_retired_rules_are_the_history_s_deletions_the_tree_no_longer_carries(tmp_path):
    lib = tmp_path / "lib"
    (lib / "rules").mkdir(parents=True)
    (lib / "rules" / "fix-the-cause.md").write_text("# rule: a — b\n", encoding="utf-8")
    _library_retiring(lib, "status-page")
    assert retired_rules(lib) == {"status-page"}
    # re-added later: carried again, so no longer retired
    (lib / "rules" / "status-page.md").write_text("# rule: x — y\n", encoding="utf-8")
    assert retired_rules(lib) == set()
    # no repo, no history: nothing reported rather than everything
    assert retired_rules(tmp_path / "nowhere") == set()


def test_citation_notes_read_the_recipe_and_the_memory_and_say_whose_move_it_is(tmp_path):
    d = tmp_path / "r"
    (d / "stages").mkdir(parents=True)
    (d / ".memory").mkdir()
    (d / "main.md").write_text("# Run flow\n\nVerified by the `status-page` rule.\n"
                               "Then read_rule name=ai-writing-tells and the `status-page` "
                               "rule again.\n", encoding="utf-8")
    (d / "stages" / "publish.md").write_text("- `read_rule status-page` first\n",
                                             encoding="utf-8")
    (d / ".memory" / "hub.md").write_text("Read the `status-page` rule for the shape.\n",
                                          encoding="utf-8")
    notes = citation_notes(d, RETIRED, LIVE)
    assert [n.split(":")[0] for n in notes] == ["main.md", "stages/publish.md", ".memory/hub.md"]
    # one row per file, each slug with its lines
    assert notes[0].startswith("main.md: cites `status-page` (line 3, 4), `ai-writing-tells` "
                               "(line 4), which the library no longer carries")
    assert "memory note" in notes[2] and "memory note" not in notes[0]


def test_validate_reports_a_stale_citation_and_stays_green(cli_server, make_routine, capsys):
    _library_retiring(cli_server.libraries_home, "status-page")
    d = make_routine(slug="testr")
    (d / "main.md").write_text("# Run flow\n\n- `read_rule status-page` — binds this routine\n",
                               encoding="utf-8")
    assert cli.cmd_validate(SimpleNamespace(routine="testr")) == 0   # a REPORT, not a gate
    out = capsys.readouterr().out
    assert "NOTE  rule citation · main.md: cites `status-page` (line 3)" in out
