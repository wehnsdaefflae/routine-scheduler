"""The routine page's config groups: what is open when you arrive, and what is remembered.

Seven groups open at once made the page 11-12 000px tall — Permissions (11 cards + 14 rows),
Secrets, Goal, Budgets, Roots, Models, the recipe editor and the state block, all unfolded,
every visit. Somebody who came to change one dial had no map and no way to get one. The head of
each group already carries a hint line, which IS the map; the fix is to ship it folded.

Nothing is removed by that, and this file is what says so: one click opens a group, the choice
survives a reload, and a setup-check fix link still lands the reader on its control — which it
can only do by opening the fold on the way.
"""
from __future__ import annotations

import yaml
from playwright.sync_api import expect

LEAD = "Schedule & triggers"


def _titles_open(page) -> list[str]:
    return page.evaluate(
        "() => [...document.querySelectorAll('details.rgroup[open] .rgroup-title')]"
        ".map(n => n.textContent)")


def test_only_the_leading_group_is_open_on_arrival(ui, ui_page):
    """The page opens on WHEN IT FIRES and folds the rest. Every other group is one click and
    reads its own hint line while folded, so the fold is an index, not a hiding place."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector(".rgroup-head")
    assert _titles_open(ui_page) == [LEAD]
    # …and the folded ones still say what they hold, which is what makes the page navigable
    models = ui_page.locator(".rgroup", has=ui_page.locator(
        ".rgroup-title", has_text="Models & resources"))
    expect(models.locator(".rgroup-hint")).to_contain_text("models")


def test_an_opened_group_is_still_open_after_a_reload(ui, ui_page):
    """The default is a starting point, not a policy: an operator who lives in one group opens
    it once. The stored list is the whole answer, so closing everything is remembered too —
    'no choice yet' and 'nothing open' are different states."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector(".rgroup-head")
    models = ui_page.locator(".rgroup", has=ui_page.locator(
        ".rgroup-title", has_text="Models & resources"))
    models.locator("summary.rgroup-head").click()
    expect(ui_page.locator("#sec-models")).to_be_visible()

    ui_page.reload()
    ui_page.wait_for_selector(".rgroup-head")
    assert sorted(_titles_open(ui_page)) == sorted([LEAD, "Models & resources"])

    # fold everything, and that is remembered as well
    ui_page.locator(".rgroup", has=ui_page.locator(
        ".rgroup-title", has_text="Models & resources")).locator("summary.rgroup-head").click()
    ui_page.locator(".rgroup", has=ui_page.locator(
        ".rgroup-title", has_text=LEAD)).locator("summary.rgroup-head").click()
    ui_page.reload()
    ui_page.wait_for_selector(".rgroup-head")
    assert _titles_open(ui_page) == []


def test_a_setup_fix_link_unfolds_the_group_it_points_into(ui, ui_page):
    """The one journey the fold could have broken. The setup check sits above the hero and its
    rows end in the act that settles them; the act's control lives in a config panel, which is
    now folded by default. `jumpToSection` opens every <details> on the way — without that the
    link would scroll to a heading with nothing under it."""
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
