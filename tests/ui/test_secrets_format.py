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
