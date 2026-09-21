"""The shared browser's screen: a nav link and a permanent read-only preview.

The operator asked for both at once: "can't we make the novnc window into the browser
available as a link in the left major sidebar and provide a permanent read only preview in
the right minor sidebar?" — after opening the Tailscale port himself, which is why the
address is configuration (`browser_view_url`) and not a constant: whether that port is
reachable is a fact about the deployment.

The load-bearing case is the NEGATIVE one. Most instances never publish a screen, and a nav
link to a port nobody opened is worse than no link at all — so with no URL configured,
neither surface may appear.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect


def test_no_browser_screen_is_published_so_neither_surface_appears(ui, ui_page):
    """The default instance: the fixture config sets no browser_view_url."""
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator("#nav-browser")).to_be_hidden()
    expect(ui_page.locator("#browser-dock")).to_be_hidden()


def test_the_page_explains_itself_instead_of_showing_an_empty_frame(ui, ui_page):
    """Reaching #/browser directly with nothing published says what to do about it."""
    ui_page.goto(f"{ui.url}/#/browser")
    view = ui_page.locator("#view")
    expect(view).to_contain_text("No browser screen is published", timeout=10_000)
    expect(view).to_contain_text("websockify")
    expect(view.get_by_role("link", name="open Settings")).to_be_visible()
    expect(view.locator("iframe")).to_have_count(0)


def test_publishing_a_screen_reveals_the_nav_link_and_the_read_only_preview(ui, ui_page):
    """With a URL set, both surfaces appear — and the preview is inert by construction."""
    ui.server_cfg.browser_view_url = "http://127.0.0.1:6080/vnc.html"

    ui_page.goto(f"{ui.url}/#/routines")
    link = ui_page.locator("#nav-browser")
    expect(link).to_be_visible(timeout=10_000)
    expect(link).to_contain_text("Browser")

    dock = ui_page.locator("#browser-dock")
    expect(dock).to_be_visible()
    frame = dock.locator("iframe.browser-dock-frame")
    expect(frame).to_have_count(1)
    # read-only twice over: noVNC's own switch, and a stylesheet that swallows the click
    assert "view_only=1" in frame.get_attribute("src")
    assert frame.evaluate(
        "el => getComputedStyle(el).pointerEvents") == "none"
    expect(dock).to_contain_text("read-only")

    # the preview is a glance; the full screen is where the keyboard goes
    dock.get_by_role("link", name="Browser").click()
    expect(ui_page).to_have_url(re.compile(r"#/browser$"), timeout=10_000)
    expect(ui_page.locator("iframe.browser-screen")).to_have_count(1)


def test_both_surfaces_load_through_the_console_not_the_raw_upstream(ui, ui_page):
    """F527, the operator's blank screen over https.

    Neither frame may point at `browser_view_url` directly: an https console cannot open the
    `ws://` socket noVNC needs, so a direct embed is permanently blank with nothing saying
    why. Both must load from this origin, and both must hand noVNC a same-origin websocket
    `path` — that parameter is what decides the socket's scheme.
    """
    ui.server_cfg.browser_view_url = "http://198.51.100.9:6080/vnc.html"

    ui_page.goto(f"{ui.url}/#/routines")
    dock_frame = ui_page.locator("#browser-dock iframe.browser-dock-frame")
    expect(dock_frame).to_have_count(1, timeout=10_000)
    dock_src = dock_frame.get_attribute("src")
    assert dock_src.startswith("/browser-view/"), dock_src
    assert "198.51.100.9" not in dock_src            # never the raw upstream
    assert "path=browser-view%2Fwebsockify" in dock_src.replace("%2f", "%2F")
    assert "view_only=1" in dock_src                  # the rail stays read-only

    ui_page.goto(f"{ui.url}/#/browser")
    screen = ui_page.locator("iframe.browser-screen")
    expect(screen).to_have_count(1, timeout=10_000)
    screen_src = screen.get_attribute("src")
    assert screen_src.startswith("/browser-view/"), screen_src
    assert "198.51.100.9" not in screen_src
    assert "view_only=1" not in screen_src            # the full screen takes the keyboard
    # the upstream is still NAMED on the page, as the thing being relayed
    expect(ui_page.locator("#view")).to_contain_text("198.51.100.9")


def test_the_relay_actually_serves_the_frame_instead_of_a_401_body(ui, ui_page):
    """F530 — the test that was missing, and the reason the bug shipped.

    Every other test here asserts on the iframe's `src` ATTRIBUTE, which proves the wiring
    and nothing else. The operator saw `{"detail":"missing or invalid token"}` rendered
    INSIDE both frames: the URLs were perfectly correct and every request they made was
    refused, because an <iframe src> sends no header and noVNC builds its own asset URLs.

    So this one LOADS the relay path in the real browser, from the page that mints the pass,
    and looks at what comes back. It fails on the pre-0.362.0 code and passes after.
    """
    ui.server_cfg.browser_view_url = "http://127.0.0.1:1/vnc.html"   # dead upstream on purpose

    ui_page.goto(f"{ui.url}/#/browser")
    expect(ui_page.locator("iframe.browser-screen")).to_have_count(1, timeout=10_000)

    # the page has now minted the pass; ask for the relay exactly as the frame's sub-resources
    # do — same origin, cookies attached by the browser, no header and no query of our own
    probe = ui_page.evaluate(
        """async () => {
             const r = await fetch('/browser-view/app/ui.js', { credentials: 'same-origin' });
             return { status: r.status, body: (await r.text()).slice(0, 120) };
           }""")
    assert probe["status"] != 401, (
        f"the frame's own requests are refused — this is what the user sees: {probe['body']}")
    assert "missing or invalid token" not in probe["body"], probe
    # 502 is the right answer here: auth passed and the relay reached a deliberately dead
    # upstream. What matters is that the gate let it through.
    assert probe["status"] == 502, probe


def test_the_preview_can_be_collapsed_and_stays_collapsed(ui, ui_page):
    """It is permanent, not compulsory: the choice survives a reload."""
    ui.server_cfg.browser_view_url = "http://127.0.0.1:6080/vnc.html"
    ui_page.goto(f"{ui.url}/#/routines")
    dock = ui_page.locator("#browser-dock")
    expect(dock).to_be_visible(timeout=10_000)
    dock.get_by_role("button", name="hide").click()
    expect(dock.locator("iframe.browser-dock-frame")).to_be_hidden()

    ui_page.reload()
    dock = ui_page.locator("#browser-dock")
    expect(dock).to_be_visible(timeout=10_000)
    expect(dock.locator("iframe.browser-dock-frame")).to_be_hidden()
    expect(dock.get_by_role("button", name="show")).to_be_visible()
