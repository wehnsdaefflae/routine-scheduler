"""A conversation's rail lists its own scripts (F833, components/runscripts.js).

The operator, 2026-11-02: *"why do conversations not show the routine's scripts in the right
side bar? routine runs seem to do it"* — decided 2026-11-05: conversations hold scripts. The
section read the ROUTINES route alone, which 404s for every conversation, and swallowed the
404, so the rail was silently empty. Both of a conversation's rails now read its own route: the
conversation view's, and the run page's when the run is a conversation's.
"""

from __future__ import annotations

from playwright.sync_api import expect

from .helpers import start_conversation

SCRIPT = '"""profile-dir — size a directory tree.\n\nusage:\n  profile-dir <path>\n"""\n'


def _keep_script(conv_dir):
    (conv_dir / "scripts").mkdir(exist_ok=True)
    (conv_dir / "scripts" / "profile_dir.py").write_text(SCRIPT, encoding="utf-8")


def test_the_conversation_view_names_its_scripts(ui, ui_page):
    ui_page.set_viewport_size({"width": 1425, "height": 900})
    _slug, conv_dir = start_conversation(ui, ui_page, "Size my photo folders.")
    _keep_script(conv_dir)
    ui_page.reload()
    row = ui_page.locator("[data-run-script='profile_dir']")
    expect(row).to_be_visible()
    expect(row).to_contain_text("size a directory tree.")
    assert "profile-dir —" not in row.inner_text()   # the name is the heading already
    expect(ui_page.locator('.rail-cap[data-rail="scripts"]')).to_be_visible()


def test_a_conversation_without_scripts_gains_no_section(ui, ui_page):
    ui_page.set_viewport_size({"width": 1425, "height": 900})
    start_conversation(ui, ui_page, "Just chat.")
    expect(ui_page.locator(".rail-cap").first).to_be_visible()
    ui_page.wait_for_timeout(500)                  # the one scripts read has answered
    assert ui_page.locator('.rail-cap[data-rail="scripts"]').count() == 0


def test_a_conversation_run_page_names_its_scripts(ui, ui_page):
    ui_page.set_viewport_size({"width": 1425, "height": 900})
    slug, conv_dir = start_conversation(ui, ui_page, "Size my photo folders.")
    _keep_script(conv_dir)
    ui.seed_run(slug, "20261105-090000", "finished", summary="done", home=ui.conversations)
    ui_page.goto(f"{ui.url}/#/run/{slug}:20261105-090000")
    expect(ui_page.locator(".run-rail [data-run-script='profile_dir']")).to_be_visible()
