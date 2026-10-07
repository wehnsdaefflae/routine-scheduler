"""The agent desktops in the console: the Desktops page (the take-over surface) and the desktop
dock (the read-only glance), beside the browser's two.

The broker is faked at its one outbound hop (`helpers.run_desktops`), so the console's real
`/api/desktops` route, its owner mapping and its token filter all run; the screens themselves
point at an address with nothing behind it, which is how most of these tests watch WHAT the
console dials without needing a VM.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from .helpers import confirm_modal, desktop, run_desktops, visible_toast

ALPHA = "a" * 32
BETA = "b" * 32
DATA = {"name": "data", "path": "/home/mark/data", "rw": False,
        "guest_path": "/home/mark/mnt/data"}


def _dials(page) -> list[str]:
    """Every socket the page opens to a desktop screen — a dock's probe IS its dial."""
    sockets: list[str] = []
    page.on("websocket",
            lambda ws: sockets.append(ws.url) if "/desktop-view/websockify" in ws.url else None)
    return sockets


def test_an_instance_without_desktops_shows_no_door_to_them(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator("table.list")).to_be_visible()
    expect(ui_page.locator("#nav-desktops")).to_be_hidden()
    expect(ui_page.locator("#desktop-dock")).to_be_hidden()

    ui_page.goto(f"{ui.url}/#/desktops")
    view = ui_page.locator("#view")
    expect(view).to_contain_text("No agent desktops are configured")
    expect(view.locator("iframe")).to_have_count(0)
    view.get_by_role("link", name="open Settings").click()
    expect(ui_page.locator("#sec-server")).to_be_in_viewport()


def test_with_nothing_running_the_link_shows_and_the_dock_does_not(ui, ui_page, monkeypatch):
    run_desktops(ui, monkeypatch)
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator("#nav-desktops")).to_be_visible()
    ui_page.wait_for_timeout(600)                   # the first fleet read has landed by now
    expect(ui_page.locator("#desktop-dock")).to_be_hidden()

    ui_page.locator("#nav-desktops").click()
    expect(ui_page.locator("#view")).to_contain_text("No desktop is running")
    expect(ui_page.locator("#crumbs")).to_have_text("Desktops")


def test_each_running_desktop_is_a_card_with_its_own_interactive_screen(ui, ui_page,
                                                                       monkeypatch):
    run_desktops(ui, monkeypatch, desktop("routines--uir", ALPHA, folders=[DATA]),
                 desktop("conversations--chat", BETA, idle_s=60))
    ui_page.goto(f"{ui.url}/#/desktops")
    cards = ui_page.locator(".desktop-card")
    expect(cards).to_have_count(2)

    uir = ui_page.locator('.desktop-card[data-desktop="routines--uir"]')
    expect(uir.get_by_role("link", name="uir")).to_have_attribute("href", "#/routine/uir")
    expect(uir).to_contain_text("data (read-only) → /home/mark/mnt/data")
    expect(uir.locator(".chip")).to_have_text("running")
    src = uir.locator("iframe.desktop-screen").get_attribute("src")
    assert src.startswith("/desktop-view/vnc.html?"), src
    # the screen token picks the desktop, on the SOCKET path noVNC opens — and nothing about the
    # upstream's own address is in the page
    assert f"path=desktop-view%2Fwebsockify%3Ftoken%3D{ALPHA}" in src, src
    assert "view_only" not in src                    # this page takes the keyboard
    assert "127.0.0.1:1" not in src

    chat = ui_page.locator('.desktop-card[data-desktop="conversations--chat"]')
    expect(chat.get_by_role("link", name="chat · conversation")).to_have_attribute(
        "href", "#/conversations/chat")
    # the page IS the screens: the corner preview gets out of the way here
    expect(ui_page.locator("#desktop-dock")).to_be_hidden()


def test_a_poll_keeps_the_screens_and_drops_only_what_stopped(ui, ui_page, monkeypatch):
    """Re-creating (or even moving) an <iframe> reloads it — the VNC connection would drop
    under the operator's hands at every poll. A card is reconciled in place; only a desktop
    that went away loses its card."""
    broker = run_desktops(ui, monkeypatch, desktop("routines--uir", ALPHA),
                          desktop("routines--two", BETA))
    ui_page.goto(f"{ui.url}/#/desktops")
    expect(ui_page.locator(".desktop-card")).to_have_count(2)
    ui_page.evaluate("() => { document.querySelector("
                     "'[data-desktop=\"routines--uir\"] iframe.desktop-screen').dataset.mark = 'x'; }")

    broker.desktops = [d for d in broker.desktops if d["name"] == "routines--uir"]
    expect(ui_page.locator(".desktop-card")).to_have_count(1, timeout=12_000)
    expect(ui_page.locator("iframe.desktop-screen[data-mark='x']")).to_have_count(1)


def test_stop_asks_first_then_powers_the_desktop_off(ui, ui_page, monkeypatch):
    broker = run_desktops(ui, monkeypatch, desktop("routines--uir", ALPHA))
    ui_page.goto(f"{ui.url}/#/desktops")
    card = ui_page.locator('.desktop-card[data-desktop="routines--uir"]')
    card.get_by_role("button", name="stop").click()
    dialog = ui_page.get_by_role("dialog")
    expect(dialog).to_contain_text("Stop uir's desktop?")
    dialog.get_by_role("button", name="cancel").click()
    assert broker.stopped == []

    card.get_by_role("button", name="stop").click()
    confirm_modal(ui_page, "stop")
    expect(visible_toast(ui_page)).to_contain_text("desktop stopped")
    assert broker.stopped == ["routines--uir"]
    expect(ui_page.locator("#view")).to_contain_text("No desktop is running")


def test_the_dock_previews_the_busiest_desktop_read_only(ui, ui_page, monkeypatch):
    run_desktops(ui, monkeypatch, desktop("routines--uir", ALPHA, idle_s=2))
    ui_page.set_viewport_size({"width": 1960, "height": 1000})
    sockets = _dials(ui_page)
    ui_page.goto(f"{ui.url}/#/routines")
    dock = ui_page.locator("#desktop-dock")
    expect(dock).to_be_visible()
    expect(dock.locator(".sd-which")).to_have_text("uir")
    # wide: it rests open, so it dials at load — with THIS desktop's token
    expect(dock.locator(".sd-note")).to_contain_text("screen unreachable")
    assert len(sockets) == 1 and sockets[0].endswith(f"token={ALPHA}"), sockets
    inert = ui_page.evaluate(
        """() => { const f = document.createElement('iframe'); f.className = 'sd-frame';
                   document.getElementById('desktop-dock').append(f);
                   const pe = getComputedStyle(f).pointerEvents; f.remove(); return pe; }""")
    assert inert == "none", f"a dock frame is steerable: pointer-events is {inert}"
    src = ui_page.evaluate("async () => (await import('/static/components/screen.js'))"
                           f".screenSrc('desktop-view', {{ viewOnly: true, token: '{ALPHA}' }})")
    assert "view_only=1" in src, src

    dock.get_by_role("link", name="Desktop").click()
    expect(ui_page).to_have_url(re.compile(r"#/desktops$"))
    expect(dock).to_be_hidden()


def test_a_folded_dock_dials_nothing_until_it_is_opened(ui, ui_page, monkeypatch):
    run_desktops(ui, monkeypatch, desktop("routines--uir", ALPHA))
    ui_page.set_viewport_size({"width": 1440, "height": 900})
    sockets = _dials(ui_page)
    ui_page.goto(f"{ui.url}/#/routines")
    dock = ui_page.locator("#desktop-dock")
    expect(dock.get_by_role("button", name="show")).to_be_visible()
    ui_page.wait_for_timeout(800)
    assert sockets == [], f"a folded dock dialled a desktop: {sockets}"
    dock.get_by_role("button", name="show").click()
    expect(dock.locator(".sd-note")).to_contain_text("screen unreachable")
    assert len(sockets) == 1, sockets


def test_several_desktops_get_a_switcher_and_switching_redials(ui, ui_page, monkeypatch):
    run_desktops(ui, monkeypatch, desktop("routines--uir", ALPHA, idle_s=1),
                 desktop("routines--two", BETA, idle_s=90))
    ui_page.set_viewport_size({"width": 1960, "height": 1000})
    sockets = _dials(ui_page)
    ui_page.goto(f"{ui.url}/#/routines")
    dock = ui_page.locator("#desktop-dock")
    pick = dock.locator("select.sd-switch")
    expect(pick).to_have_value("routines--uir")       # the most recently active one
    expect(dock.locator(".sd-note")).to_contain_text("screen unreachable")
    pick.select_option("routines--two")
    expect(dock.locator(".sd-note")).to_contain_text("screen unreachable")
    assert [s.rsplit("=", 1)[1] for s in sockets] == [ALPHA, BETA], sockets


def test_the_dock_leaves_when_the_last_desktop_stops(ui, ui_page, monkeypatch):
    broker = run_desktops(ui, monkeypatch, desktop("routines--uir", ALPHA))
    ui_page.set_viewport_size({"width": 1960, "height": 1000})
    ui_page.goto(f"{ui.url}/#/routines")
    dock = ui_page.locator("#desktop-dock")
    expect(dock).to_be_visible()
    broker.desktops = []
    expect(dock).to_be_hidden(timeout=12_000)
    # and it hung up: nothing stays connected behind a hidden dock
    expect(dock.locator(".sd-body > *")).to_have_count(0)
