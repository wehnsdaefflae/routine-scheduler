"""WHERE each way into the browser screen opens it (D194)."""

import re

from playwright.sync_api import expect

#: An address with NOTHING behind it — the state most instances are in, and the one where the
#: dock still renders its head (and so its title link). Port 1 is served by nothing, so the
#: relay's dial is refused at once instead of waiting out a timeout. Never the fleet's own 6080:
#: this repo's deployment publishes a live noVNC there.
DEAD_SCREEN = "http://127.0.0.1:1/vnc.html"


def test_the_two_browser_links_differ_in_where_they_open(ui, ui_page):
    ui.server_cfg.browser_view_url = DEAD_SCREEN
    ui_page.goto(f"{ui.url}/#/routines")

    chip = ui_page.locator("#browser-dock").get_by_role("link", name="Browser")
    expect(chip).to_have_attribute("target", "_blank")
    expect(chip).to_have_attribute("rel", "noopener")     # no window.opener back into the console

    # the sidebar entry carries no target at all: it navigates this tab
    nav = ui_page.locator("#nav-browser")
    expect(nav).to_be_visible()
    assert not nav.get_attribute("target"), "the sidebar Browser entry must open in this tab"
    nav.click()
    expect(ui_page).to_have_url(re.compile(r"#/browser$"))
    expect(ui_page.locator("iframe.browser-screen")).to_have_count(1)


def test_the_desktop_dock_keeps_navigating_in_place(ui, ui_page):
    """The option is the BROWSER dock's, not every dock's: nothing else changed behaviour."""
    ui.server_cfg.browser_view_url = DEAD_SCREEN
    ui_page.goto(f"{ui.url}/#/routines")
    desktop = ui_page.locator("#desktop-dock").get_by_role("link", name="Desktop")
    if desktop.count():                       # only mounted where a desktop is published
        assert not desktop.first.get_attribute("target")
