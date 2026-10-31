"""The routine page SAYS a stale model role (F643, rsched/trials.config_problems).

A role naming a model this instance no longer serves kills every run of that routine at turn 0 —
`EndpointError: model 'Sonnet' is not in the catalog` out of `resolve`, rc=1, no finish, no
summary, nothing to read. The page's problem panel is where the operator meets it, so the claim
is tested by LOADING the page, not by reading the attribute that feeds it.

Rendered by the same panel as a trial's problem (`views/routine.js`, one `.panel.err` row per
string): the renderer is type-agnostic, which is what makes one composed list the whole change.
"""

import yaml
from playwright.sync_api import expect

PANEL = ".panel.err"


def _roles(ui, models):
    path = ui.routine_dir("uir") / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["models"] = models
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_a_stale_role_is_shown_on_the_routine_page(ui, ui_page):
    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.wait_for_selector(".page-head h1")
    expect(ui_page.locator(PANEL)).to_have_count(0)       # the catalog serves what it names

    _roles(ui, {"main": "Sonnet"})                        # the catalog was renamed underneath it
    ui_page.reload()
    ui_page.wait_for_selector(".page-head h1")
    expect(ui_page.locator(PANEL, has_text="models.main 'Sonnet'")).to_be_visible()
    expect(ui_page.locator(PANEL, has_text="not in the model catalog")).to_be_visible()


def test_a_role_the_catalog_serves_leaves_the_page_clean(ui, ui_page):
    _roles(ui, {"main": "m", "tool_call": "m"})
    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.wait_for_selector(".page-head h1")
    expect(ui_page.locator(PANEL)).to_have_count(0)
