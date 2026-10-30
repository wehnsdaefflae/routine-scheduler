"""The browser screen's CLIPBOARD SYNC toggle (D221).

The operator asked for the screen to share his host's clipboard — "can you make the novnc
browser share the clipboard with my host machine?" — and chose option A, the full two-way
clipboard, over the recommended one-way paste. Then, seeing that the channel was already open:
"add a checkox if we want the clipboard to sync or not. under the novnc iframe".

So this is not a new channel, it is a SWITCH on one that exists. What makes the clipboard reach
the guest is the iframe's permission grant (`allow="clipboard-read; clipboard-write"`): the
embedded noVNC page can only read or write the host clipboard while the embedder permits it.
x11vnc runs with no `-nosel`/`-noclipboard` (deploy/chrome-entrypoint.sh), so the RFB side
already relays the X CLIPBOARD selection both ways, and the full-screen page already drops
`view_only` (tests/ui/test_browser_screen.py). The switch therefore belongs on the permission.

WHY A RELOAD IS PART OF THE CONTRACT: Permissions Policy is evaluated when a document loads, so
changing `allow` on a live frame does nothing until it loads again. A toggle that silently did
not take effect is worse than no toggle, so flipping it re-mounts the frame and the control says
so.

The DOCK's read-only mirror is NOT involved and must stay as it is: it carries `view_only=1`
and `pointer-events: none`, so there is nothing there to sync.
"""
from __future__ import annotations

from playwright.sync_api import expect

#: An address with nothing behind it — the same specimen tests/ui/test_browser_screen.py uses.
#: This file is about the CONTROL, which is rendered from configuration alone; whether a
#: framebuffer ever arrives is a different question and that file's.
DEAD_SCREEN = "http://127.0.0.1:1/vnc.html"


def _goto_browser(ui, ui_page):
    ui.server_cfg.browser_view_url = DEAD_SCREEN
    ui_page.goto(f"{ui.url}/#/browser")
    expect(ui_page.locator("iframe.browser-screen")).to_have_count(1)


def test_the_toggle_is_under_the_frame_and_syncs_by_default(ui, ui_page):
    """His choice was option A, so the default is ON — and the control sits BELOW the iframe,
    where he asked for it, not in the header row above it."""
    _goto_browser(ui, ui_page)

    box = ui_page.locator("input[data-clipboard-sync]")
    expect(box).to_be_visible()
    expect(box).to_be_checked()

    # "under the novnc iframe" is a layout claim, so assert the ORDER in the document rather
    # than merely that both exist.
    after = ui_page.evaluate(
        """() => {
             const f = document.querySelector('iframe.browser-screen');
             const c = document.querySelector('input[data-clipboard-sync]');
             return !!(f && c) && (f.compareDocumentPosition(c)
                                   & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
           }""")
    assert after, "the clipboard toggle must render after (below) the noVNC iframe"

    # Checked means the frame is PERMITTED to reach the clipboard.
    allow = ui_page.locator("iframe.browser-screen").get_attribute("allow") or ""
    assert "clipboard-read" in allow and "clipboard-write" in allow, allow


def test_unchecking_it_takes_the_clipboard_permission_away_from_the_frame(ui, ui_page):
    """The switch has to do something a page can observe: with sync off the frame carries no
    clipboard permission at all, so the guest cannot read what the operator last copied."""
    _goto_browser(ui, ui_page)

    ui_page.locator("input[data-clipboard-sync]").uncheck()

    frame = ui_page.locator("iframe.browser-screen")
    expect(frame).to_have_count(1)                      # re-mounted, not removed
    allow = frame.get_attribute("allow") or ""
    assert "clipboard" not in allow, f"clipboard permission survived the toggle: {allow!r}"


def test_the_choice_survives_a_reload(ui, ui_page):
    """A per-session switch the operator has to re-set on every visit is a switch he will stop
    using. The preference is remembered by the console (storage, not the server: it is a
    property of the browser he is sitting at)."""
    _goto_browser(ui, ui_page)
    ui_page.locator("input[data-clipboard-sync]").uncheck()

    ui_page.reload()
    expect(ui_page.locator("iframe.browser-screen")).to_have_count(1)
    expect(ui_page.locator("input[data-clipboard-sync]")).not_to_be_checked()
    allow = ui_page.locator("iframe.browser-screen").get_attribute("allow") or ""
    assert "clipboard" not in allow, allow

    # And back on again, so the memory is a preference and not a one-way door.
    ui_page.locator("input[data-clipboard-sync]").check()
    ui_page.reload()
    expect(ui_page.locator("input[data-clipboard-sync]")).to_be_checked()
    allow = ui_page.locator("iframe.browser-screen").get_attribute("allow") or ""
    assert "clipboard-read" in allow, allow


def test_the_control_says_what_flipping_it_does(ui, ui_page):
    """Permissions Policy is read at load, so the frame is re-mounted — which reconnects the
    screen. A control that reconnects a live session without saying so is a surprise."""
    _goto_browser(ui, ui_page)
    label = ui_page.locator("label[data-clipboard-sync-label]")
    expect(label).to_contain_text("clipboard")
    title = label.get_attribute("title") or ""
    assert "reconnect" in title.lower(), title
