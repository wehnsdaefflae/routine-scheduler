"""The Library's settings patterns: every pattern, what it carries in words, who follows it —
and the one lever the Library owns over them, delete.

A pattern is never edited: a different set of values is saved as a NEW pattern from a routine
page. Deleting one never touches a follower's values — they were always the routine's own — and
the confirmation says so before anything is removed.
"""

from __future__ import annotations

import yaml
from playwright.sync_api import expect

from .conftest import until
from .helpers import stored_config


def _follow(ui, slug, pattern) -> None:
    path = ui.routines / slug / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["pattern"] = pattern
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def test_the_library_lists_every_pattern_readably(ui, ui_page):
    _follow(ui, "uir", "watcher")
    ui_page.goto(f"{ui.url}/#/library")
    section = ui_page.locator("[data-patterns]")
    expect(section).to_be_visible()
    watcher = section.locator('.pat[data-pattern="watcher"]')
    expect(watcher.locator(".pat-title")).to_have_text("Watcher")
    expect(watcher.locator(".pat-meta")).to_contain_text("1 routine")
    expect(ui_page.locator('[data-count="patterns"]')).to_contain_text("patterns")

    watcher.locator(".pat-head").click()
    body = watcher.locator(".pat-body")
    expect(body).to_be_visible()
    # the settings read as words, never as the pattern's JSON
    schedule = body.locator('tr[data-setting="schedule"]')
    expect(schedule).to_contain_text("Schedule")
    assert "{" not in schedule.inner_text()
    expect(body.locator('tr[data-setting="budgets"]')).to_contain_text("turns")
    expect(body.locator(".pat-foot a")).to_have_attribute("href", "#/routine/uir")
    # a pattern is made on a routine page, so the Library offers no editor for one
    expect(section.locator("textarea, input[type=text]")).to_have_count(0)


def test_deleting_a_pattern_leaves_its_followers_values_alone(ui, ui_page):
    _follow(ui, "uir", "watcher")
    before = {k: v for k, v in stored_config(ui).items() if k != "pattern"}
    pattern_file = ui.server_cfg.libraries_home / "patterns" / "watcher.yaml"
    assert pattern_file.exists()

    ui_page.goto(f"{ui.url}/#/library/pattern/watcher")
    row = ui_page.locator('.pat[data-pattern="watcher"]')
    expect(row.locator(".pat-body")).to_be_visible()             # the deep link unfolds it
    row.locator("[data-delete-pattern]").click()
    dialog = ui_page.locator(".modal-overlay")
    expect(dialog).to_contain_text(
        "keep every value they hold and simply follow no pattern afterwards")
    expect(dialog).to_contain_text("uir")
    dialog.get_by_role("button", name="delete", exact=True).click()

    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("follow no pattern now")
    until(lambda: not pattern_file.exists(), what="the pattern file to go")
    after = stored_config(ui)
    assert "pattern" not in after
    assert {k: v for k, v in after.items() if k != "pattern"} == before
    expect(ui_page.locator('.pat[data-pattern="watcher"]')).to_have_count(0)
