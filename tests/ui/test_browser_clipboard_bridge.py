"""The browser screen's clipboard actually SYNCS, both ways, when the switch is on (F831).

The operator, 2026-11-02: *"the host to client clipboard sync for the sidecar browser does not
work"*. D221's switch set the iframe's `allow="clipboard-read; clipboard-write"` and its four
tests (tests/ui/test_browser_clipboard_toggle.py) assert that CONTROL against a dead screen — so
the suite could not tell "the clipboard syncs" from "the frame is permitted to sync it". It did
not sync: neither noVNC the sidecars serve (Debian bookworm's 1.3.0 for the browser, 1.6.0 for
the desktops) calls `navigator.clipboard` anywhere. Their clipboard is a manual side panel — the
textarea `#noVNC_clipboard_text`, whose `change` event sends its text to the guest
(`UI.clipboardSend` → `rfb.clipboardPasteFrom`, app/ui.js) and whose value noVNC overwrites with
a plain `.value =` when the guest copies (`UI.clipboardReceive`).

`components/clipboard-bridge.js` drives that panel from the embedder. These tests mount, in place
of the relayed `vnc.html`, a page carrying EXACTLY that contract — the panel's id, a `change`
listener standing in for `clipboardSend`, and a `receive()` doing what `clipboardReceive` does —
and drive the real console page and the real clipboard of the browser under test. What they do
NOT do is run an RFB stream: the uitest container has no VNC server, so "the guest's X selection
then holds it" rests on x11vnc's own relay (deploy/chrome-entrypoint.sh: no `-nosel` /
`-noclipboard`), which was never in doubt. The defect was the missing call, and that is what is
proven here.
"""
from __future__ import annotations

from playwright.sync_api import expect

from .conftest import until

DEAD_SCREEN = "http://127.0.0.1:1/vnc.html"

#: noVNC's clipboard contract, and nothing else of noVNC.
_FAKE_NOVNC = """<!doctype html><html><body>
<textarea id="noVNC_clipboard_text" rows=5></textarea>
<script>
  window.sent = [];
  document.getElementById('noVNC_clipboard_text')
    .addEventListener('change', (e) => window.sent.push(e.target.value));   // UI.clipboardSend
  window.receive = (text) => {                                                 // UI.clipboardReceive
    document.getElementById('noVNC_clipboard_text').value = text;
  };
</script></body></html>"""


def _open(ui, ui_page, *, sync=True):
    ui.server_cfg.browser_view_url = DEAD_SCREEN
    ui_page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=ui.url)
    ui_page.route("**/browser-view/vnc.html*",
                  lambda route: route.fulfill(content_type="text/html", body=_FAKE_NOVNC))
    ui_page.goto(f"{ui.url}/#/browser")
    expect(ui_page.locator("iframe.browser-screen")).to_have_count(1)
    if not sync:
        ui_page.locator("input[data-clipboard-sync]").uncheck()
    screen = ui_page.frame_locator("iframe.browser-screen")
    expect(screen.locator("#noVNC_clipboard_text")).to_have_count(1)
    frame = ui_page.locator("iframe.browser-screen").element_handle().content_frame()
    until(lambda: frame.evaluate("typeof window.receive") == "function", what="fake noVNC up", page=ui_page)
    return screen, frame


def _host_copy(ui_page, text):
    ui_page.evaluate("(t) => navigator.clipboard.writeText(t)", text)


def _host_clipboard(ui_page):
    return ui_page.evaluate("() => navigator.clipboard.readText()")


def test_a_click_on_the_screen_never_reads_the_clipboard(ui, ui_page):
    """2026-11-05: 0.499.0 read the clipboard on every press of the screen, and Chrome answers a
    clipboard read with its own "Paste"/"Einfügen" prompt — which swallowed every click, so the
    operator could not use the browser at all. A click is a click; nothing is sent."""
    screen, frame = _open(ui, ui_page)
    _host_copy(ui_page, "copied on the host")
    ui_page.evaluate("""() => { window.reads = 0; const r = navigator.clipboard.readText.bind(
        navigator.clipboard); navigator.clipboard.readText = () => { window.reads++; return r(); }; }""")
    frame.evaluate("""() => { window.reads = 0; const r = navigator.clipboard.readText.bind(
        navigator.clipboard); navigator.clipboard.readText = () => { window.reads++; return r(); }; }""")
    screen.locator("body").click()
    screen.locator("body").click()
    ui_page.wait_for_timeout(300)
    assert frame.evaluate("window.sent") == []
    assert ui_page.evaluate("window.reads") == 0 and frame.evaluate("window.reads") == 0


def test_the_button_sends_what_the_operator_copied_to_the_guest(ui, ui_page):
    screen, frame = _open(ui, ui_page)
    _host_copy(ui_page, "copied on the host")
    ui_page.locator("button[data-clipboard-send]").click()
    until(lambda: frame.evaluate("window.sent") == ["copied on the host"],
          what="the host clipboard handed to noVNC's send path", page=ui_page)
    # the panel holds it too, which is what noVNC's own send reads
    assert frame.evaluate(
        "document.getElementById('noVNC_clipboard_text').value") == "copied on the host"


def test_what_the_guest_copied_lands_on_this_machines_clipboard(ui, ui_page):
    screen, frame = _open(ui, ui_page)
    _host_copy(ui_page, "before")
    screen.locator("body").click()           # the screen has focus, as when he is using it
    frame.evaluate("window.receive('copied in the container')")
    until(lambda: _host_clipboard(ui_page) == "copied in the container",
          what="the guest's copy written to the host clipboard", page=ui_page)
    assert frame.evaluate("window.sent") == [], "not bounced straight back into the guest"


def test_with_the_switch_off_neither_direction_syncs(ui, ui_page):
    """OFF is the absence of the grant AND of the bridge, so the switch now controls a channel
    that exists in both directions — not a permission nothing used."""
    screen, frame = _open(ui, ui_page, sync=False)
    assert "clipboard" not in (ui_page.locator("iframe.browser-screen").get_attribute("allow") or "")
    _host_copy(ui_page, "stays on the host")
    screen.locator("body").click()
    ui_page.wait_for_timeout(500)
    assert frame.evaluate("window.sent") == []
    frame.evaluate("window.receive('stays in the container')")
    ui_page.wait_for_timeout(500)
    assert _host_clipboard(ui_page) == "stays on the host"
