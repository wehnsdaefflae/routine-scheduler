"""The conversation header: its title is edited in place and saved on blur, and its saves
happen once per press.

The save compared the edited text with the title the header was RENDERED with, and never moved
that baseline after a save: renaming "A" to "B" saved, but renaming it back to "A" compared
equal to the stale baseline and was silently skipped — the header said "A" while the
conversation stayed "B" on disk and in the sidebar. A title cleared to nothing was likewise
skipped without a word, leaving an empty heading over a conversation that still had its name.
"""

from __future__ import annotations

import yaml
from playwright.sync_api import expect

from .conftest import until


def _name(conv_dir) -> str:
    return yaml.safe_load((conv_dir / "routine.yaml").read_text(encoding="utf-8")).get("name", "")


def _retitle(title, text: str) -> None:
    title.fill(text)
    title.evaluate("el => el.blur()")


def test_a_title_renamed_back_is_saved(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Plan the trip.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    conv_dir = ui.conversations / ui_page.url.rsplit("/", 1)[-1]
    title = ui_page.locator(".conv-h1")
    expect(title).not_to_have_text("")
    original = title.inner_text().strip()

    _retitle(title, "Trip, renamed")
    until(lambda: _name(conv_dir) == "Trip, renamed", what="the rename to land")
    _retitle(title, original)
    until(lambda: _name(conv_dir) == original, what="the rename back to land")

    # a cleared title is no title: the heading puts the current one back instead of lying
    _retitle(title, "")
    expect(title).to_have_text(original)
    assert _name(conv_dir) == original


def test_a_double_click_saves_once(ui, ui_page):
    """The header's save buttons never disabled themselves, so a double click sent the PATCH
    twice; they go through util.act() now, which holds the button for the request."""
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Plan the trip.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    patches: list[str] = []
    ui_page.on("request", lambda r: patches.append(r.url)
               if r.method == "PATCH" and r.url.endswith(f"/api/conversations/{slug}") else None)

    ui_page.locator("summary", has_text="capabilities & budgets").click()
    ui_page.get_by_role("button", name="save budgets").dblclick()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("budgets saved")
    ui_page.wait_for_timeout(500)
    assert len(patches) == 1, patches
