"""Side table-of-contents (toc.js): on a wide viewport a sticky rail lists the page's h2 sections
and jumps to them; on a narrow viewport it's hidden (no margin to park in)."""

from playwright.sync_api import expect


def test_toc_lists_sections_and_jumps(ui, ui_page):
    ui_page.set_viewport_size({"width": 1600, "height": 1000})
    ui_page.goto(f"{ui.url}/#/settings")
    ui_page.wait_for_selector("#sec-connections", timeout=10_000)
    toc = ui_page.locator(".side-toc")
    expect(toc).to_be_visible()
    expect(toc.locator(".toc-link", has_text="Connections")).to_be_visible()
    expect(toc.locator(".toc-link", has_text="Secrets")).to_be_visible()
    # clicking a TOC entry scrolls its section into view
    toc.locator(".toc-link", has_text="Notifications").click()
    expect(ui_page.locator("#sec-notifications")).to_be_in_viewport()


def test_toc_hidden_on_narrow(ui, ui_page):
    ui_page.set_viewport_size({"width": 1200, "height": 900})
    ui_page.goto(f"{ui.url}/#/settings")
    ui_page.wait_for_selector("#sec-connections", timeout=10_000)
    expect(ui_page.locator(".side-toc")).to_be_hidden()
