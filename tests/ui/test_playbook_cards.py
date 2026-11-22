"""The new-conversation screen LISTS the playbooks (D307), asserted where the list is built —
in the browser. The screen used to offer them through a `<select>` whose options were bare
titles, so a playbook's `when` and `axis` were visible only AFTER it was picked and somebody who
did not already know playbooks existed had nothing to see; the operator decided (2026-11-21,
code review) that starting from a playbook must be readable where a conversation starts. Three
things are checked here and nowhere else: the card carries the `when` and the axis without a
click, clicking one SELECTS it (the value the composer posts, so the empty first message is
accepted), and an EMPTY library renders no list rather than an empty box.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from playwright.sync_api import expect

from rsched import playbooks

CARD = ".pb-card"
LIST = ".pb-pick"


def _main(title: str, when: str, axis: str, tags: list[str]) -> str:
    tag_lines = "".join(f"\n  - {t}" for t in tags)
    return (f"---\ntitle: {title}\nwhen: {when}\naxis: {axis}\ntags:{tag_lines}\n---\n\n"
            "## Instructions\n1. do the thing\n")


def _library(ui) -> Path:
    """The fixture console's library home — where its playbooks live (the `ui` fixture copies
    the seeded template into `tmp/library` and points `libraries_home` at it)."""
    return ui.tmp / "library"


def _empty(ui) -> Path:
    """A library whose `playbooks/` dir EXISTS and holds none — which is what an instance that
    has deleted its last playbook looks like. Removing the directory itself would be a
    different case: the catalog read then fails, the console's `.catch` renders nothing, and
    the empty-library assertion below would pass without the production branch ever running
    (measured 2026-11-22: that spelling held green through its own neuter)."""
    home = _library(ui)
    pb = home / "playbooks"
    pb.mkdir(parents=True, exist_ok=True)
    for sub in pb.iterdir():
        if sub.is_dir():
            shutil.rmtree(sub)
    return home


def _seed_two(ui) -> Path:
    """Two playbooks of our own in a library holding nothing else, so the order they render in
    is the catalog's (`list_playbooks` sorts by slug) and no seed playbook joins the count."""
    home = _empty(ui)
    playbooks.write_playbook(home, "audit-a-tree", main=_main(
        "Audit a tree", "a codebase needs a once-over before a release",
        "which tree, and how deep", ["code", "review"]))
    playbooks.write_playbook(home, "brief-a-vendor", main=_main(
        "Brief a vendor", "a supplier needs the work written down",
        "which vendor and which job", ["writing"]))
    return home


def test_two_playbooks_render_as_cards_with_their_when_and_axis(ui, ui_page):
    """Both are readable before anything is clicked: title, the one-line `when`, the "varies:"
    axis and the tags, in the library's order."""
    _seed_two(ui)
    ui_page.goto(f"{ui.url}/#/conversations")
    cards = ui_page.locator(CARD)
    expect(cards).to_have_count(2)
    # the `<select>` is gone from the composer panel (the pre-start settings keep their own)
    expect(ui_page.locator(".conv-new select")).to_have_count(0)
    first, second = cards.nth(0), cards.nth(1)
    expect(first.locator(".pb-card-title")).to_have_text("Audit a tree")
    expect(first.locator(".pb-card-when")).to_have_text(
        "a codebase needs a once-over before a release")
    expect(first.locator(".pb-card-axis")).to_have_text("varies: which tree, and how deep")
    expect(first.locator(".pb-tag")).to_have_count(2)
    expect(second.locator(".pb-card-title")).to_have_text("Brief a vendor")
    expect(second.locator(".pb-card-axis")).to_have_text(
        "varies: which vendor and which job")


def test_clicking_a_card_selects_it_and_an_empty_first_message_is_accepted(ui, ui_page):
    """The click carries the value the composer posts: with a playbook picked the first message
    may be left empty, because the playbook IS the brief — and the selection is undoable from
    the card's own "start fresh instead", which is absent until something is selected."""
    _seed_two(ui)
    ui_page.goto(f"{ui.url}/#/conversations")
    card = ui_page.locator('[data-pb="brief-a-vendor"]')
    expect(card).to_have_attribute("aria-pressed", "false")
    expect(ui_page.locator(".pb-fresh")).to_have_count(0)

    card.click()
    expect(card).to_have_attribute("aria-pressed", "true")
    expect(card).to_have_class("pb-card pb-on")
    expect(ui_page.locator('[data-pb="audit-a-tree"]')).to_have_attribute(
        "aria-pressed", "false")                              # one selection, not two
    expect(ui_page.locator(".pb-fresh")).to_have_count(1)
    expect(ui_page.locator(".conv-new textarea")).to_have_attribute(
        "placeholder", "Optional — anything specific for this run? The playbook is the brief…")

    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    config = (ui.conversations / slug / "routine.yaml").read_text(encoding="utf-8")
    assert "brief-a-vendor" in config, config


def test_start_fresh_instead_undoes_the_selection(ui, ui_page):
    """Undo returns the screen to where it started: nothing pressed, no undo control, and the
    message box asking for the task again."""
    _seed_two(ui)
    ui_page.goto(f"{ui.url}/#/conversations")
    card = ui_page.locator('[data-pb="audit-a-tree"]')
    card.click()
    expect(card).to_have_attribute("aria-pressed", "true")

    ui_page.locator(".pb-fresh").click()
    expect(card).to_have_attribute("aria-pressed", "false")
    expect(ui_page.locator(".pb-fresh")).to_have_count(0)
    expect(ui_page.locator(".conv-new textarea")).to_have_attribute(
        "placeholder",
        "What should the agent do? The first message becomes the conversation's task…")


def test_an_empty_library_renders_no_list_at_all(ui, ui_page):
    """The negative half: with no playbooks the list is ABSENT, not an empty box — a count, not
    a visibility check, because an empty wrapper is exactly what the order rules out and it
    would pass every content assertion above vacuously."""
    _empty(ui)
    ui_page.goto(f"{ui.url}/#/conversations")
    expect(ui_page.locator(".conv-new textarea")).to_be_visible()
    # WAIT FOR THE RENDER FIRST. `to_have_count(0)` is satisfied the instant it is asked, so
    # asserting absence straight after the navigation can pass before `/api/playbooks` has
    # answered — proving nothing about the branch. The host stamps the catalog's size whether
    # or not it mounts anything, so this waits for the empty render itself.
    expect(ui_page.locator('[data-pb-count="0"]')).to_have_count(1)
    expect(ui_page.locator(LIST)).to_have_count(0)
    expect(ui_page.locator(CARD)).to_have_count(0)
