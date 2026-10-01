"""The Library's requires panel offers exactly what a permission doc may require.

The panel kept its own list: it offered memory_read/memory_write (base kinds every routine
holds — the PUT refused them with a 422) and a "previous runs" select (a SETTING the routine's
owner chooses, which a doc may never require — refused too), and it left out the capabilities
the server does accept (shell, write_rule, schedule_run, …). It now offers the server's own
list, sent by /api/library as `capability_actions` (grants.CAPABILITY_ACTIONS).
"""

from __future__ import annotations

import frontmatter
from playwright.sync_api import expect

from rsched.grants import CAPABILITY_ACTIONS

from .conftest import until

# A doc the linter accepts; its `requires:` is the panel's to write on save.
DOC = """---
effect:
  with: runs the diagnostic probe command on the host when asked
  without: answers from what the shared utils report instead
  when: a person asks for a diagnostic probe of the host itself
tags: [test]
requires: {}
---
# permission: needs-shell — a probe

Run the probe only when asked.
"""


def test_the_requires_panel_offers_what_the_server_accepts(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/library")
    ui_page.get_by_role("button", name="+ new permission").click()
    panel = ui_page.locator(".panel", has_text="requires — the capabilities")
    expect(panel).to_be_visible()
    gated = panel.locator("div", has=ui_page.locator("> .muted.small", has_text="gated actions"))
    expect(gated.locator("label")).to_have_text(list(CAPABILITY_ACTIONS))
    expect(panel).not_to_contain_text("previous runs")
    assert panel.locator("select").count() == 0

    gated.locator("label", has_text="shell").locator("input").check()
    ui_page.get_by_placeholder("kebab-case-slug").fill("needs-shell")
    ui_page.locator("#view textarea.code").fill(DOC)
    ui_page.get_by_role("button", name="save + commit").click()
    path = ui.tmp / "library" / "permissions" / "needs-shell.md"
    until(path.exists, what="the saved permission doc", page=ui_page)
    assert frontmatter.loads(path.read_text(encoding="utf-8")).metadata["requires"] == {
        "actions": ["shell"]}
