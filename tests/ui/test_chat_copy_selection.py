"""R3085: a chat message's corner controls must not become part of the text you select.

The operator reported, from conversation c-20261029-080013, that the copy button on a chat
message's code block "copies the glyph, not the code" — he clicked it and got `⧉`.

**Clicking is not where that can happen, and this file says so with a test rather than with a
claim.** `static/md.js` renders every fence as `<pre><code>…</code></pre>` (lines 102 and 147,
the only two places it emits a `<pre>`), and `chat.js`'s `fenceBtns` reads `code.textContent`
with `pre.textContent` only as a fallback for a `<pre>` that has no `<code>` child — which that
renderer never produces. `tests/ui/test_flows.py::test_code_fence_copy_button` already asserts
the clipboard equals exactly the fence's source after a click, and it passes.

What CAN put the glyph in his clipboard is the other way of copying: every one of these controls
is a CHILD of the thing it acts on (deliberately — each is pinned to a corner, `views.css`), so
dragging across the code block or the message and pressing ⌘/Ctrl-C selects the buttons' text
along with the content. Nothing in the DOM excluded them, and `user-select: none` is the
one-property fix: a control stays clickable and focusable, it simply stops being TEXT that a
selection can take.

**Where each control lives, because a test that looks in the wrong bubble proves nothing.** ⧉
`copy-fence` sits in any `<pre>`, so a user message with a fence has one. ⧉ `copy-msg` is on
every bubble, and so is ↩ `refer-btn` — which runs and finish banners share
(`views.css:351`), so the fix lands there too. ⑂ `branch-msg`, ⟲ `rewind-msg` and ⚑ `flag-msg`
are a REPLY's only —
returns null without its callback and an integer turn (`chat.js:137,148,162`) — so they exist
only on `.msg.assistant`, and that is why these tests seed a finished run with a reply instead
of reading a freshly composed message. Every check asserts the control is PRESENT before it
asserts anything about it: a selector that matches nothing would otherwise make this whole file
pass over a console that had lost the fix.
"""

from __future__ import annotations

import json

import pytest
import yaml
from playwright.sync_api import expect

from rsched.paths import atomic_write_json

from .helpers import start_conversation

pytestmark = pytest.mark.ui

#: Every corner control a REPLY carries. `refer-btn` is the ↩ shared with runs and finish
#: banners (`views.css:351`), and the red-first run found it the same way: stripping the fix made
#: a reply's selection end `↩⧉⑂⟲⚑`, so the ↩ was riding every copied message too.
BUBBLE_CONTROLS = ("refer-btn", "copy-msg", "branch-msg", "rewind-msg", "flag-msg")

TS = "20261031-090000"
FENCE = "sudo apt install cowsay"
REPLY = f"Run this:\n```\n{FENCE}\n```\nthen tell me what it says."

#: A finished two-turn run whose reply carries a fence — one bubble holding all five controls.
EVENTS = [
    {"type": "header", "run_id": "X", "routine": "X",
     "workflow": {"slug": "converse", "commit": "abc", "version": 3}, "depth": 0},
    {"type": "assistant_action", "turn": 1, "usage": {"in": 9, "out": 1, "model": "dummy/m"},
     "payload": {"kind": "finish", "say": "replying", "status": "ok", "summary": REPLY}},
    {"ts": "2026-10-31T09:01:00+00:00", "type": "finish", "turns": 1,
     "usage_total": {"in": 9, "out": 1},
     "payload": {"status": "ok", "summary": REPLY, "authored": True}},
]

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

_STYLE = """(sel) => {
    const node = document.querySelector(sel);
    return node ? getComputedStyle(node).userSelect : 'MISSING';
}"""


def _with_a_reply(ui, ui_page):
    """A conversation whose transcript holds one finished reply carrying a code fence."""
    _, conv_dir = start_conversation(ui, ui_page, "How do I install cowsay?")
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
    raw["models"] = {"main": "m"}
    (conv_dir / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    run_dir = conv_dir / "runs" / TS
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in EVENTS), encoding="utf-8")
    atomic_write_json(run_dir / "status.json", {"state": "finished", "turn": 1})
    ui_page.reload()
    expect(ui_page.locator(".msg.assistant").first).to_be_visible()


def test_selecting_a_code_block_does_not_take_the_copy_glyph(ui, ui_page):
    """Select the whole code block the way a person does — a drag over it — and read back what
    the browser would copy. The glyph must not be in it, and the code must be.
    """
    _with_a_reply(ui, ui_page)
    block = ".msg.assistant .msg-body pre"
    expect(ui_page.locator(f"{block} .copy-fence")).to_have_count(1)   # never vacuous
    selected = ui_page.evaluate(_SELECT, block)
    assert FENCE in selected
    assert "⧉" not in selected, f"the copy glyph is selectable with the code: {selected!r}"


def test_selecting_a_whole_reply_does_not_take_its_corner_controls(ui, ui_page):
    """A reply's own ↩ ⧉ ⑂ ⟲ ⚑ sit inside `.msg` for the same layout reason, so the same
    selection would otherwise carry all five.
    """
    _with_a_reply(ui, ui_page)
    reply = ui_page.locator(".msg.assistant").first
    for cls in BUBBLE_CONTROLS:
        expect(reply.locator(f".{cls}")).to_have_count(1)              # never vacuous
    selected = ui_page.evaluate(_SELECT, ".msg.assistant")
    assert "then tell me what it says." in selected
    for glyph in ("↩", "⧉", "⑂", "⟲", "⚑"):
        assert glyph not in selected, f"{glyph} is selectable with the reply: {selected!r}"


def test_every_corner_control_declares_it_is_not_selectable_text(ui, ui_page):
    """The computed style, per control — so a later rule that re-enables selection on any ONE
    of them fails here rather than surprising somebody's clipboard.
    """
    _with_a_reply(ui, ui_page)
    for sel in (".msg.assistant .msg-body pre .copy-fence",
                *(f".msg.assistant .{cls}" for cls in BUBBLE_CONTROLS)):
        got = ui_page.evaluate(_STYLE, sel)
        assert got == "none", f"{sel} has user-select: {got!r}, so a selection takes its glyph"


def test_a_user_message_control_is_covered_too(ui, ui_page):
    """The same rule has to hold on the bubble a person writes: it carries ⧉ `copy-msg`, and a
    fence the user typed carries ⧉ `copy-fence`. Only the three reply-only controls are absent.
    """
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill(f"try:\n```\n{FENCE}\n```\nthanks")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    expect(ui_page.locator(".msg.user .msg-body pre .copy-fence")).to_have_count(1)
    for sel in (".msg.user .copy-msg", ".msg.user .refer-btn",
                ".msg.user .msg-body pre .copy-fence"):
        assert ui_page.evaluate(_STYLE, sel) == "none", f"{sel} is selectable text"
    selected = ui_page.evaluate(_SELECT, ".msg.user .msg-body pre")
    assert FENCE in selected
    assert "⧉" not in selected, f"the copy glyph rides the user's own code: {selected!r}"


def test_the_button_still_copies_the_fence_after_the_fix(ui, ui_page):
    """`user-select: none` must not break the CLICK — the thing the operator was trying to use.
    `test_flows.py::test_code_fence_copy_button` owns that assertion; it is repeated here so
    this file fails if the fix is ever made by moving the button OUT of the `<pre>` instead.
    """
    ui_page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    _with_a_reply(ui, ui_page)
    pre = ui_page.locator(".msg.assistant").first.locator("pre")
    pre.hover()
    pre.locator(".copy-fence").click()
    got = ui_page.evaluate("navigator.clipboard.readText()")
    assert got.strip() == FENCE
