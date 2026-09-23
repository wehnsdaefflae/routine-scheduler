"""Settings → Secrets: a needed secret shows the declaring util's usage + docstring under a
"format / help" expander, so a structured secret's shape (e.g. FTP_SOURCES) is discoverable where
you set it."""

import re

from playwright.sync_api import expect


def test_needed_secret_shows_format(ui, ui_page):
    util = ui.tmp / "library" / "utils" / "ftpdemo" / "main.py"
    util.parent.mkdir(parents=True, exist_ok=True)
    util.write_text(
        "# /// script\n# dependencies = []\n# ///\n"
        '"""ftpdemo — demo util.\n\n'
        "usage: gu ftpdemo --source NAME\ncalls: (none)\n"
        "secrets: FTP_SOURCES\ntags: test\nnet: outbound\nfs: roots\n\n"
        "FTP_SOURCES is a JSON map {name: {host, user, pass, port?, tls?, dir?}}.\n"
        '"""\n', encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/settings?section=secrets")
    fmt = ui_page.locator('[data-secret-fmt="FTP_SOURCES"]')
    fmt.wait_for()
    fmt.locator("summary").click()
    expect(fmt).to_contain_text("host, user, pass")     # the format hint from the util docstring


def test_optional_secret_shows_optional_not_unset(ui, ui_page):
    """D51: a secret every declaring util marks `NAME?` is OPTIONAL — when unset it reads a
    calm "optional", never the amber "unset" nag that a required-but-missing secret shows."""
    util = ui.tmp / "library" / "utils" / "optdemo" / "main.py"
    util.parent.mkdir(parents=True, exist_ok=True)
    util.write_text(
        "# /// script\n# dependencies = []\n# ///\n"
        '"""optdemo — demo util.\n\n'
        "usage: gu optdemo\ncalls: (none)\n"
        "secrets: OPT_DEMO_KEY?\ntags: test\nnet: none\nfs: roots\n"
        '"""\n', encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/settings?section=secrets")
    status = ui_page.locator('[data-secret-status="OPT_DEMO_KEY"]')
    status.wait_for()
    expect(status).to_have_text("optional")


def test_map_secret_entry_editor(ui, ui_page):
    """Add an entry to a JSON-map secret via the UI — it appears as a chip, values never shown."""
    ui_page.goto(f"{ui.url}/#/settings?section=secrets")
    # the form is a "+ add map entry" disclosure, like the endpoint and model add forms
    ui_page.locator('[data-add="map-entry"] summary').click()
    ui_page.wait_for_selector('[data-map-entry="key"]')
    ui_page.locator('[data-map-entry="key"]').fill("FTP_SOURCES")
    ui_page.locator('[data-map-entry="name"]').fill("acme")
    ui_page.locator('[data-map-entry="value"]').fill('{"host": "h", "user": "u", "pass": "p"}')
    ui_page.get_by_role("button", name="add / replace entry").click()
    expect(ui_page.locator('[data-map="FTP_SOURCES"]')).to_contain_text("acme")


def test_plain_secret_value_keeps_newlines(ui, ui_page):
    """The plain-secret value field is a TEXTAREA: a pasted multi-line value (an SSH
    private key destined for the machines section's key_var) must reach the store with
    its newlines intact — the old <input type=password> silently stripped them on paste
    (F149, operator report 2026-07-23). The store itself has round-tripped PEMs since
    0.85.2; the input element was the corruption point."""
    from rsched import secrets

    ui_page.goto(f"{ui.url}/#/settings?section=secrets")
    ui_page.wait_for_selector('textarea[placeholder="value"]')
    ui_page.get_by_placeholder("KEY (e.g. CLAUDE_CODE_OAUTH_TOKEN)").fill("TEST_PEM_KEY")
    ui_page.locator('textarea[placeholder="value"]').fill("line-one\nline-two")
    ui_page.locator('textarea[placeholder="value"]').locator("xpath=..") \
        .get_by_role("button", name="set", exact=True).click()
    expect(ui_page.locator("body")).to_contain_text("TEST_PEM_KEY")   # listed after save
    assert secrets.load_secrets()["TEST_PEM_KEY"] == "line-one\nline-two"


def test_the_secrets_table_leads_with_what_still_needs_a_value(ui, ui_page):
    """"What still needs a value?" is the only question this table answers, and the server
    hands the rows back alphabetically — so on the fleet eleven unset secrets sat scattered
    through fifty set ones over 2 000px. Three labelled, counted buckets: missing, optional,
    done. The sort is the whole assertion: alphabetically `AAA_OPTIONAL` precedes
    `ZZZ_REQUIRED`, and after bucketing it must not."""
    for name, decl in (("needy", "ZZZ_REQUIRED"), ("casual", "AAA_OPTIONAL?")):
        util = ui.tmp / "library" / "utils" / name / "main.py"
        util.parent.mkdir(parents=True, exist_ok=True)
        util.write_text(
            "# /// script\n# dependencies = []\n# ///\n"
            f'"""{name} — demo util.\n\nusage: gu {name}\ncalls: (none)\n'
            f"secrets: {decl}\ntags: test\nnet: none\nfs: roots\n"
            '"""\n', encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/settings?section=secrets")
    ui_page.wait_for_selector('[data-secret-status="ZZZ_REQUIRED"]')
    order = ui_page.evaluate(
        """() => [...document.querySelectorAll('table.list tr')]
             .map(r => r.classList.contains('subhead')
                    ? `HEAD:${r.textContent.trim()}`
                    : (r.querySelector('[data-secret-status]')?.dataset.secretStatus || ''))
             .filter(Boolean)""")
    assert any(o.startswith("HEAD:needs a value") for o in order), order
    assert order.index("ZZZ_REQUIRED") < order.index("AAA_OPTIONAL"), order
    # the bucket head carries its count, so the answer is readable without counting rows
    head = next(o for o in order if o.startswith("HEAD:needs a value"))
    assert re.search(r"needs a value · \d+", head), head
