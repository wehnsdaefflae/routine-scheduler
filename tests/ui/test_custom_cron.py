"""The schedule editor's CUSTOM cadence — the one a reader writes as cron.

It was a read-only display: a stored cron the friendly choices could not express showed as
text with "pick one to replace it", so "06:00 and 14:00" could be kept or thrown away but never
changed, and the routine PATCH refused the custom spec a page save sent back. Custom is now a
choice like the others: its field edits the cron, the accept stores it as written, and entering
it from another cadence starts from that cadence's cron.
"""

from playwright.sync_api import expect

from .conftest import until
from .helpers import accept, configure, stored_config, visible_toast

FREQ = "#sec-schedule + .panel div.row > select"
CRON = "#sec-schedule + .panel [data-custom-cron]"


def _open(ui, ui_page, cron: str) -> None:
    configure(ui, schedule={"cron": cron, "tz": "Europe/Berlin", "catchup": "skip"})
    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.wait_for_selector(FREQ)


def test_a_stored_custom_cron_is_edited_in_place(ui, ui_page):
    _open(ui, ui_page, "0 6,14 * * *")
    expect(ui_page.locator(FREQ).first).to_have_value("custom")
    field = ui_page.locator(CRON)
    expect(field).to_have_value("0 6,14 * * *")

    field.fill("30 6,14 * * 1-5")
    accept(ui_page)
    expect(visible_toast(ui_page)).to_contain_text("accepted")
    until(lambda: stored_config(ui)["schedule"]["cron"] == "30 6,14 * * 1-5",
          what="the custom cron save")

    ui_page.reload()
    expect(ui_page.locator(CRON)).to_have_value("30 6,14 * * 1-5")


def test_custom_starts_from_the_cadence_it_replaces(ui, ui_page):
    """Daily at 06:00 becomes `0 6 * * *` to extend, not a blank field written from memory."""
    _open(ui, ui_page, "0 6 * * *")
    freq = ui_page.locator(FREQ).first
    expect(freq).to_have_value("daily")
    freq.select_option("custom")
    field = ui_page.locator(CRON)
    expect(field).to_have_value("0 6 * * *")

    field.fill("0 6,14 * * *")
    accept(ui_page)
    until(lambda: stored_config(ui)["schedule"]["cron"] == "0 6,14 * * *",
          what="the custom cron save")


def test_a_cron_the_server_refuses_is_said_and_not_stored(ui, ui_page):
    _open(ui, ui_page, "0 6,14 * * *")
    field = ui_page.locator(CRON)
    field.fill("0 6 * *")
    expect(field).to_have_attribute("aria-invalid", "true")       # four fields, said at once
    field.fill("0 25 * * *")                                      # five fields, hour 25
    expect(field).to_have_attribute("aria-invalid", "false")
    accept(ui_page)
    expect(visible_toast(ui_page)).to_contain_text("cron")
    ui_page.wait_for_timeout(300)
    assert stored_config(ui)["schedule"]["cron"] == "0 6,14 * * *"
