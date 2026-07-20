"""Settings → Secrets: a needed secret shows the declaring util's usage + docstring under a
"format / help" expander, so a structured secret's shape (e.g. FTP_SOURCES) is discoverable where
you set it."""

from playwright.sync_api import expect


def test_needed_secret_shows_format(ui, ui_page):
    util = ui.tmp / "library" / "utils" / "ftpdemo" / "main.py"
    util.parent.mkdir(parents=True, exist_ok=True)
    util.write_text(
        "# /// script\n# dependencies = []\n# ///\n"
        '"""ftpdemo — demo util.\n\n'
        "usage: gu ftpdemo --source NAME\ncalls: (none)\n"
        "secrets: FTP_SOURCES\ntags: test\nnet: outbound\n\n"
        "FTP_SOURCES is a JSON map {name: {host, user, pass, port?, tls?, dir?}}.\n"
        '"""\n', encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/settings?section=secrets")
    fmt = ui_page.locator('[data-secret-fmt="FTP_SOURCES"]')
    fmt.wait_for(timeout=10_000)
    fmt.locator("summary").click()
    expect(fmt).to_contain_text("host, user, pass")     # the format hint from the util docstring


def test_map_secret_entry_editor(ui, ui_page):
    """Add an entry to a JSON-map secret via the UI — it appears as a chip, values never shown."""
    ui_page.goto(f"{ui.url}/#/settings?section=secrets")
    ui_page.wait_for_selector('[data-map-entry="key"]', timeout=10_000)
    ui_page.locator('[data-map-entry="key"]').fill("FTP_SOURCES")
    ui_page.locator('[data-map-entry="name"]').fill("acme")
    ui_page.locator('[data-map-entry="value"]').fill('{"host": "h", "user": "u", "pass": "p"}')
    ui_page.get_by_role("button", name="add / replace entry").click()
    expect(ui_page.locator('[data-map="FTP_SOURCES"]')).to_contain_text("acme")
