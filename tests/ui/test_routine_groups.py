"""The routine page's settings groups: what is open when you arrive and what is remembered.

Seven groups open at once made the page 11-12 000px tall; somebody who came to change one
dial had no map. The head of each group carries a hint line, which IS the map. The page opens
on the two questions most visits come with — when it fires and when it is finished — and
folds the rest. Inside each group, the rarely needed sections fold once more behind its "more"
menu, whose summary says what the menu holds.

Nothing is removed by that; this file is what says so. One click opens a group, the choice
survives a reload, and a setup-check fix link still lands the reader on its control — which it
can only do by opening the folds on the way.
"""
from __future__ import annotations

import yaml
from playwright.sync_api import expect

LEAD = ["Schedule & gate", "Goal"]


def _titles_open(page) -> list[str]:
    return page.evaluate(
        "() => [...document.querySelectorAll('details.rgroup[open] .rgroup-title')]"
        ".map(n => n.textContent)")


def _group(page, title):
    return page.locator(".rgroup", has=page.locator(".rgroup-title", has_text=title))


def test_only_the_leading_groups_are_open_on_arrival(ui, ui_page):
    """The page opens on WHEN IT FIRES and WHEN IT IS FINISHED and folds the rest. Every other
    group is one click and reads its own hint line while folded, so the fold is an index."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector(".rgroup-head")
    assert _titles_open(ui_page) == LEAD
    expect(_group(ui_page, "Models").locator(".rgroup-hint")).to_contain_text("model")
    # the pattern bar leads, outside every fold
    expect(ui_page.locator("[data-pattern-bar]")).to_be_visible()


def test_a_more_menu_says_what_it_holds(ui, ui_page):
    """The rarely needed sections fold behind their group's "more", whose summary is a digest
    of what is inside — the reason to open it or not — kept current with the draft."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    limits = _group(ui_page, "Limits & reach")
    limits.locator("summary.rgroup-head").click()
    more = limits.locator("details.rmore")
    expect(more).not_to_have_attribute("open", "")
    expect(more.locator(".rmore-digest")).to_contain_text("keeps 30 runs")
    expect(ui_page.locator("#sec-retention")).to_be_hidden()
    more.locator("summary").click()
    keep = ui_page.locator("input[data-keep-runs]")
    keep.fill("9")
    keep.press("Tab")
    expect(more.locator(".rmore-digest")).to_contain_text("keeps 9 runs")
    expect(more.locator(".rmore-head [data-group-changes]")).to_have_text("1 change")


def test_an_opened_group_is_still_open_after_a_reload(ui, ui_page):
    """The default is a starting point, not a policy: an operator who lives in one group opens
    it once. The stored list is the whole answer, so closing everything is remembered too —
    'no choice yet' and 'nothing open' are different states."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector(".rgroup-head")
    _group(ui_page, "Models").locator("summary.rgroup-head").click()
    expect(ui_page.locator("#sec-models")).to_be_visible()
    ui_page.wait_for_function(
        "() => (JSON.parse(localStorage.getItem('routine-settings-open')) || []).includes('Models')")

    ui_page.reload()
    ui_page.wait_for_selector(".rgroup-head")
    assert sorted(_titles_open(ui_page)) == sorted([*LEAD, "Models"])

    # fold everything; that is remembered as well — once the last toggle has been written
    for title in [*LEAD, "Models"]:
        _group(ui_page, title).locator("summary.rgroup-head").click()
    ui_page.wait_for_function(
        "() => JSON.stringify(JSON.parse(localStorage.getItem('routine-settings-open'))) === '[]'")
    ui_page.reload()
    ui_page.wait_for_selector(".rgroup-head")
    assert _titles_open(ui_page) == []


def test_a_setup_fix_link_unfolds_the_group_it_points_into(ui, ui_page):
    """The one journey the folds could have broken. The setup check sits above the hero and its
    rows end in the act that settles them; the act's control lives in a settings group, which is
    folded by default. The jump opens every <details> on the way — without that the link would
    scroll to a heading with nothing under it."""
    d = ui.server_cfg.libraries_home / "utils" / "sig"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(
        '"""sig — t.\n\nusage: gu sig\ncalls: (none)\ntags: t\n'
        'secrets: (none)\nnet: none\nfs: rw /srv/sig-sessions\n"""\n', encoding="utf-8")
    path = ui.routines / "uir" / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["permissions"] = []
    cfg["capabilities"] = {"actions": [], "utils": ["sig"], "util_tags": []}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/routine/uir")
    offer = ui_page.locator(
        '[data-setup-check] .setup-row[data-entity="fs-write:/srv/sig-sessions"] button.fix-link')
    offer.wait_for(state="visible")
    section = offer.get_attribute("data-fix-section")
    assert section, "the offer names no section to land on"
    assert ui_page.locator(f"#{section}").is_visible() is False   # folded before the press
    offer.click()
    expect(ui_page.locator(f"#{section}")).to_be_visible()


def test_the_side_index_opens_the_fold_a_section_sits_in(ui, ui_page):
    """The "On this page" index lists every section, folded or not; following it into a closed
    group opens the fold, or the reader lands on a heading with nothing under it."""
    ui_page.set_viewport_size({"width": 1425, "height": 950})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector(".rgroup-head")
    expect(ui_page.locator("#sec-machines")).to_be_hidden()
    ui_page.locator(".side-toc .toc-link", has_text="Machines").click()
    expect(ui_page.locator("#sec-machines")).to_be_visible()
