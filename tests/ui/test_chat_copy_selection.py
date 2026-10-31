"""R3085: a chat code block's copy BUTTON must not become part of the code you select.

The operator reported, from conversation c-20261029-080013, that the copy button on a chat
message's code block "copies the glyph, not the code" — he clicked it and got `⧉`.

**Clicking is not where that can happen, and this file says so with a test rather than with a
claim.** `static/md.js` renders every fence as `<pre><code>…</code></pre>` (lines 102 and 147,
the only two places it emits a `<pre>`), and `chat.js`'s `fenceBtns` reads `code.textContent`
with `pre.textContent` only as a fallback for a `<pre>` that has no `<code>` child — which that
renderer never produces. `tests/ui/test_flows.py::test_code_fence_copy_button` already asserts
the clipboard equals exactly the fence's source after a click, and it passes.

What CAN put the glyph in his clipboard is the other way of copying: the button is a child of
the `<pre>` (deliberately — it is pinned to the block's corner, `views.css`), so dragging across
the code block and pressing ⌘/Ctrl-C selects the button's text along with the code. Nothing in
the DOM excluded it, and `user-select: none` is the one-property fix: the control is still
clickable and still focusable, it simply stops being TEXT that a selection can take.

The same applied to the bubble's own corner controls (⧉ ⑂ ⟲ ⚑), so they are fixed and checked
here too: selecting a whole message would otherwise take four glyphs with it.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

CONTROLS = ("copy-fence", "copy-msg", "branch-msg", "rewind-msg", "flag-msg")

_SELECT = """(sel) => {
    const node = document.querySelector(sel);
    if (!node) return 'MISSING';
    const range = document.createRange();
    range.selectNodeContents(node);
    const s = window.getSelection();
    s.removeAllRanges();
    s.addRange(range);
    return s.toString();
}"""


def _start(ui, ui_page, text: str):
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill(text)
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")


def test_selecting_a_code_block_does_not_take_the_copy_glyph(ui, ui_page):
    """Select the whole code block the way a person does — a drag over it — and read back what
    the browser would copy. The glyph must not be in it, and the code must be.
    """
    _start(ui, ui_page, "run this:\n```\nsudo apt install cowsay\n```\nthen tell me")
    ui_page.locator(".msg.user .msg-body pre").first.wait_for()
    selected = ui_page.evaluate(_SELECT, ".msg.user .msg-body pre")
    assert "sudo apt install cowsay" in selected
    assert "⧉" not in selected, f"the copy glyph is selectable with the code: {selected!r}"


def test_selecting_a_whole_message_does_not_take_its_corner_controls(ui, ui_page):
    """The bubble's own ⧉ ⑂ ⟲ ⚑ sit inside `.msg` for the same layout reason, so the same
    selection would otherwise carry all four.
    """
    _start(ui, ui_page, "Copy me: the quick brown fox.")
    ui_page.locator(".msg.user").first.wait_for()
    selected = ui_page.evaluate(_SELECT, ".msg.user")
    assert "Copy me: the quick brown fox." in selected
    for glyph in ("⧉", "⑂", "⟲", "⚑"):
        assert glyph not in selected, f"{glyph} is selectable with the message: {selected!r}"


def test_every_corner_control_declares_it_is_not_selectable_text(ui, ui_page):
    """The computed style, per control — so a later rule that re-enables selection on any ONE
    of them fails here rather than surprising somebody's clipboard.
    """
    _start(ui, ui_page, "with a fence:\n```\necho hi\n```\nand prose")
    ui_page.locator(".msg.user .msg-body pre .copy-fence").wait_for(state="attached")
    for cls in CONTROLS:
        got = ui_page.evaluate(
            """(cls) => {
                const node = document.querySelector('.msg.user .' + cls);
                return node ? getComputedStyle(node).userSelect : 'MISSING';
            }""", cls)
        assert got == "none", f".{cls} has user-select: {got!r}, so a selection takes its glyph"


def test_the_button_still_copies_the_fence_after_the_fix(ui, ui_page):
    """`user-select: none` must not break the CLICK — the thing the operator was trying to use.
    `test_flows.py::test_code_fence_copy_button` owns that assertion; it is repeated here so
    this file fails if the fix is ever made by moving the button OUT of the `<pre>` instead.
    """
    ui_page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    _start(ui, ui_page, "run this:\n```\nsudo apt install cowsay\n```\nthen tell me")
    pre = ui_page.locator(".msg.user").first.locator("pre")
    pre.hover()
    pre.locator(".copy-fence").click()
    got = ui_page.evaluate("navigator.clipboard.readText()")
    assert got.strip() == "sudo apt install cowsay"
