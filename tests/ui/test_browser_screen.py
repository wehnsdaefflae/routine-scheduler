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

#: An address with NOTHING behind it, which is the state most of this file is written about.
#: Port 1 is served by nothing, so the relay's dial is refused at once and the dock settles on
#: "unreachable" without waiting out a timeout. It must NOT be the fleet's own 6080: this repo's
#: deployment publishes a live noVNC there, so a test that meant "no screen" silently became
#: "whatever screen this host happens to run" — and the unreachable dock, which is what the
#: negative case exists to prove, never rendered on the box the suite runs on.
DEAD_SCREEN = "http://127.0.0.1:1/vnc.html"


def test_no_browser_screen_is_published_so_neither_surface_appears(ui, ui_page):
    """The default instance: the fixture config sets no browser_view_url."""
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator("#nav-browser")).to_be_hidden()
    expect(ui_page.locator("#browser-dock")).to_be_hidden()


def test_the_page_explains_itself_instead_of_showing_an_empty_frame(ui, ui_page):
    """Reaching #/browser directly with nothing published says what to do about it."""
    ui_page.goto(f"{ui.url}/#/browser")
    view = ui_page.locator("#view")
    expect(view).to_contain_text("No browser screen is published")
    expect(view).to_contain_text("websockify")
    expect(view.get_by_role("link", name="open Settings")).to_be_visible()
    expect(view.locator("iframe")).to_have_count(0)


def test_publishing_a_screen_reveals_the_nav_link_and_the_preview(ui, ui_page):
    """With a URL set, both surfaces appear. The fixture publishes an ADDRESS with nothing
    behind it, which is a real state and the one the fleet was in — so the dock says the screen
    is unreachable rather than mounting a frame that paints noVNC's red banner on every page.

    The read-only property is asserted where it lives: the ONE url builder both surfaces share,
    and a stylesheet rule that swallows a click on any dock frame.
    """
    ui.server_cfg.browser_view_url = DEAD_SCREEN

    ui_page.goto(f"{ui.url}/#/routines")
    link = ui_page.locator("#nav-browser")
    expect(link).to_be_visible()
    expect(link).to_contain_text("Browser")

    dock = ui_page.locator("#browser-dock")
    expect(dock).to_be_visible()
    # read-only twice over: noVNC's own switch, and a stylesheet that swallows the click
    src = ui_page.evaluate(
        "async () => (await import('/static/views/browser.js')).frameSrc({ viewOnly: true })")
    assert "view_only=1" in src, src
    inert = ui_page.evaluate(
        """() => { const f = document.createElement('iframe');
                   f.className = 'browser-dock-frame';
                   document.getElementById('browser-dock').append(f);
                   const pe = getComputedStyle(f).pointerEvents;
                   f.remove(); return pe; }""")
    assert inert == "none", f"a dock frame is steerable: pointer-events is {inert}"

    # the preview is a glance; the full screen is where the keyboard goes
    dock.get_by_role("link", name="Browser").click()
    expect(ui_page).to_have_url(re.compile(r"#/browser$"))
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
    # The dock mounts a frame only once the relay has proved it has a screen behind it (the
    # fixture's upstream is an address with nothing on it), so the rail's URL is asserted on the
    # builder both surfaces share — which is the single copy this test exists to protect.
    dock_src = ui_page.evaluate(
        "async () => (await import('/static/views/browser.js')).frameSrc({ viewOnly: true })")
    assert dock_src.startswith("/browser-view/"), dock_src
    assert "198.51.100.9" not in dock_src            # never the raw upstream
    assert "path=browser-view%2Fwebsockify" in dock_src.replace("%2f", "%2F")
    assert "view_only=1" in dock_src                  # the rail stays read-only

    ui_page.goto(f"{ui.url}/#/browser")
    screen = ui_page.locator("iframe.browser-screen")
    expect(screen).to_have_count(1)
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
    ui.server_cfg.browser_view_url = DEAD_SCREEN            # dead upstream on purpose

    ui_page.goto(f"{ui.url}/#/browser")
    expect(ui_page.locator("iframe.browser-screen")).to_have_count(1)

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
    """It is permanent, not compulsory: the choice survives a reload — at the widths where
    resting open is a choice at all."""
    ui.server_cfg.browser_view_url = DEAD_SCREEN
    ui_page.set_viewport_size({"width": 1960, "height": 950})
    ui_page.goto(f"{ui.url}/#/routines")
    dock = ui_page.locator("#browser-dock")
    expect(dock).to_be_visible()
    expect(dock).not_to_have_class(re.compile(r"\bbd-collapsed\b"))
    dock.get_by_role("button", name="hide").click()
    expect(dock).to_have_class(re.compile(r"\bbd-collapsed\b"))

    ui_page.reload()
    dock = ui_page.locator("#browser-dock")
    expect(dock).to_be_visible()
    expect(dock).to_have_class(re.compile(r"\bbd-collapsed\b"))
    expect(dock.get_by_role("button", name="show")).to_be_visible()


def test_the_preview_rests_collapsed_where_it_would_lie_on_the_content(ui, ui_page):
    """The dock is `position: fixed` against the right edge, and the reading column is
    `--rail-w` 212 + `--shell-max` 1240 = 1452px wide — so between 861px and 1900px an open dock
    lies ON the page. On a 1440px laptop it covered the first five lane rows' run-now / pause /
    ✎ buttons on Routines and the nano-gpt card's `save key` row in Settings, and the only way
    past it was the four-point `hide` in its own title bar.

    So below the width the side TOC uses as its own "is there a margin?" gate it rests collapsed,
    one click opens it as a transient overlay, and leaving the page folds it again.
    """
    ui.server_cfg.browser_view_url = DEAD_SCREEN
    ui_page.set_viewport_size({"width": 1440, "height": 900})
    ui_page.goto(f"{ui.url}/#/routines")
    dock = ui_page.locator("#browser-dock")
    expect(dock).to_be_visible()
    expect(dock).to_have_class(re.compile(r"\bbd-collapsed\b"))

    dock.locator(".bd-toggle").click()
    expect(dock).not_to_have_class(re.compile(r"\bbd-collapsed\b"))
    # transient: a narrow console's dock is an overlay over live controls, so remembering it
    # open would park it on the Routines page's run-now column for good
    ui_page.evaluate("() => { location.hash = '#/settings'; }")
    expect(dock).to_have_class(re.compile(r"\bbd-collapsed\b"))


def test_an_unreachable_screen_is_a_note_not_a_red_vnc_frame(ui, ui_page):
    """noVNC's document, its assets and its chrome all LOAD when the screen behind the relay is
    down — and the relay ACCEPTS the socket before it dials websockify, so even the handshake
    succeeds. Only a BYTE settles it: RFB is server-first, so a live screen sends its version
    banner at once and a dead one sends nothing before the close (the 1006 noVNC reports).

    Without that, the dock pinned a red "Failed to connect to server" block, noVNC's dark
    toolbar and an inert Connect button — unpressable, since the frame is pointer-events:none —
    to every page of the console, in the colour reserved for what waits on a person.
    """
    ui.server_cfg.browser_view_url = DEAD_SCREEN
    ui_page.set_viewport_size({"width": 1960, "height": 950})
    ui_page.goto(f"{ui.url}/#/routines")
    dock = ui_page.locator("#browser-dock")
    expect(dock.locator(".bd-note")).to_contain_text("screen unreachable")
    expect(dock.locator("iframe.browser-dock-frame")).to_have_count(0)
    # and the reader is left somewhere to go, which the pinned error never was
    expect(dock.get_by_role("link", name="open the full page")).to_have_attribute(
        "href", "#/browser")


def test_the_preview_gets_out_of_the_way_on_the_browser_page(ui, ui_page):
    """#/browser IS this screen, full size and interactive. Mirroring it in the corner showed
    the operator the same failing frame twice — and its breadcrumb said "Conversations", because
    `crumbsFor` had no case for the route and the default named the wrong page."""
    ui.server_cfg.browser_view_url = DEAD_SCREEN
    ui_page.set_viewport_size({"width": 1440, "height": 900})
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator("#browser-dock")).to_be_visible()
    ui_page.evaluate("() => { location.hash = '#/browser'; }")
    expect(ui_page.locator("#browser-dock")).to_be_hidden()
    expect(ui_page.locator("#crumbs")).to_have_text("Browser")
