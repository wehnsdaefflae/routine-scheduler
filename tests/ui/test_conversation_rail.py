"""The conversation right rail against the REAL console: the browser-session section
(D86 / R262 pt2 — rows from the persisted util handle, blob-rendered screenshot, close
control hitting the stop endpoint) and the per-section collapse toggles with localStorage
persistence (F296 / R262 pt1)."""

from __future__ import annotations

import json
import socket

from playwright.sync_api import expect

# a 1x1 transparent PNG, byte-for-byte
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8ffff3f0300050001a5f645400000000049454e44ae426082")


def _start_conversation(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Drive a browser for me.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    return slug, ui.conversations / slug


def _write_handle(conv_dir, *, port: int, pid: int = 999_999_999) -> None:
    state = conv_dir / "state"
    state.mkdir(exist_ok=True)
    (state / "browser-view.png").write_bytes(PNG)
    (state / "browser-session.json").write_text(json.dumps({
        "cdp": f"http://127.0.0.1:{port}", "host": "127.0.0.1", "port": port,
        "pid": pid, "url": "https://example.com", "name": "default",
        "view": "state/browser-view.png", "started": 1754700000.0}), encoding="utf-8")


def test_browser_section_renders_and_close_clears_session(ui, ui_page):
    """With a live-looking handle (a really-listening port) the rail grows a 'browser'
    section: url line, screenshot, and a ✕ that hits the stop endpoint — after which the
    handle is gone and the section hides again."""
    _slug, conv_dir = _start_conversation(ui, ui_page)
    cap = ui_page.locator(".conv-view .rail-cap", has_text="browser")
    expect(cap).to_be_hidden()   # no session yet

    # a listening socket makes the liveness probe (one TCP connect) report alive=True,
    # which is what arms the close control
    srv = socket.socket()
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        _write_handle(conv_dir, port=srv.getsockname()[1])
        ui_page.reload()

        expect(cap).to_be_visible()
        expect(ui_page.locator(".browser-line")).to_contain_text("https://example.com")
        # the screenshot arrives via an authed fetch -> blob URL, never a bare <img src>
        shot = ui_page.locator(".browser-shot")
        expect(shot).to_be_visible()
        assert shot.evaluate("el => el.src.startsWith('blob:')")

        ui_page.locator(".browser-sess .bg-cancel").click()
        # the stop endpoint deletes the model-written handle (the fake pid kills nothing)
        expect(cap).to_be_hidden()
        assert not (conv_dir / "state" / "browser-session.json").exists()
    finally:
        srv.close()


def test_rail_sections_collapse_and_persist(ui, ui_page):
    """F296: a rail cap is a toggle — clicking collapses just that section, the choice
    sticks in localStorage across a full reload, and clicking again reopens it. R341: the
    key is `rail:<name>` (not `convrail:`) because the run view renders the SAME component
    — a fold meant in one view is meant in the other."""
    _start_conversation(ui, ui_page)
    cap = ui_page.locator(".conv-view .rail-cap", has_text="state").first
    graph = ui_page.locator(".stategraph")
    expect(graph).to_be_visible()

    cap.click()
    expect(graph).to_be_hidden()
    assert ui_page.evaluate("localStorage.getItem('rail:state')") == "closed"

    ui_page.reload()
    expect(ui_page.locator(".stategraph")).to_be_hidden()

    ui_page.locator(".conv-view .rail-cap", has_text="state").first.click()
    expect(ui_page.locator(".stategraph")).to_be_visible()
    assert ui_page.evaluate("localStorage.getItem('rail:state')") == "open"
