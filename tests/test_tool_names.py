"""The operator's standing order of 2026-11-13: prose names the CAPABILITY, never a tool.

> "we should have a standing order *FOR MONTHS* saying that recipes don't mention utils by name"

He said it reading a report of mine that priced a contract change at "31 routines and 14
patterns to migrate" — a cost that only exists because nothing had ever enforced the order.
So the thing owed is ENFORCEMENT, not the sentence again: the order was already written in
this repo (`engine/kindprose.py`'s rule-authoring prose, this routine's own permission note)
and bought nothing, because no check failed when prose broke it.

The two halves carry different weight, and these tests hold that asymmetry:

* the LIBRARY (patterns, workflows, rules, playbooks, permissions) names no tool today —
  measured over every file on 2026-11-13 — so a breach there is NEW and fails the lint,
  which is what stops the order being broken by whatever the library hands a routine at
  creation;
* 13 of 31 LIVE recipes already name one (28 findings), so there it is a `recipelint` note.
  Failing `rsched validate` for thirteen routines nobody asked to break is how a check gets
  switched off in a week — `recipelint`'s own charter says exactly that.

What must NOT be a finding is the reason this check can be narrow: a tool's name in ordinary
prose ("the mail account", "a git history") is English, and the catalog's own meta-verbs
(`util name=list`, `util name=search`) are how a run asks what exists — the OPPOSITE of
naming a tool. The live fleet produced one false positive of that kind and it is pinned here.
"""

from __future__ import annotations

from pathlib import Path

from rsched.workflows import lint, recipelint

RULE = """---
tags: [a, b, c]
effect:
  with: it happens
  without: it does not
  when: always
---

# rule: a-rule — one line

The principle, stated for every routine that holds it.
"""


def test_a_recipe_naming_a_tool_is_a_note(tmp_path: Path) -> None:
    """The live-recipe half: a note, naming the tool and what to say instead."""
    (tmp_path / "main.md").write_text("# r\n\nRead the inbox with `gu mail list`.\n")
    notes = [n for n in recipelint.recipe_notes(tmp_path) if "names the tool" in n]
    assert len(notes) == 1
    assert "`mail`" in notes[0]
    assert "names the CAPABILITY" in notes[0]
    # and it is a NOTE, never a contract error: `rsched validate` must not fail for it
    assert not recipelint.recipe_errors(tmp_path)


def test_the_action_call_form_counts_too(tmp_path: Path) -> None:
    """Both call forms name the tool: a shell/script `gu <name>` and the action's own."""
    (tmp_path / "main.md").write_text('# r\n\nCall `util name=["translate"]` for the text.\n')
    notes = [n for n in recipelint.recipe_notes(tmp_path) if "names the tool" in n]
    assert len(notes) == 1 and "`translate`" in notes[0]


def test_a_tool_word_in_prose_is_not_a_finding(tmp_path: Path) -> None:
    """The narrowness that keeps the check alive: only a CALL names a tool.

    A linter that fired on the word would fire on every recipe that mentions mail, a git
    history or a browser, and would be switched off — so the subject is the call form.
    """
    (tmp_path / "main.md").write_text(
        "# r\n\nCheck the mail account, read the git history, open the browser.\n"
        "Use whatever reaches the mailbox.\n")
    assert not [n for n in recipelint.recipe_notes(tmp_path) if "names the tool" in n]


def test_asking_the_catalog_what_exists_is_not_naming_a_tool(tmp_path: Path) -> None:
    """The one false positive the live fleet produced, pinned.

    `util name=list` and `util name=search` are the catalog's meta-verbs: prose telling a run
    to look up what exists is how it names a capability and lets the catalog answer with the
    tool. self-audit's evidence stage was the hit on 2026-11-13.
    """
    (tmp_path / "main.md").write_text(
        '# r\n\nRun `util name=list` for the catalog, or `util name=search` to find one '
        "by need.\n")
    assert not [n for n in recipelint.recipe_notes(tmp_path) if "names the tool" in n]


def test_a_library_rule_naming_a_tool_fails_the_lint() -> None:
    """The library half: a breach FAILS, because nothing the library holds breaches today."""
    clean = lint.lint_rule_text(RULE, filename="a-rule.md")
    assert not clean, clean
    breach = lint.lint_rule_text(
        RULE.replace("The principle, stated", "Send it with `gu mail send`. Stated"),
        filename="a-rule.md")
    assert [p for p in breach if "names the tool `mail`" in p], breach


def test_a_permission_and_a_playbook_are_held_to_it_too() -> None:
    """All three library text kinds, so a tool name cannot simply move next door."""
    perm = """---
requires: {}
effect:
  with: it can
  without: it cannot
  when: always
---

# permission: a-perm — what it allows

Fetch the page with `gu web-request`.
"""
    found = lint.lint_permission_text(perm, filename="a-perm.md")
    assert [p for p in found if "names the tool `web-request`" in p], found
    play = """---
slug: a-play
when: one line
tags: [x]
---

# A playbook

## Instructions

Render it with `gu doc`.
"""
    found = lint.lint_playbook_text(play, filename="a-play/MAIN.md")
    assert [p for p in found if "names the tool `doc`" in p], found


def test_the_live_library_is_clean_so_the_check_can_be_fatal() -> None:
    """The measurement the severity rests on, held as a test.

    If the library ever acquires a tool name, this fails — which is the point: the order is
    enforceable there precisely because nothing breaches it yet.
    """
    home = Path("/home/mark/.local/share/routine-scheduler-libraries")
    if not home.is_dir():  # the library is not part of the repo's own test fixtures
        return
    offenders = {name: [p for p in problems if "names the tool" in p]
                 for name, problems in lint.lint_all(home).items()}
    assert not {k: v for k, v in offenders.items() if v}
