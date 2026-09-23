"""Side table-of-contents (toc.js): the page's h2 sections listed in the navigation rail, with
click-to-jump; hidden where the rail has no room for words.

The index used to park in the RIGHT margin, which by the rails' arithmetic needs 1900px — the
navigation rail takes 212px off the left and the reading column 1240px, so a 1440px laptop had a
zero margin and no index at all on pages up to twenty-six thousand pixels tall. It now rides the
rail itself, between the destinations and the rail's foot, so 1425 is the width that must show
it. Below 1181px the rail is a 68px icon strip and below 861px a bottom bar: no room either way.

This file owns what the TOC DOES. That the chrome around it is positioned by base.css at all
lives in test_global_chrome.py.
"""

from playwright.sync_api import expect


def test_toc_lists_sections_and_jumps(ui, ui_page):
    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/settings")
    ui_page.wait_for_selector("#sec-connections")
    toc = ui_page.locator(".side-toc")
    expect(toc).to_be_visible()
    # it is IN the rail, not a second fixed block in a margin that may not exist
    assert toc.evaluate("e => e.closest('.topbar') !== null"), "the index is not in the rail"
    expect(toc.locator(".toc-link", has_text="Connections")).to_be_visible()
    expect(toc.locator(".toc-link", has_text="Secrets")).to_be_visible()
    # clicking a TOC entry scrolls its section into view
    toc.locator(".toc-link", has_text="Notifications").click()
    expect(ui_page.locator("#sec-notifications")).to_be_in_viewport()


def test_toc_hidden_on_the_icon_rail(ui, ui_page):
    """At 1000px the rail drops its labels for a 68px icon strip — there is nowhere to read a
    section name, so the index hides and the page is read top-to-bottom."""
    ui_page.set_viewport_size({"width": 1000, "height": 900})
    ui_page.goto(f"{ui.url}/#/settings")
    ui_page.wait_for_selector("#sec-connections")
    expect(ui_page.locator(".side-toc")).to_be_hidden()


def test_the_rail_foot_stays_at_the_foot_without_an_index(ui, ui_page):
    """The destinations stopped growing to fill the rail when the index took that slack, so the
    clock and the theme control are held down by `margin-top: auto` instead. A page with no
    index (fewer than two sections) is where losing it shows."""
    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/help")
    ui_page.wait_for_selector(".topbar .rail-foot")
    gap = ui_page.evaluate(
        "() => window.innerHeight"
        " - document.querySelector('.topbar .rail-foot').getBoundingClientRect().bottom")
    assert gap < 40, f"the rail's foot floated {gap:.0f}px above the bottom of the rail"
