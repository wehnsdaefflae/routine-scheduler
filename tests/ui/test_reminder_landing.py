"""A reminder link lands on the reminder, not on the top of the development view (F832).

Operator, 2026-11-02: *"clicking the reminder link to the routine's health page should take us
straight to the reminder's anchor instead of the top of the healtzh page so we don't have to
find the respective reminder ourselves"*. The fleet census counted 169 reminder rows across the
instance, and Recipe health is the LAST section of `#/changes/<slug>` — below the outcome, the
cost panel, the weeks, the changes table and the model fit — so landing at the top meant
scrolling past all of that and then finding one row by eye.

The shape is the house's landing journey: the link carries `?rem=<id>`
(components/conceptlinks.js `reminderLink`), each caution row carries `data-rem`
(views/routine-health.js), and the view's first paint lands on that row with
settings-field.js `landOn` — folds opened, centred, flashed, held while the sections above fill
in. The health read is stubbed with forty reminders so the target sits far below the fold,
which is the condition the complaint is about; a row near the top would pass without landing.
"""
import json
import re

from playwright.sync_api import expect

WIDE = {"width": 1400, "height": 900}
MANY = 40


def _reminders(n=MANY):
    return [{"id": f"rem-{i:02d}", "scope": "local", "regex": f"^util:example-{i} ",
             "description": f"consequence {i}", "stats": {"fires": i}} for i in range(n)]


def _stub_health(ui_page, reminders):
    def health(route):
        response = route.fetch()
        body = response.json()
        body["cautions"] = {"reminders": reminders, "assists": [],
                            "labels": ["could_not", "would_have", "did", "didnt"]}
        route.fulfill(response=response, body=json.dumps(body))
    ui_page.route("**/api/routines/uir/health", health)


def test_a_reminder_deep_link_lands_on_its_row(ui, ui_page):
    _stub_health(ui_page, _reminders())
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes/uir?rem=rem-33")
    row = ui_page.locator('tr[data-rem="rem-33"]')
    expect(row).to_be_in_viewport()
    expect(row).to_have_class(re.compile(r"\bref-flash\b"))
    # the page really is long enough that this is a landing, not luck: the section heading at
    # the top of the view has been scrolled away
    expect(ui_page.locator("#sec-outcome")).not_to_be_in_viewport()
    # every row is addressable, not only the one asked for
    expect(ui_page.locator("tr[data-rem]")).to_have_count(MANY)


def test_a_plain_visit_does_not_scroll(ui, ui_page):
    """Without `?rem=` the view opens at its top — the landing is the link's, not the page's."""
    _stub_health(ui_page, _reminders())
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes/uir")
    expect(ui_page.locator("tr[data-rem]")).to_have_count(MANY)
    expect(ui_page.locator("#sec-outcome")).to_be_in_viewport()
    expect(ui_page.locator("tr.ref-flash")).to_have_count(0)


def test_a_reminder_no_longer_held_is_said_not_silently_ignored(ui, ui_page):
    _stub_health(ui_page, _reminders(3))
    ui_page.goto(f"{ui.url}/#/changes/uir?rem=rem-gone")
    expect(ui_page.locator("tr[data-rem]")).to_have_count(3)
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text(
        "reminder rem-gone is no longer held by uir")


def test_the_link_a_transcript_prints_reaches_the_row(ui, ui_page):
    """End to end: the href reminderLink builds is the address the view lands on."""
    _stub_health(ui_page, _reminders())
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    href = ui_page.evaluate("""async () => {
      const {reminderLink} = await import('/static/components/conceptlinks.js');
      const a = reminderLink('rem-29', 'uir');
      a.id = 'probe';
      document.getElementById('view').append(a);
      return a.getAttribute('href');
    }""")
    assert href == "#/changes/uir?rem=rem-29"
    ui_page.locator("#probe").click()
    expect(ui_page.locator('tr[data-rem="rem-29"]')).to_be_in_viewport()
