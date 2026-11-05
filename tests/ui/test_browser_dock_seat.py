"""The browser dock gives the screen back on `#/browser`, and takes it again on leaving.

The dock connects the first time it is both open and on screen and then KEEPS the connection:
folding hides the frame rather than hanging up, which is what the operator asked for ("connect
only when you first open it"). On `#/browser` that outlived its welcome. The dock HIDES there —
that page IS the screen, full size and interactive — so the console sat on a live viewer
connection to a preview nobody could see while the operator was looking at the real thing.

The operator's call (docs/designs.md, 2026-11-05, option b): the dock releases the connection
while `#/browser` is shown and dials again when he leaves it. **Folding keeps it**, which he
said in the same breath and which `test_folding_the_dock_keeps_the_screen` below holds.

WHAT THE MEASUREMENT FOUND, since the decision was taken against a premise these tests get to
correct: the screen is NOT a single seat, and `#/browser` was never refused one. x11vnc runs
with `-shared` (deploy/chrome-entrypoint.sh), so the RFB server admits simultaneous viewers,
and the relay opens an independent upstream socket per viewer with no seat accounting of any
kind (web/api_screen_view._socket / ._bridge). So there is no defect half to fix here — what
the release saves is a second live screen nobody is looking at, which is the half that stands.

WHY IT IS THE BROWSER'S OWN and not every dock's: `mountScreenDock` takes it as an option
(`releaseOnPage`), the way it takes `newTab`. The desktop dock's page is a fleet of CARDS, not
a screen, so it has nothing to hand over by leaving.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

#: An address with NOTHING behind it — port 1 is served by nothing, so the dock's probe is
#: refused at once instead of waiting out its timeout (tests/ui/test_browser_screen.py).
DEAD_SCREEN = "http://127.0.0.1:1/vnc.html"

#: What a live screen sends unprompted: RFB is server-first, and this banner is the one byte
#: `components/screen.js:relayReachable` waits for before it mounts a frame.
RFB_BANNER = b"RFB 003.008\n"


def _screen_dials(page) -> list[str]:
    """Every socket the page opens to the relayed screen, as it opens one.

    The dock probes before it mounts anything and only a reachable screen gets a frame, so a
    dial IS this console taking a viewer connection — which is what the release gives back.
    """
    sockets: list[str] = []
    page.on("websocket",
            lambda ws: sockets.append(ws.url) if "/browser-view/websockify" in ws.url else None)
    return sockets


def test_the_dock_hangs_up_on_the_browser_page_and_dials_again_on_leaving(ui, ui_page):
    """The journey the decision is about, read off what the dock SHOWS and what it dials."""
    ui.server_cfg.browser_view_url = DEAD_SCREEN
    ui_page.set_viewport_size({"width": 1960, "height": 950})       # wide: it rests open
    sockets = _screen_dials(ui_page)
    ui_page.goto(f"{ui.url}/#/routines")
    dock = ui_page.locator("#browser-dock")
    expect(dock.locator(".sd-note")).to_contain_text("screen unreachable")
    assert len(sockets) == 1, sockets

    # Onto the full page. The dock hides — and keeps nothing of the screen open behind that.
    ui_page.evaluate("() => { location.hash = '#/browser'; }")
    expect(dock).to_be_hidden()
    expect(ui_page.locator("iframe.browser-screen")).to_have_count(1)   # the page has the screen
    expect(dock.locator("iframe.sd-frame")).to_have_count(0)
    expect(dock.locator(".sd-note")).to_have_count(0)

    # And off it again: the dock dials a SECOND time, which is the half the old dock lacked —
    # it kept its first connection for the life of the tab.
    ui_page.evaluate("() => { location.hash = '#/routines'; }")
    expect(dock).to_be_visible()
    expect(dock.locator(".sd-note")).to_contain_text("screen unreachable")
    assert len(sockets) == 2, f"the dock never dialled again after releasing the screen: {sockets}"


def test_a_connected_dock_gives_up_its_frame_and_gets_it_back(ui, ui_page):
    """The connection IS the frame: an iframe with a screen in it is what holds a viewer, so
    the release has to take it out of the document — hiding the dock leaves it streaming.

    The fixture's relay has nothing behind it, so the probe is answered here with the version
    banner a live screen sends, which is what makes the dock mount a real frame to give up.
    """
    ui.server_cfg.browser_view_url = DEAD_SCREEN
    ui_page.route_web_socket(re.compile(r"/browser-view/websockify"),
                             lambda ws: ws.send(RFB_BANNER))
    ui_page.set_viewport_size({"width": 1960, "height": 950})
    ui_page.goto(f"{ui.url}/#/routines")
    dock = ui_page.locator("#browser-dock")
    expect(dock.locator("iframe.sd-frame")).to_have_count(1)
    expect(dock).to_contain_text("read-only")

    ui_page.evaluate("() => { location.hash = '#/browser'; }")
    expect(dock.locator("iframe.sd-frame")).to_have_count(0)
    expect(dock.locator(".sd-body > *")).to_have_count(0)   # nothing left streaming behind it

    ui_page.evaluate("() => { location.hash = '#/routines'; }")
    expect(dock.locator("iframe.sd-frame")).to_have_count(1)


def test_folding_the_dock_keeps_the_screen(ui, ui_page):
    """Explicitly NOT what the release is, and he said so choosing it: "folding keeps the
    seat". A folded dock is the operator on another page with the preview put away for a
    moment; hanging up there would cost a reconnect every time he tidies the corner.
    """
    ui.server_cfg.browser_view_url = DEAD_SCREEN
    ui_page.set_viewport_size({"width": 1960, "height": 950})
    sockets = _screen_dials(ui_page)
    ui_page.goto(f"{ui.url}/#/routines")
    dock = ui_page.locator("#browser-dock")
    expect(dock.locator(".sd-note")).to_contain_text("screen unreachable")
    assert len(sockets) == 1, sockets

    dock.get_by_role("button", name="hide").click()
    expect(dock).to_have_class(re.compile(r"\bsd-collapsed\b"))
    # what it found stays found: the body is still there, folded away rather than hung up
    expect(dock.locator(".sd-note")).to_contain_text("screen unreachable")
    dock.get_by_role("button", name="show").click()
    expect(dock).not_to_have_class(re.compile(r"\bsd-collapsed\b"))
    ui_page.wait_for_timeout(400)
    assert len(sockets) == 1, f"folding hung up on the screen and re-dialled: {sockets}"


def test_a_dock_that_never_connected_has_nothing_to_release(ui, ui_page):
    """Opening the console straight onto `#/browser` folded: the release must not turn into a
    dial of its own. Leaving the page is still the dock's FIRST connection, not a second one.
    """
    ui.server_cfg.browser_view_url = DEAD_SCREEN
    ui_page.set_viewport_size({"width": 1440, "height": 900})       # narrow: it rests folded
    sockets = _screen_dials(ui_page)
    ui_page.goto(f"{ui.url}/#/browser")
    dock = ui_page.locator("#browser-dock")
    expect(dock).to_be_hidden()
    ui_page.wait_for_timeout(800)
    assert sockets == [], f"the hidden dock dialled the screen: {sockets}"

    ui_page.evaluate("() => { location.hash = '#/routines'; }")
    expect(dock.get_by_role("button", name="show")).to_be_visible()
    ui_page.wait_for_timeout(800)
    assert sockets == [], f"leaving the page dialled a folded dock: {sockets}"
